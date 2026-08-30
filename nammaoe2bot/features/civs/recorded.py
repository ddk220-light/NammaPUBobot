# -*- coding: utf-8 -*-
"""Small post-write hook for the basic, non-replay civilization feature."""
import time

from nammaoe2bot.runtime.console import log
from nammaoe2bot.runtime.database import db


async def link(bot_match_id, aoe2_match_id, *, db_adapter=None):
	"""Persist the bot-match ↔ AoE2-match identity without needing civ rows."""
	if bot_match_id is None or aoe2_match_id is None:
		return False
	# Unit-test/custom adapters must not leak a write to the production adapter.
	if db_adapter is not None:
		return False
	try:
		from nammaoe2bot import community
		return await community.link_match_replay(
			int(bot_match_id), int(aoe2_match_id))
	except Exception as e:
		log.error(
			f"Civ match link failed for bot match {bot_match_id} / AoE2 "
			f"{aoe2_match_id}: {e}")
		return False


async def link_existing(bot_match_id, *, db_adapter=None):
	"""Recover a link from civ rows after the bot result row is durable.

	Lobby completion can observe the AoE2 game before ``matches`` contains the
	bot result, so its first link attempt is intentionally allowed to miss.  The
	post-result civ matcher calls this helper when it sees that civs are already
	recorded, at which point ``community.link_match_replay`` can resolve the
	match's channel and create ``match_replays``.
	"""
	if bot_match_id is None:
		return False
	dbw = db_adapter or db
	try:
		row = await dbw.fetchone(
			"SELECT replay_match_id FROM civ_picks "
			"WHERE bot_match_id=%s AND replay_match_id IS NOT NULL "
			"ORDER BY id DESC LIMIT 1", [bot_match_id])
		if not row:
			return False
		return await link(
			bot_match_id, row["replay_match_id"], db_adapter=db_adapter)
	except Exception as e:
		log.error(f"Civ existing-link recovery failed for bot match {bot_match_id}: {e}")
		return False


async def refresh(channel_id, computed_at=None, *, db_adapter=None):
	"""Rebuild the owning community's small civilization W/L summary.

	``computed_at`` is retained only for compatibility with older civ writers,
	which pass the source match timestamp.  A derived summary's timestamp must
	describe when the summary was refreshed, so it is deliberately stamped now.
	"""
	when = int(time.time())
	try:
		from nammaoe2bot.derived import civ_stats
		return await civ_stats.refresh_for_channel(
			int(channel_id), when, db_adapter=db_adapter)
	except Exception as e:
		log.error(f"Civ stats refresh failed for channel {channel_id}: {e}")
		return False


async def finalize(
		channel_id, *, bot_match_id=None, aoe2_match_id=None,
		computed_at=None, db_adapter=None):
	"""Keep match linkage and civ W/L summaries current after ``civ_picks``.

	Both operations are derived/indexing work and therefore best-effort: the civ
	rows themselves are already durable and the daily recovery refresh can heal
	a failed summary.  A custom DB adapter is used by unit tests; community-link
	writes intentionally stay on the production adapter only.
	"""
	await link(bot_match_id, aoe2_match_id, db_adapter=db_adapter)
	await refresh(channel_id, computed_at, db_adapter=db_adapter)
