# -*- coding: utf-8 -*-
"""Pure betting math for audience predictions.

No nextcord / core imports on purpose (the bot/lobby/subbing.py pattern): the
pari-mutuel rules are the only genuinely new logic here, and isolating them
lets the interesting cases be unit-tested without a live Discord client or
database.
"""

# The whole economy in four numbers. STAKES is also the input-validation
# whitelist: custom_ids arrive from the client, so any stake not in this
# tuple is a forgery, not a feature request.
STAKES = (10, 50, 100)
SEED_AMOUNT = 500
MATCH_REWARD = 10
REWARD_CEILING = 500


def parse_bet_custom_id(cid):
	"""Route a component custom_id. 'bet:{post_id}:{side}:{stake}' ->
	(post_id, side, stake); anything else — foreign prefix, non-int parts,
	side not 0/1, stake not a real tier — is None."""
	if not cid or not cid.startswith("bet:"):
		return None
	parts = cid.split(":")
	if len(parts) != 4:
		return None
	try:
		post_id, side, stake = int(parts[1]), int(parts[2]), int(parts[3])
	except ValueError:
		return None
	if side not in (0, 1) or stake not in STAKES:
		return None
	return post_id, side, stake


def parse_cancel_custom_id(cid):
	"""'betcancel:{post_id}' -> post_id; anything else -> None."""
	if not cid or not cid.startswith("betcancel:"):
		return None
	parts = cid.split(":")
	if len(parts) != 2:
		return None
	try:
		return int(parts[1])
	except ValueError:
		return None


def pools(bets):
	"""[{side, stake}, ...] -> (pool0, pool1)."""
	pool0 = sum(b["stake"] for b in bets if b["side"] == 0)
	pool1 = sum(b["stake"] for b in bets if b["side"] == 1)
	return pool0, pool1


def payouts(bets, winner_idx):
	"""Pari-mutuel split: winners share the WHOLE pot in proportion to stake.

	[{user_id, side, stake}, ...] -> ({user_id: payout}, burned).
	floor() keeps gold integral; the crumbs are burned, never minted back.
	Either pool empty -> ({}, 0): a one-sided book has no odds, the caller
	refunds instead (the freeze no_action rule makes this unreachable at
	resolve, but the math must not invent an answer if it ever isn't).
	"""
	total = sum(b["stake"] for b in bets)
	win_pool = sum(b["stake"] for b in bets if b["side"] == winner_idx)
	if not win_pool or win_pool == total:
		return {}, 0
	paid = {b["user_id"]: b["stake"] * total // win_pool
			for b in bets if b["side"] == winner_idx}
	return paid, total - sum(paid.values())


def reward_amount(balance):
	"""Playing regenerates a depleted balance toward REWARD_CEILING and does
	nothing at or above it — the faucet is a lifeline, not an income."""
	return min(MATCH_REWARD, max(0, REWARD_CEILING - balance))


def multiplier(pool_side, pool_other):
	"""What a winning stake on this side multiplies by right now, or None
	when nobody has bet the side yet (no odds without a book)."""
	if not pool_side:
		return None
	return (pool_side + pool_other) / pool_side


def split_pct(votes0, votes1):
	"""(v0, v1) -> (pct0, pct1) rounded to whole numbers, (0, 0) when nobody voted."""
	total = votes0 + votes1
	if not total:
		return 0, 0
	pct0 = round(100 * votes0 / total)
	return pct0, 100 - pct0
