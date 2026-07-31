# -*- coding: utf-8 -*-
"""Read-only lookup of the Discord-user <-> AoE2-profile map for the lobby flow,
backed by the identity resolver (bot/identity.py) rather than the qc_profile_map
table this module used to wrap directly — qc_profile_map was never populated in
production (0 rows) and is dropped in a later stage.

Nothing here WRITES identity any more. This module used to learn bindings from
roster-confirmed lobbies by elimination (``eliminate`` + ``link``); both are
deleted in identity v2 (spec section 4). Elimination pinned a pair on counts
alone with no roster guard, so one outsider in the lobby plus one absent match
player left two leftovers that were not each other — written as a global
binding. bot/identity_solver.py deduces the same thing from the paired match's
two rosters, scored across every paired game, with a roster-size guard and a
margin threshold.

What survives is the read: ``known_for`` powers the optional winner-name hint
and per-player civ attribution in bot/lobby/completed.py, both best-effort. The
results/ratings loop never reads it.
"""
from bot import identity
from core.console import log


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
