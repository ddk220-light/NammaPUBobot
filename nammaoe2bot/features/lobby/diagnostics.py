# -*- coding: utf-8 -*-
"""Low-volume, privacy-conscious debug traces for the unofficial lobby socket.

The production launch decision is now the match API's durable ``started``
timestamp, logged once by ``launch.mark_confirmed``. Socket traces therefore
stay at DEBUG and record only lobby lifecycle shape. Slot events are both noisy
and identity-bearing, so they are never emitted.
"""
import json

from nammaoe2bot.runtime.console import log

from . import reducer


# Only fields needed to distinguish lifecycle transitions are safe to retain.
# Lobby/map labels can be user-created, so even DEBUG traces omit them.
_LOBBY_FIELDS = (
	"started", "finished", "status", "totalSlotCount", "blockedSlotCount",
)
_LIFECYCLE_EVENTS = frozenset(("lobbyAdded", "lobbyUpdated", "lobbyRemoved"))
_LIFECYCLE_UPDATE_FIELDS = frozenset(("started", "finished", "status"))


def event_summary(source, event, entry=None):
	"""Return the safe subset of one socket event plus last-known lobby state.

	``entry`` is especially important for ``lobbyRemoved``: the reducer deletes
	the lobby on that event, so the pre-removal snapshot is the only local
	evidence of whether it was full and whether ``started`` had changed first.
	"""
	data = event.get("data") if isinstance(event, dict) else None
	data = data if isinstance(data, dict) else {}
	event_type = event.get("type") if isinstance(event, dict) else None
	out = {
		"source": str(source),
		"event": event_type,
		"match_id": data.get("matchId"),
	}
	if event_type in _LIFECYCLE_EVENTS:
		for key in _LOBBY_FIELDS:
			if key in data:
				out[key] = data.get(key)

	if entry:
		lobby = entry.get("lobby") or {}
		filled, open_count = reducer.capacity(entry)
		out.update({
			"last_started": lobby.get("started"),
			"last_finished": lobby.get("finished"),
			"last_status": lobby.get("status"),
			"occupied_slots": filled,
			"open_slots": open_count,
			"lobby_full": reducer.is_full(entry),
		})
	return out


def trace_event(source, event, entry=None):
	"""Emit one compact DEBUG lifecycle line; return whether one was emitted."""
	data = event.get("data") if isinstance(event, dict) else None
	data = data if isinstance(data, dict) else {}
	event_type = event.get("type") if isinstance(event, dict) else None
	if event_type not in _LIFECYCLE_EVENTS:
		return False
	if event_type == "lobbyUpdated" and not (_LIFECYCLE_UPDATE_FIELDS & data.keys()):
		return False
	summary = event_summary(source, event, entry)
	log.debug("LOBBY_SOCKET_TRACE " + json.dumps(summary, sort_keys=True, default=str))
	return True
