#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Analyze past match data.

This script:
1. Fetches the last 10 match IDs from the bot's MySQL database
2. Displays match details (teams, players, results, ratings)
3. Optionally fetches AoE2 DE online match data via AoE2 Companion API

Usage:
    # Analyze matches from the bot database
    python utils/analyze_matches.py --db

    # Fetch last 10 online matches for a player by profile ID
    python utils/analyze_matches.py --online --profile-id 196240

    # Fetch last N matches (default 10)
    python utils/analyze_matches.py --online --profile-id 196240 --count 5
"""

import argparse
import asyncio
import json
import sys
import os
import urllib.request
import urllib.error
from datetime import datetime
from importlib.machinery import SourceFileLoader

# Add project root to path so we can import bot modules
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

AOE2_COMPANION_API = "https://data.aoe2companion.com/api"


# ── AoE2 Companion API (online matches) ─────────────────────────────────────

def fetch_online_matches(profile_id, count=10):
    """Fetch recent matches for a player from AoE2 Companion API."""
    url = f"{AOE2_COMPANION_API}/matches?profile_ids={profile_id}&count={count}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NammaPUBobot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            return data.get("matches", [])
    except urllib.error.HTTPError as e:
        print(f"Error: HTTP {e.code} fetching matches from AoE2 Companion API")
        return []
    except urllib.error.URLError as e:
        print(f"Error: Could not reach AoE2 Companion API: {e.reason}")
        return []


def print_online_match(match, index):
    """Pretty-print a single online match."""
    match_id = match.get("matchId", "N/A")
    started = match.get("started")
    finished = match.get("finished")
    leaderboard = match.get("leaderboard", "Unknown")
    map_name = match.get("map", "Unknown")

    start_str = datetime.fromisoformat(started).strftime("%Y-%m-%d %H:%M") if started else "N/A"
    if finished and started:
        duration_sec = (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds()
        duration_str = f"{int(duration_sec // 60)}m {int(duration_sec % 60)}s"
    else:
        duration_str = "N/A"

    print(f"\n{'='*60}")
    print(f"  Match #{index+1}  |  ID: {match_id}")
    print(f"  {leaderboard}  |  Map: {map_name}")
    print(f"  Started: {start_str}  |  Duration: {duration_str}")
    print(f"{'='*60}")

    teams = match.get("teams", [])
    for team in teams:
        team_id = team.get("teamId", "?")
        players = team.get("players", [])
        for p in players:
            name = p.get("name", "Unknown")
            rating = p.get("rating", "?")
            rating_diff = p.get("ratingDiff")
            civ = p.get("civName", "Unknown")
            won = p.get("won")
            country = p.get("country", "")

            result_icon = "W" if won is True else ("L" if won is False else "?")
            diff_str = f" ({rating_diff:+d})" if rating_diff is not None else ""
            country_str = f" [{country.upper()}]" if country else ""

            print(f"  [{result_icon}] Team {team_id}: {name} ({rating}{diff_str}) - {civ}{country_str}")


def analyze_online_matches(matches):
    """Print aggregate stats from online matches."""
    if not matches:
        print("\nNo matches to analyze.")
        return

    print(f"\n{'='*60}")
    print(f"  AGGREGATE ANALYSIS  ({len(matches)} matches)")
    print(f"{'='*60}")

    # Collect all unique maps and leaderboards
    maps = {}
    leaderboards = {}
    total_duration = 0
    duration_count = 0

    for m in matches:
        map_name = m.get("map", "Unknown")
        maps[map_name] = maps.get(map_name, 0) + 1

        lb = m.get("leaderboard", "Unknown")
        leaderboards[lb] = leaderboards.get(lb, 0) + 1

        started = m.get("started")
        finished = m.get("finished")
        if started and finished:
            dur = (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds()
            total_duration += dur
            duration_count += 1

    print("\n  Maps played:")
    for name, count in sorted(maps.items(), key=lambda x: -x[1]):
        print(f"    {name}: {count}")

    print("\n  Game modes:")
    for name, count in sorted(leaderboards.items(), key=lambda x: -x[1]):
        print(f"    {name}: {count}")

    if duration_count:
        avg_min = (total_duration / duration_count) / 60
        print(f"\n  Average match duration: {avg_min:.1f} minutes")


# ── Bot Database (internal matches) ─────────────────────────────────────────

async def fetch_db_matches(count=10):
    """Fetch the last N matches from the bot's MySQL database."""
    try:
        cfg = SourceFileLoader('cfg', os.path.join(PROJECT_ROOT, 'config.cfg')).load_module()
    except Exception:
        print("Error: Could not load config.cfg. Copy config.example.cfg and fill in DB_URI.")
        return None

    db_uri = getattr(cfg, 'DB_URI', '')
    if not db_uri:
        print("Error: DB_URI not set in config.cfg")
        return None

    import aiomysql

    # Parse DB_URI: mysql://user:password@hostname:port/database
    uri = db_uri
    for prefix in ('mysql://', 'mysql+aiomysql://'):
        if uri.startswith(prefix):
            uri = uri[len(prefix):]
            break

    user, rest = uri.split(':', 1)
    password, rest = rest.split('@', 1)
    host_part, db_name = rest.split('/', 1)
    if ':' in host_part:
        host, port = host_part.split(':')
        port = int(port)
    else:
        host = host_part
        port = 3306

    pool = await aiomysql.create_pool(
        host=host, user=user, password=password, db=db_name,
        port=port, charset='utf8mb4', autocommit=True,
        cursorclass=aiomysql.cursors.DictCursor
    )

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # Get last N matches
            await cur.execute(
                "SELECT * FROM qc_matches ORDER BY match_id DESC LIMIT %s", (count,)
            )
            matches = await cur.fetchall()

            if not matches:
                print("No matches found in the database.")
                pool.close()
                await pool.wait_closed()
                return []

            match_ids = [m['match_id'] for m in matches]

            # Get players for those matches
            format_ids = ','.join(['%s'] * len(match_ids))
            await cur.execute(
                f"SELECT * FROM qc_player_matches WHERE match_id IN ({format_ids}) "
                f"ORDER BY match_id DESC, team ASC",
                match_ids
            )
            players = await cur.fetchall()

            # Get rating history for those matches
            await cur.execute(
                f"SELECT * FROM qc_rating_history WHERE match_id IN ({format_ids}) "
                f"ORDER BY match_id DESC",
                match_ids
            )
            ratings = await cur.fetchall()

    pool.close()
    await pool.wait_closed()

    # Group players and ratings by match_id
    players_by_match = {}
    for p in players:
        players_by_match.setdefault(p['match_id'], []).append(p)

    ratings_by_match = {}
    for r in ratings:
        if r['match_id'] is not None:
            ratings_by_match.setdefault(r['match_id'], {})[r['user_id']] = r

    return matches, players_by_match, ratings_by_match


def print_db_match(match, players, ratings, index):
    """Pretty-print a single database match."""
    match_id = match['match_id']
    queue_name = match.get('queue_name', 'Unknown')
    at = match.get('at')
    ranked = match.get('ranked', False)
    winner = match.get('winner')
    alpha_name = match.get('alpha_name', 'Alpha')
    beta_name = match.get('beta_name', 'Beta')
    alpha_score = match.get('alpha_score')
    beta_score = match.get('beta_score')
    maps = match.get('maps', '')

    time_str = datetime.fromtimestamp(at).strftime("%Y-%m-%d %H:%M") if at else "N/A"
    ranked_str = "Ranked" if ranked else "Unranked"

    if winner == 0:
        result_str = f"{alpha_name} wins"
    elif winner == 1:
        result_str = f"{beta_name} wins"
    elif winner is None and ranked:
        result_str = "Draw"
    else:
        result_str = "Not reported"

    score_str = ""
    if alpha_score is not None and beta_score is not None:
        score_str = f"  |  Score: {alpha_score}-{beta_score}"

    print(f"\n{'='*60}")
    print(f"  Match #{index+1}  |  ID: {match_id}  |  {ranked_str}")
    print(f"  Queue: {queue_name}  |  {time_str}")
    print(f"  Result: {result_str}{score_str}")
    if maps:
        print(f"  Maps: {', '.join(maps.split(chr(10)))}")
    print(f"{'='*60}")

    match_players = players.get(match_id, [])
    match_ratings = ratings.get(match_id, {})

    alpha_players = [p for p in match_players if p.get('team') == 0]
    beta_players = [p for p in match_players if p.get('team') == 1]
    unassigned = [p for p in match_players if p.get('team') is None]

    for team_name, team_players in [(alpha_name, alpha_players), (beta_name, beta_players)]:
        if team_players:
            marker = " *" if (
                (winner == 0 and team_name == alpha_name) or
                (winner == 1 and team_name == beta_name)
            ) else ""
            print(f"\n  {team_name}{marker}:")
            for p in team_players:
                nick = p.get('nick', 'Unknown')
                r = match_ratings.get(p['user_id'])
                if r:
                    rating_str = f" (Rating: {r['rating_before']} -> {r['rating_before'] + r['rating_change']})"
                else:
                    rating_str = ""
                print(f"    - {nick}{rating_str}")

    if unassigned:
        print(f"\n  Unassigned:")
        for p in unassigned:
            print(f"    - {p.get('nick', 'Unknown')}")


def analyze_db_matches(matches, players_by_match, ratings_by_match):
    """Print aggregate stats from database matches."""
    if not matches:
        print("\nNo matches to analyze.")
        return

    print(f"\n{'='*60}")
    print(f"  AGGREGATE ANALYSIS  ({len(matches)} matches)")
    print(f"{'='*60}")

    ranked_count = sum(1 for m in matches if m.get('ranked'))
    unranked_count = len(matches) - ranked_count

    queues = {}
    for m in matches:
        q = m.get('queue_name', 'Unknown')
        queues[q] = queues.get(q, 0) + 1

    all_maps = {}
    for m in matches:
        if m.get('maps'):
            for map_name in m['maps'].split('\n'):
                if map_name.strip():
                    all_maps[map_name.strip()] = all_maps.get(map_name.strip(), 0) + 1

    # Collect unique players
    unique_players = set()
    for plist in players_by_match.values():
        for p in plist:
            unique_players.add((p['user_id'], p.get('nick', 'Unknown')))

    print(f"\n  Ranked: {ranked_count}  |  Unranked: {unranked_count}")
    print(f"  Unique players: {len(unique_players)}")

    if queues:
        print("\n  Queues:")
        for name, count in sorted(queues.items(), key=lambda x: -x[1]):
            print(f"    {name}: {count}")

    if all_maps:
        print("\n  Maps:")
        for name, count in sorted(all_maps.items(), key=lambda x: -x[1]):
            print(f"    {name}: {count}")

    # Rating changes summary
    all_changes = []
    for match_ratings in ratings_by_match.values():
        for uid, r in match_ratings.items():
            all_changes.append(r['rating_change'])

    if all_changes:
        avg_change = sum(abs(c) for c in all_changes) / len(all_changes)
        max_gain = max(all_changes)
        max_loss = min(all_changes)
        print(f"\n  Rating changes:")
        print(f"    Avg absolute change: {avg_change:.1f}")
        print(f"    Biggest gain: +{max_gain}")
        print(f"    Biggest loss: {max_loss}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Analyze past AoE2 match data from bot DB or AoE2 Companion API."
    )

    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--db", action="store_true",
        help="Fetch matches from the bot's MySQL database"
    )
    source.add_argument(
        "--online", action="store_true",
        help="Fetch matches from AoE2 Companion API (requires --profile-id)"
    )

    parser.add_argument(
        "--profile-id", type=int,
        help="AoE2 DE profile ID for online match lookup"
    )
    parser.add_argument(
        "--count", type=int, default=10,
        help="Number of recent matches to fetch (default: 10)"
    )

    args = parser.parse_args()

    if args.online and not args.profile_id:
        parser.error("--online requires --profile-id")

    if args.online:
        print(f"Fetching last {args.count} online matches for profile {args.profile_id}...")
        matches = fetch_online_matches(args.profile_id, args.count)

        if not matches:
            print("No matches found.")
            return

        print(f"Found {len(matches)} matches.")

        for i, match in enumerate(matches):
            print_online_match(match, i)

        analyze_online_matches(matches)

        # Print match IDs summary
        print(f"\n{'='*60}")
        print(f"  MATCH IDs (last {len(matches)})")
        print(f"{'='*60}")
        for i, m in enumerate(matches):
            started = m.get("started", "")
            if started:
                started = datetime.fromisoformat(started).strftime("%Y-%m-%d %H:%M")
            print(f"  {i+1}. {m.get('matchId', 'N/A')}  ({started})")

    elif args.db:
        result = asyncio.run(fetch_db_matches(args.count))
        if result is None:
            return

        matches, players_by_match, ratings_by_match = result

        if not matches:
            print("No matches found in database.")
            return

        print(f"Found {len(matches)} matches in database.")

        for i, match in enumerate(matches):
            print_db_match(match, players_by_match, ratings_by_match, i)

        analyze_db_matches(matches, players_by_match, ratings_by_match)

        # Print match IDs summary
        print(f"\n{'='*60}")
        print(f"  MATCH IDs (last {len(matches)})")
        print(f"{'='*60}")
        for i, m in enumerate(matches):
            at = m.get('at')
            time_str = datetime.fromtimestamp(at).strftime("%Y-%m-%d %H:%M") if at else "N/A"
            print(f"  {i+1}. {m['match_id']}  ({time_str})")


if __name__ == "__main__":
    main()
