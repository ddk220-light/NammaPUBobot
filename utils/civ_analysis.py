#!/usr/bin/env python3
"""
Analyze player civ performance from PUB bot matches.

Strategy:
1. For each mapped player, fetch ALL their matches from the API covering the time range
2. Build a pool of API matches indexed by (timestamp, player set)
3. For each bot match, find the corresponding API match by time + player overlap
4. Cache mappings in data/match_id_map.csv

Usage:
    python utils/civ_analysis.py
    python utils/civ_analysis.py --days 90
"""

import csv
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from collections import defaultdict

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
AOE2_API = "https://data.aoe2companion.com/api"
MATCH_MAP_PATH = os.path.join(PROJECT_ROOT, 'data', 'match_id_map.csv')

# Bot timestamps are IST (UTC+5:30), API timestamps are UTC.
# Bot records approximate end time, API records start time.
MAX_TIME_DIFF_MIN = 120
MIN_TIME_DIFF_MIN = 30


# ── Data loading ─────────────────────────────────────────────────────────────

def load_profile_map():
    """Load player_profile_map.csv. Returns (pid_to_nick, nick_to_pids)."""
    path = os.path.join(PROJECT_ROOT, 'data', 'player_profile_map.csv')
    pid_to_nick = {}
    nick_to_pids = {}
    with open(path, 'r') as f:
        for row in csv.DictReader(f):
            nick = row['nick']
            pid_str = row.get('profile_id', '').strip()
            if not pid_str:
                continue
            for pid in pid_str.split(' / '):
                pid = int(pid.strip())
                pid_to_nick[pid] = nick
                nick_to_pids.setdefault(nick, []).append(pid)
    return pid_to_nick, nick_to_pids


def load_bot_matches(cutoff):
    """Load bot matches since cutoff with their players."""
    matches_path = os.path.join(PROJECT_ROOT, 'data', 'qc_matches.csv')
    players_path = os.path.join(PROJECT_ROOT, 'data', 'qc_player_matches.csv')
    players_csv_path = os.path.join(PROJECT_ROOT, 'data', 'qc_players.csv')

    nick_lookup = {}
    with open(players_csv_path, 'r') as f:
        for row in csv.DictReader(f):
            nick_lookup[row['user_id']] = row['nick']

    matches = []
    with open(matches_path, 'r') as f:
        for row in csv.DictReader(f):
            at = datetime.strptime(row['at'], '%Y-%m-%d %H:%M:%S')
            winner = row['winner_team']
            if at >= cutoff and winner not in ('NULL', ''):
                matches.append({
                    'match_id': int(row['match_id']),
                    'at': at,
                    'winner_team': int(winner),
                })

    match_ids = {m['match_id'] for m in matches}
    player_matches = defaultdict(list)
    with open(players_path, 'r') as f:
        for row in csv.DictReader(f):
            mid = int(row['match_id'])
            if mid in match_ids:
                uid = row['user_id']
                player_matches[mid].append({
                    'user_id': uid,
                    'nick': nick_lookup.get(uid, uid),
                    'team': int(row['team']),
                })

    for m in matches:
        m['players'] = player_matches.get(m['match_id'], [])

    return matches


def load_match_id_map():
    """Load cached bot_match_id -> aoe2_match_id mappings (positive hits only)."""
    cache = {}
    if os.path.exists(MATCH_MAP_PATH):
        with open(MATCH_MAP_PATH, 'r') as f:
            for row in csv.DictReader(f):
                bot_id = int(row['bot_match_id'])
                aoe2_id = row['aoe2_match_id']
                if aoe2_id:
                    cache[bot_id] = int(aoe2_id)
    return cache


def save_match_id_map(cache):
    """Save the match ID map to CSV (only positive matches)."""
    with open(MATCH_MAP_PATH, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['bot_match_id', 'aoe2_match_id', 'matched_at'])
        for bot_id in sorted(cache.keys()):
            aoe2_id = cache[bot_id]
            if aoe2_id:
                writer.writerow([bot_id, aoe2_id, datetime.now().isoformat()])


# ── API interaction ──────────────────────────────────────────────────────────

def fetch_all_matches_for_player(profile_id, cutoff):
    """Fetch all matches for a player back to cutoff date."""
    all_matches = []
    page = 1
    while True:
        url = f"{AOE2_API}/matches?profile_ids={profile_id}&count=20&page={page}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "NammaPUBobot/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                matches = data.get("matches", [])
                if not matches:
                    break
                all_matches.extend(matches)

                # Check if we've gone past cutoff
                last_started = matches[-1].get("started", "")
                if last_started:
                    last_time = datetime.fromisoformat(
                        last_started.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                    if last_time < cutoff - timedelta(days=2):
                        break

                # Keep paginating if we got a full page
                if len(matches) < 20:
                    break
                page += 1
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            print(f" API error: {e}", file=sys.stderr)
            break
        time.sleep(0.2)
    return all_matches


# ── Match ID mapping ─────────────────────────────────────────────────────────

def find_aoe2_match_id(bot_match, nick_to_pids, pid_to_nick, api_pool):
    """
    Find the AoE2 match ID for a bot match from the pre-built API pool.

    Returns (aoe2_match_id, api_match_data) or (None, None).
    """
    bot_nicks = {p['nick'] for p in bot_match['players']}

    best_match = None
    best_score = 0

    for api_match in api_pool.values():
        started = api_match.get("started", "")
        if not started:
            continue
        api_time = datetime.fromisoformat(started.replace("Z", "+00:00")).replace(tzinfo=None)

        # Bot time (IST end) - API time (UTC start) should be ~85-100 min
        diff_min = (bot_match['at'] - api_time).total_seconds() / 60
        if not (MIN_TIME_DIFF_MIN < diff_min < MAX_TIME_DIFF_MIN):
            continue

        # Count player overlap
        api_pids = set()
        for team in api_match.get("teams", []):
            for player in team.get("players", []):
                api_pids.add(player.get("profileId"))

        overlap = 0
        for nick in bot_nicks:
            pids = nick_to_pids.get(nick, [])
            if any(pid in api_pids for pid in pids):
                overlap += 1

        score = overlap / len(bot_nicks) if bot_nicks else 0
        if score > best_score and score >= 0.5:
            best_score = score
            best_match = api_match

    if best_match:
        return best_match.get("matchId"), best_match
    return None, None


def find_player_in_match(api_match, profile_ids):
    """Find a player's data in an API match by profile ID."""
    for team in api_match.get("teams", []):
        for player in team.get("players", []):
            if player.get("profileId") in profile_ids:
                return player
    return None


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Analyze player civ performance from PUB bot matches.")
    parser.add_argument("--days", type=int, default=60, help="Number of days to look back (default: 60)")
    args = parser.parse_args()

    cutoff = datetime.now() - timedelta(days=args.days)

    print("Loading data...")
    pid_to_nick, nick_to_pids = load_profile_map()
    bot_matches = load_bot_matches(cutoff)
    cache = load_match_id_map()

    print(f"Found {len(bot_matches)} bot matches in last {args.days} days")
    print(f"Mapped {len(nick_to_pids)} players with profile IDs")
    print(f"Cached match mappings: {len(cache)}")

    # Determine which profile IDs are active in these bot matches
    active_pids = set()
    for m in bot_matches:
        for p in m['players']:
            for pid in nick_to_pids.get(p['nick'], []):
                active_pids.add(pid)

    # Phase 1: Build API match pool by fetching all matches per player
    print(f"\nPhase 1: Fetching API matches for {len(active_pids)} players...")
    api_pool = {}  # matchId -> match data
    for i, pid in enumerate(sorted(active_pids)):
        nick = pid_to_nick.get(pid, str(pid))
        print(f"  [{i+1}/{len(active_pids)}] {nick} (profile {pid})...", end="", flush=True)
        matches = fetch_all_matches_for_player(pid, cutoff)
        new = 0
        for m in matches:
            mid = m.get("matchId")
            if mid and mid not in api_pool:
                api_pool[mid] = m
                new += 1
        print(f" {len(matches)} fetched, {new} new (pool: {len(api_pool)})")
        time.sleep(0.2)

    print(f"\nTotal API matches in pool: {len(api_pool)}")

    # Phase 2: Match each bot match to an API match
    print(f"\nPhase 2: Matching {len(bot_matches)} bot matches...")
    player_civs = defaultdict(lambda: defaultdict(lambda: {"wins": 0, "losses": 0}))
    matched_count = 0
    cached_hits = 0

    for i, bot_match in enumerate(bot_matches):
        bot_id = bot_match['match_id']

        # Check cache
        if bot_id in cache:
            aoe2_id = cache[bot_id]
            api_match = api_pool.get(aoe2_id)
            if api_match:
                cached_hits += 1
                matched_count += 1
                status = f"cached -> {aoe2_id}"
            else:
                status = f"cached {aoe2_id} but not in pool"
                aoe2_id, api_match = find_aoe2_match_id(bot_match, nick_to_pids, pid_to_nick, api_pool)
                if aoe2_id:
                    cache[bot_id] = aoe2_id
                    matched_count += 1
                    status = f"re-found -> {aoe2_id}"
        else:
            aoe2_id, api_match = find_aoe2_match_id(bot_match, nick_to_pids, pid_to_nick, api_pool)
            if aoe2_id:
                cache[bot_id] = aoe2_id
                matched_count += 1
                status = f"found -> {aoe2_id}"
            else:
                status = "no match"

        print(f"  [{i+1}/{len(bot_matches)}] Bot {bot_id} ({bot_match['at'].strftime('%m-%d %H:%M')}): {status}")

        if not api_match:
            continue

        # Extract civ + win/loss per player
        for bp in bot_match['players']:
            nick = bp['nick']
            pids = nick_to_pids.get(nick, [])
            if not pids:
                continue
            player_data = find_player_in_match(api_match, set(pids))
            if not player_data:
                continue
            civ = player_data.get("civName", "Unknown")
            won = bp['team'] == bot_match['winner_team']
            if won:
                player_civs[nick][civ]["wins"] += 1
            else:
                player_civs[nick][civ]["losses"] += 1

    # Save match ID map
    save_match_id_map(cache)
    print(f"\nMatch ID map saved to {MATCH_MAP_PATH} ({len(cache)} entries)")

    total = len(bot_matches)
    pct = matched_count * 100 // total if total else 0
    print(f"Results: {matched_count}/{total} matched ({pct}%), "
          f"{cached_hits} from cache")

    # Phase 3: Output civ performance
    print(f"\n{'='*70}")
    print(f"  CIV PERFORMANCE ANALYSIS (last {args.days} days, PUB bot matches only)")
    print(f"{'='*70}")

    for nick in sorted(player_civs.keys(), key=str.lower):
        civs = player_civs[nick]
        if not civs:
            continue

        total_games = sum(c["wins"] + c["losses"] for c in civs.values())
        total_wins = sum(c["wins"] for c in civs.values())

        print(f"\n  {nick} ({total_wins}W/{total_games - total_wins}L across {len(civs)} civs)")
        print(f"  {'-'*50}")

        civ_stats = []
        for civ, stats in civs.items():
            games = stats["wins"] + stats["losses"]
            winrate = stats["wins"] / games if games > 0 else 0
            civ_stats.append((civ, stats["wins"], stats["losses"], games, winrate))

        civ_stats.sort(key=lambda x: (-x[4], -x[3]))

        print(f"  Top 5 Best:")
        for civ, w, l, g, wr in civ_stats[:5]:
            print(f"    {civ:20s}  {w}W/{l}L  ({wr:.0%})  [{g} games]")

        print(f"  Top 5 Worst:")
        worst = [c for c in reversed(civ_stats) if c[4] < 1.0][:5]
        for civ, w, l, g, wr in worst:
            print(f"    {civ:20s}  {w}W/{l}L  ({wr:.0%})  [{g} games]")

    # Save to CSV
    output_path = os.path.join(PROJECT_ROOT, 'data', 'player_civ_stats.csv')
    with open(output_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["nick", "civ", "wins", "losses", "games", "winrate"])
        for nick in sorted(player_civs.keys(), key=str.lower):
            for civ, stats in sorted(player_civs[nick].items()):
                games = stats["wins"] + stats["losses"]
                wr = stats["wins"] / games if games > 0 else 0
                writer.writerow([nick, civ, stats["wins"], stats["losses"], games, f"{wr:.2f}"])
    print(f"\nDetailed stats saved to {output_path}")


if __name__ == "__main__":
    main()
