# -*- coding: utf-8 -*-
"""Read-only lookup of the Discord-user <-> AoE2-profile map for the lobby flow,
backed by the identity resolver (bot/identity.py) rather than the dedicated
lobby-side map table this module used to wrap directly — that table was never
populated in production (0 rows), is no longer declared anywhere, and is
dropped by a later migration.

Nothing here WRITES identity any more. This module used to learn bindings from
roster-confirmed lobbies by elimination (``eliminate`` + ``link``); both are
deleted in identity v2 (spec section 4) and replaced by bot/identity_solver.py.
Why elimination was unsafe is stated once, in that module's docstring ("WHAT
THIS MODULE REPLACED"); do not restate it here.

What survives is the read: ``known_for`` powers the optional winner-name hint
and per-player civ attribution in bot/lobby/completed.py, both best-effort. The
results/ratings loop never reads it.
"""
from bot import identity
from nammaoe2bot.runtime.console import log


async def known_for(profile_ids):
	"""``{profileId: user_id}`` for any of the given profileIds already mapped."""
	out = {}
	for pid in profile_ids:
		try:
			uid = await identity.user_for_profile(pid)
		except Exception as e:
			log.error(f"profile_map lookup failed for {pid}: {e}")
			continue
		if uid is not None:
			out[pid] = uid
	return out
