#!/usr/bin/env python3
"""Backfill cls_* strategy/tag rows from replay-stats re-ingest.

Default scope is parsed matches with no classification rows. Use --all-parsed to rebuild every
parsed match. This downloads/parses replays again, so keep a small --limit while validating.
"""
import argparse
import asyncio
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
	sys.path.insert(0, ROOT)
REPLAY_SCRATCH = os.path.join(ROOT, ".replay_scratch")
if REPLAY_SCRATCH not in sys.path:
	sys.path.insert(0, REPLAY_SCRATCH)

from core import database
from core.database import db


async def _work_items(all_parsed, limit):
	where = ""
	if not all_parsed:
		where = (
			"WHERE NOT EXISTS ("
			"SELECT 1 FROM cls_results cr WHERE cr.aoe2_match_id=rm.aoe2_match_id"
			") AND NOT EXISTS ("
			"SELECT 1 FROM cls_match_ingest ci "
			"WHERE ci.aoe2_match_id=rm.aoe2_match_id "
			"AND (ci.status='done' OR ci.status LIKE 'unavailable:%%')"
			") "
		)
	sql = (
		"SELECT rm.aoe2_match_id, MAX(rm.bot_match_id) AS bot_match_id, MAX(qm.at) AS at "
		"FROM rs_matches rm LEFT JOIN qc_matches qm ON qm.match_id=rm.bot_match_id "
		+ where +
		"GROUP BY rm.aoe2_match_id ORDER BY at DESC, rm.aoe2_match_id DESC"
	)
	if limit:
		sql += " LIMIT %s"
		return await db.fetchall(sql, [limit])
	return await db.fetchall(sql)


async def _ensure_match_ingest_table():
	exists = await db.fetchone("SHOW TABLES LIKE 'cls_match_ingest'")
	if exists:
		return
	await db.execute(
		"CREATE TABLE IF NOT EXISTS cls_match_ingest ("
		"aoe2_match_id BIGINT NOT NULL, classified_at BIGINT, result_rows BIGINT, "
		"status VARCHAR(191), PRIMARY KEY (aoe2_match_id))")


async def _upsert_registry():
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
			{"key": c.key, "field": r["field"], "source": r["source"], "status": r["status"], "note": r["note"]}
			for r in c.requirements
		]
		if rows:
			await db.insert_many("cls_data_requirements", rows, on_dublicate="replace")


async def _write_classifications(extracted, played_at_epoch):
	from utils.classifications.pipeline import classify

	aoe2_match_id = int(extracted["match"]["aoe2_match_id"])
	result_rows, metric_rows, _player_rows = classify.classify_game(extracted, aoe2_match_id, played_at_epoch or 0)
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
	return len(result_rows)


async def _mark_unavailable(aoe2_match_id, status):
	await db.insert("cls_match_ingest", {
		"aoe2_match_id": int(aoe2_match_id),
		"classified_at": int(time.time()),
		"result_rows": 0,
		"status": "unavailable:{}".format(str(status or "unknown")[:175]),
	}, on_dublicate="replace")


async def _rebuild_player_totals():
	await db.execute("DELETE FROM cls_player_totals")
	await db.execute(
		"REPLACE INTO cls_player_totals (identity, games, wins, losses) "
		"SELECT MIN(identity), COUNT(*), SUM(winner=1), SUM(winner=0) "
		"FROM rs_player_games WHERE identity IS NOT NULL AND identity <> '' GROUP BY identity")


def _extract_for_match(aoe2_match_id, played_at_epoch, resolved, date_map):
	from utils.replay_quiz import download as dl
	from utils.replay_quiz.extract import extract_match

	path = status = None
	pids = dl.resolve_profile_ids(aoe2_match_id)
	for pid in pids[:4]:
		path, status = dl.download_replay(aoe2_match_id, pid)
		if path:
			break
	if not path:
		return None, status or "unavailable"
	dates = dict(date_map)
	if played_at_epoch:
		dates[aoe2_match_id] = time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(played_at_epoch)))
	return extract_match(path, resolved, dates), "ok"


async def run(all_parsed=False, limit=0, pace=10.0, dry_run=False):
	await database.db.connect()
	try:
		await _ensure_match_ingest_table()
		rows = await _work_items(all_parsed, limit)
		print("strategy-tag backfill candidates: {}".format(len(rows)), flush=True)
		if dry_run:
			for r in rows[:20]:
				print("{} bot_match={} at={}".format(r["aoe2_match_id"], r.get("bot_match_id"), r.get("at")))
			return 0

		from utils.replay_quiz.extract import load_date_map, load_resolved

		resolved = load_resolved()
		date_map = load_date_map()
		await _upsert_registry()
		done = failed = 0
		for r in rows:
			try:
				mid = int(r["aoe2_match_id"])
				extracted, status = await asyncio.to_thread(
					_extract_for_match, mid, r.get("at"), resolved, date_map)
				if not extracted:
					failed += 1
					await _mark_unavailable(mid, status)
					print("unavailable {}: {}".format(mid, status), flush=True)
				else:
					written = await _write_classifications(extracted, r.get("at"))
					done += 1
					print("classified {}: {} rows".format(mid, written), flush=True)
			except Exception as e:
				failed += 1
				print("failed {}: {}".format(r["aoe2_match_id"], e), flush=True)
			if done % 10 == 0:
				print("processed={} failed={}".format(done, failed), flush=True)
			if pace:
				await asyncio.sleep(float(pace))
		await _rebuild_player_totals()
		print("strategy-tag backfill done: processed={} failed={}".format(done, failed), flush=True)
		return 0 if failed == 0 else 1
	finally:
		await database.db.close()


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--all-parsed", action="store_true",
	                    help="re-ingest every parsed rs_matches row, not only matches with no cls rows")
	parser.add_argument("--limit", type=int, default=0)
	parser.add_argument("--pace", type=float, default=10.0,
	                    help="seconds between replay fetches; keep nonzero to avoid aoe.ms 429s")
	parser.add_argument("--dry-run", action="store_true")
	args = parser.parse_args()
	raise SystemExit(asyncio.run(run(
		all_parsed=args.all_parsed,
		limit=args.limit,
		pace=args.pace,
		dry_run=args.dry_run,
	)))


if __name__ == "__main__":
	main()
