# -*- coding: utf-8 -*-
"""Pure unit tests for bot/lobby/view.py (candidate pick + link decision + fill
lines). Builds state through the reducer using Phase-0-shaped events."""
from bot.lobby import reducer, view

MID = 999


def _lobby(mid=MID, name="test123", total=2, blocked=0):
	return {"type": "lobbyAdded", "data": {
		"matchId": mid, "name": name, "mapName": "Arabia", "server": "koreacentral",
		"totalSlotCount": total, "blockedSlotCount": blocked,
	}}


def _slot(idx, pid, name, mid=MID, team=1, civ=None):
	return {"type": "slotAdded", "data": {
		"matchId": mid, "slot": idx, "profileId": pid, "name": name, "team": team, "civName": civ,
	}}


def test_link_ready_true_when_full_and_size_matches():
	st = reducer.fold([_lobby(total=2), _slot(0, 1, "A"), _slot(1, 2, "B")])
	assert view.link_ready(st[MID], 2)


def test_link_ready_false_when_not_full():
	st = reducer.fold([_lobby(total=2), _slot(0, 1, "A")])
	assert not view.link_ready(st[MID], 2)


def test_link_ready_false_when_size_mismatch():
	# A full 1v1 lobby is not our 2v2 match even though it is "full".
	st = reducer.fold([_lobby(total=2), _slot(0, 1, "A"), _slot(1, 2, "B")])
	assert not view.link_ready(st[MID], 4)


def test_pick_candidate_prefers_exact_size():
	st = reducer.new_state()
	a, b = 100, 200
	reducer.fold([
		_lobby(mid=a, total=4), _slot(0, 1, "a", mid=a),                       # 1/4 — under size
		_lobby(mid=b, total=2), _slot(0, 2, "b", mid=b), _slot(1, 3, "c", mid=b),  # 2/2 — exact
	], st)
	mid, _entry = view.pick_candidate(st, 2)
	assert mid == b


def test_pick_candidate_none_when_empty():
	assert view.pick_candidate({}, 2) is None


def test_fill_lines_lists_players_civs_and_open_slots():
	st = reducer.fold([_lobby(total=4), _slot(0, 1, "Alice", civ="Mongols"), _slot(1, 2, "Bob")])
	lines, filled, playable = view.fill_lines(st[MID])
	assert (filled, playable) == (2, 4)
	assert "`Alice` — Mongols" in lines
	assert "`Bob`" in lines
	assert any("open slot" in line for line in lines)


def test_fill_lines_no_open_marker_when_full():
	st = reducer.fold([_lobby(total=2), _slot(0, 1, "A"), _slot(1, 2, "B")])
	lines, filled, playable = view.fill_lines(st[MID])
	assert filled == playable == 2
	assert not any("open slot" in line for line in lines)


def test_deep_link_join_and_spectate():
	assert view.deep_link(16072058) == "aoe2de://0/16072058"
	assert view.deep_link(16072058, mode="spectate") == "aoe2de://1/16072058"
	assert view.deep_link(None) is None


def test_join_and_spectate_urls():
	assert view.join_url("https://x.up.railway.app", 123) == "https://x.up.railway.app/join/123"
	assert view.spectate_url("https://x.up.railway.app/", 123) == "https://x.up.railway.app/spectate/123"


def test_join_url_none_without_base_or_id():
	assert view.join_url("", 123) is None
	assert view.join_url("https://x", None) is None
	assert view.spectate_url(None, 123) is None
