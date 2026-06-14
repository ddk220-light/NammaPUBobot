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
