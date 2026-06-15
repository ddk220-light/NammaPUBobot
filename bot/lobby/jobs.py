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
	FLOOR_SECONDS = 15 * 60  # don't poll a launched game for completion until 15 min in
	POLL_CONCURRENCY = 5     # max in-flight completion resolutions
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
		await self._poll_completions(now)

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

	async def _poll_completions(self, now):
		"""Phase 3 — select due in_progress / awaiting_confirm rows and dispatch
		each to completed.resolve_row as a GC-tracked background task. One SELECT
		per pass; per-row scheduling lives in last_edit_at (next-poll timestamp).
		Two-status OR uses raw SQL because db.select where= is equality-only."""
		try:
			rows = await db.fetchall(
				"SELECT id, aoe2_game_id, match_id, channel_id, profile_ids, "
				"created_at, last_edit_at, completed_message_id, status "
				"FROM qc_lobbies WHERE status IN ('in_progress','awaiting_confirm')"
			)
		except Exception as e:
			log.error(f"Lobby poll select skipped: {e}")
			return
		if not rows:
			return
		from bot.lobby import completed
		sem = asyncio.Semaphore(self.POLL_CONCURRENCY)
		for r in rows:
			row_id = r.get("id")
			# Skip rows already being resolved: a resolution can take up to the
			# API timeout (~15s) while the poll pass fires every 15s — without this
			# the same row would be dispatched twice and double-post. _inflight.add
			# is synchronous (no await before it), so a row is claimed atomically.
			if row_id in _inflight or not self._due(r, now):
				continue
			_inflight.add(row_id)
			task = asyncio.create_task(self._guarded_resolve(completed, r, sem))
			_pending.add(task)
			task.add_done_callback(_pending.discard)

	def _due(self, row, now):
		"""15-min floor since launch, then gated by the per-row next-poll timestamp
		stored in last_edit_at (reboot-safe; no stored attempt counter)."""
		created = row.get("created_at") or 0
		if now - created < self.FLOOR_SECONDS:
			return False
		return now >= (row.get("last_edit_at") or 0)

	async def _guarded_resolve(self, completed, row, sem):
		try:
			async with sem:
				await completed.resolve_row(row)
		except Exception as e:
			log.error(f"Lobby resolve_row({row.get('id')}) failed: {e}")
		finally:
			_inflight.discard(row.get("id"))


# Keep create_task'd background jobs from being GC'd mid-run (civ_matcher pattern).
_pending = set()
# Row ids currently being resolved — prevents the same qc_lobbies row being
# dispatched by two overlapping poll passes (single-process guard).
_inflight = set()

jobs = LobbyJobs()
