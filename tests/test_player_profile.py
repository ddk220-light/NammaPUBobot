"""Unit tests for the pure helpers behind /rank's player profile.

The DB aggregation + matplotlib render are integration-tested by hand against
prod; these lock down the pure transforms (civ best/worst selection, recent-form
mapping) that decide what the embed shows.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta

from bot.player_profile import (
	IST, civ_breakdown, daily_candles, form_from_results, web_profile_link, web_profile_url,
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


class TestDailyCandles:
	TODAY = date(2026, 7, 28)
	NOW = _ts(TODAY, 20)

	def _candles(self, history, **kw):
		return daily_candles(history, now=self.NOW, **kw)

	def test_one_candle_per_day_with_ohlc_and_net_change(self):
		d = self.TODAY - timedelta(days=1)
		# Won to 1020, lost back to 990, won to 1035. Open is the pre-first-game rating.
		out = self._candles([
			(_ts(d, 10), 1000, 1020),
			(_ts(d, 11), 1020, 990),
			(_ts(d, 12), 990, 1035),
		])
		assert len(out) == 1
		c = out[0]
		assert (c["open"], c["close"], c["high"], c["low"]) == (1000, 1035, 1035, 990)
		assert c["change"] == 35
		assert c["games"] == 3

	def test_open_is_carried_in_rating_not_previous_close(self):
		# A decay tick between days moves rating without a candle of its own;
		# the next day's open must be what the player actually started with.
		d1, d2 = self.TODAY - timedelta(days=3), self.TODAY - timedelta(days=1)
		out = self._candles([(_ts(d1), 1000, 1050), (_ts(d2), 1040, 1060)])
		assert [c["open"] for c in out] == [1000, 1040]
		assert [c["change"] for c in out] == [50, 20]

	def test_days_outside_window_are_dropped(self):
		old = self.TODAY - timedelta(days=90)
		edge = self.TODAY - timedelta(days=59)  # oldest day still inside a 60-day window
		out = self._candles([(_ts(old), 900, 910), (_ts(edge), 910, 930)])
		assert [c["date"] for c in out] == [edge]

	def test_window_length_is_configurable(self):
		d = self.TODAY - timedelta(days=10)
		assert self._candles([(_ts(d), 1000, 1010)], days=7) == []
		assert len(self._candles([(_ts(d), 1000, 1010)], days=30)) == 1

	def test_candles_are_date_ascending_regardless_of_gaps(self):
		days = [self.TODAY - timedelta(days=n) for n in (20, 5, 1)]
		out = self._candles([(_ts(d), 1000, 1005) for d in days])
		assert [c["date"] for c in out] == sorted(days)

	def test_flat_day_has_zero_change(self):
		d = self.TODAY - timedelta(days=2)
		out = self._candles([(_ts(d, 10), 1000, 1020), (_ts(d, 11), 1020, 1000)])
		assert out[0]["change"] == 0
		assert (out[0]["high"], out[0]["low"]) == (1020, 1000)

	def test_ist_day_boundary(self):
		# 00:30 IST on the 28th is still the 27th in UTC — must bucket as the 28th.
		out = self._candles([(_ts(self.TODAY, 0) + 1800, 1000, 1010)])
		assert [c["date"] for c in out] == [self.TODAY]

	def test_null_rows_are_skipped_not_fatal(self):
		d = self.TODAY - timedelta(days=1)
		out = self._candles([(_ts(d), None, 1010), (_ts(d), 1000, 1010)])
		assert len(out) == 1 and out[0]["games"] == 1

	def test_empty_history(self):
		assert self._candles([]) == []


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
