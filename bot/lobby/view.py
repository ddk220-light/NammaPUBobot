# -*- coding: utf-8 -*-
"""Pure presentation + decision helpers for the lobby watcher.

No Discord, no DB, no network — just functions over reducer state, so they are
unit-testable in isolation (tests/test_lobby_view.py). watcher.py turns these
strings/choices into actual Discord embeds and DB writes.
"""
from . import reducer


def pick_candidate(state, match_size):
	"""Choose the most relevant lobby to track from the (already name-filtered)
	state: prefer one whose filled count == ``match_size``, then the fullest.
	Returns ``(matchId, entry)`` or None. Tie-broken by matchId for stability."""
	if not state:
		return None

	def score(item):
		mid, entry = item
		filled, _open = reducer.capacity(entry)
		exact = 1 if filled == match_size else 0
		return (exact, filled, mid)

	mid, entry = max(state.items(), key=score)
	return mid, entry


def link_ready(entry, match_size):
	"""True when this lobby is full and its player count equals the match size —
	enough to confirm the link in single-active-match mode (name + full + count)."""
	return reducer.is_full(entry) and len(reducer.occupied_slots(entry)) == match_size


def fill_lines(entry):
	"""Return ``(lines, filled, playable)`` for the live-fill embed body.

	``lines`` is one ``name [— civ]`` entry per occupied seat (slot order) plus a
	trailing ``+N open slot(s)`` when seats remain. ``playable`` excludes blocked
	(closed / AI / spectator) seats.
	"""
	lob = entry.get("lobby") or {}
	total = lob.get("totalSlotCount") or 0
	blocked = lob.get("blockedSlotCount") or 0
	playable = max(0, total - blocked)

	occupied = sorted(
		reducer.occupied_slots(entry),
		key=lambda s: (s.get("slot") if s.get("slot") is not None else 0),
	)
	lines = []
	for s in occupied:
		name = s.get("name") or "?"
		civ = s.get("civName")
		lines.append(f"`{name}`" + (f" — {civ}" if civ else ""))

	filled = len(occupied)
	open_count = max(0, playable - filled)
	if open_count:
		lines.append(f"*+{open_count} open slot(s)*")
	return lines, filled, playable


# ── join / spectate deep links ───────────────────────────────────────────
# AoE2:DE registers an `aoe2de://` protocol handler. `0/<id>` joins the lobby,
# `1/<id>` spectates. Discord link buttons only allow http(s)/discord schemes, so
# the button points at an https redirect on our own web server (bot/web.py
# /join|/spectate/<id>) which bounces the browser to the aoe2de:// link.

def deep_link(game_id, mode="join"):
	"""The raw `aoe2de://` deep link for an AoE2 game id (None if no id). Used as the
	redirect target and as a copy-paste fallback when no web base URL is configured."""
	if game_id is None:
		return None
	return f"aoe2de://1/{game_id}" if mode == "spectate" else f"aoe2de://0/{game_id}"


# AoE2 player-colour slot → coloured dot (1-indexed, the in-game order).
_COLOR_DOT = {1: "🔵", 2: "🔴", 3: "🟢", 4: "🟡", 5: "🟦", 6: "🟣", 7: "⚪", 8: "🟠"}


def settings_lines(lob):
	"""Lobby-settings lines (game mode · speed, map, server, ranked/avg-Elo, flags).
	Every field is optional — the socket may omit any of them. Pure."""
	out = []
	mode_speed = " · ".join(x for x in (lob.get("gameModeName"), lob.get("speedName")) if x)
	if mode_speed:
		out.append(mode_speed)
	if lob.get("mapName"):
		out.append(f"Map: {lob['mapName']}")
	if lob.get("server"):
		out.append(f"Server: {lob['server']}")
	ranked = []
	if lob.get("leaderboardName"):
		ranked.append(lob["leaderboardName"])
	if lob.get("averageRating"):
		ranked.append(f"avg {lob['averageRating']} Elo")
	if ranked:
		out.append(" · ".join(ranked))
	flags = []
	if lob.get("password"):
		flags.append("🔒 password")          # presence only — never print the value
	if lob.get("recordGame"):
		flags.append("⏺ recorded")
	if flags:
		out.append(" · ".join(flags))
	return out


def roster_lines(entry):
	"""One line per occupied seat: `<colour-dot> <name> — <civ>` (civ omitted if
	unknown), in seat order. Pure."""
	out = []
	for s in sorted(reducer.occupied_slots(entry),
					key=lambda s: (s.get("slot") if s.get("slot") is not None else 0)):
		dot = _COLOR_DOT.get(s.get("color"), "▫️")
		name = s.get("name") or "?"
		civ = s.get("civName")
		out.append(f"{dot} {name}" + (f" — {civ}" if civ else ""))
	return out


def lobby_card_lines(entry, game_id):
	"""Full reference-style lobby card body (the AOE2LobbyBOT look): the aoe2de:// join
	link as a copyable code block, the lobby settings (mode/speed/map/server/ranked/
	flags), then the player roster with coloured dots + civ and `Open` placeholders for
	empty seats under a `+N slots remaining` header. Pure — no Discord."""
	lob = entry.get("lobby") or {}
	total = lob.get("totalSlotCount") or 0
	blocked = lob.get("blockedSlotCount") or 0
	playable = max(0, total - blocked)
	roster = roster_lines(entry)
	open_count = max(0, playable - len(roster))

	lines = []
	link = deep_link(game_id)
	if link:
		lines.append(f"`{link}`")            # copyable, like the reference card
	settings = settings_lines(lob)
	if settings:
		lines += ["", *settings]
	remaining = f"+{open_count} slot(s) remaining" if open_count else "full"
	lines += ["", f"**Players** · {remaining}", *roster, *(["Open"] * open_count)]
	return lines


def join_url(base_url, game_id):
	"""https URL of the join redirect, or None when the base URL / id is missing."""
	if not base_url or game_id is None:
		return None
	return f"{base_url.rstrip('/')}/join/{game_id}"


def spectate_url(base_url, game_id):
	"""https URL of the spectate redirect, or None when the base URL / id is missing."""
	if not base_url or game_id is None:
		return None
	return f"{base_url.rstrip('/')}/spectate/{game_id}"
