"""Unit tests for audience-prediction betting math.

Votes are read back off the Discord message as unordered sets at freeze time,
so the interesting logic is all in how ballots are derived from those sets and
how they grade against a result. Both modules are import-light on purpose (no
nextcord, no bot package), which is what makes this testable at all.
"""
from __future__ import annotations

from bot.predictions import scoring, view


class TestParseBetCustomId:
	def test_parses_a_valid_bet(self):
		assert scoring.parse_bet_custom_id("bet:12:0:50") == (12, 0, 50)

	def test_side_one(self):
		assert scoring.parse_bet_custom_id("bet:7:1:100") == (7, 1, 100)

	def test_foreign_prefix_is_none(self):
		assert scoring.parse_bet_custom_id("quiz:12:reveal") is None

	def test_forged_stake_is_none(self):
		# custom_ids come from the client; only the three tiers are money.
		assert scoring.parse_bet_custom_id("bet:12:0:9999") is None

	def test_forged_side_is_none(self):
		assert scoring.parse_bet_custom_id("bet:12:2:50") is None

	def test_garbage_is_none(self):
		assert scoring.parse_bet_custom_id("bet:x:y:z") is None
		assert scoring.parse_bet_custom_id("") is None


class TestPools:
	def test_sums_each_side(self):
		bets = [dict(user_id=1, side=0, stake=150), dict(user_id=2, side=0, stake=50),
				dict(user_id=3, side=1, stake=100)]
		assert scoring.pools(bets) == (200, 100)

	def test_empty(self):
		assert scoring.pools([]) == (0, 0)


class TestPayouts:
	def test_winners_split_the_whole_pot_proportionally(self):
		# The spec's worked example: 150+50 vs 100, side 0 wins.
		bets = [dict(user_id=1, side=0, stake=150), dict(user_id=2, side=0, stake=50),
				dict(user_id=3, side=1, stake=100)]
		paid, burned = scoring.payouts(bets, 0)
		assert paid == {1: 225, 2: 75}
		assert burned == 0

	def test_flooring_burns_the_remainder(self):
		# total=25, win_pool=20: 10*25//20=12, 10*25//20=12, burned 1.
		bets = [dict(user_id=1, side=0, stake=10), dict(user_id=2, side=0, stake=10),
				dict(user_id=3, side=1, stake=5)]
		paid, burned = scoring.payouts(bets, 0)
		assert paid == {1: 12, 2: 12}
		assert burned == 1

	def test_every_winner_gets_at_least_their_stake_back(self):
		bets = [dict(user_id=1, side=0, stake=10), dict(user_id=2, side=0, stake=100),
				dict(user_id=3, side=1, stake=10)]
		paid, _ = scoring.payouts(bets, 0)
		assert paid[1] >= 10 and paid[2] >= 100

	def test_empty_losing_pool_signals_refund(self):
		bets = [dict(user_id=1, side=0, stake=10)]
		assert scoring.payouts(bets, 0) == ({}, 0)

	def test_empty_winning_pool_signals_refund(self):
		bets = [dict(user_id=1, side=0, stake=10)]
		assert scoring.payouts(bets, 1) == ({}, 0)

	def test_no_bets(self):
		assert scoring.payouts([], 0) == ({}, 0)


class TestRewardAmount:
	def test_full_reward_below_the_ceiling(self):
		assert scoring.reward_amount(480) == 10

	def test_partial_reward_tops_up_to_exactly_500(self):
		assert scoring.reward_amount(496) == 4

	def test_nothing_at_the_ceiling(self):
		assert scoring.reward_amount(500) == 0

	def test_nothing_above_the_ceiling(self):
		assert scoring.reward_amount(620) == 0

	def test_zero_balance_gets_the_full_reward(self):
		assert scoring.reward_amount(0) == 10


class TestMultiplier:
	def test_underdog_pays_more(self):
		assert scoring.multiplier(100, 200) == 3.0
		assert scoring.multiplier(200, 100) == 1.5

	def test_empty_side_is_none(self):
		assert scoring.multiplier(0, 100) is None


class TestSplitPct:
	def test_splits_to_a_hundred(self):
		pct0, pct1 = scoring.split_pct(3, 1)
		assert (pct0, pct1) == (75, 25)

	def test_rounding_never_overshoots_a_hundred(self):
		# 1/3 rounds to 33; the remainder is derived so the pair always sums to 100.
		pct0, pct1 = scoring.split_pct(1, 2)
		assert pct0 + pct1 == 100

	def test_no_votes_is_zero_zero(self):
		assert scoring.split_pct(0, 0) == (0, 0)


class TestViewBuilders:
	def test_frozen_lines_name_the_favourite(self):
		text = "\n".join(view.frozen_lines("Alpha", "Beta", 5, 2))
		assert "Alpha" in text and "71%" in text

	def test_frozen_lines_call_out_a_dead_split(self):
		assert "Dead split" in "\n".join(view.frozen_lines("Alpha", "Beta", 2, 2))

	def test_frozen_lines_handle_nobody_voting(self):
		assert "no predictions" in "\n".join(view.frozen_lines("Alpha", "Beta", 0, 0)).lower()

	def test_result_lines_list_the_winners(self):
		text = "\n".join(view.result_lines("Alpha", ["ann", "bob"], 3))
		assert "2/3" in text and "ann, bob" in text

	def test_result_lines_when_everyone_was_wrong(self):
		assert "wrong" in "\n".join(view.result_lines("Alpha", [], 4)).lower()

	def test_result_lines_when_nobody_voted(self):
		assert "Nobody predicted" in "\n".join(view.result_lines("Alpha", [], 0))

	def test_rank_field_is_none_without_predictions(self):
		# /rank omits the field entirely rather than showing 0/0.
		assert view.rank_field(0, 0) is None

	def test_rank_field_summarises_record(self):
		assert view.rank_field(3, 4) == "3 pt · 3/4 correct (75%)"

	def test_leaderboard_lines_rank_and_paginate(self):
		rows = [{"nick": f"p{i}", "correct": 10 - i, "total": 10} for i in range(12)]
		first = view.leaderboard_lines(rows, page=1, per_page=10)
		assert len(first) == 10 and "p0" in first[0]
		second = view.leaderboard_lines(rows, page=2, per_page=10)
		assert len(second) == 2 and "11." in second[0]

	def test_leaderboard_lines_when_nothing_scored(self):
		assert "No predictions" in view.leaderboard_lines([])[0]
