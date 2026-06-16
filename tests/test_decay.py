"""Unit tests for the inactivity-gated rating/deviation decay policy.

Background: the weekly decay tick used to grow every player's deviation (sigma)
by ``rating_deviation_decay`` *unconditionally* — so even someone who played 10
games that week got +15 sigma, and no active player ever reached the 75 floor.
The policy now mirrors the rating decay: BOTH rating and deviation decay apply
only to players inactive for at least the grace window (1 month), and a single
game inside that window cancels the decay entirely.

These tests pin the pure decision logic in ``bot.stats.decay`` so it can be
verified without importing the rating engines (glicko2/trueskill) or the DB.
"""
from __future__ import annotations

import bot.stats.decay as decay

DAY = 60 * 60 * 24
NOW = 1_700_000_000
# rank thresholds: 0 is the lowest rank, the rest are climb-points
RANKS = [0, 500, 1000, 1200, 1400, 1600]


def _decay(
	rating=1500, deviation=90, last_at=None, days_ago=None,
	rating_decay=15, deviation_decay=15, init_deviation=200,
	ranks=None, grace=None,
):
	if days_ago is not None:
		last_at = NOW - days_ago * DAY
	return decay.compute_decay(
		rating, deviation, last_at, NOW,
		rating_decay, deviation_decay, init_deviation,
		RANKS if ranks is None else ranks,
		decay.MONTH if grace is None else grace,
	)


def test_grace_window_is_one_month():
	assert decay.MONTH == 60 * 60 * 24 * 30


def test_active_player_is_left_untouched():
	# played 3 days ago, well inside the 1-month grace
	assert _decay(rating=1500, deviation=90, days_ago=3) == (1500, 90)


def test_a_single_recent_game_cancels_decay():
	# one game 29 days ago (grace is 30) -> nothing decays at all
	assert _decay(rating=1820, deviation=80, days_ago=29) == (1820, 80)


def test_player_at_exact_grace_boundary_is_not_decayed():
	# last game exactly one month ago is not yet "inactive" (strict <)
	assert _decay(rating=1500, deviation=90, days_ago=30) == (1500, 90)


def test_inactive_player_gains_deviation():
	_, dev = _decay(rating=1500, deviation=90, days_ago=40, deviation_decay=15)
	assert dev == 105


def test_inactive_player_rating_decays_toward_rank_floor():
	# rating 1500 sits above the 1400 floor, so it drops by the decay amount
	new_rating, _ = _decay(rating=1500, deviation=90, days_ago=40, rating_decay=15)
	assert new_rating == 1485


def test_deviation_is_capped_at_initial_value():
	_, dev = _decay(rating=1500, deviation=195, days_ago=40, deviation_decay=15, init_deviation=200)
	assert dev == 200


def test_rating_decay_never_drops_below_the_rank_floor():
	# rating 1410 is only 10 above the 1400 floor; a 15-point decay clamps to 1400
	new_rating, _ = _decay(rating=1410, deviation=90, days_ago=40, rating_decay=15)
	assert new_rating == 1400


def test_no_rank_floor_means_rating_holds_but_deviation_still_grows():
	# rating 300 is below the lowest climb-point (500) -> floor is 0 -> no rating decay,
	# but an inactive player's uncertainty should still grow
	new_rating, dev = _decay(rating=300, deviation=90, days_ago=40)
	assert new_rating == 300
	assert dev == 105


def test_player_who_never_played_is_not_decayed():
	# last_at None (no ranked match on record) -> left alone
	assert _decay(rating=1000, deviation=200, last_at=None) == (1000, 200)


def test_zero_decay_settings_are_a_no_op_even_when_inactive():
	assert _decay(rating=1500, deviation=90, days_ago=90, rating_decay=0, deviation_decay=0) == (1500, 90)
