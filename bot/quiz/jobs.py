# -*- coding: utf-8 -*-
"""Daily-post / close-and-reveal / weekly-leaderboard job on the shared 1-s think()
tick. Bulletproof and cadence-gated like LobbyJobs — a failure here can never break
the tick or any existing flow. Does nothing unless a qc_quiz_config row has
enabled=1. nextcord / core.client / embeds are imported lazily inside the methods so
importing bot.quiz (hence this module) stays test-safe under the conftest stubs."""
import asyncio
import datetime
import json
import time

from core.console import log

from . import pool, scoring, store

_POOL = pool.load()        # loaded once at import; [] if not generated yet
_pending = set()           # keep create_task'd jobs from being GC'd mid-run


def _hour(value, default):
	"""Honour an explicitly-configured 0 (midnight / 00:00 UTC). `value or default`
	would wrongly override hour 0 because 0 is falsy; return the default only when the
	field is unset (None)."""
	return default if value is None else int(value)


class QuizJobs:
	POLL_INTERVAL = 30     # seconds between quiz maintenance passes

	def __init__(self):
		self.next_run = 0
		self._running = False

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
					log.error(f"Quiz job crashed: {t.exception()}")

			_pending.add(task)
			task.add_done_callback(_done)
		except Exception as e:
			self._running = False
			log.error(f"Quiz think() error (ignored): {e}")

	async def _run(self):
		now = int(time.time())
		await self._close_due(now)            # always resolve open posts, even if disabled
		cfg = await store.get_config()
		if not cfg or not cfg.get("enabled"):
			return
		await self._maybe_post_daily(cfg, now)
		await self._maybe_leaderboard(cfg, now)

	async def _maybe_post_daily(self, cfg, now):
		if not scoring.daily_due(now, _hour(cfg.get("quiz_hour"), 9), cfg.get("last_post_ymd")):
			return
		await self._post_question(
			cfg["channel_id"], int(cfg.get("open_window") or 86400), now,
			min_difficulty=cfg.get("min_difficulty"))

	async def _post_question(self, channel_id, open_window, now, min_difficulty=None):
		"""Pick the next unused question and post the card. Claims last_post_ymd only
		AFTER a confirmed post, so (a) a missing channel / empty pool leaves the day
		un-claimed for the next tick to retry rather than silently burning it, and
		(b) a manual /quiz post_now also satisfies the once-per-day dedup, so the
		scheduler won't double-post later the same day. The only double-post window is
		a crash strictly between channel.send and this claim (one DB round-trip) —
		accepted, and far rarer than the cold-cache miss it replaces. Returns the post
		id, or None when the pool is exhausted / the channel is missing."""
		asked = await store.asked_ids(channel_id)
		recent = await store.recent_categories(channel_id)
		q = pool.pick_next(_POOL, asked, recent, min_difficulty=min_difficulty)
		if not q:
			log.info("Quiz pool exhausted — nothing to post.")
			return None
		from core.client import dc
		from . import embeds
		channel = dc.get_channel(channel_id)
		if channel is None:
			log.error(f"Quiz channel {channel_id} not found.")
			return None
		post_id = await store.create_post(channel_id, q, now, now + open_window)
		msg = await channel.send(
			embed=embeds.card_embed(q["category"], q["difficulty"], post_id, open_window / 3600),
			view=embeds.card_view(post_id))
		await store.set_message_id(post_id, msg.id)
		await store.upsert_config(channel_id, last_post_ymd=scoring._ymd(now))
		log.info(f"Quiz posted #{post_id} ({q['id']}) in channel {channel_id}.")
		return post_id

	async def force_post(self, channel_id):
		"""Post a quiz immediately, ignoring the daily schedule (admin /quiz post_now).
		Returns the post id or None. Uses the channel's configured open_window /
		min_difficulty if any; claims the day (in _post_question) so the scheduler
		won't post a second quiz later today."""
		cfg = await store.get_config(channel_id)
		open_window = int((cfg or {}).get("open_window") or 86400)
		return await self._post_question(
			channel_id, open_window, int(time.time()),
			min_difficulty=(cfg or {}).get("min_difficulty"))

	async def _close_due(self, now):
		due = await store.due_to_close(now)
		if not due:
			return
		import nextcord
		from core.client import dc
		from . import embeds
		for post in due:
			try:
				ans = await store.answers_for_post(post["id"])
				winners = [a["nick"] for a in ans if int(a.get("is_correct") or 0)]
				options = json.loads(post["options_json"])
				channel = dc.get_channel(post["channel_id"])
				if channel and post.get("message_id"):
					try:
						msg = await channel.fetch_message(post["message_id"])
						await msg.edit(embed=embeds.result_embed(
							post["prompt"], options, post["correct_index"],
							post["explanation"], winners), view=None)
					except nextcord.NotFound:
						pass
				await store.close_post(post["id"])
			except Exception as e:
				log.error(f"Quiz close({post.get('id')}) failed: {e}")

	async def _maybe_leaderboard(self, cfg, now):
		if not scoring.leaderboard_due(now, cfg.get("leaderboard_dow") or 7,
									   _hour(cfg.get("leaderboard_hour"), 18),
									   cfg.get("last_leaderboard_ymd")):
			return
		await store.upsert_config(cfg["channel_id"], last_leaderboard_ymd=scoring._ymd(now))
		rows = await store.week_answers(cfg["channel_id"], now - 7 * 86400, now)
		tallied = scoring.tally(rows)
		from core.client import dc
		from . import embeds
		channel = dc.get_channel(cfg["channel_id"])
		if channel is None:
			return
		label = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%b %d")
		await channel.send(embed=embeds.leaderboard_embed(tallied, f"week to {label}"))


jobs = QuizJobs()
