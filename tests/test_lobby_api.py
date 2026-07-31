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


# ── /profiles/{id} — the /link validation source ─────────────────────────
# Bodies below are the live 2026-07-30 responses, verbatim (trimmed of fields
# nothing reads). `name` is the REAL in-game name — the value stored as
# identities.aoe2_name. The 404 body is the trap: it carries a `profileId` of
# its own, so "has a profileId" is NOT enough to call a payload a profile.

def test_parse_profile_extracts_id_and_name():
	payload = {
		"avatarhash": "e2b0c1f0",
		"name": "ddk220",
		"profileId": 612690,
		"country": "us",
		"games": "2647",
		"leaderboards": [{"leaderboardId": "rm_team", "rating": 1559}],
	}
	assert api._parse_profile(payload) == {"profile_id": 612690, "name": "ddk220"}


def test_parse_profile_returns_none_for_the_404_body():
	payload = {"success": False, "error": "profile couldn't be found", "profileId": 999999999}
	assert api._parse_profile(payload) is None


def test_parse_profile_coerces_a_string_profile_id():
	"""The same API serialises `games` as a string, so don't trust profileId to
	always arrive as an int — a str id must still parse, as an int."""
	parsed = api._parse_profile({"profileId": "612690", "name": "ddk220"})
	assert parsed == {"profile_id": 612690, "name": "ddk220"}
	assert isinstance(parsed["profile_id"], int)


def test_parse_profile_tolerates_a_missing_name():
	"""A profile with no name is still a real profile — the caller just has no
	in-game name to echo back for the player to eyeball."""
	assert api._parse_profile({"profileId": 612690}) == {"profile_id": 612690, "name": ""}


def test_parse_profile_returns_none_without_a_usable_profile_id():
	assert api._parse_profile({"name": "ddk220"}) is None
	assert api._parse_profile({"profileId": "not-a-number", "name": "ddk220"}) is None
	assert api._parse_profile(None) is None
	assert api._parse_profile("profile couldn't be found") is None
