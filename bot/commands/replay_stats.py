# -*- coding: utf-8 -*-
"""Slash-command handlers for the replay-stats pipeline (admin). Thin: logic lives in
bot.replay_stats. All bot.replay_stats imports are lazy so this module loads during the
`from . import commands` step without pulling heavy modules early."""
__all__ = ["replaystats_status", "replaystats_enable", "replaystats_disable",
           "replaystats_backfill", "replaystats_reingest"]


async def replaystats_status(ctx):
    from bot.replay_stats import store, PARSER_VERSION
    from core.database import db
    counts = await db.fetchall("SELECT status, COUNT(*) n FROM rs_ingest GROUP BY status")
    done = await db.fetchall("SELECT MAX(parsed_at) m, COUNT(*) n FROM rs_matches")
    pend = await db.fetchall(
        "SELECT save_version, COUNT(*) n FROM rs_ingest WHERE status='pending_parser_update' "
        "GROUP BY save_version")
    enabled = await store.is_enabled()
    parts = [f"Replay-stats **{'ON' if enabled else 'OFF'}** · parser `{PARSER_VERSION}`"]
    parts.append("Ingest: " + (", ".join(f"{r['status']}={r['n']}" for r in counts) or "none"))
    if done and done[0]["n"]:
        parts.append(f"Parsed matches: {done[0]['n']} (latest parsed_at {done[0]['m']})")
    if pend:
        parts.append("Pending parser update: " + ", ".join(f"save {r['save_version']}×{r['n']}" for r in pend))
    await ctx.reply("\n".join(parts))


async def replaystats_enable(ctx):
    ctx.check_perms(ctx.Perms.ADMIN)
    from bot.replay_stats import store
    await store.set_enabled(True)
    await ctx.success("Replay-stats ingestion enabled.", title="Replay-stats")


async def replaystats_disable(ctx):
    ctx.check_perms(ctx.Perms.ADMIN)
    from bot.replay_stats import store
    await store.set_enabled(False)
    await ctx.success("Replay-stats ingestion disabled.", title="Replay-stats")


async def replaystats_backfill(ctx, days=90):
    ctx.check_perms(ctx.Perms.ADMIN)
    from bot.replay_stats import backfill
    started = await backfill.kick_off(int(days))
    if started:
        await ctx.success(f"Backfill started for the last {int(days)} days (newest first). "
                          "Watch progress with /replaystats status.", title="Replay-stats")
    else:
        await ctx.error("A backfill is already running.")


async def replaystats_reingest(ctx, match_id):
    ctx.check_perms(ctx.Perms.ADMIN)
    from bot.replay_stats import store
    from bot.replay_stats.jobs import jobs
    import time
    await store.upsert_ingest(int(match_id), status="processing", attempts=0,
                              first_seen_at=int(time.time()))
    await jobs.ingest_one(int(match_id), None, None, int(time.time()))
    await ctx.success(f"Re-ingested aoe2 match {int(match_id)} (see /replaystats status).",
                      title="Replay-stats")
