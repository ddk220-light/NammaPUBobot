# -*- coding: utf-8 -*-
import traceback  # noqa: F401
import json
import time
from nextcord import Interaction  # noqa: F401

from core.console import log
from core.database import db
from core.utils import error_embed, ok_embed, get  # noqa: F401

import bot

# Durable snapshot of in-flight state (queues + active matches + expire timers)
# in MySQL. The bot service disk is ephemeral (only MySQL has a volume), so
# saved_state.json alone was lost on every redeploy/crash — silently dropping
# in-flight matches (captain then can't /report, players re-queue; the 1390237
# incident). MySQL is durable, so the periodic DB snapshot survives restarts.
db.ensure_table(dict(
	tname="bot_state",
	columns=[
		dict(cname="id", ctype=db.types.int),
		dict(cname="data", ctype=db.types.dict),   # MEDIUMTEXT — JSON blob
		dict(cname="updated_at", ctype=db.types.int),
	],
	primary_keys=["id"]
))


def update_qc_lang(qc_cfg):
	bot.queue_channels[qc_cfg.p_key].update_lang()


def update_rating_system(qc_cfg):
	bot.queue_channels[qc_cfg.p_key].update_rating_system()


def _serialize_state():
	queues = []
	for qc in bot.queue_channels.values():
		for q in qc.queues:
			if q.length > 0:
				queues.append(q.serialize())
	matches = [match.serialize() for match in bot.active_matches]
	return dict(queues=queues, matches=matches, expire=bot.expire.serialize())


def save_state():
	"""Best-effort local snapshot to disk. Survives only same-container restarts
	(the bot disk is ephemeral); the DURABLE copy is save_state_db(). Kept for
	local dev and the sync signal/crash handlers, which can't await."""
	log.info("Saving state...")
	try:
		with open("saved_state.json", "w") as f:
			f.write(json.dumps(_serialize_state()))
	except Exception as e:
		log.error(f"save_state (file) failed: {e}")


async def save_state_db():
	"""Durable state snapshot to MySQL — survives Railway redeploys/crashes."""
	try:
		await db.insert(
			"bot_state",
			dict(id=1, data=json.dumps(_serialize_state()), updated_at=int(time.time())),
			on_duplicate="replace",
		)
	except Exception as e:
		log.error(f"save_state_db failed: {e}")


async def load_state():
	# Prefer the durable MySQL snapshot; fall back to the local file (dev or a
	# same-container restart). Either way, restore via the existing from_json.
	data = None
	try:
		row = await db.select_one(["data"], "bot_state", where=dict(id=1))
		if row and row.get("data"):
			data = json.loads(row["data"])
	except Exception as e:
		log.error(f"load_state (db) failed, trying file: {e}")
	if data is None:
		try:
			with open("saved_state.json", "r") as f:
				data = json.loads(f.read())
		except IOError:  # noqa: UP024
			return

	log.info("Loading state...")


	for qd in data['queues']:
		if qd.get('queue_type') in ['PickupQueue', None]:
			try:
				await bot.PickupQueue.from_json(qd)
			except bot.Exc.ValueError as e:
				log.error(f"Failed to load queue state ({qd.get('queue_id')}): {str(e)}")
		else:
			log.error(f"Got unknown queue type '{qd.get('queue_type')}'.")

	for md in data['matches']:
		try:
			await bot.Match.from_json(md)
		except bot.Exc.ValueError as e:
			log.error(f"Failed to load match {md['match_id']}: {str(e)}")

	if 'expire' in data.keys():
		await bot.expire.load_json(data['expire'])


async def remove_players(*users, reason=None):
	for qc in set((q.qc for q in bot.active_queues)):
		await qc.remove_members(*users, reason=reason)

