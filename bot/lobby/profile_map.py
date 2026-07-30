# -*- coding: utf-8 -*-
"""Self-healing Discord-user <-> AoE2-profile map, backed by the identity resolver
(bot/identity.py) rather than the qc_profile_map table this module used to wrap
directly — qc_profile_map was never populated in production (0 rows) and is
dropped in a later stage.

Learned from roster-confirmed lobbies: each captured slot's (profileId, name) is
authoritative for that match's players. The results/ratings loop NEVER reads this
— it only powers the optional winner-name hint and per-player civ attribution,
both best-effort. ``eliminate`` is a pure function (unit-tested); the read/write
helpers wrap bot.identity and swallow their own errors.
"""
from bot import identity
from core.console import log


def eliminate(match_user_ids, slot_profile_ids, known_pid_to_uid):
	"""New ``(user_id, profile_id)`` pairs learnable by elimination.

	Given the match's Discord user_ids, the lobby's captured slot profileIds, and
	the currently-known profileId->user_id map, pin the leftover pair ONLY when
	exactly one user AND one profileId remain unmatched. Pure, no guessing — if
	two or more are unknown we learn nothing this game (and try again next time).
	"""
	matched_uids = {known_pid_to_uid[p] for p in slot_profile_ids if p in known_pid_to_uid}
	unknown_pids = [p for p in slot_profile_ids if p not in known_pid_to_uid]
	unmatched_uids = [u for u in match_user_ids if u not in matched_uids]
	if len(unknown_pids) == 1 and len(unmatched_uids) == 1:
		return [(unmatched_uids[0], unknown_pids[0])]
	return []


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


async def link(user_id, profile_id, name, source="learned"):
	"""Persist a discord<->profile binding via the identity resolver (idempotent).

	This is automated self-healing (see LobbyWatcher._confirm), never a human
	correction, so `source` must stay within the non-manual tiers identity.learn
	accepts — 'manual' is reserved for human corrections and would be silently
	rejected from overwriting one anyway.
	"""
	try:
		await identity.learn(profile_id, user_id, source, aoe2_name=name or None)
		log.info(f"profile_map: linked user {user_id} <-> profile {profile_id} ({name}).")
	except Exception as e:
		log.error(f"profile_map link failed ({user_id}<->{profile_id}): {e}")
