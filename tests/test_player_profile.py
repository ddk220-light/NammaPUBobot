"""Unit tests for the pure helpers behind /rank's player profile.

The DB aggregation + matplotlib render are integration-tested by hand against
prod; these lock down the pure transforms (civ best/worst selection, recent-form
mapping) that decide what the embed shows.
"""
from __future__ import annotations

from bot.player_profile import civ_breakdown, form_from_results, web_profile_url


def _civ(name, wins, games):
	return {"civ": name, "wins": wins, "games": games}


class TestCivBreakdown:
	def test_best_sorted_by_winrate(self):
		out = civ_breakdown([_civ("Franks", 9, 10), _civ("Goths", 1, 10), _civ("Mayans", 5, 10)])
		assert [c["civ"] for c in out["best"]] == ["Franks", "Mayans", "Goths"]
		assert out["best"][0]["wr"] == 0.9

	def test_min_games_threshold_excludes_low_sample(self):
		out = civ_breakdown([_civ("Franks", 2, 2), _civ("Goths", 3, 6)])  # MIN_CIV_GAMES = 3
		assert [c["civ"] for c in out["best"]] == ["Goths"]
		assert out["total"] == 1

	def test_most_played_ignores_threshold(self):
		out = civ_breakdown([_civ("Franks", 1, 2), _civ("Goths", 3, 5)])
		assert out["most_played"]["civ"] == "Goths"

	def test_no_worst_until_more_than_six_civs(self):
		rows = [_civ(f"C{i}", i, 6) for i in range(6)]  # 6 qualified civs
		assert civ_breakdown(rows)["worst"] == []
		rows.append(_civ("C6", 3, 6))  # 7th -> worst now disjoint from best
		assert civ_breakdown(rows)["worst"]

	def test_empty(self):
		assert civ_breakdown([]) == {"best": [], "worst": [], "most_played": None, "total": 0}


class TestFormFromResults:
	def test_win_loss_draw(self):
		rows = [{"winner": 0, "team": 0}, {"winner": 1, "team": 0}, {"winner": None, "team": 1}]
		assert form_from_results(rows) == ["W", "L", "D"]

	def test_null_team_counts_as_loss_not_crash(self):
		assert form_from_results([{"winner": 0, "team": None}]) == ["L"]


class TestWebProfileUrl:
	def test_builds_player_route_and_normalizes_root(self):
		assert web_profile_url(" https://nammapub.example/ ", 123) == "https://nammapub.example/player/123"

	def test_missing_root_omits_link(self):
		assert web_profile_url("", 123) is None
