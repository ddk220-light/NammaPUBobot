# -*- coding: utf-8 -*-
"""Pure unit tests for the lobby socket delta reducer (bot/lobby/reducer.py).

The fixture events mirror the real shapes captured live in Phase 0 (see
utils/lobby_spike.py and the PHASE 0 results in
docs/aoe2-lobby-replication-plan.md): each is ``{"type", "data"}`` with
``slot.matchId`` linking back to ``lobby.matchId``. Small and curated — the 1MB
ambient scratch capture is gitignored; this is the committed golden lifecycle.
"""
from bot.lobby import reducer

MID = 485355768


def _lobby_added(name="test123", total=2, blocked=0):
	return {"type": "lobbyAdded", "data": {
		"matchId": MID, "name": name, "server": "koreacentral",
		"mapName": "Arabia", "mapImageUrl": "http://x/arabia.png",
		"started": None, "finished": None,
		"totalSlotCount": total, "blockedSlotCount": blocked, "averageRating": 1100,
	}}


def _slot_added(slot, pid, name, team):
	return {"type": "slotAdded", "data": {
		"matchId": MID, "slot": slot, "profileId": pid, "name": name,
		"team": team, "color": slot + 1, "status": "player", "civName": None,
	}}


def test_lobby_added_creates_entry():
	st = reducer.fold([_lobby_added()])
	assert MID in st
	assert st[MID]["lobby"]["name"] == "test123"
	assert reducer.occupied_slots(st[MID]) == []


def test_slots_fill_and_full():
	st = reducer.fold([
		_lobby_added(total=2),
		_slot_added(0, 111, "Alice", 1),
		_slot_added(1, 222, "Bob", 2),
	])
	entry = st[MID]
	assert reducer.profile_ids(entry) == {111, 222}
	assert reducer.is_full(entry)
	assert reducer.capacity(entry) == (2, 0)


def test_not_full_with_open_slot():
	st = reducer.fold([_lobby_added(total=2), _slot_added(0, 111, "Alice", 1)])
	entry = st[MID]
	assert not reducer.is_full(entry)
	assert reducer.capacity(entry) == (1, 1)


def test_blocked_slots_excluded_from_capacity():
	# 4 seats, 2 blocked (closed/AI) -> 2 playable. Two players => full.
	st = reducer.fold([
		_lobby_added(total=4, blocked=2),
		_slot_added(0, 111, "Alice", 1),
		_slot_added(1, 222, "Bob", 2),
	])
	assert reducer.is_full(st[MID])


def test_empty_lobby_not_full():
	st = reducer.fold([_lobby_added(total=2)])
	assert not reducer.is_full(st[MID])  # no players yet, even though "0 open" math must not trigger


def test_slot_updated_merges_not_replaces():
	st = reducer.fold([
		_lobby_added(),
		_slot_added(0, 111, "Alice", 1),
		{"type": "slotUpdated", "data": {"matchId": MID, "slot": 0, "civName": "Mongols"}},
	])
	s0 = st[MID]["slots"][0]
	assert s0["civName"] == "Mongols"
	assert s0["profileId"] == 111  # preserved through the partial update


def test_slot_removed_frees_seat():
	st = reducer.fold([
		_lobby_added(),
		_slot_added(0, 111, "Alice", 1),
		{"type": "slotRemoved", "data": {"matchId": MID, "slot": 0}},
	])
	assert reducer.profile_ids(st[MID]) == set()


def test_lobby_removed_drops_lobby():
	st = reducer.fold([_lobby_added(), _slot_added(0, 111, "Alice", 1)])
	mid = reducer.apply_event(st, {"type": "lobbyRemoved", "data": {"matchId": MID}})
	assert mid == MID
	assert MID not in st


def test_roster_sorted_by_slot():
	st = reducer.fold([
		_lobby_added(total=2),
		_slot_added(1, 222, "Bob", 2),
		_slot_added(0, 111, "Alice", 1),
	])
	assert [row[0] for row in reducer.roster(st[MID])] == [111, 222]  # slot 0 then 1


def test_find_by_name_case_insensitive():
	st = reducer.fold([_lobby_added(name="Test123 ")])
	assert reducer.find_by_name(st, "test123") == [MID]
	assert reducer.find_by_name(st, "other") == []


def test_slot_before_lobby_meta_still_tracked():
	# A slotAdded can arrive before its lobbyAdded; the entry is created lazily
	# and capacity is unknown (open=0) until the lobby meta lands.
	st = reducer.fold([_slot_added(0, 111, "Alice", 1)])
	assert reducer.profile_ids(st[MID]) == {111}
	assert reducer.capacity(st[MID]) == (1, 0)


def test_unknown_event_type_ignored():
	st = reducer.fold([_lobby_added(), {"type": "somethingNew", "data": {"matchId": MID}}])
	assert MID in st  # didn't crash, didn't corrupt existing state


def test_malformed_events_never_raise():
	st = reducer.new_state()
	for bad in [None, {}, {"type": "slotAdded"}, {"type": "slotAdded", "data": None},
				{"data": {"matchId": 1}}, {"type": "lobbyAdded", "data": {}}]:
		assert reducer.apply_event(st, bad) is None
	assert st == {}
