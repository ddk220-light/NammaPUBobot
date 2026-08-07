"""Unit tests for the pure helpers behind /rank's player profile.

The DB aggregation + matplotlib render are integration-tested by hand against
prod; these lock down the pure transforms (civ best/worst selection, recent-form
mapping) that decide what the embed shows.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta

from nammaoe2bot.features.scouting.profile import (
	IST, bucket_candles, civ_breakdown, form_from_results, web_profile_link, web_profile_url,
)


def _ts(day: date, hour: int = 12) -> int:
	"""Unix ts for a given IST calendar day/hour."""
	return int(datetime.combine(day, dtime(hour), tzinfo=IST).timestamp())


def _civ(name, wins, games):
	return {"civ": name, "wins": wins, "games": games}


class TestCivBreakdown:
	def test_best_sorted_by_winrate(self):
		out = civ_breakdown([_civ("Franks", 18, 20), _civ("Goths", 2, 20), _civ("Mayans", 10, 20)])
		assert [c["civ"] for c in out["best"]] == ["Franks", "Mayans", "Goths"]
		assert out["best"][0]["wr"] == 0.9

	def test_min_games_threshold_excludes_low_sample(self):
		out = civ_breakdown([_civ("Franks", 12, 14), _civ("Goths", 8, 15)])  # MIN_CIV_GAMES = 15
		assert [c["civ"] for c in out["best"]] == ["Goths"]
		assert out["total"] == 1

	def test_most_played_ignores_threshold(self):
		out = civ_breakdown([_civ("Franks", 1, 2), _civ("Goths", 3, 5)])
		assert out["most_played"]["civ"] == "Goths"

	def test_no_worst_until_more_than_six_civs(self):
		rows = [_civ(f"C{i}", i, 16) for i in range(6)]  # 6 qualified civs
		assert civ_breakdown(rows)["worst"] == []
		rows.append(_civ("C6", 3, 16))  # 7th -> worst now disjoint from best
		assert civ_breakdown(rows)["worst"]

	def test_empty(self):
		assert civ_breakdown([]) == {"best": [], "worst": [], "most_played": None, "total": 0}


class TestBucketCandles:
	"""Slots are Mon-Thu (one per week) + Fri + Sat + Sun, bucketed in IST."""

	TODAY = date(2026, 7, 28)          # a Tuesday
	NOW = _ts(TODAY, 20)

	def _slots(self, history, **kw):
		return bucket_candles(history, now=self.NOW, **kw)

	def _played(self, history, **kw):
		return [c for c in self._slots(history, **kw) if c["games"]]

	def test_weekend_days_get_their_own_slot(self):
		fri, sat, sun = date(2026, 7, 24), date(2026, 7, 25), date(2026, 7, 26)
		played = self._played([(_ts(d), 1000, 1010) for d in (fri, sat, sun)])
		assert [c["kind"] for c in played] == ["FRI", "SAT", "SUN"]
		assert [c["label"] for c in played] == ["F", "Sa", "Su"]

	def test_mon_to_thu_collapse_into_one_slot_per_week(self):
		# Mon 20th .. Thu 23rd July, four separate days, one candle.
		days = [date(2026, 7, 20), date(2026, 7, 21), date(2026, 7, 22), date(2026, 7, 23)]
		played = self._played([(_ts(d, 10), 1000 + 10 * n, 1010 + 10 * n) for n, d in enumerate(days)])
		assert len(played) == 1
		c = played[0]
		assert c["kind"] == "MT" and c["label"] == "M–T"
		assert (c["open"], c["close"]) == (1000, 1040)
		assert c["games"] == 4 and c["days"] == 4

	def test_mon_to_thu_of_different_weeks_stay_separate(self):
		a, b = date(2026, 7, 14), date(2026, 7, 21)  # two Tuesdays, a week apart
		played = self._played([(_ts(a), 1000, 1010), (_ts(b), 1010, 1020)])
		assert len(played) == 2
		assert played[0]["week"] != played[1]["week"]

	def test_slots_are_chronological_and_indexed(self):
		slots = self._slots([])
		assert [c["index"] for c in slots] == list(range(len(slots)))
		starts = [c["start"] for c in slots]
		assert starts == sorted(starts)

	def test_empty_slots_are_still_returned_so_the_axis_is_identical(self):
		# An inactive player and an active one must get the same axis shape.
		empty = self._slots([])
		active = self._slots([(_ts(date(2026, 7, 25)), 1000, 1020)])
		assert len(empty) == len(active) > 0
		assert [c["kind"] for c in empty] == [c["kind"] for c in active]
		assert all(c["games"] == 0 and c["open"] is None for c in empty)

	def test_ohlc_and_net_change_within_a_slot(self):
		sat = date(2026, 7, 25)
		played = self._played([
			(_ts(sat, 10), 1000, 1020),
			(_ts(sat, 11), 1020, 990),
			(_ts(sat, 12), 990, 1035),
		])
		c = played[0]
		assert (c["open"], c["close"], c["high"], c["low"]) == (1000, 1035, 1035, 990)
		assert c["change"] == 35 and c["games"] == 3

	def test_open_is_carried_in_rating_not_previous_close(self):
		# Decay between slots moves rating with no games; the next slot's open
		# must be what the player actually started with.
		a, b = date(2026, 7, 24), date(2026, 7, 25)
		played = self._played([(_ts(a), 1000, 1050), (_ts(b), 1040, 1060)])
		assert [c["open"] for c in played] == [1000, 1040]
		assert [c["change"] for c in played] == [50, 20]

	def test_days_outside_window_are_dropped(self):
		old = self.TODAY - timedelta(days=90)
		edge = self.TODAY - timedelta(days=59)
		played = self._played([(_ts(old), 900, 910), (_ts(edge), 910, 930)])
		assert len(played) == 1 and played[0]["start"] <= edge <= played[0]["end"]

	def test_window_length_is_configurable(self):
		d = self.TODAY - timedelta(days=10)
		assert self._played([(_ts(d), 1000, 1010)], days=7) == []
		assert len(self._played([(_ts(d), 1000, 1010)], days=30)) == 1

	def test_flat_slot_has_zero_change(self):
		sun = date(2026, 7, 26)
		c = self._played([(_ts(sun, 10), 1000, 1020), (_ts(sun, 11), 1020, 1000)])[0]
		assert c["change"] == 0 and (c["high"], c["low"]) == (1020, 1000)

	def test_ist_day_boundary(self):
		# 00:30 IST Sunday is still Saturday in UTC — must land in the Sunday slot.
		sun = date(2026, 7, 26)
		assert self._played([(_ts(sun, 0) + 1800, 1000, 1010)])[0]["kind"] == "SUN"

	def test_null_rows_are_skipped_not_fatal(self):
		d = self.TODAY - timedelta(days=1)
		played = self._played([(_ts(d), None, 1010), (_ts(d), 1000, 1010)])
		assert len(played) == 1 and played[0]["games"] == 1

	def test_empty_history_still_lays_out_the_window(self):
		slots = self._slots([])
		assert slots and all(c["games"] == 0 for c in slots)


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


class TestWebProfileLink:
	def test_links_nick_when_configured(self):
		assert web_profile_link("https://nammapub.example", 123, "Player") == \
			"[Player](https://nammapub.example/player/123)"

	def test_plain_nick_when_not_configured(self):
		assert web_profile_link("", 123, "Player") == "Player"

	def test_brackets_in_nick_are_neutralized(self):
		assert web_profile_link("https://nammapub.example", 123, "[TAG] Bob") == \
			"[(TAG) Bob](https://nammapub.example/player/123)"
