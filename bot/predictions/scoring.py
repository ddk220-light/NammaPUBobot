# -*- coding: utf-8 -*-
"""Pure grading logic for audience predictions.

No nextcord / core imports on purpose (the bot/lobby/subbing.py pattern): the
ballot rules are the only genuinely new logic here, and isolating them lets the
interesting cases be unit-tested without a live Discord client or database.
"""


def resolve_ballots(team0_ids, team1_ids, excluded_ids=()):
	"""Reaction sets -> {user_id: team_idx}.

	Votes are read back off the message at freeze time, so we get sets with no
	ordering. That means a user who reacted to both teams cannot be resolved to
	an intent and is discarded rather than guessed at. ``excluded_ids`` drops
	the players themselves (and the bot) silently.
	"""
	excluded = set(excluded_ids)
	a, b = set(team0_ids), set(team1_ids)
	ballots = {}
	for uid in a - b:
		if uid not in excluded:
			ballots[uid] = 0
	for uid in b - a:
		if uid not in excluded:
			ballots[uid] = 1
	return ballots


def tally(ballots):
	"""{user_id: team_idx} -> (votes_for_team0, votes_for_team1)."""
	values = list(ballots.values())
	return values.count(0), values.count(1)


def grade(ballots, winner_idx):
	"""{user_id: team_idx} -> {user_id: bool}, one point per correct call.

	``winner_idx`` of None means the match never resolved to a win/loss, which
	the caller treats as a void — nothing is graded.
	"""
	if winner_idx is None:
		return {}
	return {uid: idx == winner_idx for uid, idx in ballots.items()}


def split_pct(votes0, votes1):
	"""(v0, v1) -> (pct0, pct1) rounded to whole numbers, (0, 0) when nobody voted."""
	total = votes0 + votes1
	if not total:
		return 0, 0
	pct0 = round(100 * votes0 / total)
	return pct0, 100 - pct0
