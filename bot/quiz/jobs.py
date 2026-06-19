# -*- coding: utf-8 -*-
"""Daily-post / close-and-reveal / weekly-leaderboard job on the shared 1-s think()
tick. Bulletproof and cadence-gated like LobbyJobs — a failure here can never break
the tick or any existing flow. Does nothing unless a qc_quiz_config row has
enabled=1. nextcord / core.client / embeds are imported lazily inside the methods so
importing bot.quiz (hence this module) stays test-safe under the conftest stubs."""
import asyncio
import json
import time

from core.console import log

from . import schedule, scoring, store

_SCHEDULE = schedule.load()    # ordered, numbered question schedule; [] until generated
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
		cfg = await store.get_config()
		if cfg and cfg.get("enabled"):
			await self._maybe_post_daily(cfg, now)
		await self._close_due(now)                    # always: resolve any leftover open posts

	async def _maybe_post_daily(self, cfg, now):
		ti = cfg.get("test_interval")
		if ti:
			if now - int(cfg.get("last_post_at") or 0) < int(ti):
				return
		elif not scoring.daily_due(now, _hour(cfg.get("quiz_hour"), 9), cfg.get("last_post_ymd")):
			return
		await self._reveal_previous(cfg["channel_id"])
		await self._maybe_week_leaderboard(cfg["channel_id"])
		await self._post_question(cfg["channel_id"], int(cfg.get("open_window") or 86400), now)

	async def _post_question(self, channel_id, open_window, now):
		"""Post the channel's next scheduled question (by seq). Claims last_post_ymd only
		after a confirmed send. Returns the post id, or None when the schedule is
		exhausted / the channel is missing."""
		seq = await store.next_seq(channel_id)
		q = schedule.entry_for_seq(_SCHEDULE, seq)
		if not q:
			log.info(f"Quiz schedule exhausted at seq {seq} — regenerate quiz_schedule.json.")
			return None
		from core.client import dc
		from . import embeds
		channel = dc.get_channel(channel_id)
		if channel is None:
			log.error(f"Quiz channel {channel_id} not found.")
			return None
		post_id = await store.create_post(channel_id, q, now, now + open_window)
		msg = await channel.send(
			embed=embeds.card_embed(q["category"], q["difficulty"], q["seq"], q["week"],
									q["day"], open_window / 3600),
			view=embeds.card_view(post_id))
		await store.set_message_id(post_id, msg.id)
		await store.upsert_config(channel_id, last_post_ymd=scoring._ymd(now), last_post_at=now)
		log.info(f"Quiz posted #{q['seq']} ({q['id']}) in channel {channel_id}.")
		return post_id

	async def reveal_now(self, channel_id):
		"""Admin: immediately reveal the previous still-open question."""
		await self._reveal_previous(channel_id)

	async def force_post(self, channel_id):
		"""Post a quiz immediately, ignoring the daily schedule (admin /quiz post_now).
		Returns the post id or None. Uses the channel's configured open_window if any;
		claims the day (in _post_question) so the scheduler won't post a second quiz
		later today."""
		cfg = await store.get_config(channel_id)
		open_window = int((cfg or {}).get("open_window") or 86400)
		return await self._post_question(channel_id, open_window, int(time.time()))

	async def _reveal(self, post, fresh):
		"""Resolve one post: edit its original card into the result, and — when `fresh` —
		ALSO send a new 'Yesterday's answer' message so the answer shows in the channel
		feed right before the next question. Marks the post closed. Bulletproof."""
		import nextcord
		from core.client import dc
		from . import embeds
		ans = await store.answers_for_post(post["id"])
		winners = [a["nick"] for a in ans if int(a.get("is_correct") or 0)]
		options = json.loads(post["options_json"])
		correct = json.loads(post["correct_indices"])
		channel = dc.get_channel(post["channel_id"])
		if channel:
			if post.get("message_id"):
				try:
					msg = await channel.fetch_message(post["message_id"])
					await msg.edit(embed=embeds.result_embed(
						post["prompt"], options, correct,
						post["explanation"], winners), view=None)
				except nextcord.NotFound:
					pass
			if fresh:
				await channel.send(embed=embeds.result_embed(
					post["prompt"], options, correct, post["explanation"],
					winners, title="Yesterday's answer"))
		await store.close_post(post["id"])

	async def _reveal_previous(self, channel_id):
		"""Reveal the previous still-open question as a fresh announcement (called right
		before posting the next one). No-op on the very first question."""
		prev = await store.latest_open_post(channel_id)
		if prev:
			try:
				await self._reveal(prev, fresh=True)
			except Exception as e:
				log.error(f"Quiz reveal-previous({prev.get('id')}) failed: {e}")

	async def _maybe_week_leaderboard(self, channel_id):
		"""Post the leaderboard for EVERY completed schedule week not yet posted. Keyed to
		schedule weeks -> robust to calendar drift AND to several weeks completing in one
		tick (which happens under the fast test cadence, so no week is silently skipped)."""
		posted = await store.posted_seqs(channel_id)
		done_weeks = [w for w in sorted({q["week"] for q in _SCHEDULE})
					  if schedule.week_is_complete(_SCHEDULE, w, posted)]
		if not done_weeks:
			return
		cfg = await store.get_config(channel_id)
		last = int((cfg or {}).get("last_leaderboard_week") or 0)
		from core.client import dc
		from . import embeds
		for week in done_weeks:
			if week <= last:
				continue
			await store.upsert_config(channel_id, last_leaderboard_week=week)
			rows = await store.week_answers_by_week(channel_id, week)
			channel = dc.get_channel(channel_id)
			if channel:
				await channel.send(embed=embeds.leaderboard_embed(scoring.tally(rows), f"Week {week}"))

	async def _close_due(self, now):
		"""Fallback resolver: any still-open post past its closes_at gets an in-place
		edit (no fresh message — these are stale leftovers, e.g. the quiz was disabled).
		The normal daily path reveals the previous question via _reveal_previous."""
		for post in await store.due_to_close(now):
			try:
				await self._reveal(post, fresh=False)
			except Exception as e:
				log.error(f"Quiz close({post.get('id')}) failed: {e}")


jobs = QuizJobs()
