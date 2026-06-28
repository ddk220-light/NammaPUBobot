#!/usr/bin/env python3
"""Backfill rs_player_game_tags from existing replay-stats tables.

No replay download or mgz parse needed; this reads rs_player_games/units/techs/events.
"""
import argparse
import asyncio
import importlib.util
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
	sys.path.insert(0, ROOT)

from core import database
from core.database import db


def _load_player_tags_module():
	path = os.path.join(ROOT, "bot", "replay_stats", "player_tags.py")
	spec = importlib.util.spec_from_file_location("player_tags_backfill", path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


async def _match_ids(limit, all_matches):
	where = ""
	if not all_matches:
		where = (
			"WHERE NOT EXISTS ("
			"SELECT 1 FROM rs_player_game_tags t WHERE t.aoe2_match_id=rm.aoe2_match_id"
			") "
		)
	sql = (
		"SELECT rm.aoe2_match_id FROM rs_matches rm " + where +
		"ORDER BY rm.aoe2_match_id DESC"
	)
	if limit:
		sql += " LIMIT %s"
		rows = await db.fetchall(sql, [limit])
	else:
		rows = await db.fetchall(sql)
	return [int(r["aoe2_match_id"]) for r in rows or []]


async def run(limit=0, all_matches=False, dry_run=False):
	await database.db.connect()
	try:
		player_tags = _load_player_tags_module()
		await player_tags.ensure_table()
		ids = await _match_ids(limit, all_matches)
		print("player-game tag backfill candidates: {}".format(len(ids)), flush=True)
		if dry_run:
			for mid in ids[:20]:
				print(mid)
			return 0
		done = tags = 0
		for mid in ids:
			n = await player_tags.write_match_tags(mid)
			done += 1
			tags += n
			if done % 50 == 0:
				print("processed={} tags={}".format(done, tags), flush=True)
		print("player-game tag backfill done: processed={} tags={}".format(done, tags), flush=True)
		return 0
	finally:
		await database.db.close()


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--limit", type=int, default=0)
	parser.add_argument("--all", action="store_true", help="rebuild all parsed matches")
	parser.add_argument("--dry-run", action="store_true")
	args = parser.parse_args()
	raise SystemExit(asyncio.run(run(limit=args.limit, all_matches=args.all, dry_run=args.dry_run)))


if __name__ == "__main__":
	main()
