# -*- coding: utf-8 -*-
"""The gold bank — the ONLY module that moves gold.

Every movement is one transaction: an append-only gold_ledger row plus the
matching gold_balances update, committed together or not at all. Non-bet
movements carry an idem_key with a unique index, so seeds, rewards, refunds
and payouts are impossible to apply twice — re-running a half-finished sweep
skips the rows that already exist (INSERT IGNORE, rowcount 0) and applies the
rest. The two exceptions are 'bet' and 'cancel', which a user may repeat on
one post and which are therefore guarded by a conditional write's rowcount
instead (see place_bet and cancel_bet). Balance truth is
SUM(gold_ledger.amount); gold_balances is the spendable cache, and
reconcile() can prove the two agree.

No nextcord and no time.time() in here: callers pass `now`, and the module
stays importable (and its control flow testable) under the conftest stubs."""
from nammaoe2bot.runtime.console import log
from nammaoe2bot.runtime.database import db

from . import scoring


class _Insufficient(Exception):
	pass


class _SideLocked(Exception):
	pass


class _Closed(Exception):
	pass


async def balance(community_id, user_id):
	"""Spendable gold; 0 for a user who has never been seeded."""
	row = await db.fetchone(
		"SELECT balance FROM gold_balances WHERE community_id=%s AND user_id=%s",
		[community_id, user_id])
	return int(row["balance"]) if row else 0


async def ensure_seeded(community_id, user_id, now):
	"""Grant the one-time starting gold. True only when the seed applied NOW —
	the caller can greet a first-time bettor. Safe to call on every touch."""
	async with db.transaction() as tx:
		applied = await tx.insert("gold_ledger", dict(
			community_id=community_id, user_id=user_id, entry_type="seed",
			amount=scoring.SEED_AMOUNT, created_at=now,
			idem_key=f"seed:{community_id}:{user_id}"), on_duplicate="ignore")
		if not applied:
			return False
		# Upsert-with-increment: correct even if a balances row somehow already
		# exists, and creates it when (normally) it does not.
		await tx.execute(
			"INSERT INTO gold_balances (community_id, user_id, balance, updated_at) "
			"VALUES (%s, %s, %s, %s) "
			"ON DUPLICATE KEY UPDATE balance=balance+%s, updated_at=%s",
			[community_id, user_id, scoring.SEED_AMOUNT, now, scoring.SEED_AMOUNT, now])
		return True


async def bulk_seed(now):
	"""Seed every known player in every community. Idempotent per
	(community, user) via the seed idem_key, so running this on every boot is
	safe — after the first pass it inserts nothing. Returns newly seeded."""
	rows = await db.fetchall(
		"SELECT DISTINCT cc.community_id, pr.user_id "
		"FROM player_ratings pr "
		"JOIN community_channels cc ON cc.channel_id = pr.channel_id") or []
	seeded = 0
	for r in rows:
		try:
			if await ensure_seeded(r["community_id"], r["user_id"], now):
				seeded += 1
		except Exception as e:
			log.error(f"Gold seed failed for {r['community_id']}/{r['user_id']}: {e}")
	return seeded


async def place_bet(community_id, user_id, post_id, side, stake, nick, now, is_player=False):
	"""One press of a bet button, atomically.

	-> ('ok', new_balance) | ('insufficient', balance) | ('side_locked', locked_side)
	   | ('closed', None)

	`is_player` says the bettor is on the side they just backed, decided by the
	caller against the live roster and stored on the row. It is captured here
	because it cannot be recovered later: the roster lives in app.active_matches
	and the match has left it by the time the result is reported.

	Inside one transaction: the book re-read and row-locked (see below), the
	conditional balance decrement (matching zero rows means not enough gold),
	the prediction_bets upsert whose composite PK IS the side lock (same-side
	UPDATE, else INSERT; a duplicate-key error means the user is on the other
	side), and the ledger row. Any rejection raises, so the stake deduction can
	never survive a refused bet."""
	if stake not in scoring.STAKES:
		raise ValueError(f"stake {stake} is not one of {scoring.STAKES}")
	try:
		async with db.transaction() as tx:
			# THE BOOK, RE-READ AND LOCKED — not a courtesy re-check of what the
			# handler already read.
			#
			# The handler's status/freezes_at check ran against a row it read at
			# the top of its own invocation, and a freeze / void / restart /
			# settle sweep can flip that row at any moment in between. Those
			# sweeps snapshot the book (store.bets_for) and then refund or pay
			# what they found. A press that commits AFTER the snapshot but while
			# the post is still 'open' is refunded by nobody and paid by nobody:
			# refund_post/pay_post are only ever called from those sweeps, the
			# post never returns to live_for_match once it is terminal, and
			# reconcile() cannot see the loss either because the ledger and the
			# balance cache agree perfectly — the gold is simply gone from
			# circulation.
			#
			# FOR UPDATE closes it by serialising the press against
			# store.close_betting's compare-and-set on the same row: either this
			# transaction commits first and the sweep's snapshot contains the
			# bet, or the sweep's flip to 'frozen' commits first and this read
			# sees it and refuses. No third outcome.
			book = await tx.fetchone(
				"SELECT status, freezes_at FROM prediction_posts WHERE id=%s FOR UPDATE",
				[post_id])
			if book is None or book["status"] != "open" or now >= int(book["freezes_at"]):
				raise _Closed()
			spent = await tx.execute(
				"UPDATE gold_balances SET balance=balance-%s, updated_at=%s "
				"WHERE community_id=%s AND user_id=%s AND balance>=%s",
				[stake, now, community_id, user_id, stake])
			if not spent:
				raise _Insufficient()
			# is_player is re-asserted on every press rather than written once:
			# the additive path never reaches the INSERT, so a flag set only
			# there would depend on which press happened to create the row.
			added = await tx.execute(
				"UPDATE prediction_bets SET stake=stake+%s, nick=%s, is_player=%s, updated_at=%s "
				"WHERE post_id=%s AND user_id=%s AND side=%s",
				[stake, nick, 1 if is_player else 0, now, post_id, user_id, side])
			if not added:
				try:
					await tx.insert("prediction_bets", dict(
						post_id=post_id, user_id=user_id, nick=nick,
						side=side, stake=stake, updated_at=now,
						is_player=1 if is_player else 0))
				except db.errors.IntegrityError:
					raise _SideLocked() from None
			await tx.insert("gold_ledger", dict(
				community_id=community_id, user_id=user_id, entry_type="bet",
				amount=-stake, post_id=post_id, created_at=now))
			row = await tx.fetchone(
				"SELECT balance FROM gold_balances WHERE community_id=%s AND user_id=%s",
				[community_id, user_id])
			return "ok", int(row["balance"])
	except _Closed:
		return "closed", None
	except _Insufficient:
		return "insufficient", await balance(community_id, user_id)
	except _SideLocked:
		row = await db.fetchone(
			"SELECT side FROM prediction_bets WHERE post_id=%s AND user_id=%s",
			[post_id, user_id])
		return "side_locked", int(row["side"]) if row else side


async def cancel_bet(community_id, user_id, post_id, now):
	"""Back out of a bet entirely, before the freeze.

	-> ('ok', refunded) | ('nothing', 0) | ('closed', 0)

	EXACTLY-ONCE WITHOUT AN IDEM_KEY, and that is deliberate. Every other credit
	here is made once by a UNIQUE idem_key, but a user may bet -> cancel -> bet
	-> cancel on ONE post, so cancel:{post_id}:{user_id} is not unique and the
	second cancel would be swallowed as "already applied". The prediction_bets
	ROW is the refund token instead: the DELETE's rowcount is the guard, so a
	double press finds no row and refunds nothing. Same discipline as
	place_bet's conditional balance decrement — the conditional write IS the
	guard.

	Deleting the row also releases the side lock — after cancelling, the user
	may bet again on either side (subject to the own-team rule if playing).

	THE BOOK IS RE-READ AND LOCKED HERE, not merely checked by the caller —
	see place_bet's comment for the race. Its mirror duplicates gold rather
	than destroying it: a sweep snapshots the book (store.bets_for) and then
	refunds what it found, so a cancel committing after that snapshot but
	before the status flip pays the stake back TWICE — once as this 'cancel'
	row, once as the sweep's idempotent refund:{post_id}:{user_id} row. The
	ledger and the balance cache would agree perfectly, so reconcile() would
	never see it. FOR UPDATE on prediction_posts serialises the two, and the
	'closed' verdict is therefore decided HERE, under the lock, never by the
	caller's earlier read.

	THE SECOND `FOR UPDATE`, on the bets row, IS BELT AND BRACES — recorded
	here rather than left as an unexplained line. What actually makes the
	read-then-delete safe is the POST row lock taken one statement above:
	prediction_bets has exactly two writers, this function and place_bet, and
	both take that lock first, so while it is held no other transaction can
	touch this post's bet rows at all. The DELETE's rowcount is the
	exactly-once guard on top of that. The row lock is therefore unobservable
	today — removing it cannot change any single-transaction outcome — and it
	is kept only so that a future third writer which forgets the post lock
	still cannot interleave here. The invariant it leans on is the pinnable
	one, and tests/test_predictions_gold.py pins it on both writers."""
	async with db.transaction() as tx:
		book = await tx.fetchone(
			"SELECT status, freezes_at FROM prediction_posts WHERE id=%s FOR UPDATE",
			[post_id])
		if book is None or book["status"] != "open" or now >= int(book["freezes_at"]):
			return "closed", 0
		row = await tx.fetchone(
			"SELECT stake FROM prediction_bets WHERE post_id=%s AND user_id=%s FOR UPDATE",
			[post_id, user_id])
		if not row:
			return "nothing", 0
		stake = int(row["stake"])
		removed = await tx.execute(
			"DELETE FROM prediction_bets WHERE post_id=%s AND user_id=%s", [post_id, user_id])
		if not removed:
			return "nothing", 0
		await tx.insert("gold_ledger", dict(
			community_id=community_id, user_id=user_id, entry_type="cancel",
			amount=stake, post_id=post_id, created_at=now))
		bumped = await tx.execute(
			"UPDATE gold_balances SET balance=balance+%s, updated_at=%s "
			"WHERE community_id=%s AND user_id=%s",
			[stake, now, community_id, user_id])
		if not bumped:
			raise RuntimeError(
				f"gold_balances row missing for {community_id}/{user_id} (cancel post {post_id})")
		return "ok", stake


async def _credit(community_id, user_id, entry_type, amount, idem_key, now,
				  match_id=None, post_id=None):
	"""Apply one positive movement exactly once. False when idem_key already
	applied. A missing balances row aborts the whole transaction — the ledger
	and the cache move together or not at all."""
	if amount <= 0:
		return False
	async with db.transaction() as tx:
		applied = await tx.insert("gold_ledger", dict(
			community_id=community_id, user_id=user_id, entry_type=entry_type,
			amount=amount, match_id=match_id, post_id=post_id,
			created_at=now, idem_key=idem_key), on_duplicate="ignore")
		if not applied:
			return False
		bumped = await tx.execute(
			"UPDATE gold_balances SET balance=balance+%s, updated_at=%s "
			"WHERE community_id=%s AND user_id=%s",
			[amount, now, community_id, user_id])
		if not bumped:
			raise RuntimeError(
				f"gold_balances row missing for {community_id}/{user_id} "
				f"({entry_type} {idem_key})")
		return True


async def refund_post(community_id, bets, post_id, now):
	"""Give every bettor their full stake back, exactly once each.
	`bets` is store.bets_for(post_id). Returns how many applied now."""
	done = 0
	for b in bets:
		if await _credit(community_id, b["user_id"], "refund", b["stake"],
						 f"refund:{post_id}:{b['user_id']}", now, post_id=post_id):
			done += 1
	return done


async def pay_post(community_id, paid, post_id, now):
	"""Apply scoring.payouts() to the bank, exactly once per winner."""
	done = 0
	for user_id, amount in paid.items():
		if await _credit(community_id, user_id, "payout", amount,
						 f"payout:{post_id}:{user_id}", now, post_id=post_id):
			done += 1
	return done


async def grant_match_reward(community_id, user_id, match_id, now):
	"""The playing faucet: top the balance up toward the ceiling, never above.
	Returns the gold granted (0 at/above ceiling, or when already granted)."""
	async with db.transaction() as tx:
		row = await tx.fetchone(
			"SELECT balance FROM gold_balances "
			"WHERE community_id=%s AND user_id=%s FOR UPDATE",
			[community_id, user_id])
		amount = scoring.reward_amount(int(row["balance"]) if row else 0)
		if not amount:
			return 0
		applied = await tx.insert("gold_ledger", dict(
			community_id=community_id, user_id=user_id, entry_type="match_reward",
			amount=amount, match_id=match_id, created_at=now,
			idem_key=f"reward:{match_id}:{user_id}"), on_duplicate="ignore")
		if not applied:
			return 0
		bumped = await tx.execute(
			"UPDATE gold_balances SET balance=balance+%s, updated_at=%s "
			"WHERE community_id=%s AND user_id=%s",
			[amount, now, community_id, user_id])
		if not bumped:
			raise RuntimeError(f"gold_balances row missing for {community_id}/{user_id} (reward)")
		return amount


async def grant_quiz_reward(community_id, user_id, quiz_post_id, correct, now):
	"""The quiz faucet: 50 for a correct answer, 10 for a cast vote, same
	ceiling as the match faucet. ONE ledger row per (quiz post, user) — the
	amount depends on correctness, so the idem key is per post+user, not per
	entry type. gold_ledger.post_id stays NULL: that column references
	prediction_posts, and a quiz post id in it would be a foreign lie — the
	quiz post id travels in the idem key. Caller must ensure_seeded first.
	Returns the gold granted (0 when capped out or already paid).

	THE AMOUNT IS DECIDED AT APPLY TIME, SO A RETRY CAN PAY A VOTER THE CEILING
	DENIED THEM ON THE FIRST ATTEMPT. The room is read off the balance inside
	this transaction: a voter sitting at 500 is granted 0 and NO ledger row is
	written, so no idem key is claimed. If the resolve then fails on some other
	voter and bot/quiz/jobs.py::_close_due re-enters it — a tick or a day
	later, by which time this voter has staked gold on a prediction and dropped
	to 300 — the retry finds room and pays the full 50. Never a double-pay (a
	grant that DID apply owns its idem key, and a second application is a
	no-op), but the payout for one quiz post genuinely depends on when the
	resolve managed to complete. Deliberate: the alternative is writing a
	0-amount row purely to burn the key, which would permanently deny the gold
	to everyone whose one failed resolve happened to catch them at the
	ceiling."""
	async with db.transaction() as tx:
		row = await tx.fetchone(
			"SELECT balance FROM gold_balances "
			"WHERE community_id=%s AND user_id=%s FOR UPDATE",
			[community_id, user_id])
		amount = scoring.quiz_reward_amount(int(row["balance"]) if row else 0, correct)
		if not amount:
			return 0
		applied = await tx.insert("gold_ledger", dict(
			community_id=community_id, user_id=user_id,
			entry_type="quiz_correct" if correct else "quiz_played",
			amount=amount, created_at=now,
			idem_key=f"quiz:{quiz_post_id}:{user_id}"), on_duplicate="ignore")
		if not applied:
			return 0
		bumped = await tx.execute(
			"UPDATE gold_balances SET balance=balance+%s, updated_at=%s "
			"WHERE community_id=%s AND user_id=%s",
			[amount, now, community_id, user_id])
		if not bumped:
			raise RuntimeError(f"gold_balances row missing for {community_id}/{user_id} (quiz)")
		return amount


async def quiz_paid_total(quiz_post_id):
	"""What this quiz post actually paid, read back from the ledger — the
	honest figure even on a resolve retried after a partial crash, where a
	loop accumulator would count only the newly applied grants."""
	rows = await db.fetchall(
		"SELECT COALESCE(SUM(amount), 0) s FROM gold_ledger "
		"WHERE idem_key LIKE %s", [f"quiz:{quiz_post_id}:%"])
	return int(rows[0]["s"]) if rows else 0


async def top_balances(community_id, limit=200):
	return await db.fetchall(
		"SELECT user_id, balance FROM gold_balances "
		"WHERE community_id=%s ORDER BY balance DESC, user_id ASC LIMIT " + str(int(limit)),
		[community_id]) or []


async def balances_by_user(community_id):
	"""{user_id: balance} for the whole community, unbounded.

	NOT top_balances(): that one is LIMIT 200 ordered by balance, so using it as
	a lookup silently reports "no balance" for anyone poorer than the 200th
	holder rather than their real number. A board that annotates rows by user
	needs every row it might annotate, not a leaderboard's worth.
	"""
	return {
		r["user_id"]: int(r["balance"])
		for r in await db.fetchall(
			"SELECT user_id, balance FROM gold_balances WHERE community_id=%s",
			[community_id]) or []
	}


async def recent_entries(community_id, user_id, limit=8):
	return await db.fetchall(
		"SELECT entry_type, amount, match_id, post_id, created_at FROM gold_ledger "
		"WHERE community_id=%s AND user_id=%s ORDER BY id DESC LIMIT " + str(int(limit)),
		[community_id, user_id]) or []


async def reconcile():
	"""Every (community, user) whose cached balance disagrees with its ledger
	sum. Empty means the invariant holds everywhere. Any row here is a bug."""
	return await db.fetchall(
		"SELECT b.community_id, b.user_id, b.balance, "
		"       COALESCE(SUM(l.amount), 0) AS ledger_sum "
		"FROM gold_balances b "
		"LEFT JOIN gold_ledger l "
		"  ON l.community_id=b.community_id AND l.user_id=b.user_id "
		"GROUP BY b.community_id, b.user_id, b.balance "
		"HAVING b.balance <> ledger_sum") or []
