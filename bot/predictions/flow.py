# -*- coding: utf-8 -*-
"""Open / freeze / resolve jobs for audience predictions, on the shared 1-s
think() tick. Bulletproof and cadence-gated like QuizJobs — a failure here can
never break the tick or the match flow it hangs off. nextcord / core.client /
embeds are imported lazily inside the methods so importing bot.predictions
(hence this module) stays test-safe under the conftest stubs."""
import asyncio
import time

from core.console import log

from . import scoring, store
from .view import TEAM_EMOJIS

VOTE_WINDOW = 10 * 60      # seconds of open voting after teams are formed
MAX_NAMED_WINNERS = 25     # cap the payout roll-call so the embed can't overflow

_pending = set()           # keep create_task'd jobs from being GC'd mid-run


class PredictionJobs:
	POLL_INTERVAL = 15     # seconds between freeze sweeps

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
					log.error(f"Prediction job crashed: {t.exception()}")

			_pending.add(task)
			task.add_done_callback(_done)
		except Exception as e:
			self._running = False
			log.error(f"Prediction think() error (ignored): {e}")

	async def _run(self):
		# _freeze is a MODULE function, not a method -- `self._freeze` raises
		# AttributeError, which the per-post guard below would then swallow into
		# a log line on every sweep. It never surfaced because no post ever
		# reached "due to freeze": open_for_match was itself unreachable (see
		# this package's __init__), so the two bugs hid each other exactly.
		now = int(time.time())
		for post in await store.due_to_freeze(now):
			try:
				await _freeze(post, now)
			except Exception as e:
				log.error(f"Prediction freeze failed (post {post.get('id')}): {e}")


# ── open ─────────────────────────────────────────────────────────────────
async def open_for_match(match):
	"""Post the voting card for a freshly-formed ranked match. Best-effort: a
	failure here must never affect the match flow."""
	try:
		if not match.ranked or not match.cfg.get("predictions_enabled", True):
			return
		now = int(time.time())
		post_id = await store.create_post(
			match.qc.id, match.id, match.teams[0].name, match.teams[1].name,
			now, now + VOTE_WINDOW)

		from . import embeds
		from core.client import dc

		channel = dc.get_channel(match.qc.id)
		if channel is None:
			return
		message = await channel.send(embed=embeds.open_embed(
			match.teams[0].name, match.teams[1].name, VOTE_WINDOW // 60, match.id))
		await store.set_message_id(post_id, message.id)
		for emoji in TEAM_EMOJIS:
			await message.add_reaction(emoji)
	except Exception as e:
		log.error(f"Prediction open failed (match {getattr(match, 'id', '?')}): {e}")


async def restart_for_match(match):
	"""Roster changed under a live vote — void what's there and re-open.

	Called from /subauto, which rebalances both teams, so the sides people voted
	on no longer exist. Old ballots are discarded rather than carried over.
	"""
	try:
		now = int(time.time())
		post = await store.live_for_match(match.id)
		if post is None:
			return
		await store.void(post["id"], now)
		await _edit_message(post, _voided_note("Teams changed — predictions reset."))
		await open_for_match(match)
	except Exception as e:
		log.error(f"Prediction restart failed (match {getattr(match, 'id', '?')}): {e}")


# ── freeze ───────────────────────────────────────────────────────────────
async def _freeze(post, now):
	"""Read the reactions back off the message, tally, and lock the vote in.

	Reactions are re-read here rather than accumulated in memory: the message is
	the source of truth, so a redeploy mid-window costs nothing.
	"""
	from . import embeds
	from core.client import dc

	channel = dc.get_channel(post["channel_id"])
	message = None
	if channel is not None and post.get("message_id"):
		try:
			message = await channel.fetch_message(post["message_id"])
		except Exception as e:
			log.warning(f"Prediction message gone (post {post['id']}): {e}")

	ballots, nicks = {}, {}
	if message is not None:
		reactors = []
		for emoji in TEAM_EMOJIS:
			users = set()
			for reaction in message.reactions:
				if str(reaction.emoji) != emoji:
					continue
				async for user in reaction.users():
					if getattr(user, "bot", False):
						continue
					users.add(user.id)
					nicks[user.id] = getattr(user, "display_name", None) or user.name
			reactors.append(users)
		ballots = scoring.resolve_ballots(reactors[0], reactors[1],
										  excluded_ids=_player_ids(post["match_id"]))

	votes0, votes1 = scoring.tally(ballots)
	await store.save_ballots(post["id"], ballots, nicks, now)
	await store.freeze(post["id"], votes0, votes1)

	if message is not None:
		try:
			await message.edit(embed=embeds.frozen_embed(
				post["team0_name"], post["team1_name"], votes0, votes1))
			await message.clear_reactions()
		except Exception as e:
			log.warning(f"Prediction freeze edit failed (post {post['id']}): {e}")
	log.info(f"Predictions frozen for match {post['match_id']}: {votes0}-{votes1}.")


def _player_ids(match_id):
	"""Ids of everyone playing the match, so their own votes drop out silently.

	Reads the live match when it is still active; an already-finished match has
	nobody left to exclude (the vote froze long before that).
	"""
	import bot
	for m in bot.active_matches:
		if m.id == match_id:
			return {p.id for p in m.players}
	return set()


# ── resolve ──────────────────────────────────────────────────────────────
async def resolve_for_match(match):
	"""Score a finished ranked match. A match that never reported a clean
	win/loss (draw, cancelled report) is voided rather than graded."""
	try:
		now = int(time.time())
		post = await store.live_for_match(match.id)
		if post is None:
			return
		winner_idx = getattr(match, "winner", None)
		if winner_idx is None:
			await store.void(post["id"], now)
			await _edit_message(post, _voided_note("No win/loss reported — predictions void."))
			return

		ballots, nicks = await store.ballots_for(post["id"])
		results = scoring.grade(ballots, winner_idx)
		await store.mark_correct(post["id"], results)
		await store.resolve(post["id"], winner_idx, now)

		winner_name = post["team0_name"] if winner_idx == 0 else post["team1_name"]
		correct = [nicks.get(uid, str(uid)) for uid, ok in results.items() if ok]
		await _announce_result(post, winner_name, sorted(correct), len(results))
	except Exception as e:
		log.error(f"Prediction resolve failed (match {getattr(match, 'id', '?')}): {e}")


async def void_for_match(match_id, reason="Match cancelled — predictions void."):
	"""Drop a live vote on the floor (match aborted)."""
	try:
		post = await store.live_for_match(match_id)
		if post is None:
			return
		await store.void(post["id"], int(time.time()))
		await _edit_message(post, _voided_note(reason))
	except Exception as e:
		log.error(f"Prediction void failed (match {match_id}): {e}")


# ── discord helpers ──────────────────────────────────────────────────────
async def _announce_result(post, winner_name, correct_nicks, total_voters):
	from . import embeds
	from core.client import dc

	channel = dc.get_channel(post["channel_id"])
	if channel is None:
		return
	shown = correct_nicks[:MAX_NAMED_WINNERS]
	if len(correct_nicks) > MAX_NAMED_WINNERS:
		shown.append(f"+{len(correct_nicks) - MAX_NAMED_WINNERS} more")
	try:
		await channel.send(embed=embeds.result_embed(winner_name, shown, total_voters))
	except Exception as e:
		log.warning(f"Prediction result send failed (post {post['id']}): {e}")


def _voided_note(text):
	from nextcord import Embed, Colour
	return Embed(title="\U0001F52E Audience prediction", description=text, colour=Colour(0x95A5A6))


async def _edit_message(post, embed):
	"""Best-effort rewrite of a post's card; a deleted message is not an error."""
	from core.client import dc

	channel = dc.get_channel(post["channel_id"])
	if channel is None or not post.get("message_id"):
		return
	try:
		message = await channel.fetch_message(post["message_id"])
		await message.edit(embed=embed)
		await message.clear_reactions()
	except Exception:
		pass


jobs = PredictionJobs()
