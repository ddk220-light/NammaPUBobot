# -*- coding: utf-8 -*-
"""DB-backed, self-healing Discord-user <-> AoE2-profile map (qc_profile_map).

Learned from roster-confirmed lobbies: each captured slot's (profileId, name) is
authoritative for that match's players. The results/ratings loop NEVER reads this
— it only powers the optional winner-name hint and per-player civ attribution,
both best-effort. ``eliminate`` is a pure function (unit-tested); the read/write
helpers wrap qc_profile_map and swallow their own errors.
"""
import time

from core.console import log
from core.database import db


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
			row = await db.select_one(["user_id"], "qc_profile_map", where={"profile_id": pid})
		except Exception as e:
			log.error(f"profile_map lookup failed for {pid}: {e}")
			continue
		if row:
			out[pid] = row["user_id"]
	return out


async def link(user_id, profile_id, name, source="lobby"):
	"""Persist a discord<->profile binding (idempotent on the composite PK)."""
	try:
		await db.insert("qc_profile_map", {
			"user_id": user_id,
			"profile_id": profile_id,
			"name": name,
			"linked_at": int(time.time()),
			"source": source,
		}, on_dublicate="replace")
		log.info(f"profile_map: linked user {user_id} <-> profile {profile_id} ({name}).")
	except Exception as e:
		log.error(f"profile_map link failed ({user_id}<->{profile_id}): {e}")
