# -*- coding: utf-8 -*-
"""One-time, resumable, newest-first backfill. Reuses the live ingest path (jobs.ingest_one),
so it writes the same rows and is idempotent. Kicked off by /replaystats backfill; runs as a
background asyncio task, one match at a time (polite to aoe.ms)."""
import asyncio
import time

from core.console import log

from . import store
from .jobs import jobs

_task = None


async def kick_off(days):
    """Start the backfill if not already running. Returns True if it started."""
    global _task
    if _task is not None and not _task.done():
        return False
    _task = asyncio.create_task(_run(days))
    return True


async def _run(days):
    await store.seed_profiles_from_csv()
    done = 0
    try:
        while True:
            work = await store.find_new_match(max_age_days=days)
            if not work:
                break
            now = int(time.time())
            await jobs.ingest_one(work["aoe2_match_id"], work.get("bot_match_id"),
                                  work.get("at"), now)
            done += 1
            if done % 20 == 0:
                log.info(f"Replay-stats backfill: {done} matches processed…")
            await asyncio.sleep(2)   # gentle pacing between external fetches
    except Exception as e:
        log.error(f"Replay-stats backfill error: {e}")
    log.info(f"Replay-stats backfill finished: {done} matches processed.")
