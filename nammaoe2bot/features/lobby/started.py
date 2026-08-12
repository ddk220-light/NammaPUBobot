# -*- coding: utf-8 -*-
"""The one durable launch question betting asks the lobby feature.

``lobbies.status`` describes workflow, not truth: production traces proved a
full lobby emits the same removal sequence when it is launched and when it is
cancelled/remade. ``launched_at`` is different. It is written only after the
match API exposes a parseable ``started`` timestamp, and survives process
restarts, later result states, and duplicate verifier calls.
"""
from nammaoe2bot.runtime.database import db


async def launched_among(match_ids):
	"""The subset of these bot match ids with an API-confirmed game start."""
	ids = [int(m) for m in match_ids if m is not None]
	if not ids:
		return set()
	id_slots = ", ".join(["%s"] * len(ids))
	rows = await db.fetchall(
		f"SELECT DISTINCT match_id FROM lobbies "
		f"WHERE match_id IN ({id_slots}) AND launched_at IS NOT NULL",
		ids,
	)
	return {r["match_id"] for r in rows or []}
