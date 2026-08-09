# -*- coding: utf-8 -*-
"""Structured, privacy-conscious traces for the unofficial lobby socket.

The launch cutoff is temporarily observation-only while real matches establish
what the socket emits when a host starts a game versus when a lobby is simply
closed.  Do not log whole event payloads here: lobby metadata can contain a
password and slot events carry player names/profile ids.  The allowlisted
summary below preserves the lifecycle evidence without copying either into the
Railway log.
"""
import json

from nammaoe2bot.runtime.console import log

from . import reducer


_LOBBY_FIELDS = (
	"started", "finished", "status", "totalSlotCount", "blockedSlotCount",
	"gameModeName", "leaderboardName", "mapName", "server",
)


def event_summary(source, event, entry=None):
	"""Return the safe subset of one socket event plus last-known lobby state.

	``entry`` is especially important for ``lobbyRemoved``: the reducer deletes
	the lobby on that event, so the pre-removal snapshot is the only local
	evidence of whether it was full and whether ``started`` had changed first.
	"""
	data = event.get("data") if isinstance(event, dict) else None
	data = data if isinstance(data, dict) else {}
	out = {
		"source": str(source),
		"event": event.get("type") if isinstance(event, dict) else None,
		"match_id": data.get("matchId"),
		# Key names reveal a new protocol field without exposing its value. This
		# is how tomorrow's review can spot a possible launch/cancel discriminator
		# without dumping passwords, tokens or identities tonight.
		"payload_keys": sorted(str(key) for key in data),
	}
	for key in _LOBBY_FIELDS:
		if key in data:
			out[key] = data.get(key)
	if "name" in data:
		# Useful for proving the automatic name filter selected the intended
		# lobby. json.dumps below escapes control characters in hostile names.
		out["lobby_name"] = data.get("name")
	if "slot" in data:
		out["slot"] = data.get("slot")
		out["slot_occupied"] = bool(data.get("profileId"))
		out["team"] = data.get("team")
		out["civ_selected"] = bool(data.get("civName"))

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
	"""Write one greppable JSON line to the ordinary Railway log."""
	summary = event_summary(source, event, entry)
	log.info("LOBBY_SOCKET_TRACE " + json.dumps(summary, sort_keys=True, default=str))
