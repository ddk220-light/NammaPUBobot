# -*- coding: utf-8 -*-
"""Bridge replay-stats extracts into cls_* strategy/luck tables.

Replay-stats already parses the full replay once. Write classification rows from that same
extract so match/player tags stay fresh without a separate offline runner.
"""
import time

from core.database import db


async def upsert_registry():
	from utils.classifications.registry import REGISTRY

	for c in REGISTRY.values():
		await db.insert("cls_classifications", {
			"key": c.key,
			"title": c.title,
			"trigger_spec": c.trigger_spec,
			"version": c.version,
			"status": c.status,
			"updated_at": int(time.time()),
		}, on_dublicate="replace")
		await db.execute("DELETE FROM cls_data_requirements WHERE `key`=%s", [c.key])
		rows = [
			{
				"key": c.key,
				"field": r["field"],
				"source": r["source"],
				"status": r["status"],
				"note": r["note"],
			}
			for r in c.requirements
		]
		if rows:
			await db.insert_many("cls_data_requirements", rows, on_dublicate="replace")


async def rebuild_player_totals_from_rs():
	"""Use all parsed replay-stats player-games as the corpus denominator."""
	await db.execute("DELETE FROM cls_player_totals")
	await db.execute(
		"REPLACE INTO cls_player_totals (identity, games, wins, losses) "
		"SELECT MIN(identity), COUNT(*), SUM(winner=1), SUM(winner=0) "
		"FROM rs_player_games WHERE identity IS NOT NULL AND identity <> '' "
		"GROUP BY identity")


async def write_extracted_match(extracted, played_at_epoch=None, rebuild_totals=True):
	"""Replace classification rows for one extracted match."""
	from utils.classifications.pipeline import classify

	await upsert_registry()
	aoe2_match_id = int(extracted["match"]["aoe2_match_id"])
	result_rows, metric_rows, _player_rows = classify.classify_game(
		extracted, aoe2_match_id, int(played_at_epoch or 0))
	await db.execute("DELETE FROM cls_results WHERE aoe2_match_id=%s", [aoe2_match_id])
	await db.execute("DELETE FROM cls_result_metrics WHERE aoe2_match_id=%s", [aoe2_match_id])
	if result_rows:
		await db.insert_many("cls_results", result_rows, on_dublicate="replace")
	if metric_rows:
		await db.insert_many("cls_result_metrics", metric_rows, on_dublicate="replace")
	await db.insert("cls_match_ingest", {
		"aoe2_match_id": aoe2_match_id,
		"classified_at": int(time.time()),
		"result_rows": len(result_rows),
		"status": "done",
	}, on_dublicate="replace")
	if rebuild_totals:
		await rebuild_player_totals_from_rs()
	return len(result_rows)
