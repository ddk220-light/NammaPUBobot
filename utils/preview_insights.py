#!/usr/bin/env python3
"""Preview / replay the team-insights storyline for recent matches.

Reconstructs what ``nammaoe2bot/features/storylines/insights.build_insights_embed`` WOULD have posted
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
import random
import sys
import types

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS_DIR, ".."))

# Load the analysis helpers from nammaoe2bot/features/storylines/insights WITHOUT dragging in the bot's
# DB/Discord import chain. team_insights only imports `nammaoe2bot.runtime.database` (at load)
# and `nammaoe2bot.runtime.utils.join_and` (lazily, inside _phrase) — stub both so the pure
# scoring/selection/phrasing functions load with just aiomysql present.
_fake_db = types.ModuleType("nammaoe2bot.runtime.database")
_fake_db.db = None
sys.modules.setdefault("nammaoe2bot.runtime.database", _fake_db)
_fake_utils = types.ModuleType("nammaoe2bot.runtime.utils")
_fake_utils.join_and = lambda names: (", ".join(names[:-1]) + f" & {names[-1]}") if len(names) > 1 else names[0]
sys.modules.setdefault("nammaoe2bot.runtime.utils", _fake_utils)

sys.path.insert(0, _THIS_DIR)        # db_helpers
sys.path.insert(0, _REPO_ROOT)       # repo root (for the `bot` package path)

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "team_insights", os.path.join(_REPO_ROOT, "bot", "insights.py")
)
ti = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ti)

# Load nammaoe2bot/features/storylines/payoff.py the same way, without dragging in bot/__init__.py
# (which imports nextcord). storyline_payoff does `from nammaoe2bot.features.storylines import insights`,
# so register a bare `bot` package in sys.modules pointing at the already-loaded
# `ti` module — that satisfies the import without executing bot/__init__.py.
sys.modules["bot"] = types.ModuleType("bot")
sys.modules["bot"].__path__ = [os.path.join(_REPO_ROOT, "bot")]
sys.modules["bot"].team_insights = ti
sys.modules["nammaoe2bot.features.storylines.insights"] = ti

_sp_spec = importlib.util.spec_from_file_location(
    "storyline_payoff", os.path.join(_REPO_ROOT, "bot", "storyline_payoff.py")
)
sp = importlib.util.module_from_spec(_sp_spec)
_sp_spec.loader.exec_module(sp)

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
        "SELECT user_id, nick, team FROM match_players WHERE match_id=%s AND channel_id=%s",
        (mid, ch),
    )
    nick = {p["user_id"]: p["nick"] for p in pms}
    team0 = [p for p in pms if p["team"] == 0]
    team1 = [p for p in pms if p["team"] == 1]

    print("=" * 74)
    print(f"#{mid}  {mrow['queue_name']}  ·  {_fmt_when(mrow['reported_at'])} UTC  ·  "
          f"{'ranked' if mrow['ranked'] else 'unranked'}  ·  channel {ch}")
    a_name = mrow["alpha_name"] or "Alpha"
    b_name = mrow["beta_name"] or "Beta"
    print(f"  {a_name}: " + (", ".join(nick[p['user_id']] for p in team0) or "(none)"))
    print(f"  {b_name}: " + (", ".join(nick[p['user_id']] for p in team1) or "(none)"))

    if not team0 or not team1:
        print("  → no insights (not a two-team match)")
        return

    t0 = [p["user_id"] for p in team0]
    t1 = [p["user_id"] for p in team1]
    user_ids = t0 + t1
    placeholders = ", ".join(["%s"] * len(user_ids))
    rows = await _fetchall(
        pool,
        "SELECT pm.match_id, pm.user_id, pm.nick, pm.team, m.winner "
        "FROM match_players pm "
        "JOIN matches m ON m.match_id = pm.match_id AND m.channel_id = pm.channel_id "
        "WHERE pm.channel_id = %s AND m.ranked = 1 AND pm.team IS NOT NULL "
        f"AND m.match_id < %s AND m.reported_at >= %s AND pm.user_id IN ({placeholders}) "
        "ORDER BY pm.match_id ASC",
        (ch, mid, ti.window_start(mrow["reported_at"]), *user_ids),
    )
    hist = ti._index_history(rows)
    if not hist.order:
        print("  → no insights (no prior ranked history for these players)")
        return

    meta = [{"name": a_name, "emoji": ""}, {"name": b_name, "emoji": ""}]
    rosters = {0: t0, 1: t1}
    rng = random.Random(mid)
    # This harness reads history ONCE (cut off at `mid`) and drives both the tease
    # section above and the payoff section below off that single hist/rng. The live
    # bot reads twice -- once at team-formation, once at report time -- and the
    # window, roster and seed can all drift between those two reads (that drift is
    # exactly what nammaoe2bot/features/storylines/insights.py's storyline_ctx stash and
    # nammaoe2bot/features/storylines/payoff.py's module docstring exist to pin down). So a clean run
    # through this harness is evidence about the copy reading well; it is not
    # evidence that the tease and payoff will agree live, because this harness
    # cannot reproduce that drift by construction.
    chosen = ti._select(ti._candidates(hist.order, hist.matches, t0, t1), rng=rng)
    if not chosen:
        print(f"  → nothing surfaced ({len(hist.order)} prior games, nothing met the thresholds)")
        return
    print(f"  ⚔️ Tale of the Tape (from {len(hist.order)} prior ranked games):")
    for c in chosen:
        print("    " + ti._phrase(c, nick, meta, rosters, rng=rng))

    winner = mrow.get("winner")
    if winner is None:
        print("  ⚔️ Final Tale of the Tape: draw — nothing to settle")
        return
    team_of = {**{u: 0 for u in t0}, **{u: 1 for u in t1}}
    # build_payoff_embed seeds its OWN Random(seed) and re-runs _select on it, so
    # its stream has absorbed the selection draws — but not the tease's phrasing
    # draws — by the time it phrases anything. Mirror that exactly, or the preview
    # shows a variant the bot will never post for this match.
    payoff_rng = random.Random(mid)
    payoff_chosen = ti._select(ti._candidates(hist.order, hist.matches, t0, t1), rng=payoff_rng)
    print("  ⚔️ Final Tale of the Tape:")
    for c in payoff_chosen:
        verdict = sp.resolve(c, winner, team_of)
        if verdict is None:
            continue
        print("    " + sp.payoff_phrase(c, verdict, nick, meta, rosters, rng=payoff_rng))


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
            "SELECT match_id, channel_id, queue_name, reported_at, ranked, winner, alpha_name, beta_name "
            f"FROM matches {where}ORDER BY reported_at DESC, match_id DESC LIMIT %s",
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
