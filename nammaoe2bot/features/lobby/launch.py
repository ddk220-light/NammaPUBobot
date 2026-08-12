# -*- coding: utf-8 -*-
"""Authoritative lobby-launch confirmation.

The lobby socket removes a lobby for two different reasons: the host launched
the game, or the lobby was closed/remade. Weekend production traces proved the
terminal socket frames are byte-for-byte indistinguishable. The match endpoint
is the discriminator: a real game has a parseable ``started`` timestamp while
cancelled lobby ids remain absent.

This module owns the durable fact. Callers may poll or retry freely;
``mark_confirmed`` is a compare-and-set, so concurrent watcher/job checks and a
redeploy cannot create two meanings for one row. Betting only reads
``launched_at`` through :mod:`started` and never infers from lobby status.
"""
import asyncio
import time

from nammaoe2bot.runtime.console import log
from nammaoe2bot.runtime.database import db

from . import api

VERIFY_INTERVAL = 2
VERIFY_TIMEOUT = 90


def started_at(match_api):
	"""The authoritative launch epoch from one match response, or ``None``."""
	if not isinstance(match_api, dict):
		return None
	return api.parse_iso(match_api.get("started"))


async def fetch_started_at(game_id):
	"""Fetch one game and return its launch epoch once the API knows it."""
	return started_at(await api.fetch_match_by_id(game_id))


async def mark_confirmed(row_id, game_id, launched_at, observed_at=None):
	"""Persist a verified launch exactly once.

	True means the row now carries a launch fact, whether this call wrote it or
	an overlapping verifier won first. Terminal rows are never revived.
	"""
	launched_at = int(launched_at)
	observed_at = int(observed_at if observed_at is not None else time.time())
	async with db.transaction() as tx:
		changed = await tx.execute(
			"UPDATE lobbies SET launched_at=%s, "
			"status=CASE WHEN status IN ('created','filling','verifying') "
			"THEN 'in_progress' ELSE status END, last_edit_at=0 "
			"WHERE id=%s AND aoe2_game_id=%s AND launched_at IS NULL "
			"AND status NOT IN ('completed','expired')",
			[launched_at, row_id, game_id],
		)
		if changed:
			log.info(
				f"Lobby launch confirmed: game {game_id} started at {launched_at} "
				f"(observed {max(0, observed_at - launched_at)}s later).")
			return True
		row = await tx.fetchone(
			"SELECT launched_at FROM lobbies WHERE id=%s AND aoe2_game_id=%s FOR UPDATE",
			[row_id, game_id],
		)
		return bool(row and row.get("launched_at") is not None)


async def verify_row(row, observed_at=None):
	"""Confirm one selected lobbies row if the API exposes ``started``."""
	row_id = row.get("id")
	game_id = row.get("aoe2_game_id")
	if row_id is None or game_id is None:
		return False
	when = await fetch_started_at(game_id)
	if when is None:
		return False
	return await mark_confirmed(row_id, game_id, when, observed_at=observed_at)


async def wait_for_start(row_id, game_id, timeout=VERIFY_TIMEOUT):
	"""Short-lived UI helper; the recurring job remains the durable fallback.

	A watcher uses this to update its card promptly. If it times out or is
	cancelled, the lobbies row remains ``filling``/``verifying`` and LobbyJobs
	continues the same API confirmation after a redeploy.
	"""
	deadline = time.monotonic() + timeout
	row = {"id": row_id, "aoe2_game_id": game_id}
	while True:
		if await verify_row(row):
			return True
		remaining = deadline - time.monotonic()
		if remaining <= 0:
			return False
		await asyncio.sleep(min(VERIFY_INTERVAL, remaining))
