# -*- coding: utf-8 -*-
"""Recurring lobby maintenance job, registered on the 1s think() tick.

Cadence-gates like StatsJobs / CivReconcile and is wrapped so that an error here
can NEVER break the tick or touch the existing match / civ / rating flow. The
lobby feature is strictly opt-in and must do no harm to the path that already
works (create-your-own-lobby + manual /report).

Phase 2 responsibilities:
  - boot rehydration log of non-terminal qc_lobbies rows
  - reap stale created/filling rows that never launched (one UPDATE, throttled)
Phase 3 adds the IN_PROGRESS -> COMPLETED result poll here. All edits land in
THIS file, never in the hot tick path.
"""
import asyncio
import time

from core.console import log
from core.database import db


class LobbyJobs:
	POLL_INTERVAL = 15      # seconds between maintenance passes
	REAP_INTERVAL = 600     # seconds between stale-row sweeps
	STALE_AFTER = 1800      # a created/filling row older than this never launched
	TERMINAL = ("completed", "expired")

	def __init__(self):
		self.next_run = 0
		self.next_reap = 0
		self._booted = False
		self._running = False

	async def think(self, frame_time):
		# Bulletproof by design: this runs on the shared tick alongside the core
		# match/rating jobs, so any failure must stay fully contained here.
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
					log.error(f"Lobby job crashed: {t.exception()}")

			_pending.add(task)
			task.add_done_callback(_done)
		except Exception as e:
			self._running = False
			log.error(f"Lobby think() error (ignored): {e}")

	async def _run(self):
		if not self._booted:
			self._booted = True
			await self._rehydrate()
		now = int(time.time())
		if now >= self.next_reap:
			self.next_reap = now + self.REAP_INTERVAL
			await self._reap_stale(now - self.STALE_AFTER)
		# Phase 3: drive in_progress completion polls here.

	async def _rehydrate(self):
		"""On boot, note any lobbies a redeploy left mid-flight. Phase 2 only logs
		them; Phase 3 resumes their completion polls. Wrapped so a missing table on
		first boot can't surface an error."""
		try:
			rows = await db.select(["id", "aoe2_game_id", "match_id", "status"], "qc_lobbies")
			live = [r for r in (rows or []) if r.get("status") not in self.TERMINAL]
			if live:
				log.info(f"Lobby rehydrate: {len(live)} non-terminal row(s) (Phase 3 will resume).")
		except Exception as e:
			log.error(f"Lobby rehydrate skipped: {e}")

	async def _reap_stale(self, cutoff):
		"""Expire created/filling rows that never reached in_progress — a lobby was
		announced/detected but the game never launched (or the match ended another
		way). One throttled UPDATE; nothing consumes these rows yet so it is pure
		hygiene."""
		try:
			await db.execute(
				"UPDATE qc_lobbies SET status='expired' "
				"WHERE status IN ('created','filling') AND created_at < %s",
				[cutoff],
			)
		except Exception as e:
			log.error(f"Lobby reap skipped: {e}")


# Keep create_task'd background jobs from being GC'd mid-run (civ_matcher pattern).
_pending = set()

jobs = LobbyJobs()
