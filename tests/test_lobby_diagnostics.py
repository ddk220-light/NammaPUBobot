# -*- coding: utf-8 -*-
"""Lobby debug traces retain lifecycle evidence without identities or noise."""
import json
from types import SimpleNamespace

from nammaoe2bot.features.lobby import diagnostics, reducer, watcher


def _entry():
	return reducer.fold([
		{"type": "lobbyAdded", "data": {
			"matchId": 77, "name": "NammaNomad", "started": None,
			"finished": None, "totalSlotCount": 2, "blockedSlotCount": 0,
			"password": "do-not-log-this",
		}},
		{"type": "slotAdded", "data": {
			"matchId": 77, "slot": 0, "profileId": 12345,
			"name": "Private Player Name", "team": 1, "civName": "Mongols",
		}},
	])


def test_removed_trace_keeps_the_last_known_launch_evidence():
	entry = _entry()[77]
	out = diagnostics.event_summary(
		"manual:game=77", {"type": "lobbyRemoved", "data": {"matchId": 77}}, entry)

	assert out == {
		"source": "manual:game=77",
		"event": "lobbyRemoved",
		"match_id": 77,
		"last_started": None,
		"last_finished": None,
		"last_status": None,
		"occupied_slots": 1,
		"open_slots": 1,
		"lobby_full": False,
	}


def test_trace_allowlists_fields_instead_of_copying_the_payload():
	event = {"type": "lobbyAdded", "data": {
		"matchId": 77, "name": "NammaNomad", "started": None,
		"totalSlotCount": 8, "password": "secret", "profileId": 12345,
		"token": "also-secret", "mapName": "Private custom map title",
	}}
	out = diagnostics.event_summary("auto:match=9", event)

	assert out["totalSlotCount"] == 8
	encoded = json.dumps(out)
	for private in (
			"NammaNomad", "secret", "12345", "also-secret",
			"Private custom map title"):
		assert private not in encoded


def test_slot_trace_records_shape_not_identity():
	event = {"type": "slotUpdated", "data": {
		"matchId": 77, "slot": 2, "profileId": 12345,
		"name": "Private Player Name", "team": 2, "civName": "Franks",
	}}
	out = diagnostics.event_summary("manual:game=77", event)

	encoded = json.dumps(out)
	assert out["event"] == "slotUpdated"
	for private in ("Private Player Name", "12345", "Franks"):
		assert private not in encoded


def test_slot_churn_is_never_logged_and_lifecycle_is_debug_only(monkeypatch):
	debug = []
	info = []
	monkeypatch.setattr(diagnostics, "log", SimpleNamespace(
		debug=debug.append, info=info.append))
	slot = {"type": "slotUpdated", "data": {
		"matchId": 77, "slot": 2, "profileId": 12345,
		"name": "Private Player Name", "team": 2, "civName": "Franks",
	}}
	for _ in range(1000):
		assert diagnostics.trace_event("manual:game=77", slot) is False
	assert debug == []
	assert info == []

	lifecycle = {"type": "lobbyUpdated", "data": {
		"matchId": 77, "started": "2026-08-08T21:00:00.000Z",
		"name": "Private-looking lobby title",
	}}
	assert diagnostics.trace_event("auto:match=9", lifecycle) is True
	assert len(debug) == 1
	assert info == []
	assert "Private-looking lobby title" not in debug[0]


def test_automatic_watcher_keeps_a_partial_update_with_no_repeated_name(monkeypatch):
	"""Socket updates are deltas. A `started` update that omitted the unchanged
	name used to delete the tracked lobby before either launch handling or the
	new diagnostic could see it."""
	seen = []
	monkeypatch.setattr(diagnostics, "trace_event", lambda source, event, entry: seen.append(
		(source, event["type"], (entry.get("lobby") or {}).get("started"))))
	match = SimpleNamespace(id=9, players=[object(), object()])
	w = watcher.LobbyWatcher(match, channel=None)

	w._ingest([{"type": "lobbyAdded", "data": {
		"matchId": 77, "name": watcher.TARGET_NAME, "totalSlotCount": 2,
		"blockedSlotCount": 0, "started": None,
	}}])
	w._ingest([{"type": "lobbyUpdated", "data": {
		"matchId": 77, "started": "2026-08-08T21:00:00.000Z",
	}}])

	assert w.state[77]["lobby"]["name"] == watcher.TARGET_NAME
	assert w.state[77]["lobby"]["started"] == "2026-08-08T21:00:00.000Z"
	assert seen[-1] == ("auto:match=9", "lobbyUpdated", "2026-08-08T21:00:00.000Z")


def test_automatic_watcher_treats_removal_as_a_candidate_not_a_launch(monkeypatch):
	monkeypatch.setattr(diagnostics, "trace_event", lambda *_args: None)
	match = SimpleNamespace(id=9, players=[object(), object()])
	w = watcher.LobbyWatcher(match, channel=None)
	w.linked = True
	w.game_id = 77
	w._ingest([{"type": "lobbyAdded", "data": {
		"matchId": 77, "name": watcher.TARGET_NAME, "totalSlotCount": 2,
		"blockedSlotCount": 0,
	}}])
	w._ingest([{"type": "lobbyRemoved", "data": {"matchId": 77}}])

	assert w.launched is False
	assert w._removed[0][0] == 77
	assert w._removed[0][1]["lobby"]["name"] == watcher.TARGET_NAME
