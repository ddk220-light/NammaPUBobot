# -*- coding: utf-8 -*-
"""Daily-post / close-and-reveal / weekly-leaderboard job on the shared 1-s think()
tick. Bulletproof and cadence-gated like LobbyJobs — a failure here can never break
the tick or any existing flow. Does nothing unless a quiz_settings row has
enabled=1. nextcord / nammaoe2bot.runtime.client / embeds are imported lazily inside the methods so
importing nammaoe2bot.features.quiz (hence this module) stays test-safe under the conftest stubs."""
import asyncio
import json
import time

from nammaoe2bot.runtime.console import log

from . import player_bank, schedule, scoring, store

_SCHEDULE = schedule.load()    # the committed GAME queue; [] until generated

# How many of a channel's most recent questions are read back to keep a player
# metric from repeating. 6 covers the last three player days (they alternate
# with game days), which is the span over which a repeat would be noticed.
RECENT_WINDOW = 6
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

	async def _next_question(self, channel_id, seq, day):
		"""The question for this channel's `seq`-th slot, already stamped with its
		source, or None when neither bank can fill it.

		Player days are generated live from the community's metric_boards; a day
		that cannot produce a FAIR player question (too few leaders on every
		board, a young community, no community at all) falls back to the game
		queue rather than posting a degraded one. Falling back keeps the daily
		cadence, which is the whole point of the feature; posting a padded
		four-option question from a board with two leaders would not."""
		if schedule.source_for_day(day) == "player":
			try:
				recent = await store.recent_question_ids(channel_id, RECENT_WINDOW)
				q = await player_bank.question_for_channel(
					channel_id, seq, player_bank.metrics_of_ids(recent))
				if q:
					return q
				log.info(f"Quiz seq {seq}: no board can carry a player question — using the game bank.")
			except Exception as e:
				log.error(f"Quiz player-bank generation failed at seq {seq} (using the game bank): {e}")
		return schedule.next_game_entry(_SCHEDULE, await store.asked_ids(channel_id))

	async def next_up(self, channel_id):
		"""(seq, week, day, question|None) for the channel's next slot, stamped and
		ready to store, but not posted.

		/quiz status and /quiz skip go through here rather than reading the
		schedule file themselves: half the questions are generated live and exist
		nowhere until something asks for them, so "what is next" is only
		answerable by producing it."""
		seq = await store.next_seq(channel_id)
		week, day = schedule.slot_for_seq(seq)
		q = await self._next_question(channel_id, seq, day)
		return seq, week, day, (dict(q, seq=seq, week=week, day=day) if q else None)

	async def _post_question(self, channel_id, open_window, now):
		"""Post the channel's next question. Claims last_post_ymd only after a
		confirmed send. Returns the post id, or None when neither bank can fill the
		slot / the channel is missing."""
		seq, _week, _day, q = await self.next_up(channel_id)
		if not q:
			log.info(f"Quiz game bank exhausted at seq {seq} — regenerate quiz_schedule.json.")
			return None
		from nammaoe2bot.runtime.client import dc
		from . import embeds
		channel = dc.get_channel(channel_id)
		if channel is None:
			log.error(f"Quiz channel {channel_id} not found.")
			return None
		post_id = await store.create_post(channel_id, q, now, now + open_window)
		post = await store.get_post(post_id)
		msg = await channel.send(
			embed=embeds.poll_embed(post, []),
			view=embeds.vote_view(post_id, q["options"], scoring.is_multi_category(q["category"])))
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
		"""Resolve one poll: grade every cast vote, pay gold, edit the card
		into the final tally, optionally announce, and only THEN mark the post
		closed — money first, terminal status last. A crash (or a payment
		failure, which raises) anywhere before the close leaves the post
		'open' past closes_at, and _close_due re-enters here next tick:
		grading is deterministic, every payment idem-keyed, the card edit
		harmless to repeat."""
		# 0. Shut the door FIRST — before the snapshot, before anything that can
		# await. The vote gate in interactions.py refuses a press from closes_at
		# onward, so pulling closes_at back to now is what makes the snapshot
		# below FINAL. _close_due only ever feeds this method posts already past
		# their deadline, but _reveal_previous does not: /quiz reveal_now and the
		# daily cadence both reach here while the poll is still nominally open,
		# and a press in that window would be accepted, written after the
		# snapshot, and then closed past forever — ungraded, unpaid, and
		# invisible (the ledger and the balance cache still agree). This does NOT
		# spend the retry ticket: the post stays status='open', so a crash
		# anywhere below leaves it matching due_to_close for certain.
		now = int(time.time())
		await store.clamp_closes_at(post["id"], now)
		post["closes_at"] = min(int(post["closes_at"]), now)   # keep later renders honest
		import nextcord
		from nammaoe2bot.runtime.client import dc
		from nammaoe2bot import community
		from nammaoe2bot.features.betting import gold as gold_bank
		from . import embeds
		votes = await store.answers_for_post(post["id"])       # cast votes only (answered_at filter)
		options = json.loads(post["options_json"])
		correct = json.loads(post["correct_indices"])
		multi = scoring.is_multi_category(post["category"])
		# 1. Grade.
		graded = []
		for v in votes:
			if multi:
				ok = scoring.grade_multi(json.loads(v["choice_indices"] or "[]"), correct)
			else:
				ok = v.get("choice_index") is not None and scoring.grade(v["choice_index"], post["correct_index"])
			await store.write_grade(post["id"], v["user_id"], ok)
			graded.append(dict(v, is_correct=ok))
		# 2. Pay. If ANY payment fails, raise after the loop — closing past an
		# unpaid voter would put their gold beyond the retry loop forever.
		gold_note = None
		community_id = await community.community_for_channel(post["channel_id"])
		if community_id is None:
			if graded:
				log.info(f"Quiz {post['id']}: channel {post['channel_id']} has no community — no gold.")
		else:
			failures = 0
			for v in graded:
				try:
					await gold_bank.ensure_seeded(community_id, v["user_id"], now)
					await gold_bank.grant_quiz_reward(
						community_id, v["user_id"], post["id"], v["is_correct"], now)
				except Exception as e:
					failures += 1
					log.error(f"Quiz gold for {v['user_id']} on post {post['id']} failed: {e}")
			if failures:
				raise RuntimeError(
					f"{failures} quiz payment(s) failed on post {post['id']} — left open to retry")
			total = await gold_bank.quiz_paid_total(post["id"])
			if total:
				gold_note = f"\U0001FA99 {total} gold paid out — 50 correct, 10 played, never past 500."
		winners = [v["nick"] for v in graded if v["is_correct"]]
		# 3. Results.
		channel = dc.get_channel(post["channel_id"])
		if channel:
			if post.get("message_id"):
				# THE CARD EDIT IS COSMETIC, AND BY HERE THE MONEY IS ALREADY
				# COMMITTED. Every Discord-side failure of it is swallowed, not
				# just the 404: a Forbidden (someone revoked Manage Messages, the
				# channel was locked down) is PERSISTENT, and a persistent raise
				# here would abort the resolve before the close — leaving the post
				# open past closes_at, so _close_due re-enters it every 30 seconds
				# forever, re-fetching and re-editing and never closing, while the
				# grades and the payouts it already wrote are re-applied as
				# idempotent no-ops. A stale card is a cosmetic defect; a poll that
				# can never close is not.
				#
				# This is NOT the payment failure's `except`. That one lives above,
				# raises out of _reveal on purpose, and must keep blocking the
				# close — 'still open past closes_at' is the only retry ticket
				# unpaid gold has. Nothing below this line may widen to cover it.
				try:
					msg = await channel.fetch_message(post["message_id"])
					await msg.edit(embed=embeds.final_card_embed(post, graded), view=None)
				except nextcord.HTTPException as e:
					log.error(f"Quiz {post['id']}: card edit failed ({e}) — closing anyway.")
			if fresh:
				await channel.send(embed=embeds.result_embed(
					post["prompt"], options, correct, post["explanation"], winners,
					title="Yesterday's answer", gold_note=gold_note))
		# 4. Close — last.
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
		done_weeks = schedule.completed_weeks(posted)
		if not done_weeks:
			return
		cfg = await store.get_config(channel_id)
		last = int((cfg or {}).get("last_leaderboard_week") or 0)
		from nammaoe2bot.runtime.client import dc
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
		The normal daily path reveals the previous question via _reveal_previous.

		THIS IS ALSO THE ONLY RETRY the resolve has. `status='open'` past
		closes_at IS the retry ticket, which is why _reveal closes last and not
		at all on a payment failure: the raise below is logged and the post is
		picked up again on the next pass. Close first and a voter's unpaid gold
		is out of this query forever, with the ledger and the balance cache
		still agreeing, so nothing downstream could detect the loss either."""
		for post in await store.due_to_close(now):
			try:
				await self._reveal(post, fresh=False)
			except Exception as e:
				log.error(f"Quiz close({post.get('id')}) failed: {e}")


jobs = QuizJobs()
