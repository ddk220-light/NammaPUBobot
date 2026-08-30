# -*- coding: utf-8 -*-
import json
import time

from nammaoe2bot.runtime.console import log
from nammaoe2bot.runtime.database import db, query_scope

from nammaoe2bot.exceptions import Exceptions as Exc
from nammaoe2bot.pickup.expire import expire
from nammaoe2bot.pickup.match.match import Match
from nammaoe2bot.pickup.queue import PickupQueue

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


STATE_PATH = "saved_state.json"
_JSON_OPTIONS = dict(sort_keys=True, separators=(",", ":"))

# Each destination advances independently.  In particular, a successful local
# snapshot must not hide a failed durable write: the next cadence tick skips the
# already-current file and retries MySQL with the same payload.
_last_local_payload = None
_last_db_payload = None


def _stable_rows(rows):
	"""A deterministic order for top-level snapshot collections.

	The live containers happen to preserve insertion order, but that is not part
	of the persisted-state contract.  Sorting by the row's canonical JSON keeps a
	reordered set of queues, matches or expiry timers from looking like a state
	change and waking MySQL for an identical snapshot.
	"""
	return sorted(rows, key=lambda row: json.dumps(row, **_JSON_OPTIONS))


def _serialize_state(app):
	queues = []
	for qc in app.channels.values():
		for q in qc.queues:
			if q.length > 0:
				queues.append(q.serialize())
	matches = [match.serialize() for match in app.active_matches]
	return dict(
		queues=_stable_rows(queues),
		matches=_stable_rows(matches),
		expire=_stable_rows(expire.serialize()),
	)


def canonical_state_payload(app):
	"""The stable JSON payload used by both local and durable snapshots."""
	return json.dumps(_serialize_state(app), **_JSON_OPTIONS)


def save_state(app, payload=None):
	"""Best-effort local snapshot to disk. Survives only same-container restarts
	(the bot disk is ephemeral); the DURABLE copy is save_state_db(). Kept for
	local dev and the sync signal/crash handlers, which can't await.

	Returns True only when a changed payload was successfully written.  Quiet
	no-op snapshots are the common case and deliberately produce no log line.
	"""
	global _last_local_payload
	payload = canonical_state_payload(app) if payload is None else payload
	if payload == _last_local_payload:
		return False
	try:
		with open(STATE_PATH, "w") as f:
			f.write(payload)
	except Exception as e:
		log.error(f"save_state (file) failed: {e}")
		return False
	_last_local_payload = payload
	return True


async def save_state_db(app, payload=None):
	"""Persist changed state to MySQL; retry failures on the next call.

	The successful-payload baseline advances only after the awaited INSERT
	returns.  A transient failure therefore cannot turn the next identical call
	into a no-op.
	"""
	global _last_db_payload
	payload = canonical_state_payload(app) if payload is None else payload
	if payload == _last_db_payload:
		return False
	try:
		with query_scope("state.snapshot"):
			await db.insert(
				"bot_state",
				dict(id=1, data=payload, updated_at=int(time.time())),
				on_duplicate="replace",
			)
	except Exception as e:
		log.error(f"save_state_db failed: {e}")
		return False
	_last_db_payload = payload
	return True


async def save_state_if_changed(app):
	"""Snapshot one canonical view to each destination if that view changed."""
	payload = canonical_state_payload(app)
	local_written = save_state(app, payload)
	db_written = await save_state_db(app, payload)
	return local_written, db_written


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
			with open(STATE_PATH, "r") as f:
				data = json.loads(f.read())
		except IOError:  # noqa: UP024
			return

	log.info("Loading state...")


	for qd in data['queues']:
		if qd.get('queue_type') in ['PickupQueue', None]:
			try:
				await PickupQueue.from_json(qd)
			except Exc.ValueError as e:
				log.error(f"Failed to load queue state ({qd.get('queue_id')}): {str(e)}")
		else:
			log.error(f"Got unknown queue type '{qd.get('queue_type')}'.")

	for md in data['matches']:
		try:
			await Match.from_json(md)
		except Exc.ValueError as e:
			log.error(f"Failed to load match {md['match_id']}: {str(e)}")

	if 'expire' in data.keys():
		await expire.load_json(data['expire'])
