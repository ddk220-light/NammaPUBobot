#!/usr/bin/env python3
"""
Analyze player civ performance from PUB bot matches (last 60 days).

Cross-references bot matches with AoE2 Companion API to get civ data,
then reports top 5 best and worst civs per player.
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
DAYS = 60
CUTOFF = datetime.now() - timedelta(days=DAYS)

# Bot timestamp offset: bot records ~IST end time, API records UTC start time
# Difference is roughly timezone (5:30) + game duration (~30min) = ~85-100 min
# We use a wide window for matching
MAX_TIME_DIFF_MIN = 120


def load_profile_map():
    """Load player_profile_map.csv, return dict of profile_id -> nick."""
    path = os.path.join(PROJECT_ROOT, 'data', 'player_profile_map.csv')
    pid_to_nick = {}
    nick_to_pids = {}
    with open(path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            nick = row['nick']
            pid_str = row.get('profile_id', '').strip()
            if not pid_str:
                continue
            # Handle multiple profile IDs (e.g. "6823812 / 9039952")
            for pid in pid_str.split(' / '):
                pid = int(pid.strip())
                pid_to_nick[pid] = nick
                nick_to_pids.setdefault(nick, []).append(pid)
    return pid_to_nick, nick_to_pids


def load_bot_matches():
    """Load bot matches from last 60 days with their players."""
    matches_path = os.path.join(PROJECT_ROOT, 'data', 'qc_matches.csv')
    players_path = os.path.join(PROJECT_ROOT, 'data', 'qc_player_matches.csv')
    players_csv_path = os.path.join(PROJECT_ROOT, 'data', 'qc_players.csv')

    # Load nick lookup
    nick_lookup = {}
    with open(players_csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            nick_lookup[row['user_id']] = row['nick']

    # Load matches
    matches = []
    with open(matches_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            at = datetime.strptime(row['at'], '%Y-%m-%d %H:%M:%S')
            winner = row['winner_team']
            if at >= CUTOFF and winner not in ('NULL', ''):
                matches.append({
                    'match_id': int(row['match_id']),
                    'at': at,
                    'winner_team': int(winner),
                })

    # Load player assignments
    match_ids = {m['match_id'] for m in matches}
    player_matches = defaultdict(list)
    with open(players_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
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


def fetch_api_matches(profile_id, count=100):
    """Fetch recent matches for a player from AoE2 Companion API."""
    all_matches = []
    page = 1
    while len(all_matches) < count:
        url = f"{AOE2_API}/matches?profile_ids={profile_id}&count=20&page={page}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "NammaPUBobot/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                matches = data.get("matches", [])
                if not matches:
                    break
                all_matches.extend(matches)
                if not data.get("hasMore", False):
                    break
                page += 1
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            print(f"  API error for profile {profile_id}: {e}", file=sys.stderr)
            break
        time.sleep(0.3)  # rate limiting

    # Filter to last 60 days
    result = []
    for m in all_matches:
        started = m.get("started")
        if started:
            st = datetime.fromisoformat(started.replace("Z", "+00:00")).replace(tzinfo=None)
            if st >= CUTOFF - timedelta(days=1):  # small buffer
                result.append(m)
            else:
                break  # matches are sorted by time desc
    return result


def find_player_in_match(api_match, profile_ids):
    """Find a player's data in an API match by profile ID."""
    for team in api_match.get("teams", []):
        for player in team.get("players", []):
            if player.get("profileId") in profile_ids:
                return player
    return None


def match_bot_to_api(bot_match, api_matches, nick_to_pids, pid_to_nick):
    """Find the best API match for a bot match based on player overlap and time."""
    bot_nicks = {p['nick'] for p in bot_match['players']}

    best = None
    best_score = 0

    for api_match in api_matches:
        started = api_match.get("started", "")
        if not started:
            continue
        api_time = datetime.fromisoformat(started.replace("Z", "+00:00")).replace(tzinfo=None)

        # Bot time is IST (~UTC+5:30), API is UTC
        # Bot records end time, API records start time
        # So bot_time - api_time should be ~85-100 min
        diff_min = (bot_match['at'] - api_time).total_seconds() / 60
        if not (30 < diff_min < MAX_TIME_DIFF_MIN):
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

        # Also check reverse: API profile IDs that map to bot nicks
        for pid in api_pids:
            mapped_nick = pid_to_nick.get(pid)
            if mapped_nick and mapped_nick in bot_nicks:
                pass  # already counted above

        score = overlap / len(bot_nicks) if bot_nicks else 0
        if score > best_score and score >= 0.5:
            best_score = score
            best = api_match

    return best, best_score


def main():
    print(f"Loading data...")
    pid_to_nick, nick_to_pids = load_profile_map()
    bot_matches = load_bot_matches()
    print(f"Found {len(bot_matches)} bot matches in last {DAYS} days")
    print(f"Mapped {len(nick_to_pids)} players with profile IDs")

    # Collect all unique profile IDs we need to query
    # Use the most active players to fetch API matches, then cross-reference
    active_pids = set()
    for m in bot_matches:
        for p in m['players']:
            pids = nick_to_pids.get(p['nick'], [])
            for pid in pids:
                active_pids.add(pid)

    print(f"\nFetching API matches for {len(active_pids)} profile IDs...")

    # Fetch API matches for each profile ID and build a combined pool
    api_match_pool = {}  # matchId -> match data
    for i, pid in enumerate(sorted(active_pids)):
        nick = pid_to_nick.get(pid, str(pid))
        print(f"  [{i+1}/{len(active_pids)}] Fetching matches for {nick} (profile {pid})...", end="", flush=True)
        matches = fetch_api_matches(pid, count=500)
        new = 0
        for m in matches:
            mid = m.get("matchId")
            if mid and mid not in api_match_pool:
                api_match_pool[mid] = m
                new += 1
        print(f" {len(matches)} matches ({new} new)")
        time.sleep(0.3)

    print(f"\nTotal unique API matches in pool: {len(api_match_pool)}")
    api_matches_list = sorted(api_match_pool.values(),
                               key=lambda m: m.get("started", ""), reverse=True)

    # Cross-reference each bot match with API matches
    print(f"\nCross-referencing bot matches with API matches...")
    # player_nick -> {civ: {wins: N, losses: N}}
    player_civs = defaultdict(lambda: defaultdict(lambda: {"wins": 0, "losses": 0}))
    matched_count = 0
    unmatched_count = 0

    for bot_match in bot_matches:
        api_match, score = match_bot_to_api(bot_match, api_matches_list, nick_to_pids, pid_to_nick)

        if not api_match:
            unmatched_count += 1
            continue

        matched_count += 1

        # For each bot player, find them in the API match and record their civ
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

    print(f"Matched: {matched_count}/{len(bot_matches)} ({matched_count*100//len(bot_matches)}%)")
    print(f"Unmatched: {unmatched_count}")

    # Output results
    print(f"\n{'='*70}")
    print(f"  CIV PERFORMANCE ANALYSIS (last {DAYS} days, PUB bot matches only)")
    print(f"{'='*70}")

    for nick in sorted(player_civs.keys(), key=str.lower):
        civs = player_civs[nick]
        if not civs:
            continue

        total_games = sum(c["wins"] + c["losses"] for c in civs.values())
        total_wins = sum(c["wins"] for c in civs.values())

        print(f"\n  {nick} ({total_wins}W/{total_games - total_wins}L across {len(civs)} civs)")
        print(f"  {'-'*50}")

        # Calculate win rate per civ (min 1 game)
        civ_stats = []
        for civ, stats in civs.items():
            games = stats["wins"] + stats["losses"]
            winrate = stats["wins"] / games if games > 0 else 0
            civ_stats.append((civ, stats["wins"], stats["losses"], games, winrate))

        # Sort by winrate desc, then by games desc
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
