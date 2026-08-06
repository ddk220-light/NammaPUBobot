# -*- coding: utf-8 -*-
"""The gold bank — the ONLY module that moves gold.

Every movement is one transaction: an append-only gold_ledger row plus the
matching gold_balances update, committed together or not at all. Non-bet
movements carry an idem_key with a unique index, so seeds, rewards, refunds
and payouts are impossible to apply twice — re-running a half-finished sweep
skips the rows that already exist (INSERT IGNORE, rowcount 0) and applies the
rest. Balance truth is SUM(gold_ledger.amount); gold_balances is the
spendable cache, and reconcile() can prove the two agree.

No nextcord and no time.time() in here: callers pass `now`, and the module
stays importable (and its control flow testable) under the conftest stubs."""
from core.console import log
from core.database import db

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


async def place_bet(community_id, user_id, post_id, side, stake, nick, now):
	"""One press of a bet button, atomically.

	-> ('ok', new_balance) | ('insufficient', balance) | ('side_locked', locked_side)
	   | ('closed', None)

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
			added = await tx.execute(
				"UPDATE prediction_bets SET stake=stake+%s, nick=%s, updated_at=%s "
				"WHERE post_id=%s AND user_id=%s AND side=%s",
				[stake, nick, now, post_id, user_id, side])
			if not added:
				try:
					await tx.insert("prediction_bets", dict(
						post_id=post_id, user_id=user_id, nick=nick,
						side=side, stake=stake, updated_at=now))
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


async def top_balances(community_id, limit=200):
	return await db.fetchall(
		"SELECT user_id, balance FROM gold_balances "
		"WHERE community_id=%s ORDER BY balance DESC, user_id ASC LIMIT " + str(int(limit)),
		[community_id]) or []


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
