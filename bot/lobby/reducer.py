# -*- coding: utf-8 -*-
"""Pure delta reducer for the aoe2companion lobby socket.

The socket (wss://socket.aoe2companion.com/listen?handler=lobbies) pushes JSON
frames; each frame is an ARRAY of {"type", "data"} events. The event types and
field shapes were captured live in Phase 0 — see utils/lobby_spike.py and the
"PHASE 0 — SPIKE RESULTS" section of docs/aoe2-lobby-replication-plan.md. This
module reassembles current lobby state from those deltas.

It is intentionally PURE: no aiohttp, no DB, no Discord, no clock. That keeps it
unit-testable in isolation (tests/test_lobby_reducer.py); the live socket client
(Phase 2) is the only thing that feeds events into it.

State shape::

    {matchId: {"lobby": {...lobby fields...},
               "slots": {slotIndex: {...slot fields...}}}}

A lobby is keyed by its matchId (== the ``aoe2de://0/<id>`` join id). Each slot
links to its lobby via ``slot["matchId"]`` and is keyed by ``slot["slot"]`` (the
seat index).
"""
from __future__ import annotations

# The six event types the socket emits (Phase 0). 'pong' keepalives are dropped
# by the socket client before they ever reach the reducer.
LOBBY_ADDED = "lobbyAdded"
LOBBY_UPDATED = "lobbyUpdated"
LOBBY_REMOVED = "lobbyRemoved"
SLOT_ADDED = "slotAdded"
SLOT_UPDATED = "slotUpdated"
SLOT_REMOVED = "slotRemoved"


def new_state():
	"""A fresh, empty reducer state."""
	return {}


def apply_event(state, event):
	"""Apply one ``{"type", "data"}`` event to ``state`` in place.

	Returns the affected matchId, or None if the event was unusable. Unknown
	event types and malformed payloads are ignored (never raised) so a new
	server-side event type or a junk frame can never crash the live watcher.
	"""
	if not isinstance(event, dict):
		return None
	etype = event.get("type")
	data = event.get("data")
	if not isinstance(data, dict):
		return None
	mid = data.get("matchId")
	if mid is None:
		return None

	if etype == LOBBY_ADDED:
		state[mid] = {"lobby": dict(data), "slots": {}}
	elif etype == LOBBY_UPDATED:
		entry = state.setdefault(mid, {"lobby": {}, "slots": {}})
		entry["lobby"].update(data)
	elif etype == LOBBY_REMOVED:
		# Host launched the game OR the lobby was cancelled/closed. Either way it
		# is no longer live; a watcher will already have captured its roster
		# while the lobby was full.
		state.pop(mid, None)
	elif etype in (SLOT_ADDED, SLOT_UPDATED):
		entry = state.setdefault(mid, {"lobby": {}, "slots": {}})
		slot_idx = data.get("slot")
		if etype == SLOT_UPDATED and slot_idx in entry["slots"]:
			entry["slots"][slot_idx].update(data)
		else:
			entry["slots"][slot_idx] = dict(data)
	elif etype == SLOT_REMOVED:
		entry = state.get(mid)
		if entry is not None:
			entry["slots"].pop(data.get("slot"), None)
	else:
		return None
	return mid


def fold(events, state=None):
	"""Apply a list of events (one socket frame, or many concatenated) onto
	``state`` (created fresh if not given). Returns the state."""
	if state is None:
		state = new_state()
	for event in events:
		apply_event(state, event)
	return state


# ── views over a single lobby entry ──────────────────────────────────────

def occupied_slots(entry):
	"""Slots actually held by a human player (have a profileId)."""
	return [s for s in entry["slots"].values() if s.get("profileId")]


def roster(entry):
	"""Sorted ``[(profileId, name, team, slot), ...]`` of occupied slots."""
	rows = [
		(s.get("profileId"), s.get("name"), s.get("team"), s.get("slot"))
		for s in occupied_slots(entry)
	]
	return sorted(rows, key=lambda r: (r[3] if r[3] is not None else 0))


def profile_ids(entry):
	"""Set of profileIds currently occupying the lobby."""
	return {s["profileId"] for s in occupied_slots(entry) if s.get("profileId")}


def capacity(entry):
	"""``(filled, open_count)`` for the lobby. ``open`` = playable seats not yet
	filled, where playable = totalSlotCount − blockedSlotCount (blocked = closed
	/ AI / spectator seats). When the lobby meta hasn't arrived yet, open is 0."""
	lob = entry.get("lobby") or {}
	total = lob.get("totalSlotCount")
	blocked = lob.get("blockedSlotCount") or 0
	filled = len(occupied_slots(entry))
	if total is None:
		return filled, 0
	return filled, max(0, (total - blocked) - filled)


def is_full(entry):
	"""True when every playable seat is occupied (and at least one player is in)."""
	filled, open_count = capacity(entry)
	return open_count == 0 and filled > 0


def find_by_name(state, name):
	"""matchIds of lobbies whose name == ``name`` (case-insensitive, trimmed).

	Drives the auto-search adapter: pick the configured lobby out of the
	unfiltered feed without knowing its id in advance.
	"""
	target = (name or "").strip().lower()
	out = []
	for mid, entry in state.items():
		lname = (entry.get("lobby") or {}).get("name") or ""
		if lname.strip().lower() == target:
			out.append(mid)
	return out
