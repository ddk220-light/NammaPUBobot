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


def test_lobby_card_lines_matches_reference_shape():
	st = reducer.fold([_lobby(total=4), _slot(0, 1, "ddk220"), _slot(1, 2, "Shadeslayer")])
	body = "\n".join(view.lobby_card_lines(st[MID], MID))
	assert f"`aoe2de://0/{MID}`" in body          # copyable deep link
	assert "Map: Arabia" in body and "Server: koreacentral" in body
	assert "ddk220" in body and "Shadeslayer" in body
	assert "+2 slot(s) remaining" in body
	assert body.count("Open") == 2                # two empty seats rendered as Open


def test_lobby_card_lines_full_lobby_says_full():
	st = reducer.fold([_lobby(total=2), _slot(0, 1, "A"), _slot(1, 2, "B")])
	body = "\n".join(view.lobby_card_lines(st[MID], MID))
	assert "full" in body and "Open" not in body


def _rich_lobby(mid=MID):
	return {"type": "lobbyAdded", "data": {
		"matchId": mid, "name": "letsgo", "mapName": "Arabia", "server": "ukwest",
		"totalSlotCount": 4, "blockedSlotCount": 0, "gameModeName": "Random Map",
		"speedName": "Fast", "leaderboardName": "1v1 RM", "averageRating": 1187,
		"password": "secret", "recordGame": True,
	}}


def _civ_slot(idx, pid, name, color, civ, mid=MID):
	return {"type": "slotAdded", "data": {
		"matchId": mid, "slot": idx, "profileId": pid, "name": name, "color": color, "civName": civ,
	}}


def test_settings_lines_render_all_known_fields():
	st = reducer.fold([_rich_lobby()])
	out = "\n".join(view.settings_lines(st[MID]["lobby"]))
	assert "Random Map · Fast" in out
	assert "Map: Arabia" in out and "Server: ukwest" in out
	assert "1v1 RM" in out and "avg 1187 Elo" in out
	assert "🔒 password" in out and "secret" not in out   # presence only, never the value
	assert "⏺ recorded" in out


def test_roster_lines_have_colour_dot_and_civ():
	st = reducer.fold([_rich_lobby(), _civ_slot(0, 1, "ddk220", 1, "Mongols"),
					   _civ_slot(1, 2, "Shadeslayer", 2, "Franks")])
	out = view.roster_lines(st[MID])
	assert out[0] == "🔵 ddk220 — Mongols"
	assert out[1] == "🔴 Shadeslayer — Franks"


def test_full_card_includes_settings_and_rich_roster():
	st = reducer.fold([_rich_lobby(), _civ_slot(0, 1, "ddk220", 1, "Mongols")])
	body = "\n".join(view.lobby_card_lines(st[MID], MID))
	assert f"`aoe2de://0/{MID}`" in body
	assert "Random Map · Fast" in body and "avg 1187 Elo" in body
	assert "🔵 ddk220 — Mongols" in body
	assert "+3 slot(s) remaining" in body and body.count("Open") == 3
