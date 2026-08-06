# -*- coding: utf-8 -*-
"""Open / freeze / resolve jobs for audience betting, on the shared 1-s think()
tick. Bulletproof and cadence-gated like QuizJobs — a failure here can never
break the tick or the match flow it hangs off. nextcord / core.client / embeds
are imported lazily inside the methods so importing bot.predictions (hence this
module) stays test-safe under the conftest stubs.

The freeze sweep reads `prediction_bets`, not the message's reactions: gold is
staked through buttons that route to bot/predictions/interactions.py, and every
press is committed to the DB inside the same transaction that moves the money.
The database is therefore the source of truth for a book, which keeps the whole
flow restart-safe — a redeploy mid-window costs nothing and a deleted card costs
only the card.
"""
import asyncio
import time

from core.console import log

from . import gold, scoring, store

VOTE_WINDOW = 10 * 60      # seconds of open betting after teams are formed

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
	"""Post the betting card for a freshly-formed ranked match. Best-effort: a
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
		message = await channel.send(
			embed=embeds.open_embed(
				match.teams[0].name, match.teams[1].name, VOTE_WINDOW // 60, match.id),
			view=embeds.bet_view(post_id))
		await store.set_message_id(post_id, message.id)
	except Exception as e:
		log.error(f"Prediction open failed (match {getattr(match, 'id', '?')}): {e}")


async def restart_for_match(match):
	"""Roster changed under a live book — refund what's there and re-open.

	Called from /subauto, which rebalances both teams, so the sides people staked
	on no longer exist. Stakes go back rather than being carried over.
	"""
	try:
		now = int(time.time())
		post = await store.live_for_match(match.id)
		if post is None:
			return
		await _void_with_refunds(post, "Teams changed — all bets refunded. A fresh book is open.", now)
		await open_for_match(match)
	except Exception as e:
		log.error(f"Prediction restart failed (match {getattr(match, 'id', '?')}): {e}")


# ── freeze ───────────────────────────────────────────────────────────────
async def _freeze(post, now):
	"""Lock the book. One-sided (either pool empty) -> no_action: every stake
	back immediately, because a book with no opposing gold has no odds to
	settle. Otherwise the pots lock and the card shows the final multipliers."""
	from . import embeds

	bets = await store.bets_for(post["id"])
	pool0, pool1 = scoring.pools(bets)
	if not pool0 or not pool1:
		community_id = await _community_for_post(post)
		if bets and community_id is not None:
			await gold.refund_post(community_id, bets, post["id"], now)
		elif bets:
			log.error(f"Prediction post {post['id']} has bets but no community — refunds skipped!")
		await store.no_action(post["id"], now)
		await _edit_message(post, embeds.no_action_embed(post["team0_name"], post["team1_name"]))
		log.info(f"Bets refunded for match {post['match_id']}: one-sided book ({pool0}-{pool1}).")
		return

	bettors0 = sum(1 for b in bets if b["side"] == 0)
	bettors1 = len(bets) - bettors0
	await store.freeze(post["id"], bettors0, bettors1)
	await _edit_message(post, embeds.frozen_embed(
		post["team0_name"], post["team1_name"], pool0, pool1, bettors0, bettors1))
	log.info(f"Bets locked for match {post['match_id']}: {pool0}-{pool1} gold.")


def _player_ids(match_id):
	"""Ids of everyone playing the match, so their own presses are refused.

	Reads the live match when it is still active; an already-finished match has
	nobody left to exclude (the book froze long before that).
	"""
	import bot
	for m in bot.active_matches:
		if m.id == match_id:
			return {p.id for p in m.players}
	return set()


async def _community_for_post(post):
	from bot import community
	return await community.community_for_channel(post["channel_id"])


# ── resolve ──────────────────────────────────────────────────────────────
# Settlement runs once, at report time, like the votes era. If the process
# dies mid-settlement the post stays 'frozen'; every movement below is an
# idempotent ledger insert, so re-running settlement for a post is always
# safe — a future sweep (or a manual call) can finish a half-settled book
# without double-paying anyone.
async def resolve_for_match(match):
	"""Settle a finished ranked match: pay the playing faucet, then the book.
	A match that never reported a clean win/loss (draw, cancelled report) is
	voided — every stake refunded — rather than settled."""
	try:
		now = int(time.time())
		winner_idx = getattr(match, "winner", None)

		# The faucet first, and independently of any post: playing regenerates
		# gold toward the ceiling whether or not anyone bet on this match.
		if winner_idx is not None:
			from bot import community
			community_id = await community.community_for_channel(match.qc.id)
			if community_id is not None:
				for p in match.players:
					try:
						await gold.ensure_seeded(community_id, p.id, now)
						await gold.grant_match_reward(community_id, p.id, match.id, now)
					except Exception as e:
						log.error(f"Match reward failed ({match.id}/{p.id}): {e}")

		post = await store.live_for_match(match.id)
		if post is None:
			return
		if winner_idx is None:
			await _void_with_refunds(post, "No win/loss reported — all bets refunded.", now)
			return

		bets = await store.bets_for(post["id"])
		paid, burned = scoring.payouts(bets, winner_idx)
		if bets and not paid:
			# Defensive: a one-sided book that somehow reached resolve. The
			# freeze rule makes this unreachable; refund rather than settle.
			await _void_with_refunds(post, "One-sided book — all bets refunded.", now)
			return
		community_id = await _community_for_post(post)
		if paid and community_id is not None:
			await gold.pay_post(community_id, paid, post["id"], now)
		elif paid:
			log.error(f"Prediction post {post['id']} has winners but no community — payouts skipped!")
		await store.resolve(post["id"], winner_idx, now)
		await _announce_report(post, winner_idx, bets, paid)
		if burned:
			log.info(f"Match {match.id} betting settled: {sum(paid.values())} paid, {burned} burned.")
	except Exception as e:
		log.error(f"Prediction resolve failed (match {getattr(match, 'id', '?')}): {e}")


async def _void_with_refunds(post, reason, now):
	"""Terminal no-settle: refund every stake exactly once, then mark void."""
	from . import embeds

	bets = await store.bets_for(post["id"])
	community_id = await _community_for_post(post)
	if bets and community_id is not None:
		await gold.refund_post(community_id, bets, post["id"], now)
	elif bets:
		log.error(f"Prediction post {post['id']} has bets but no community — refunds skipped!")
	await store.void(post["id"], now)
	await _edit_message(post, embeds.voided_embed(reason))


async def void_for_match(match_id, reason="Match cancelled — all bets refunded."):
	"""Drop a live book on the floor (match aborted). Stakes go back."""
	try:
		post = await store.live_for_match(match_id)
		if post is None:
			return
		await _void_with_refunds(post, reason, int(time.time()))
	except Exception as e:
		log.error(f"Prediction void failed (match {match_id}): {e}")


# ── discord helpers ──────────────────────────────────────────────────────
async def _announce_report(post, winner_idx, bets, paid):
	from . import embeds
	from core.client import dc

	channel = dc.get_channel(post["channel_id"])
	if channel is None:
		return
	try:
		await channel.send(embed=embeds.report_embed(
			post["team0_name"], post["team1_name"], winner_idx, bets, paid))
	except Exception as e:
		log.warning(f"Betting report send failed (post {post['id']}): {e}")


async def _edit_message(post, embed):
	"""Best-effort rewrite of a post's card; a deleted message is not an error.
	view=None strips the bet buttons — every caller is a terminal state."""
	from core.client import dc

	channel = dc.get_channel(post["channel_id"])
	if channel is None or not post.get("message_id"):
		return
	try:
		message = await channel.fetch_message(post["message_id"])
		await message.edit(embed=embed, view=None)
	except Exception:
		pass


jobs = PredictionJobs()
