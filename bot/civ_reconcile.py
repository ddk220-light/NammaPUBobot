# -*- coding: utf-8 -*-
"""Background safety-net that fills in civs for matches the live recorder missed.

The live path (bot/stats/stats.py -> civ_matcher.schedule) records civs when a
match is reported, but misses matches whose result was never reported (e.g. the
match was lost from active_matches), whose AoE2 API data wasn't ready inside the
~11-minute retry window, or that errored. Result: qc_match_civs only ever held a
fraction of matches.

This job periodically sweeps qc_matches for rows with NO civs and re-runs the
linking via the AoE2 API (civ_matcher._find_and_record), writing to qc_match_civs
with the bot's own DB creds. It does NOT post replay links (would spam old
matches). A small qc_civ_reconcile table tracks attempts so permanently
un-linkable matches (e.g. too few mapped players, or an old game no longer in the
API's recent window) aren't re-fetched forever.

Registered on the 1s think() tick (bot/events.py). Each sweep runs as a
background task so a slow batch never blocks the tick.
"""
import asyncio
import time

from core.console import log
from core.database import db

from .civ_matcher import _find_and_record
from .civ_sync import find_and_record_lobby_from_history

# Tracks reconcile attempts per bot match so we don't re-hit the API forever for
# matches that will never link. status: 'pending' (keep trying) | 'done' | 'gaveup'.
db.ensure_table(dict(
	tname="qc_civ_reconcile",
	columns=[
		dict(cname="bot_match_id", ctype=db.types.int),
		dict(cname="attempts", ctype=db.types.int),
		dict(cname="last_at", ctype=db.types.int),
		dict(cname="status", ctype=db.types.str),
	],
	primary_keys=["bot_match_id"]
))

# Keep references so create_task'd sweeps aren't garbage-collected mid-run.
_pending = set()


class CivReconcile:
	SWEEP_INTERVAL = 180   # seconds between sweeps
	BATCH = 5              # matches processed per sweep (each ~ up to 8 API calls)
	MAX_ATTEMPTS = 5       # stop retrying a match after this many tries
	RETRY_BACKOFF = 3600   # seconds before a still-'pending' match is retried

	def __init__(self):
		self.next_run = 0
		self._running = False

	async def think(self, frame_time):
		# Only one sweep in flight at a time; cadence-gated by next_run.
		if self._running or frame_time < self.next_run:
			return
		self.next_run = frame_time + self.SWEEP_INTERVAL
		self._running = True
		task = asyncio.create_task(self._sweep())
		_pending.add(task)

		def _done(t):
			self._running = False
			_pending.discard(t)
			if not t.cancelled() and t.exception() is not None:
				log.error(f"Civ reconcile sweep crashed: {t.exception()}")

		task.add_done_callback(_done)

	async def _candidates(self):
		"""Recent-first matches with no civs that are due for a (re)try."""
		now = int(time.time())
		return await db.fetchall(
			"SELECT m.match_id, m.channel_id, m.winner, m.`at` "
			"FROM qc_matches m "
			"LEFT JOIN qc_match_civs c ON c.bot_match_id = m.match_id "
			"LEFT JOIN qc_civ_reconcile r ON r.bot_match_id = m.match_id "
			"WHERE c.bot_match_id IS NULL "
			"  AND (r.bot_match_id IS NULL "
			"       OR (r.status = 'pending' AND r.attempts < %s AND r.last_at < %s)) "
			"ORDER BY m.match_id DESC LIMIT %s",
			[self.MAX_ATTEMPTS, now - self.RETRY_BACKOFF, self.BATCH]
		)

	@staticmethod
	async def _players(match_id):
		rows = await db.fetchall(
			"SELECT user_id, nick, team FROM qc_player_matches WHERE match_id=%s", [match_id]
		)
		return [(r["user_id"], r["nick"], r["team"]) for r in rows]

	@staticmethod
	async def _mark(match_id, status):
		now = int(time.time())
		existing = await db.select_one(["attempts"], "qc_civ_reconcile", where={"bot_match_id": match_id})
		if existing:
			await db.update(
				"qc_civ_reconcile",
				{"attempts": existing["attempts"] + 1, "last_at": now, "status": status},
				keys={"bot_match_id": match_id}
			)
		else:
			await db.insert(
				"qc_civ_reconcile",
				{"bot_match_id": match_id, "attempts": 1, "last_at": now, "status": status}
			)

	async def _sweep(self):
		candidates = await self._candidates()
		if not candidates:
			return
		linked = 0
		for r in candidates:
			match_id = r["match_id"]
			status = "pending"
			try:
				players = await self._players(match_id)
				done = await _find_and_record(
					r["channel_id"], match_id, players, r["winner"], r["at"], post_replay=False
				)
				if not done:
					try:
						from core.client import dc
						channel = dc.get_channel(r["channel_id"])
						done = await find_and_record_lobby_from_history(
							channel, r["channel_id"], match_id, players, r["winner"], r["at"]
						)
					except Exception as e:
						log.error(f"Civ history reconcile error for match {match_id}: {e}")
				if await db.fetchone("SELECT 1 AS x FROM qc_match_civs WHERE bot_match_id=%s LIMIT 1", [match_id]):
					status, linked = "done", linked + 1
				elif done:
					status = "gaveup"   # resolved but unmappable (too few mapped players) — stop trying
				# else: transient (API not ready yet) -> stay 'pending', retried after backoff
			except Exception as e:
				log.error(f"Civ reconcile error for match {match_id}: {e}")
			await self._mark(match_id, status)
		log.info(f"Civ reconcile: swept {len(candidates)} matches, linked {linked}.")


reconcile = CivReconcile()
