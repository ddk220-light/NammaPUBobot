# -*- coding: utf-8 -*-
"""Prediction activity may refresh board recency without weakening its gates."""
from types import SimpleNamespace

from nammaoe2bot.pickup import leaderboard


def _row(user_id, *, last=100, games=50, hidden=False):
	return dict(
		user_id=user_id, nick=f"p{user_id}", rating=1_500, deviation=100,
		wins=games, losses=0, draws=0, streak=0, is_hidden=hidden,
		last_ranked_match_at=last,
	)


def _cfg():
	return SimpleNamespace(lb_last_match_limit=300, lb_min_matches=40)


def test_recent_bet_refreshes_only_the_prediction_boards_recency():
	# now=1000: ranked activity at 100 is stale; activity at 900 is recent.
	rows = [_row(7)]
	assert leaderboard.eligible_rows(rows, _cfg(), 1_000) == [], (
		"the ordinary rating board is unchanged")
	assert [r["user_id"] for r in leaderboard.eligible_rows(
		rows, _cfg(), 1_000, additional_activity={7: 900})] == [7]


def test_betting_does_not_bypass_hidden_or_minimum_match_gates():
	rows = [
		_row(7, hidden=True),
		_row(8, games=39),
		_row(9, games=40),
	]
	activity = {7: 900, 8: 900, 9: 900}

	assert [r["user_id"] for r in leaderboard.eligible_rows(
		rows, _cfg(), 1_000, additional_activity=activity)] == [9]
