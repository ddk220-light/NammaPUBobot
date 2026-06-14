# -*- coding: utf-8 -*-
"""Recurring lobby maintenance job, registered on the 1s think() tick.

Phase 1: a SKELETON. It cadence-gates like StatsJobs / CivReconcile and is
wrapped so that an error here can NEVER break the tick or touch the existing
match / civ / rating flow. The lobby feature is strictly opt-in and must do no
harm to the path that already works (create-your-own-lobby + manual /report).

It does no user-visible work yet. Phase 2/3 fill in, all inside THIS file (never
in the hot tick path):
  - boot rehydration of non-terminal qc_lobbies rows (re-open sockets / resume polls)
  - reaping watchers past their TTL
  - driving the IN_PROGRESS -> COMPLETED result poll on civ_matcher._RETRY_DELAYS

Wiring it in now (doing nothing) means later phases are additive edits here.
"""
import asyncio

from core.console import log
from core.database import db

# Keep create_task'd background jobs from being GC'd mid-run (civ_matcher pattern).
_pending = set()


class LobbyJobs:
	POLL_INTERVAL = 15                  # seconds between maintenance passes
	TERMINAL = ("completed", "expired")  # statuses that never need attention again

	def __init__(self):
		self.next_run = 0
		self._booted = False
		self._running = False

	async def think(self, frame_time):
		# Bulletproof by design: this runs on the shared tick alongside the core
		# match/rating jobs. Any failure must stay fully contained here.
		try:
			if self._running or frame_time < self.next_run:
				return
			self.next_run = frame_time + self.POLL_INTERVAL
			self._running = True
			task = asyncio.create_task(self._run())
			_pending.add(task)

			def _done(t):
				self._running = False
				_pending.discard(t)
				if not t.cancelled() and t.exception() is not None:
					log.error(f"Lobby job crashed: {t.exception()}")

			task.add_done_callback(_done)
		except Exception as e:
			# Never propagate into on_think — a broken lobby job must not starve
			# matches or the periodic state save.
			self._running = False
			log.error(f"Lobby think() error (ignored): {e}")

	async def _run(self):
		# One-time boot rehydration, then periodic maintenance. Both are no-ops
		# until Phase 2/3 register live watchers — a fresh deploy has no lobbies.
		if not self._booted:
			self._booted = True
			await self._rehydrate()
		# Phase 2/3: tick active watchers (result polls, TTL reaping) here.

	async def _rehydrate(self):
		"""On boot, recover lobbies a redeploy left mid-flight. Phase 1 writes
		nothing to qc_lobbies yet, so this finds nothing — but running it proves
		the query path + table exist before Phase 2 depends on them, and it is
		wrapped so a missing table on first boot can't surface an error."""
		try:
			rows = await db.select(
				["id", "aoe2_game_id", "channel_id", "message_id", "status"],
				"qc_lobbies",
			)
			live = [r for r in (rows or []) if r.get("status") not in self.TERMINAL]
			if live:
				log.info(f"Lobby rehydrate: {len(live)} non-terminal row(s) found (Phase 2 will resume them).")
		except Exception as e:
			log.error(f"Lobby rehydrate skipped: {e}")


jobs = LobbyJobs()
