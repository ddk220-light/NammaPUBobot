# -*- coding: utf-8 -*-
"""Replay-stats ingest job on the shared 1-s think() tick. Self-isolating and cadence-gated
like QuizJobs — a failure here can never break the tick. Does nothing unless rs_config.enabled.
One match per sweep (bounded load, polite to aoe.ms)."""
import asyncio
import os
import time

from core.console import log

from . import policy, store
from .fetch import fetch_replay
from .parse import parse_replay
from . import PARSER_VERSION

_pending = set()


class ReplayStatsJobs:
    POLL_INTERVAL = 150     # seconds between ingest sweeps

    def __init__(self):
        self.next_run = 0
        self._running = False
        self._reopened = False   # one-time parser-version reopen per process

    async def think(self, frame_time):
        try:
            if self._running or frame_time < self.next_run:
                return
            self.next_run = frame_time + self.POLL_INTERVAL
            self._running = True
            task = asyncio.create_task(self._run())

            def _done(t):
                self._running = False
                _pending.discard(t)
                if not t.cancelled() and t.exception() is not None:
                    log.error(f"Replay-stats job crashed: {t.exception()}")

            _pending.add(task)
            task.add_done_callback(_done)
        except Exception as e:
            self._running = False
            log.error(f"Replay-stats think() error (ignored): {e}")

    async def _run(self):
        if not await store.is_enabled():
            return
        if not self._reopened:
            await store.reopen_pending_parser_update(PARSER_VERSION)
            self._reopened = True
        now = int(time.time())
        work = await store.find_new_match()
        if work:
            await self.ingest_one(work["aoe2_match_id"], work.get("bot_match_id"),
                                  work.get("at"), now)
            return
        retry = await store.find_due_retry(now)
        if retry:
            await self.ingest_one(retry["aoe2_match_id"], None, None, now,
                                  attempts=retry.get("attempts") or 0,
                                  first_seen_at=retry.get("first_seen_at") or now)

    async def ingest_one(self, aoe2_match_id, bot_match_id, played_at_epoch, now,
                         attempts=0, first_seen_at=None):
        """Run one match through fetch -> gate/parse -> store. Updates rs_ingest. Bulletproof."""
        first_seen_at = first_seen_at or now
        try:
            await store.upsert_ingest(aoe2_match_id, status="processing", attempts=attempts,
                                      first_seen_at=first_seen_at, last_attempt_at=now)
            path, fstatus = await fetch_replay(aoe2_match_id)
            if not path:
                return await self._mark_unavailable(aoe2_match_id, attempts, first_seen_at, now, fstatus)

            resolved = await asyncio.to_thread(_load_resolved)
            date_map = {aoe2_match_id: _date_str(played_at_epoch)} if played_at_epoch else {}
            result, pstatus, sv = await parse_replay(path, resolved, date_map)
            _safe_unlink(path)

            if pstatus == "pending_parser_update":
                await store.upsert_ingest(aoe2_match_id, status="pending_parser_update",
                                          save_version=sv, parser_version=PARSER_VERSION,
                                          attempts=attempts, error_reason="save_version too new")
                return
            if pstatus != "ok" or not result:
                if policy.parse_failed_exhausted(attempts + 1):
                    return await store.upsert_ingest(aoe2_match_id, status="gave_up",
                                                     attempts=attempts + 1, error_reason="parse_failed")
                return await store.upsert_ingest(aoe2_match_id, status="parse_failed",
                                                 save_version=sv, attempts=attempts + 1,
                                                 next_attempt_at=now + 3600, error_reason="parse error")

            await store.write_match(result, bot_match_id, now, PARSER_VERSION)
            await store.upsert_ingest(aoe2_match_id, status="done", save_version=sv,
                                      parser_version=PARSER_VERSION, attempts=attempts + 1)
            log.info(f"Replay-stats ingested aoe2 match {aoe2_match_id} (save {sv}).")
        except Exception as e:
            log.error(f"Replay-stats ingest({aoe2_match_id}) failed: {e}")
            await store.upsert_ingest(aoe2_match_id, status="parse_failed", attempts=attempts + 1,
                                      next_attempt_at=now + 3600, error_reason=str(e)[:180])

    async def _mark_unavailable(self, aoe2_match_id, attempts, first_seen_at, now, reason):
        if policy.should_give_up_unavailable(first_seen_at, now):
            return await store.upsert_ingest(aoe2_match_id, status="gave_up", attempts=attempts,
                                             error_reason=f"unavailable:{reason}")
        await store.upsert_ingest(aoe2_match_id, status="unavailable", attempts=attempts + 1,
                                  first_seen_at=first_seen_at,
                                  next_attempt_at=now + policy.unavailable_backoff(attempts),
                                  error_reason=reason)


def _load_resolved():
    import sys
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if root not in sys.path:
        sys.path.insert(0, root)
    from utils.replay_quiz.extract import load_resolved
    return load_resolved()


def _date_str(epoch):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(epoch)))


def _safe_unlink(path):
    try:
        os.remove(path)
    except OSError:
        pass


jobs = ReplayStatsJobs()
