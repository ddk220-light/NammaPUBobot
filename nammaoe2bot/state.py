# -*- coding: utf-8 -*-
import json
import os
import time

from nammaoe2bot.runtime.console import log
from nammaoe2bot.runtime.database import db, query_scope

from nammaoe2bot.exceptions import Exceptions as Exc
from nammaoe2bot.pickup.expire import expire
from nammaoe2bot.pickup import stats
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
	local development and same-container recovery.

	Returns True only when a changed payload was successfully written.  Quiet
	no-op snapshots are the common case and deliberately produce no log line.
	"""
	global _last_local_payload
	if not app.state_restored:
		return False
	payload = canonical_state_payload(app) if payload is None else payload
	if payload == _last_local_payload:
		return False
	try:
		with open(STATE_PATH + ".tmp", "w") as f:
			f.write(payload)
		os.replace(STATE_PATH + ".tmp", STATE_PATH)
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
	if not app.state_restored:
		return False
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
	if not app.state_restored:
		return False, False
	payload = canonical_state_payload(app)
	local_written = save_state(app, payload)
	db_written = await save_state_db(app, payload)
	return local_written, db_written


async def load_state(app):
	"""Read the durable source before allowing any snapshot or match tick.

	A failed DB read is not evidence of an empty installation. Failing closed
	leaves the durable copy intact for the next startup/reconnect.
	"""
	async with app.state_restore_lock:
		if app.state_restored:
			return
		row = await db.select_one(["data"], "bot_state", where=dict(id=1))
		if row is not None:
			data = json.loads(row["data"])
		else:
			try:
				with open(STATE_PATH) as f:
					data = json.load(f)
			except FileNotFoundError:
				data = dict(queues=[], matches=[], expire=[])

		# Validate the collection envelope before performing any restoration.
		if (not isinstance(data, dict)
				or any(not isinstance(data.get(key), list) for key in ("queues", "matches"))
				or not isinstance(data.get("expire", []), list)):
			raise Exc.ValueError("Invalid saved state; refusing to overwrite it.")
		ids = [md["match_id"] for md in data["matches"]]
		if any(type(mid) is not int or mid < 0 for mid in ids) or len(set(ids)) != len(ids):
			raise Exc.ValueError("Invalid or duplicate saved match IDs.")
		await stats.reserve_restored_ids(ids)
		log.info("Loading state...")
		for qd in data["queues"]:
			if qd.get("queue_type") not in ("PickupQueue", None):
				raise Exc.ValueError(f"Unknown queue type {qd.get('queue_type')}.")
			await PickupQueue.from_json(qd)

		for md in data["matches"]:
			existing = await db.select_one(["*"], "matches", where=dict(match_id=md["match_id"]))
			if existing:
				if existing["channel_id"] != md["channel_id"] or existing["queue_id"] != md["queue_id"]:
					raise Exc.ValueError(f"Saved match {md['match_id']} conflicts with its recorded result.")
				await stats.validate_recorded_match(db, existing, md["players"], md["teams"])
				continue  # Committed after the last snapshot; never rate it again.
			if not any(m.id == md["match_id"] for m in app.active_matches):
				await Match.from_json(md)
		await expire.load_json(data.get("expire", []))
		app.state_restored = True
