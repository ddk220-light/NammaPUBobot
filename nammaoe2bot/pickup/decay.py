# -*- coding: utf-8 -*-
"""Pure decay-decision helpers for the rating system.

Deliberately dependency-free (stdlib only) so the decay *policy* can be unit
tested without importing the rating engines (glicko2 / trueskill) or touching
the database. ``BaseRating.apply_decay`` is a thin wrapper around
``compute_decay`` — all the behaviour lives here.

Policy: a weekly tick may decay a player's rating (toward the nearest rank
floor) and grow their deviation (toward the initial value), but BOTH apply only
to players who have been inactive for at least ``grace`` seconds. A player who
has played even a single ranked game inside the grace window is left untouched.
This is the difference from the legacy behaviour, where deviation grew for every
player on every tick regardless of activity.
"""
from __future__ import annotations

MONTH = 60 * 60 * 24 * 30  # inactivity grace before any decay applies


def rank_floor(rating, ranks):
	"""Highest rank threshold at or below ``rating`` (0 if none)."""
	return max([r for r in ranks if r <= rating] + [0])


def compute_decay(
	rating, deviation, last_at, now,
	rating_decay, deviation_decay, init_deviation, ranks, grace=MONTH,
):
	"""Return ``(new_rating, new_deviation)`` for one player after a decay tick.

	``last_at`` is the player's most recent ranked-match timestamp (or ``None``
	if they have never played a ranked game). Decay applies only when the player
	has been inactive for at least ``grace`` seconds; otherwise the player is
	returned unchanged so a single recent game cancels the decay.
	"""
	inactive = last_at is not None and last_at < (now - grace)
	if not inactive:
		return rating, deviation

	new_deviation = min(init_deviation, deviation + deviation_decay)

	floor = rank_floor(rating, ranks)
	new_rating = max(floor, rating - rating_decay) if floor != 0 else rating

	return new_rating, new_deviation
