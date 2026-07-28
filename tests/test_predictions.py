"""Unit tests for audience-prediction grading.

Votes are read back off the Discord message as unordered sets at freeze time,
so the interesting logic is all in how ballots are derived from those sets and
how they grade against a result. Both modules are import-light on purpose (no
nextcord, no bot package), which is what makes this testable at all.
"""
from __future__ import annotations

from bot.predictions import scoring, view


class TestResolveBallots:
	def test_maps_each_side_to_its_team_index(self):
		assert scoring.resolve_ballots({1, 2}, {3}) == {1: 0, 2: 0, 3: 1}

	def test_voting_both_sides_is_discarded(self):
		# Sets carry no ordering, so "which did they mean?" is unanswerable —
		# the vote is dropped rather than guessed.
		assert scoring.resolve_ballots({1, 2}, {2, 3}) == {1: 0, 3: 1}

	def test_excluded_ids_are_dropped_silently(self):
		# Match players react like anyone else; they just don't count.
		assert scoring.resolve_ballots({1, 2}, {3, 4}, excluded_ids={2, 4}) == {1: 0, 3: 1}

	def test_exclusion_applies_to_both_sides_and_can_empty_the_vote(self):
		assert scoring.resolve_ballots({1}, {2}, excluded_ids={1, 2}) == {}

	def test_no_reactions_is_empty(self):
		assert scoring.resolve_ballots(set(), set()) == {}


class TestTally:
	def test_counts_each_side(self):
		assert scoring.tally({1: 0, 2: 0, 3: 1}) == (2, 1)

	def test_empty_is_zero_zero(self):
		assert scoring.tally({}) == (0, 0)


class TestGrade:
	def test_one_point_for_calling_the_winner(self):
		assert scoring.grade({1: 0, 2: 1}, winner_idx=0) == {1: True, 2: False}

	def test_winner_one_flips_the_result(self):
		assert scoring.grade({1: 0, 2: 1}, winner_idx=1) == {1: False, 2: True}

	def test_unresolved_match_grades_nothing(self):
		# A match that never reported a clean win/loss is voided, not scored.
		assert scoring.grade({1: 0, 2: 1}, winner_idx=None) == {}

	def test_no_ballots_grades_nothing(self):
		assert scoring.grade({}, winner_idx=0) == {}


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
