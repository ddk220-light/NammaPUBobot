# -*- coding: utf-8 -*-
"""Open / freeze / resolve jobs for audience betting, on the shared 1-s think()
tick. Bulletproof and cadence-gated like QuizJobs — a failure here can never
break the tick or the match flow it hangs off. nextcord / nammaoe2bot.runtime.client / embeds
are imported lazily inside the methods so importing nammaoe2bot.features.betting (hence this
module) stays test-safe under the conftest stubs.

The freeze sweep reads `prediction_bets`, not the message's reactions: gold is
staked through buttons that route to nammaoe2bot/features/betting/interactions.py, and every
press is committed to the DB inside the same transaction that moves the money.
The database is therefore the source of truth for a book, which keeps the whole
flow restart-safe — a redeploy mid-window costs nothing and a deleted card costs
only the card.

Two rules run through every path below, and both are about gold going missing
rather than about tidiness:

1. CLOSE THE BOOK BEFORE READING IT. store.bets_for is a snapshot; a press that
   commits after the snapshot and before the post leaves 'open' is refunded by
   nobody and paid by nobody, and reconcile() cannot see the loss because the
   ledger and the balance cache still agree perfectly. _close_book is the one
   door, and gold.place_bet's FOR UPDATE re-read is its other half.
2. STAKE THE BRANCH BEFORE THE MONEY, AND WRITE THE TERMINAL STATUS AFTER IT.
   `terminal_intent` says which way a post ends and is claimed in the same
   statement that closes the book (store.claim_terminal), before a coin moves.
   `status` still comes last, so a crash leaves the post retryable — but every
   resume now DISPATCHES ON THE STORED INTENT rather than re-deriving one from
   `matches.winner`. Without that, a two-sided book that was refunded and then
   died before `status='void'` landed was still 'frozen' when its match
   reported, and settlement paid out the very stakes that had just gone back:
   refund:{post}:{user} and payout:{post}:{user} are different idem_keys, so
   nothing collided and nothing self-healed. A settlement that CANNOT apply
   its payouts still writes nothing at all, leaving the book frozen (and
   staked 'settle') for the resume sweep in _run.
"""
import asyncio
import time

from nammaoe2bot.runtime.client import dc
from nammaoe2bot.runtime.console import log

from . import gold, scoring, store

VOTE_WINDOW = 10 * 60      # seconds of open betting after teams are formed

# How long after a match reports a book may still legitimately be settling.
# resolve_for_match runs within seconds of the `matches` row being written, so
# anything still 'frozen' this much later did not finish — and the resume sweep
# below picks it up. Generous on purpose: the cost of waiting is a late payout,
# the cost of racing the live settlement is a duplicated betting report.
RESUME_AFTER = 10 * 60

# How long a book may sit frozen with its match having reported NOTHING before
# the stakes are handed back. A pickup match is long over by then; a match that
# is not is one nobody can report any more (its Match object died with a process
# that never wrote saved_state.json). Refunding is the only safe answer either
# way — there is no result to settle against, and a void post is simply not live
# for a report that arrives afterwards.
ABANDON_AFTER = 12 * 60 * 60

ABANDONED_REASON = "The match never reported a result — all bets refunded."

# What the card says when a void is FINISHED by a later sweep rather than by
# the caller that staked it. The original reason ("Teams changed", "Match
# cancelled") lived only in that caller's arguments, and storing a
# presentation string in the posts table to survive a crash window measured in
# microseconds is not worth the column. Everything that matters — the branch
# and the money — is recorded; only the phrasing generalises.
VOID_RESUMED_REASON = "This book was voided before the match settled."

# What the locked card will lead with when the GAME is what closed the book.
# The provider is currently disabled in nammaoe2bot/wiring.py while live socket
# evidence is collected, so production books remain timer-only; keeping the
# rendering path here lets the evidence-backed cutover reuse the tested close.
LAUNCH_HEADLINE = "The game has started — betting is closed. The pots are locked:"

_pending = set()           # keep create_task'd jobs from being GC'd mid-run


class PredictionJobs:
	POLL_INTERVAL = 15     # seconds between freeze sweeps

	# "Which of these matches have already started?", injected by
	# nammaoe2bot/wiring.py from the lobby feature — the only part of the bot
	# that knows. None means nobody wired it (or the lobby feature is off), and
	# the sweep falls back to the timer alone, which is exactly the behaviour
	# that existed before. An attribute on the existing singleton rather than a
	# module-level global: see nammaoe2bot/app.py's docstring on what the last
	# set of those cost.
	launched_among = None

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
		for post, headline in await self._posts_to_freeze(now):
			try:
				await _freeze(post, now, headline)
			except Exception as e:
				log.error(f"Prediction freeze failed (post {post.get('id')}): {e}")

		# THE OTHER HALF OF CRASH SAFETY. Idempotent ledger inserts make
		# re-running a half-finished sweep safe; they do not make it HAPPEN.
		# resolve_for_match is called from exactly one place — finish_match —
		# which has already dropped the Match from bot.active_matches, so a
		# process that dies mid-payout takes the only caller with it and the
		# book stays 'frozen' with some winners paid and the rest owed. Nothing
		# above sees it: due_to_freeze only looks at 'open'. This loop is the
		# re-runner, and it is the only reason the spec's "a crash mid-sweep
		# resumes where it stopped" is true of the code rather than of the
		# inserts alone.
		for post in await store.unsettled_books(now - RESUME_AFTER):
			try:
				await _resume(post, now)
			except Exception as e:
				log.error(f"Prediction resume failed (post {post.get('id')}): {e}")

		# And the other way a stake gets stranded: a match that never reported
		# anything at all, so nothing ever called settlement for it. Every other
		# exit from a live book is driven by the match — report, cancel,
		# rebalance — which leaves a match that simply disappears holding
		# everybody's gold with no event left to give it back.
		for post in await store.abandoned_books(now - ABANDON_AFTER):
			try:
				# Same dispatch rule as _resume: a post that already staked a
				# refund branch is FINISHED on that branch, never re-decided.
				# This sweep is the only thing that ever looks at these posts
				# (their match wrote no row at all), so a book that died between
				# claiming 'no_action' at freeze and writing it would otherwise
				# sit frozen forever — refunded, but never closed out.
				if not await _finish_staked_branch(post, now):
					await _void_with_refunds(post, ABANDONED_REASON, now)
				log.info(f"Refunded an abandoned book for match {post['match_id']} (post {post['id']}).")
			except Exception as e:
				log.error(f"Prediction abandon-refund failed (post {post.get('id')}): {e}")

	async def _posts_to_freeze(self, now):
		"""[(post, headline)] for every book that should stop taking money now.

		TWO SUPPORTED REASONS, ONE LIST. The timer is the production rule:
		`freezes_at` elapsed. The optional second is that the game itself started,
		which the lobby feature
		reports through `launched_among` (nammaoe2bot/features/lobby/started.py
		holds the query, and the reason it lives there rather than here). That
		provider is deliberately None during the 2026-08-08 observation period,
		so only the timer contributes posts today.
		Betting past the launch is not predicting — the civs, the starting
		positions and the first fight are all visible, on the bot's own lobby
		card, while the buttons are still live.

		THIS IS A PULL, on the sweep that already closes books, rather than a
		callback fired at launch, and that is deliberate. `lobbies.status` is
		durable, so a redeploy between the launch and the freeze changes
		nothing: the very next sweep still sees a started game and still closes
		the book. A one-shot in-memory signal would have died with the watcher
		task that sent it, leaving the book open for the rest of its window —
		the exact failure this exists to prevent. It costs up to one
		POLL_INTERVAL of lateness, which is a tolerance the freeze already had.

		Dedupes on post id, because a book can be both overdue AND launched and
		freezing it twice would put two sweeps on one snapshot. The timer wins
		that tie: a book past its own deadline closed on time whatever else
		happened to be true of it.
		"""
		found = {}
		for post in await store.due_to_freeze(now):
			found[post["id"]] = (post, None)
		if self.launched_among is None:
			return list(found.values())
		# Only ask about books the timer has not already claimed — there is no
		# second answer to be had about those.
		open_posts = [p for p in await store.open_posts() if p["id"] not in found]
		if not open_posts:
			return list(found.values())
		try:
			started = await self.launched_among([p["match_id"] for p in open_posts])
		except Exception as e:
			# The lobby side is best-effort by design and betting must not
			# inherit its outages. A book that cannot find out whether its game
			# started still has a timer, which is the whole pre-existing
			# behaviour — so this degrades to it rather than skipping the sweep.
			log.error(f"Launch check failed, freezing on the timer alone: {e}")
			return list(found.values())
		for post in open_posts:
			if post["match_id"] in started:
				found[post["id"]] = (post, LAUNCH_HEADLINE)
		return list(found.values())


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
		from nammaoe2bot.runtime.client import dc

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


# ── closing the book ─────────────────────────────────────────────────────
async def _close_book(post, intent):
	"""Stop the book taking money, stake the terminal branch, and hand back the
	post as it now is — or None when this post is not ours to finish.

	EVERY path that reads the book to refund or pay it goes through here
	first, and both halves of what it does are load-bearing rather than tidy.

	CLOSING FIRST. store.bets_for is a snapshot; a press that commits after
	the snapshot but before the post leaves 'open' is refunded by nobody and
	paid by nobody, and the gold it took is gone with the ledger and the
	balance cache still in perfect agreement. Closing first means the snapshot
	is taken from a book that can no longer grow (see store.claim_terminal and
	gold.place_bet's FOR UPDATE).

	STAKING THE BRANCH FIRST. `intent` is recorded in the same statement,
	before a coin moves, so a run that dies part-way through a refund cannot
	be finished as a PAYOUT by whoever picks the post up next. None here means
	somebody else staked a different branch (or the post is already terminal),
	and the only correct answer to that is to leave it entirely alone.

	The returned dict carries status='frozen' and the staked intent, so the
	callers below do not try to close it a second time and mistake their own
	success for someone else's."""
	if await store.claim_terminal(post["id"], intent) != intent:
		return None
	return dict(post, status="frozen", terminal_intent=intent)


# ── freeze ───────────────────────────────────────────────────────────────
async def _freeze(post, now, headline=None):
	"""Lock the book. One-sided (either pool empty) -> no_action: every stake
	back immediately, because a book with no opposing gold has no odds to
	settle. Otherwise the pots lock and the card shows the final multipliers.

	`headline` is what the locked card leads with, and it is the ONLY thing
	that differs between the two ways a book closes — the timer running out and
	the game starting lock identical pots by identical rules, so nothing below
	branches on it. None takes view.frozen_lines' own default.

	The close here is deliberately NOT a terminal claim: a two-sided book
	leaves this function alive, frozen and waiting for its match, so there is
	no branch to stake. Only the one-sided arm is terminal, and it claims
	'no_action' below — after bets_for has said which arm this is, and still
	before any gold moves."""
	from . import embeds

	if not await store.close_betting(post["id"]):
		return                       # another sweep flipped it out from under us
	post = dict(post, status="frozen")

	bets = await store.bets_for(post["id"])
	pool0, pool1 = scoring.pools(bets)
	if not pool0 or not pool1:
		if await store.claim_terminal(post["id"], "no_action") != "no_action":
			return                   # a settlement or a void owns this post now
		await _apply_no_action(post, now, bets=bets)
		log.info(f"Bets refunded for match {post['match_id']}: one-sided book ({pool0}-{pool1}).")
		return

	bettors0 = sum(1 for b in bets if b["side"] == 0)
	bettors1 = len(bets) - bettors0
	await store.freeze(post["id"], bettors0, bettors1)
	await _edit_message(post, embeds.frozen_embed(
		post["team0_name"], post["team1_name"], pool0, pool1, bettors0, bettors1, headline))
	log.info(f"Bets locked for match {post['match_id']}: {pool0}-{pool1} gold"
			 f"{' (game started)' if headline else ''}.")


def _team_ids(match_id):
	"""(team0_ids, team1_ids) for a live match, or two empty sets.

	Match.teams has THREE entries — [0], [1] and a "unpicked" pseudo-team at
	[2] with idx=-1 — so this indexes the two real sides explicitly. Iterating
	match.teams would turn unpicked players into a third side.
	"""
	for m in dc.app.active_matches:
		if m.id == match_id:
			return {p.id for p in m.teams[0]}, {p.id for p in m.teams[1]}
	return set(), set()


async def _community_for_post(post):
	from nammaoe2bot import community
	return await community.community_for_channel(post["channel_id"])


# ── resolve ──────────────────────────────────────────────────────────────
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
			from nammaoe2bot import community
			community_id = await community.community_for_channel(match.qc.id)
			await _pay_faucet(community_id, match.id, [p.id for p in match.players], now)

		post = await store.live_for_match(match.id)
		if post is None:
			return
		await _settle(post, winner_idx, now)
	except Exception as e:
		log.error(f"Prediction resolve failed (match {getattr(match, 'id', '?')}): {e}")


async def _resume(post, now):
	"""Finish a settlement that started and stopped (see store.unsettled_books).

	Everything it needs comes out of the database, because the Match object
	this would otherwise read is gone: finish_match removes it from
	app.active_matches before it ever reaches settlement, and a redeploy takes
	the process with it. The faucet is repeated too — it is an idempotent grant
	keyed by (match, user), and a crash between the faucet and the payout
	sweep leaves it half-applied exactly like the payouts.

	`matches.winner` decides only the branch nobody has staked yet. A post
	that already committed to a refund is FINISHED as a refund, whatever the
	match went on to report — see _finish_staked_branch."""
	winner = post.get("match_winner")
	log.info(f"Resuming unfinished settlement for match {post['match_id']} (post {post['id']}).")
	if winner is not None:
		# Independent of the book, exactly as in resolve_for_match: playing a
		# ranked match tops the players up whether or not anybody bet on it, so
		# this runs on the refund branches too.
		community_id = await _community_for_post(post)
		await _pay_faucet(community_id, post["match_id"],
						  await store.match_player_ids(post["match_id"]), now)
	if await _finish_staked_branch(post, now):
		return
	await _settle(post, None if winner is None else int(winner), now)


async def _finish_staked_branch(post, now):
	"""Finish the refund branch this post already committed to, if any.
	True when it handled the post; False when nothing is staked yet, or when
	the staked branch is 'settle' and the caller's own settlement is what
	finishes it.

	THE DOUBLE-PAYOUT FIX, on the reading side. `terminal_intent` is written
	before the gold moves (store.claim_terminal); this is the half that makes
	it worth writing. A resume that re-derived the branch from
	`matches.winner` instead paid out a book whose stakes had already gone
	back — the refund and the payout carry different idem_keys, so the bank
	applied both and reconcile() saw nothing wrong."""
	intent = post.get("terminal_intent")
	if intent == "void":
		await _apply_void(post, VOID_RESUMED_REASON, now)
		return True
	if intent == "no_action":
		await _apply_no_action(post, now)
		return True
	return False


async def _settle(post, winner_idx, now):
	"""Close the book if it is still open, then settle it: pay the winners,
	mark the post resolved, retire the card, post the report.

	Safe to run twice, and that is the design rather than a happy accident —
	every gold movement is an idempotent insert keyed by idem_key, and the post
	only leaves 'frozen' once the money it owed has been applied. An
	interrupted run is therefore finished by the next sweep instead of being
	stranded, and a run that cannot apply the money writes nothing at all."""
	from . import embeds

	if winner_idx is None:
		# Not a settlement at all. Route it through the void path so the branch
		# staked on the row is 'void' — claiming 'settle' here and refunding
		# under it would tell every later resume the opposite of the truth.
		await _void_with_refunds(post, "No win/loss reported — all bets refunded.", now)
		return

	post = await _close_book(post, "settle")
	if post is None:
		return

	bets = await store.bets_for(post["id"])
	paid, burned = scoring.payouts(bets, winner_idx)
	if bets and not paid:
		# Defensive: a one-sided book that somehow reached resolve. The freeze
		# rule makes this unreachable; refund rather than settle. _apply_void
		# directly rather than _void_with_refunds, because 'settle' is already
		# staked on this row: re-staking is impossible AND unnecessary —
		# payouts() paid nobody, so no gold has moved on the settle branch, and
		# a resume re-derives this same refund from the same one-sided book.
		await _apply_void(post, "One-sided book — all bets refunded.", now)
		return

	community_id = await _community_for_post(post)
	if paid and community_id is None:
		# NOTHING is written here — not the payouts, not 'resolved', not the
		# report. A betting report is a public statement that named people were
		# paid named amounts; posting one over a payout that never happened
		# tells the channel a lie it has no way to check. And 'resolved' would
		# put the post beyond store.unsettled_books forever, so the debt could
		# never be settled by anything. Frozen, silent and owed is the only
		# recoverable state.
		log.error(f"Prediction post {post['id']} has winners but no community — payout skipped, "
				  f"post left frozen for the resume sweep.")
		return

	if paid:
		await gold.pay_post(community_id, paid, post["id"], now)
	# votes0/votes1 stay unwritten on a book that never passed through _freeze
	# (a match reported inside the betting window). They are write-only columns
	# — the bettor counts every embed shows are counted from prediction_bets at
	# read time — and writing them here would mean an UPDATE that sets
	# status='frozen' one statement before this one sets 'resolved'.
	await store.resolve(post["id"], winner_idx, now)
	# The card, retired. Reached with the buttons still live whenever the match
	# reported inside the betting window (report before freezes_at): _freeze
	# never ran for this post, so nothing else has ever taken the view off it,
	# and a settled match advertising "🔵 10/50/100" is an invitation the
	# router can only answer with a refusal.
	pool0, pool1 = scoring.pools(bets)
	bettors0 = sum(1 for b in bets if b["side"] == 0)
	await _edit_message(post, embeds.frozen_embed(
		post["team0_name"], post["team1_name"], pool0, pool1, bettors0, len(bets) - bettors0))
	await _announce_report(post, winner_idx, bets, paid)
	if burned:
		log.info(f"Match {post['match_id']} betting settled: {sum(paid.values())} paid, {burned} burned.")


async def _pay_faucet(community_id, match_id, user_ids, now):
	"""Playing regenerates gold toward the ceiling. Idempotent per (match,
	user), so a resumed settlement repeats it at no cost."""
	if community_id is None:
		return
	for user_id in user_ids:
		try:
			await gold.ensure_seeded(community_id, user_id, now)
			await gold.grant_match_reward(community_id, user_id, match_id, now)
		except Exception as e:
			log.error(f"Match reward failed ({match_id}/{user_id}): {e}")


async def _void_with_refunds(post, reason, now):
	"""Terminal no-settle: close the book and stake the void branch, refund
	every stake exactly once, then mark void. The closing comes first for the
	reason spelled out in _close_book — a snapshot of a book that can still
	grow is a refund list that can still be wrong."""
	post = await _close_book(post, "void")
	if post is None:
		return
	await _apply_void(post, reason, now)


async def _apply_void(post, reason, now):
	"""The money and the status half of a void, for a post whose 'void' (or
	'settle'-then-refund) branch is ALREADY staked. Refunds are idempotent per
	(post, user), so a resume that repeats this costs nothing and finishes
	whatever the first run left owing."""
	from . import embeds

	bets = await store.bets_for(post["id"])
	await _refund(post, bets, now)
	await store.void(post["id"], now)
	await _edit_message(post, embeds.voided_embed(reason))


async def _apply_no_action(post, now, bets=None):
	"""The money and the status half of a one-sided book's refund, for a post
	whose 'no_action' branch is already staked. `bets` is passed in by _freeze,
	which has just read them; a resume reads them itself."""
	from . import embeds

	if bets is None:
		bets = await store.bets_for(post["id"])
	await _refund(post, bets, now)
	await store.no_action(post["id"], now)
	await _edit_message(post, embeds.no_action_embed(post["team0_name"], post["team1_name"]))


async def _refund(post, bets, now):
	"""Hand every stake back, exactly once each. A book with real stakes and
	no community to apply them in is the one outcome that must never pass
	unnoticed — it is gold owed to named people that nothing else will find."""
	community_id = await _community_for_post(post)
	if bets and community_id is not None:
		await gold.refund_post(community_id, bets, post["id"], now)
	elif bets:
		log.error(f"Prediction post {post['id']} has bets but no community — refunds skipped!")


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
	from nammaoe2bot.runtime.client import dc

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
	from nammaoe2bot.runtime.client import dc

	channel = dc.get_channel(post["channel_id"])
	if channel is None or not post.get("message_id"):
		return
	try:
		message = await channel.fetch_message(post["message_id"])
		await message.edit(embed=embed, view=None)
	except Exception:
		pass


jobs = PredictionJobs()
