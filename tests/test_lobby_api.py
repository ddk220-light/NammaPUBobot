# -*- coding: utf-8 -*-
"""Pure unit tests for bot/lobby/api.py parsers (no network). Shapes mirror the
live /api/matches/{id} response verified in the Phase 3 understand workflow:
ISO started/finished strings, per-player `won` bool, per-team teamId, civName."""
from bot.lobby import api


def test_parse_iso_epoch_and_invalid():
	assert api.parse_iso("1970-01-01T00:00:00.000Z") == 0
	assert api.parse_iso("2026-06-09T23:00:00.000Z") == api.parse_iso("2026-06-09T23:00:00+00:00")
	assert api.parse_iso(None) is None
	assert api.parse_iso("not-a-date") is None


def test_is_finished():
	assert api.is_finished({"finished": "2026-06-09T23:28:58.000Z"}) is True
	assert api.is_finished({"finished": None}) is False
	assert api.is_finished({}) is False


def test_match_duration_seconds():
	m = {"started": "2026-06-09T23:00:00.000Z", "finished": "2026-06-09T23:28:00.000Z"}
	assert api.match_duration_seconds(m) == 28 * 60
	assert api.match_duration_seconds({"started": "2026-06-09T23:00:00.000Z"}) is None
	assert api.match_duration_seconds({}) is None


def _teams_clean():
	return [
		{"teamId": 1, "players": [{"profileId": 10, "won": True, "civName": "Mongols"},
								  {"profileId": 11, "won": True, "civName": "Franks"}]},
		{"teamId": 2, "players": [{"profileId": 20, "won": False, "civName": "Aztecs"},
								  {"profileId": 21, "won": False, "civName": "Mayans"}]},
	]


def test_winning_teamid_clean():
	assert api.winning_teamid({"teams": _teams_clean()}) == 1


def test_winning_teamid_ambiguous_mixed():
	teams = [
		{"teamId": 1, "players": [{"won": True}, {"won": False}]},
		{"teamId": 2, "players": [{"won": False}, {"won": False}]},
	]
	assert api.winning_teamid({"teams": teams}) is None


def test_winning_teamid_all_none_is_draw():
	teams = [{"teamId": 1, "players": [{"won": None}]}, {"teamId": 2, "players": [{"won": None}]}]
	assert api.winning_teamid({"teams": teams}) is None


def test_winning_teamid_empty():
	assert api.winning_teamid({"teams": []}) is None
	assert api.winning_teamid({}) is None


def test_players_by_team_and_pid_civ_map():
	m = {"teams": _teams_clean()}
	assert api.players_by_team(m) == {1: [10, 11], 2: [20, 21]}
	assert api.pid_civ_map(m) == {10: "Mongols", 11: "Franks", 20: "Aztecs", 21: "Mayans"}
