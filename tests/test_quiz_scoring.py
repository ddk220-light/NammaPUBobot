"""Pure-function tests for bot.quiz.scoring (no DB, no nextcord — see conftest)."""
from __future__ import annotations

import bot.quiz.scoring as s


def test_parse_custom_id_reveal_and_answer():
	assert s.parse_custom_id("quiz:42:reveal") == ("reveal", 42, None)
	assert s.parse_custom_id("quiz:42:ans:3") == ("answer", 42, 3)
	assert s.parse_custom_id("quiz:7:ans:0") == ("answer", 7, 0)


def test_parse_custom_id_rejects_foreign_and_malformed():
	assert s.parse_custom_id("other:1:reveal") is None
	assert s.parse_custom_id("quiz:nope:reveal") is None
	assert s.parse_custom_id("quiz:42:ans:x") is None
	assert s.parse_custom_id("quiz:42") is None
	assert s.parse_custom_id("") is None


def test_grade():
	assert s.grade(2, 2) is True
	assert s.grade(0, 2) is False


def test_iso_week_key_buckets_same_week():
	mon = 1718236800   # 2024-06-13 (Thursday) UTC
	later = mon + 24 * 3600
	assert s.iso_week_key(mon) == s.iso_week_key(later)


def test_tally_counts_correct_and_accuracy():
	rows = [
		{"user_id": 1, "nick": "a", "is_correct": 1},
		{"user_id": 1, "nick": "a", "is_correct": 0},
		{"user_id": 2, "nick": "b", "is_correct": 1},
		{"user_id": 1, "nick": "a", "is_correct": 1},
	]
	out = s.tally(rows)
	assert out[0] == {"user_id": 1, "nick": "a", "correct": 2, "answered": 3}
	assert out[1] == {"user_id": 2, "nick": "b", "correct": 1, "answered": 1}


def test_tally_empty():
	assert s.tally([]) == []


def test_daily_due_fires_once_per_day_at_hour():
	at = 1718269200   # 2024-06-13 09:00 UTC
	assert s.daily_due(now_ts=at, hour=9, last_post_ymd="2024-06-12") is True
	assert s.daily_due(now_ts=at, hour=9, last_post_ymd="2024-06-13") is False   # already today
	assert s.daily_due(now_ts=at, hour=10, last_post_ymd="2024-06-12") is False  # before hour


def test_leaderboard_due_gates_on_weekday_hour_and_dedup():
	# 2024-06-13 is a Thursday -> isoweekday 4
	at = 1718269200   # 09:00 UTC Thursday
	assert s.leaderboard_due(now_ts=at, dow=4, hour=9, last_leaderboard_ymd="2024-06-06") is True
	assert s.leaderboard_due(now_ts=at, dow=4, hour=9, last_leaderboard_ymd="2024-06-13") is False
	assert s.leaderboard_due(now_ts=at, dow=7, hour=9, last_leaderboard_ymd="") is False  # wrong dow
	assert s.leaderboard_due(now_ts=at, dow=4, hour=10, last_leaderboard_ymd="") is False  # before hour


from bot.quiz.scoring import grade_multi, parse_custom_id


def test_grade_multi_exact_match_is_correct():
	assert grade_multi([2, 0], [0, 2]) is True          # order-independent
	assert grade_multi([1], [1]) is True


def test_grade_multi_subset_or_superset_is_wrong():
	assert grade_multi([0], [0, 2]) is False             # missed one
	assert grade_multi([0, 1, 2], [0, 2]) is False       # extra one
	assert grade_multi([], [0]) is False                 # empty


def test_grade_multi_dedups_repeats():
	assert grade_multi([2, 2, 0], [0, 2]) is True


def test_parse_custom_id_multiselect_route():
	assert parse_custom_id("quiz:7:msel") == ("mselect", 7, None)
	assert parse_custom_id("quiz:7:ans:2") == ("answer", 7, 2)
	assert parse_custom_id("quiz:7:reveal") == ("reveal", 7, None)
