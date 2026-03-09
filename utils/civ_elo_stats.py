#!/usr/bin/env python3
"""
Generate civ_elo_stats.csv — aggregate civ winrates by player elo and team elo brackets.

Reads match_civ_details.csv (from civ_analysis.py) and joins with rating history
to compute winrates at different elo thresholds.

Usage:
    python utils/civ_elo_stats.py                          # Read ratings from DB
    python utils/civ_elo_stats.py --csv                    # Read ratings from CSV exports
    python utils/civ_elo_stats.py --player-threshold 1000 --team-threshold 1100
"""

import asyncio
import csv
import os
import sys
from collections import defaultdict

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
from db_helpers import create_pool

DATA_DIR = os.path.join(PROJECT_ROOT, 'data')


# -- Data loading -------------------------------------------------------------

def load_match_civ_details():
    """Load match_civ_details.csv. Returns list of dicts."""
    path = os.path.join(DATA_DIR, 'match_civ_details.csv')
    rows = []
    with open(path, 'r') as f:
        for row in csv.DictReader(f):
            rows.append({
                'bot_match_id': int(row['bot_match_id']),
                'nick': row['nick'],
                'team': int(row['team']),
                'civ': row['civ'],
                'won': row['result'] == 'W',
            })
    return rows


def load_nick_to_userid_from_csv():
    """Build nick -> user_id map from qc_players.csv."""
    path = os.path.join(DATA_DIR, 'qc_players.csv')
    mapping = {}
    with open(path, 'r') as f:
        for row in csv.DictReader(f):
            mapping[row['nick']] = row['user_id']
    return mapping


def load_rating_history_from_csv():
    """Load qc_rating_history.csv. Returns dict of (match_id, user_id) -> rating_before."""
    path = os.path.join(DATA_DIR, 'qc_rating_history.csv')
    ratings = {}
    with open(path, 'r') as f:
        for row in csv.DictReader(f):
            match_id = row['match_id']
            if match_id == 'NULL' or not match_id:
                continue
            key = (int(match_id), row['user_id'])
            ratings[key] = int(row['rating_before'])
    return ratings


async def load_nick_to_userid_from_db(pool):
    """Build nick -> user_id map from qc_players DB table."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT user_id, nick FROM qc_players")
            rows = await cur.fetchall()
    return {r['nick']: str(r['user_id']) for r in rows}


async def load_rating_history_from_db(pool):
    """Load rating history from DB. Returns dict of (match_id, user_id) -> rating_before."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT match_id, user_id, rating_before FROM qc_rating_history "
                "WHERE match_id IS NOT NULL"
            )
            rows = await cur.fetchall()
    return {(r['match_id'], str(r['user_id'])): r['rating_before'] for r in rows}


# -- Core logic ---------------------------------------------------------------

def compute_civ_elo_stats(civ_details, nick_to_uid, rating_history, player_threshold, team_threshold):
    """
    Join civ details with rating history, compute per-civ aggregates.

    For each row in civ_details:
      - Look up player elo via nick -> user_id -> (match_id, user_id) -> rating_before
      - Compute team avg elo for that match+team
      - Aggregate wins/games per civ across elo brackets
    """
    # Step 1: Enrich each row with player_elo
    enriched = []
    missing_nick = 0
    missing_rating = 0
    for row in civ_details:
        uid = nick_to_uid.get(row['nick'])
        if uid is None:
            missing_nick += 1
            enriched.append({**row, 'player_elo': None})
            continue
        key = (row['bot_match_id'], uid)
        elo = rating_history.get(key)
        if elo is None:
            missing_rating += 1
        enriched.append({**row, 'player_elo': elo, 'user_id': uid})

    print(f"  Enriched {len(enriched)} rows: {missing_nick} missing nick, {missing_rating} missing rating")

    # Step 2: Compute team avg elo per (match_id, team)
    team_elos = defaultdict(list)
    for row in enriched:
        if row['player_elo'] is not None:
            team_elos[(row['bot_match_id'], row['team'])].append(row['player_elo'])

    team_avg = {}
    for key, elos in team_elos.items():
        team_avg[key] = sum(elos) / len(elos)

    # Step 3: Aggregate by civ
    civ_stats = defaultdict(lambda: {
        'wins': 0, 'games': 0,
        'wins_player_above': 0, 'games_player_above': 0,
        'wins_player_below': 0, 'games_player_below': 0,
        'wins_team_above': 0, 'games_team_above': 0,
        'wins_team_below': 0, 'games_team_below': 0,
    })

    for row in enriched:
        civ = row['civ']
        won = 1 if row['won'] else 0
        s = civ_stats[civ]

        s['games'] += 1
        s['wins'] += won

        if row['player_elo'] is not None:
            if row['player_elo'] >= player_threshold:
                s['games_player_above'] += 1
                s['wins_player_above'] += won
            else:
                s['games_player_below'] += 1
                s['wins_player_below'] += won

        t_avg = team_avg.get((row['bot_match_id'], row['team']))
        if t_avg is not None:
            if t_avg >= team_threshold:
                s['games_team_above'] += 1
                s['wins_team_above'] += won
            else:
                s['games_team_below'] += 1
                s['wins_team_below'] += won

    return dict(civ_stats)


def write_csv(civ_stats, player_threshold, team_threshold):
    """Write civ_elo_stats.csv sorted alphabetically by civ."""
    output_path = os.path.join(DATA_DIR, 'civ_elo_stats.csv')
    pt, tt = player_threshold, team_threshold
    fieldnames = [
        'civ', 'games', 'winrate',
        f'games_player_elo_above_{pt}', f'winrate_player_elo_above_{pt}',
        f'games_player_elo_below_{pt}', f'winrate_player_elo_below_{pt}',
        f'games_team_elo_above_{tt}', f'winrate_team_elo_above_{tt}',
        f'games_team_elo_below_{tt}', f'winrate_team_elo_below_{tt}',
    ]

    def wr(wins, games):
        return f"{wins / games:.2f}" if games > 0 else "0.00"

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for civ in sorted(civ_stats.keys()):
            s = civ_stats[civ]
            writer.writerow({
                'civ': civ,
                'games': s['games'],
                'winrate': wr(s['wins'], s['games']),
                f'games_player_elo_above_{pt}': s['games_player_above'],
                f'winrate_player_elo_above_{pt}': wr(s['wins_player_above'], s['games_player_above']),
                f'games_player_elo_below_{pt}': s['games_player_below'],
                f'winrate_player_elo_below_{pt}': wr(s['wins_player_below'], s['games_player_below']),
                f'games_team_elo_above_{tt}': s['games_team_above'],
                f'winrate_team_elo_above_{tt}': wr(s['wins_team_above'], s['games_team_above']),
                f'games_team_elo_below_{tt}': s['games_team_below'],
                f'winrate_team_elo_below_{tt}': wr(s['wins_team_below'], s['games_team_below']),
            })

    return output_path


# -- Main ---------------------------------------------------------------------

async def async_main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate civ winrate stats by elo brackets.")
    parser.add_argument("--csv", action="store_true", help="Read from CSV exports instead of MySQL")
    parser.add_argument("--player-threshold", type=int, default=1000, help="Player elo cutoff (default: 1000)")
    parser.add_argument("--team-threshold", type=int, default=1100, help="Team avg elo cutoff (default: 1100)")
    args = parser.parse_args()

    player_threshold = args.player_threshold
    team_threshold = args.team_threshold
    print(f"Generating civ elo stats (player threshold: {player_threshold}, team threshold: {team_threshold})...\n")

    print("Loading match_civ_details.csv...")
    civ_details = load_match_civ_details()
    print(f"  {len(civ_details)} rows loaded")

    if args.csv:
        print("Loading ratings from CSV...")
        nick_to_uid = load_nick_to_userid_from_csv()
        rating_history = load_rating_history_from_csv()
    else:
        print("Loading ratings from DB...")
        pool = await create_pool()
        if pool is None:
            print("DB unavailable, falling back to CSV...")
            nick_to_uid = load_nick_to_userid_from_csv()
            rating_history = load_rating_history_from_csv()
        else:
            try:
                nick_to_uid = await load_nick_to_userid_from_db(pool)
                rating_history = await load_rating_history_from_db(pool)
            finally:
                pool.close()
                await pool.wait_closed()

    print(f"  {len(nick_to_uid)} player nick mappings")
    print(f"  {len(rating_history)} rating history entries")

    print("\nComputing civ stats by elo brackets...")
    civ_stats = compute_civ_elo_stats(civ_details, nick_to_uid, rating_history, player_threshold, team_threshold)

    output_path = write_csv(civ_stats, player_threshold, team_threshold)
    print(f"\nSaved {len(civ_stats)} civs to {output_path}")

    total_games = sum(s['games'] for s in civ_stats.values())
    rated_games = sum(s['games_player_above'] + s['games_player_below'] for s in civ_stats.values())
    print(f"  Total civ-games: {total_games}")
    print(f"  With player elo data: {rated_games} ({rated_games * 100 // total_games if total_games else 0}%)")


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
