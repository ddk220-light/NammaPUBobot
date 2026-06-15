# -*- coding: utf-8 -*-
"""Pure unit tests for bot/lobby/completed.py decision helpers — the winner ->
losing-captain resolution and its degrade modes, plus poll/giveup arithmetic and
pid parsing. No nextcord / DB / network touched."""
import types

from bot.lobby import completed


def _member(uid):
	return types.SimpleNamespace(id=uid)


def _api(win_team=1, mapped=True):
	"""Two API teams; team `win_team` all won. profileIds 101..104."""
	return {"teams": [
		{"teamId": 1, "players": [{"profileId": 101, "won": win_team == 1},
								  {"profileId": 102, "won": win_team == 1}]},
		{"teamId": 2, "players": [{"profileId": 103, "won": win_team == 2},
								  {"profileId": 104, "won": win_team == 2}]},
	]}


def test_parse_pids():
	assert completed.parse_pids("101, 102 ,3,x,") == [101, 102, 3]
	assert completed.parse_pids(None) == []
	assert completed.parse_pids("") == []


def test_parse_game_id():
	assert completed.parse_game_id("485355768") == 485355768
	assert completed.parse_game_id("aoe2de://0/485355768") == 485355768
	assert completed.parse_game_id("  aoe2de://0/485355768/ ") == 485355768
	assert completed.parse_game_id("AOE2DE://0/123") == 123
	assert completed.parse_game_id("not-a-number") is None
	assert completed.parse_game_id("") is None
	assert completed.parse_game_id(None) is None


def test_next_poll_at_and_should_giveup():
	assert completed.next_poll_at(1000) == 1000 + completed.POLL_AFTER_SECONDS
	assert completed.should_giveup(0, completed.GIVEUP_AFTER + 1) is True
	assert completed.should_giveup(0, completed.GIVEUP_AFTER - 1) is False
	assert completed.should_giveup(None, 10) is False


def test_resolve_result_confident_gates_losing_captain():
	# bot team0 = [cap 1, p 2]; team1 = [cap 3, p 4]. API team1 won (101,102 -> 1,2).
	bot_teams = [[_member(1), _member(2)], [_member(3), _member(4)]]
	p2u = {101: 1, 102: 2, 103: 3, 104: 4}
	win_idx, captain = completed.resolve_result(_api(win_team=1), p2u, bot_teams)
	assert win_idx == 0           # winners (users 1,2) are in bot team 0
	assert captain.id == 3        # losing bot team's captain (first member)


def test_resolve_result_ambiguous_winner_degrades_fully():
	bot_teams = [[_member(1)], [_member(3)]]
	draw = {"teams": [
		{"teamId": 1, "players": [{"profileId": 101, "won": None}]},
		{"teamId": 2, "players": [{"profileId": 103, "won": None}]},
	]}
	assert completed.resolve_result(draw, {101: 1, 103: 3}, bot_teams) == (None, None)


def test_resolve_result_unmapped_losers_keeps_winner_drops_captain():
	# Winner determinable, but losing profiles not in the map -> winner_idx known
	# (for Flow 3 W/L), captain None (degrade Flow 2 to generic prompt).
	bot_teams = [[_member(1), _member(2)], [_member(3), _member(4)]]
	p2u = {101: 1, 102: 2}   # only the winning side mapped
	win_idx, captain = completed.resolve_result(_api(win_team=1), p2u, bot_teams)
	assert win_idx == 0
	assert captain is None


def test_resolve_result_winner_spans_two_bot_teams_degrades():
	# Winning API profiles map to users sitting in different bot teams -> ambiguous.
	bot_teams = [[_member(1)], [_member(2)]]
	p2u = {101: 1, 102: 2, 103: 99, 104: 99}
	assert completed.resolve_result(_api(win_team=1), p2u, bot_teams) == (None, None)


def test_resolve_result_single_resolved_loser_below_confidence():
	# Only ONE losing-team member resolved and it is NOT the captain -> not confident.
	bot_teams = [[_member(1), _member(2)], [_member(3), _member(4)]]
	p2u = {101: 1, 102: 2, 104: 4}   # loser 104->user4 (not captain 3), 103 unmapped
	win_idx, captain = completed.resolve_result(_api(win_team=1), p2u, bot_teams)
	assert win_idx == 0
	assert captain is None


def test_should_record_civs_skips_unmapped_real_winner():
	# real winner but couldn't map to a bot team -> skip (let civ_matcher backfill)
	assert completed.should_record_civs(None, 2) is False
	# mapped winner -> record with W/L
	assert completed.should_record_civs(0, 1) is True
	assert completed.should_record_civs(1, 2) is True
	# genuine API draw (no winner) -> record (result NULL is correct for a draw)
	assert completed.should_record_civs(None, None) is True


def test_resolve_result_captain_resolved_is_enough():
	# The losing captain themself is resolved -> confident even with one mapping.
	bot_teams = [[_member(1), _member(2)], [_member(3), _member(4)]]
	p2u = {101: 1, 102: 2, 103: 3}   # loser 103 -> user 3 = captain
	win_idx, captain = completed.resolve_result(_api(win_team=1), p2u, bot_teams)
	assert win_idx == 0
	assert captain.id == 3
