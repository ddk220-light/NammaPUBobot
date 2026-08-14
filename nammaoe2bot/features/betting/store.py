# -*- coding: utf-8 -*-
"""Async MySQL access for audience predictions: persistence for posts, plus
the read side of bets. All WRITES to prediction_bets live in
nammaoe2bot.features.betting.gold, inside the same transaction that moves the gold; this
module only reads them. Grading logic lives in nammaoe2bot.features.betting.scoring (pure,
tested). No nextcord import here so importing nammaoe2bot.features.betting stays test-safe."""
from nammaoe2bot.runtime.database import db


# ── posts ────────────────────────────────────────────────────────────────
async def create_post(channel_id, match_id, team0_name, team1_name, opened_at, freezes_at):
	"""Insert an open post and return its id."""
	return await db.insert("prediction_posts", dict(
		channel_id=channel_id, match_id=match_id, message_id=None,
		team0_name=team0_name, team1_name=team1_name,
		opened_at=opened_at, freezes_at=freezes_at, status="open"))


async def set_message_id(post_id, message_id):
	await db.update("prediction_posts", {"message_id": message_id}, {"id": post_id})


async def get_post(post_id):
	return await db.fetchone("SELECT * FROM prediction_posts WHERE id=%s", [post_id])


async def open_posts():
	"""Every book still taking money, awaiting a confirmed game launch.

	Small by construction — a channel runs one match at a time, so this is one
	or two rows — which is what makes it affordable to ask a second, external
	question about each of them on every sweep.
	"""
	return await db.fetchall(
		"SELECT * FROM prediction_posts WHERE status='open' ORDER BY id ASC") or []


async def live_for_match(match_id):
	"""The post still riding on this match (open or frozen), or None."""
	rows = await db.fetchall(
		"SELECT * FROM prediction_posts "
		"WHERE match_id=%s AND status IN ('open','frozen') ORDER BY id DESC LIMIT 1", [match_id])
	return rows[0] if rows else None


async def unsettled_books(reported_before):
	"""Books that locked but never finished settling — the resume queue.

	THE JOIN IS THE CONDITION. A post sitting at 'frozen' is the normal state
	for as long as the match is being played, so "stale" can never be a clock
	reading on the post alone. What makes one unsettled is `matches` already
	holding a row for it — nammaoe2bot/pickup/stats.py writes that row inside
	finish_match, BEFORE the match flow calls resolve_for_match — while the
	post has still not left 'frozen'. That combination means settlement started
	and did not finish (a redeploy mid-sweep, a process kill, a payout that
	could not resolve its community), and every movement in it is idempotent,
	so re-running it is always safe.

	`m.winner` rides along because the live Match object is long gone by then:
	app.active_matches dropped it at the top of finish_match, so the recorded
	result is the only surviving answer to "who won". NULL means the match
	reported no clean win/loss, which the caller voids rather than settles.

	Read-only across the boundary — this module writes nothing outside
	prediction_*. LIMIT keeps one pathological backlog from monopolising a
	sweep; the next sweep takes the next batch."""
	return await db.fetchall(
		"SELECT p.*, m.winner AS match_winner FROM prediction_posts p "
		"JOIN matches m ON m.match_id = p.match_id "
		"WHERE p.status='frozen' AND m.reported_at<=%s "
		"ORDER BY p.id ASC LIMIT 50", [reported_before]) or []


async def abandoned_books(frozen_before):
	"""Books whose match never reported ANYTHING — no `matches` row at all.

	The sibling of unsettled_books, for the other way a stake gets stranded:
	the settlement path is driven entirely by the match reporting, and a match
	that disappears (a hard kill before saved_state.json is written, so nothing
	restores it and nobody can /report it) takes the book's only caller with it.
	The stakes are debited and there is no event left that would ever hand them
	back. The caller voids these, refunding every stake — which is also the
	right answer if such a match somehow does report later, because a void post
	is simply not live for it any more.

	`frozen_before` is therefore deliberately generous: a pickup match is never
	still being played hours after its book locked, and the cost of being wrong
	is a refund, never a wrong payout."""
	return await db.fetchall(
		"SELECT p.* FROM prediction_posts p "
		"LEFT JOIN matches m ON m.match_id = p.match_id "
		"WHERE p.status='frozen' AND p.freezes_at<=%s AND m.match_id IS NULL "
		"ORDER BY p.id ASC LIMIT 50", [frozen_before]) or []


async def match_player_ids(match_id):
	"""Everyone who played, for the faucet a resumed settlement still owes.
	Read-only across the boundary, like unsettled_books above."""
	rows = await db.fetchall(
		"SELECT user_id FROM match_players WHERE match_id=%s", [match_id])
	return [r["user_id"] for r in rows or []]


async def close_betting(post_id, closed_at):
	"""End the betting window: the ONE write that stops a book taking money.
	True when this call is what closed it.

	Two properties make it that, and both matter:

	* It is a COMPARE-AND-SET (`AND status='open'`), so two sweeps racing — or
	  a freeze sweep racing a match report — agree on exactly one winner
	  instead of both proceeding to settle the same book.
	* Its partner is gold.place_bet, which re-reads this row `FOR UPDATE`
	  inside the transaction that moves the stake. The two serialise on the row
	  lock: either the press commits first and every later snapshot of the book
	  contains it, or this flip commits first and the press sees 'frozen' and
	  is refused. There is no third outcome — which is what stops a stake being
	  taken by a book that has already been snapshotted for refund or payout,
	  the one failure mode that destroys gold silently (the ledger and the
	  balance cache agree perfectly, so reconcile() cannot see it either).

	It runs in a transaction purely for the ROWCOUNT: the autocommit adapter's
	execute() answers lastrowid, which says nothing about an UPDATE.

	This one is the NON-terminal close, and _freeze is its only caller: a
	two-sided book leaves that sweep alive, frozen and waiting for its match,
	so there is no branch to stake yet. Every path that is about to move gold
	for the last time goes through claim_terminal below instead."""
	async with db.transaction() as tx:
		return bool(await tx.execute(
			"UPDATE prediction_posts SET status='frozen', freezes_at=%s "
			"WHERE id=%s AND status='open'",
			[closed_at, post_id]))


# The three ways a book ends. 'settle' pays the winners out of the pot; 'void'
# and 'no_action' both hand every stake back and differ only in what the card
# and the audit trail say happened.
TERMINAL_INTENTS = ("settle", "void", "no_action")


async def claim_terminal(post_id, intent, closed_at):
	"""Stop the book taking money AND stake the terminal branch it will take,
	in ONE compare-and-set.

	-> the intent now recorded on the row: `intent` when this call is what
	   staked it, ANOTHER actor's intent when they got there first, or None
	   when the post has already gone terminal.

	THIS IS WHAT STOPS A REFUNDED BOOK ALSO BEING PAID OUT. Refunds and
	payouts move gold under different idem_keys — `refund:{post}:{user}` and
	`payout:{post}:{user}` — so nothing at the ledger level makes them
	mutually exclusive, and reconcile() cannot see the damage either because
	the ledger and the balance cache move together. The only thing that can
	make them exclusive is agreement about which branch the post is on.

	"Money first, terminal status last" was the old rule, and it is sound
	only if a crash between the two leaves the post retryable ON THE SAME
	BRANCH. It did not. A two-sided book voided by /subauto — refunds
	applied, then a failure before `status='void'` landed — was still
	'frozen', so live_for_match handed it straight to the settlement its
	match report triggered moments later, and scoring.payouts() paid out the
	very stakes that had just gone back. Gold minted, silently.

	The single UPDATE below is the compare-and-set. `terminal_intent IS NULL`
	makes it a claim only one caller can win; `status IN ('open','frozen')`
	makes that same statement the CLOSE, so an open book stops taking money
	in the very statement that decides how it ends and no press can land
	between the two (gold.place_bet's FOR UPDATE is the other half). A
	matched row always changes — NULL to a value — so the affected-row count
	is a true answer rather than MySQL's "did anything differ"."""
	if intent not in TERMINAL_INTENTS:
		raise ValueError(f"{intent!r} is not one of {TERMINAL_INTENTS}")
	async with db.transaction() as tx:
		if await tx.execute(
				"UPDATE prediction_posts SET "
				"freezes_at=CASE WHEN status='open' THEN %s ELSE freezes_at END, "
				"status='frozen', terminal_intent=%s "
				"WHERE id=%s AND status IN ('open','frozen') AND terminal_intent IS NULL",
				[closed_at, intent, post_id]):
			return intent
		# We lost the claim. FOR UPDATE rather than a plain read: this has to
		# be the CURRENT row, not this transaction's snapshot of it, or a
		# caller that lost by microseconds reads NULL back and both branches
		# proceed — which is the whole failure this function exists to close.
		row = await tx.fetchone(
			"SELECT status, terminal_intent FROM prediction_posts WHERE id=%s FOR UPDATE",
			[post_id])
		if row is None or row["status"] not in ("open", "frozen"):
			return None                  # already void / no_action / resolved
		return row["terminal_intent"]


async def freeze(post_id, votes0, votes1):
	await db.update("prediction_posts",
					{"status": "frozen", "votes0": votes0, "votes1": votes1}, {"id": post_id})


async def resolve(post_id, winner_idx, now):
	await db.update("prediction_posts",
					{"status": "resolved", "winner_idx": winner_idx, "resolved_at": now},
					{"id": post_id})


async def void(post_id, now):
	"""Drop a post out of scoring. Votes are left in place for the audit trail but
	never counted — the leaderboard only joins resolved posts."""
	await db.update("prediction_posts", {"status": "void", "resolved_at": now}, {"id": post_id})


# ── bets ─────────────────────────────────────────────────────────────────
# All WRITES to prediction_bets live in nammaoe2bot/features/betting/gold.py (inside the
# same transaction that moves the gold); the store only reads them.
async def bets_for(post_id):
	"""[{user_id, nick, side, stake, is_player}] biggest stake first — the order
	every report and payout roll-call presents. `is_player` is the roster fact
	captured when the bet was placed; it cannot be recomputed at report time."""
	return await db.fetchall(
		"SELECT user_id, nick, side, stake, is_player FROM prediction_bets "
		"WHERE post_id=%s ORDER BY stake DESC, user_id ASC", [post_id]) or []


async def bet_activity_by_user(channel_id):
	"""{user_id: latest bet-placement epoch} for one prediction channel.

	Read the append-only ledger rather than ``prediction_bets``: cancelling a
	bet deletes the live stake row, but it does not undo the fact that the player
	participated. Refunds, payouts and balance grants are deliberately excluded.
	"""
	rows = await db.fetchall(
		"SELECT l.user_id, MAX(l.created_at) AS last_bet_at "
		"FROM gold_ledger l JOIN prediction_posts p ON p.id=l.post_id "
		"WHERE p.channel_id=%s AND l.entry_type='bet' "
		"GROUP BY l.user_id",
		[channel_id],
	) or []
	return {r["user_id"]: int(r["last_bet_at"] or 0) for r in rows}


async def no_action(post_id, now):
	"""Terminal status for a one-sided book: nobody on the other side, every
	stake refunded at freeze time. Distinct from void so the card and the
	audit trail say what actually happened."""
	await db.update("prediction_posts", {"status": "no_action", "resolved_at": now}, {"id": post_id})


# ── aggregates ───────────────────────────────────────────────────────────
# One prediction = one row of `u`: historical reaction votes (frozen
# is_correct) UNION ALL gold bets (side graded against winner_idx at read
# time). A user appears once per predicted match either way, so the
# accuracy leaderboard carried straight over when betting replaced votes.
_LB_UNION = (
	"SELECT v.user_id, v.nick, COALESCE(v.is_correct, 0) AS correct, p.channel_id "
	"FROM prediction_votes v JOIN prediction_posts p ON p.id = v.post_id "
	"WHERE p.status='resolved' "
	"UNION ALL "
	"SELECT b.user_id, b.nick, (b.side = p.winner_idx) AS correct, p.channel_id "
	"FROM prediction_bets b JOIN prediction_posts p ON p.id = b.post_id "
	"WHERE p.status='resolved'"
)

_LB_SQL = (
	"SELECT user_id, MAX(nick) AS nick, "
	"       COALESCE(SUM(correct), 0) AS correct, COUNT(*) AS total "
	"FROM (" + _LB_UNION + ") u "
	"{channel}"
	"GROUP BY user_id "
	"ORDER BY correct DESC, total ASC"
)


async def leaderboard(channel_id=None):
	"""[{user_id, nick, correct, total}] best-first across resolved posts."""
	if channel_id is None:
		return await db.fetchall(_LB_SQL.format(channel="")) or []
	return await db.fetchall(_LB_SQL.format(channel="WHERE channel_id=%s "), [channel_id]) or []


async def user_stats(user_id, channel_id=None):
	"""(correct, total) for one user across resolved posts."""
	sql = (
		"SELECT COALESCE(SUM(correct), 0) AS correct, COUNT(*) AS total "
		"FROM (" + _LB_UNION + ") u WHERE user_id=%s"
	)
	params = [user_id]
	if channel_id is not None:
		sql += " AND channel_id=%s"
		params.append(channel_id)
	rows = await db.fetchall(sql, params)
	if not rows:
		return 0, 0
	return int(rows[0]["correct"] or 0), int(rows[0]["total"] or 0)
