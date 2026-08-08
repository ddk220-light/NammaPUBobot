# -*- coding: utf-8 -*-
"""Has the game for a bot match actually begun?

The lobby feature is the only thing in the bot that knows. The watcher writes
an `in_progress` row the moment the tracked lobby leaves the socket's lobby
list (`lobbyRemoved`), which is the host pressing Start — there is no earlier
or more honest signal available to us.

Betting needs the answer. A book that stays open into the first minutes of play
is a book people can stake with the civs, the starting positions and an early
kill already on screen, which is not a prediction. But betting must not learn
that a `lobbies` table exists: it belongs to this feature, its status vocabulary
is this feature's business, and a second module writing SQL against it is how a
column rename becomes a silent behaviour change somewhere else. So the query
lives here, next to the table, and nammaoe2bot/wiring.py hands it to betting.

WHICH STATUSES COUNT, AND WHY `expired` DOES NOT. A row walks
``created`` -> ``filling`` -> ``in_progress`` -> ``awaiting_confirm`` ->
``completed``. Everything from ``in_progress`` onward means the game started;
``completed`` is included because a game that has already finished has
certainly started, and on a short game the completion poller can win the race
against a betting sweep. ``expired`` is the one that looks like it belongs and
must not be here: LobbyJobs._reap_stale writes it over ``created``/``filling``
rows precisely when a lobby was announced and the game NEVER launched. Counting
it would close books because somebody abandoned a lobby.
"""
from nammaoe2bot.runtime.database import db

LAUNCHED_STATUSES = ("in_progress", "awaiting_confirm", "completed")


async def launched_among(match_ids):
	"""The subset of these bot match ids whose game is under way (or already over).

	A set, so the caller tests membership without caring about order or repeats:
	one match can own more than one `lobbies` row — a lobby that filled, was
	abandoned and remade — and any one of them having launched is enough.

	Asks about a named handful rather than scanning the table, because the
	caller's handful is "books still taking money", which is one or two rows.
	"""
	ids = [int(m) for m in match_ids if m is not None]
	if not ids:
		return set()
	id_slots = ", ".join(["%s"] * len(ids))
	status_slots = ", ".join(["%s"] * len(LAUNCHED_STATUSES))
	rows = await db.fetchall(
		f"SELECT DISTINCT match_id FROM lobbies "
		f"WHERE match_id IN ({id_slots}) AND status IN ({status_slots})",
		ids + list(LAUNCHED_STATUSES))
	return {r["match_id"] for r in rows or []}
