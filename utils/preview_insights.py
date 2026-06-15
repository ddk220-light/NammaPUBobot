#!/usr/bin/env python3
"""Preview / replay the team-insights storyline for recent matches.

Reconstructs what ``bot/team_insights.build_insights_embed`` WOULD have posted
when each of the last N matches' teams were formed — using only the ranked
history that existed *before* that match (``match_id`` strictly less than the
target), so it's a faithful replay rather than hindsight.

Usage:
    python3 utils/preview_insights.py [N] [--channel CHANNEL_ID]

    N            how many recent matches to replay (default 5)
    --channel    restrict to one channel (default: across all channels)

Reads ``DB_URI`` from ``config.cfg`` (same as the bot). Needs aiomysql.
"""
import argparse
import asyncio
import datetime
import os
import sys
import types

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS_DIR, ".."))

# Load the analysis helpers from bot/team_insights WITHOUT dragging in the bot's
# DB/Discord import chain. team_insights only imports `core.database` (at load)
# and `core.utils.join_and` (lazily, inside _phrase) — stub both so the pure
# scoring/selection/phrasing functions load with just aiomysql present.
_fake_db = types.ModuleType("core.database")
_fake_db.db = None
sys.modules.setdefault("core.database", _fake_db)
_fake_utils = types.ModuleType("core.utils")
_fake_utils.join_and = lambda names: (", ".join(names[:-1]) + f" & {names[-1]}") if len(names) > 1 else names[0]
sys.modules.setdefault("core.utils", _fake_utils)

sys.path.insert(0, _THIS_DIR)        # db_helpers
sys.path.insert(0, _REPO_ROOT)       # repo root (for the `bot` package path)

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "team_insights", os.path.join(_REPO_ROOT, "bot", "team_insights.py")
)
ti = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ti)

from db_helpers import create_pool  # noqa: E402


async def _fetchall(pool, sql, args=()):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            return await cur.fetchall()


def _fmt_when(at):
    if not at:
        return "?"
    return datetime.datetime.fromtimestamp(int(at), datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")


async def preview_one(pool, mrow):
    mid, ch = mrow["match_id"], mrow["channel_id"]
    pms = await _fetchall(
        pool,
        "SELECT user_id, nick, team FROM qc_player_matches WHERE match_id=%s AND channel_id=%s",
        (mid, ch),
    )
    nick = {p["user_id"]: p["nick"] for p in pms}
    team0 = [p for p in pms if p["team"] == 0]
    team1 = [p for p in pms if p["team"] == 1]

    print("=" * 74)
    print(f"#{mid}  {mrow['queue_name']}  ·  {_fmt_when(mrow['at'])} UTC  ·  "
          f"{'ranked' if mrow['ranked'] else 'unranked'}  ·  channel {ch}")
    a_name = mrow["alpha_name"] or "Alpha"
    b_name = mrow["beta_name"] or "Beta"
    print(f"  {a_name}: " + (", ".join(nick[p['user_id']] for p in team0) or "(none)"))
    print(f"  {b_name}: " + (", ".join(nick[p['user_id']] for p in team1) or "(none)"))

    if not team0 or not team1:
        print("  → no insights (not a two-team match)")
        return

    user_ids = [p["user_id"] for p in pms if p["team"] in (0, 1)]
    placeholders = ", ".join(["%s"] * len(user_ids))
    rows = await _fetchall(
        pool,
        "SELECT pm.match_id, pm.user_id, pm.team, m.winner "
        "FROM qc_player_matches pm "
        "JOIN qc_matches m ON m.match_id = pm.match_id AND m.channel_id = pm.channel_id "
        "WHERE pm.channel_id = %s AND m.ranked = 1 AND pm.team IS NOT NULL "
        f"AND m.match_id < %s AND pm.user_id IN ({placeholders})",
        (ch, mid, *user_ids),
    )
    by_match = ti._index_history(rows)
    if not by_match:
        print("  → no insights (no prior ranked history for these players)")
        return

    t0 = [p["user_id"] for p in team0]
    t1 = [p["user_id"] for p in team1]
    synergy = ti._synergy_candidates(by_match, t0, 0) + ti._synergy_candidates(by_match, t1, 1)
    rivalry = ti._rivalry_candidates(by_match, t0, t1)
    chosen = ti._select(synergy, rivalry)
    if not chosen:
        print(f"  → nothing surfaced ({len(by_match)} prior games, nothing met the thresholds)")
        return

    meta = [{"name": a_name, "emoji": ""}, {"name": b_name, "emoji": ""}]
    print(f"  Insights (from {len(by_match)} prior ranked games):")
    for c in chosen:
        print("    " + ti._phrase(c, nick, meta))


async def main():
    ap = argparse.ArgumentParser(description="Replay team-insights for recent matches.")
    ap.add_argument("n", nargs="?", type=int, default=5, help="how many recent matches (default 5)")
    ap.add_argument("--channel", type=int, default=None, help="restrict to one channel id")
    args = ap.parse_args()

    pool = await create_pool()
    if pool is None:
        return
    try:
        where = "WHERE channel_id = %s " if args.channel else ""
        params = ([args.channel, args.n] if args.channel else [args.n])
        matches = await _fetchall(
            pool,
            "SELECT match_id, channel_id, queue_name, at, ranked, winner, alpha_name, beta_name "
            f"FROM qc_matches {where}ORDER BY at DESC, match_id DESC LIMIT %s",
            tuple(params),
        )
        if not matches:
            print("No matches found.")
            return
        for mrow in matches:
            await preview_one(pool, mrow)
    finally:
        pool.close()
        await pool.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
