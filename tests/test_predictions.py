"""Unit tests for audience-prediction betting math.

Votes are read back off the Discord message as unordered sets at freeze time,
so the interesting logic is all in how ballots are derived from those sets and
how they grade against a result. Both modules are import-light on purpose (no
nextcord, no bot package), which is what makes this testable at all.
"""
from __future__ import annotations

from nammaoe2bot.features.betting import scoring, view


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


class TestParseCancelCustomId:
	def test_parses_a_cancel(self):
		assert scoring.parse_cancel_custom_id("betcancel:12") == 12

	def test_a_bet_custom_id_is_not_a_cancel(self):
		# 'bet:' is a prefix of 'betcancel:' only in the other direction, but be sure.
		assert scoring.parse_cancel_custom_id("bet:12:0:50") is None

	def test_a_cancel_custom_id_is_not_a_bet(self):
		assert scoring.parse_bet_custom_id("betcancel:12") is None

	def test_garbage_is_none(self):
		assert scoring.parse_cancel_custom_id("betcancel:x") is None
		assert scoring.parse_cancel_custom_id("") is None


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
	def test_zero_balance_gets_the_full_reward(self):
		assert scoring.reward_amount(0) == 100

	def test_low_balance_gets_the_full_reward(self):
		assert scoring.reward_amount(399) == 100

	def test_balance_at_400_gets_the_full_reward(self):
		assert scoring.reward_amount(400) == 100

	def test_balance_at_401_gets_partial(self):
		assert scoring.reward_amount(401) == 99

	def test_full_reward_below_the_ceiling(self):
		assert scoring.reward_amount(480) == 20

	def test_partial_reward_tops_up_to_exactly_500(self):
		assert scoring.reward_amount(496) == 4

	def test_balance_at_499_gets_one(self):
		assert scoring.reward_amount(499) == 1

	def test_nothing_at_the_ceiling(self):
		assert scoring.reward_amount(500) == 0

	def test_nothing_above_the_ceiling(self):
		assert scoring.reward_amount(620) == 0

	def test_quiz_reward_amount_correct_pays_50(self):
		assert scoring.quiz_reward_amount(0, True) == 50
		assert scoring.quiz_reward_amount(449, True) == 50
		assert scoring.quiz_reward_amount(450, True) == 50
		assert scoring.quiz_reward_amount(451, True) == 49
		assert scoring.quiz_reward_amount(499, True) == 1
		assert scoring.quiz_reward_amount(500, True) == 0
		assert scoring.quiz_reward_amount(700, True) == 0

	def test_quiz_reward_amount_played_pays_10(self):
		assert scoring.quiz_reward_amount(0, False) == 10
		assert scoring.quiz_reward_amount(490, False) == 10
		assert scoring.quiz_reward_amount(491, False) == 9
		assert scoring.quiz_reward_amount(499, False) == 1
		assert scoring.quiz_reward_amount(500, False) == 0
		assert scoring.quiz_reward_amount(700, False) == 0


class TestMultiplier:
	def test_underdog_pays_more(self):
		assert scoring.multiplier(100, 200) == 3.0
		assert scoring.multiplier(200, 100) == 1.5

	def test_empty_side_is_none(self):
		assert scoring.multiplier(0, 100) is None


class TestOpenLines:
	def test_shows_both_pools_and_the_buttons_copy(self):
		lines = view.open_lines("Alpha", "Beta", 10, 33, pool0=200, pool1=100)
		text = "\n".join(lines)
		assert "Alpha" in text and "Beta" in text and "#33" in text
		assert "200" in text and "100" in text
		assert "React" not in text          # the reaction era is over

	def test_the_card_states_the_rule_amendment_1_actually_left_behind(self):
		""" The card carried "_Spectators only — players in this match cannot
		bet._" on every ranked match, re-rendered on every press, long after
		Amendment 1 §A superseded it and the router started welcoming
		participants onto their own side. For almost everyone in the channel
		the card IS the rulebook, and it was barring half of them. """
		text = "\n".join(view.open_lines("Alpha", "Beta", 10, 33))
		assert "Spectators only" not in text
		assert "cannot bet" not in text
		assert "own team" in text, "a player has to be told what they MAY do"
		assert "Either side" in text, "and a spectator that they are unrestricted"
		assert "whole pot" in text, "the pari-mutuel rule survives both amendments"

	def test_fresh_card_has_zero_pools_and_no_multiplier(self):
		text = "\n".join(view.open_lines("Alpha", "Beta", 10, 33))
		assert "×" not in text

	def test_multiplier_appears_once_both_pools_exist(self):
		text = "\n".join(view.open_lines("Alpha", "Beta", 10, 33, pool0=200, pool1=100))
		assert "×1.5" in text and "×3" in text


class TestFrozenLines:
	def test_names_the_bigger_pool_as_favourite(self):
		text = "\n".join(view.frozen_lines("Alpha", "Beta", 300, 100, 3, 1))
		assert "Alpha" in text and "300" in text and "100" in text

	def test_no_bets_reads_plainly(self):
		text = "\n".join(view.frozen_lines("Alpha", "Beta", 0, 0, 0, 0))
		assert "no" in text.lower()


class TestNoActionLines:
	def test_says_refunded(self):
		text = "\n".join(view.no_action_lines("Alpha", "Beta"))
		assert "refund" in text.lower()


class TestReportLines:
	BETS = [
		dict(user_id=1, nick="anu", side=0, stake=150),
		dict(user_id=2, nick="bala", side=0, stake=50),
		dict(user_id=3, nick="chetan", side=1, stake=100),
	]

	def test_full_report_names_everyone_with_stakes_and_payouts(self):
		text = "\n".join(view.report_lines("Alpha", "Beta", 0, self.BETS, {1: 225, 2: 75}))
		assert "Alpha" in text                       # the winner
		assert "anu" in text and "225" in text and "+75" in text
		assert "bala" in text and "75" in text
		assert "chetan" in text and "100" in text    # the loser and what they lost
		assert "300" in text                         # the pot

	def test_nobody_bet(self):
		text = "\n".join(view.report_lines("Alpha", "Beta", 0, [], {}))
		assert "Nobody bet" in text

	def test_overflow_is_capped_with_a_more_line(self):
		bets = [dict(user_id=i, nick=f"u{i}", side=0, stake=10) for i in range(30)]
		bets.append(dict(user_id=99, nick="loser", side=1, stake=10))
		paid = {i: 10 for i in range(30)}
		lines = view.report_lines("Alpha", "Beta", 0, bets, paid, max_named=25)
		text = "\n".join(lines)
		assert "+5 more" in text

	SELF_BETS = [
		dict(user_id=1, nick="anu", side=0, stake=150, is_player=1),
		dict(user_id=2, nick="bala", side=0, stake=50, is_player=0),
		dict(user_id=3, nick="chetan", side=1, stake=100, is_player=1),
	]

	def test_a_player_who_backed_themselves_and_won_is_named_as_such(self):
		text = "\n".join(view.report_lines("Alpha", "Beta", 0, self.SELF_BETS, {1: 225, 2: 75}))
		anu = next(ln for ln in text.split("\n") if "anu" in ln)
		assert "backed themselves" in anu

	def test_a_player_who_backed_themselves_and_lost_is_named_as_such(self):
		text = "\n".join(view.report_lines("Alpha", "Beta", 0, self.SELF_BETS, {1: 225, 2: 75}))
		chetan = next(ln for ln in text.split("\n") if "chetan" in ln)
		assert "backed themselves" in chetan

	def test_a_spectator_is_not_annotated(self):
		text = "\n".join(view.report_lines("Alpha", "Beta", 0, self.SELF_BETS, {1: 225, 2: 75}))
		bala = next(ln for ln in text.split("\n") if "bala" in ln)
		assert "backed themselves" not in bala

	def test_rows_without_the_flag_still_render(self):
		# Historical posts predate the column; they must not crash the report.
		text = "\n".join(view.report_lines("Alpha", "Beta", 0, self.BETS, {1: 225, 2: 75}))
		assert "anu" in text and "backed themselves" not in text


class TestBetConfirmLines:
	def test_states_stake_side_pools_and_balance(self):
		text = "\n".join(view.bet_confirm_lines("Alpha", 50, 60, 230, 180, 430))
		assert "50" in text and "Alpha" in text and "60" in text
		assert "230" in text and "180" in text and "430" in text


class TestMeLines:
	""" /predictions me absorbed /gold: record, balance and movements on one card. """

	def test_record_balance_and_entries(self):
		entries = [
			dict(entry_type="payout", amount=225, match_id=None, post_id=12, created_at=0),
			dict(entry_type="bet", amount=-50, match_id=None, post_id=12, created_at=0),
			dict(entry_type="seed", amount=500, match_id=None, post_id=None, created_at=0),
		]
		text = "\n".join(view.me_lines("anu", 3, 4, 430, entries))
		assert "anu" in text and "3/4" in text
		assert "430" in text and "Winnings" in text and "+225" in text
		assert "Bet placed" in text and "-50" in text and "Starting gold" in text

	def test_balance_without_entries(self):
		text = "\n".join(view.me_lines("anu", 1, 2, 500, []))
		assert "500" in text

	def test_seeded_now_greets_instead_of_reporting(self):
		text = "\n".join(view.me_lines("anu", 0, 0, 500, [], seeded_now=True))
		assert "start with 500" in text
		assert "Holding" not in text

	def test_balance_shows_even_with_no_prediction_record(self):
		# The two halves are independent — someone who only wanted their gold
		# should not be told "no record" instead of being given it.
		text = "\n".join(view.me_lines("anu", 0, 0, 500, []))
		assert "No matches predicted yet." in text
		assert "500" in text

	def test_absent_community_omits_gold_entirely(self):
		# balance=None means "this channel has no gold", not "you hold zero".
		text = "\n".join(view.me_lines("anu", 2, 3, None, []))
		assert "2/3" in text
		assert "Holding" not in text and "0 " not in text


class TestViewBuilders:
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
