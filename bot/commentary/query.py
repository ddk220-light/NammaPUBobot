# -*- coding: utf-8 -*-
import json

from core.database import db

from . import COMMENTARY_VERSION

APPROVED_STATUSES = ("approved_local", "approved", "live")


def _decode_json(raw):
	if not raw:
		return None
	if isinstance(raw, (dict, list)):
		return raw
	try:
		return json.loads(raw)
	except (TypeError, ValueError):
		return None


def _row_payload(row):
	if not row:
		return None
	commentary = _decode_json(row.get("commentary_json"))
	if not commentary:
		return None
	return {
		"user_id": str(row["user_id"]),
		"period": row["period"],
		"prompt_version": row["prompt_version"],
		"source_hash": row.get("source_hash"),
		"generated_at": row.get("generated_at"),
		"model": row.get("model"),
		"status": row.get("status"),
		"commentary": commentary,
	}


async def player_commentary(user_id, period, prompt_version=COMMENTARY_VERSION):
	status_clause = ",".join(["%s"] * len(APPROVED_STATUSES))
	args = [int(user_id), period, prompt_version, *APPROVED_STATUSES]
	rows = await db.fetchall(
		"SELECT * FROM bot_player_commentary "
		"WHERE user_id=%s AND period=%s AND prompt_version=%s "
		f"AND status IN ({status_clause}) ORDER BY generated_at DESC LIMIT 1",
		args)
	if rows:
		return _row_payload(rows[0])

	args = [int(user_id), period, *APPROVED_STATUSES]
	rows = await db.fetchall(
		"SELECT * FROM bot_player_commentary "
		"WHERE user_id=%s AND period=%s "
		f"AND status IN ({status_clause}) ORDER BY generated_at DESC LIMIT 1",
		args)
	return _row_payload(rows[0]) if rows else None
