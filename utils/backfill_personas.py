#!/usr/bin/env python3
"""Backfill rs_player_personas for every player with parsed replay data.

One-time (idempotent) materialization; afterwards store.write_match keeps the
rows fresh per ingested match. Reads DB config the same way the bot does
(config.cfg or environment DB_URI).
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


def _load_persona_store():
	path = os.path.join(ROOT, "bot", "replay_stats", "persona_store.py")
	spec = importlib.util.spec_from_file_location("persona_store_backfill", path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


async def run(limit=0, dry_run=False):
	await database.db.connect()
	try:
		persona_store = _load_persona_store()
		sql = "SELECT DISTINCT user_id FROM rs_player_games WHERE user_id IS NOT NULL"
		if limit:
			sql += " LIMIT %s"
			rows = await db.fetchall(sql, [limit])
		else:
			rows = await db.fetchall(sql)
		user_ids = [int(r["user_id"]) for r in rows or []]
		print("persona backfill candidates: {}".format(len(user_ids)), flush=True)
		if dry_run:
			for uid in user_ids[:20]:
				print(uid)
			return 0
		done = 0
		for uid in user_ids:
			await persona_store.refresh_user(uid)
			done += 1
			if done % 10 == 0:
				print("processed={}".format(done), flush=True)
		print("persona backfill done: processed={}".format(done), flush=True)
		return 0
	finally:
		await database.db.close()


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--limit", type=int, default=0)
	parser.add_argument("--dry-run", action="store_true")
	args = parser.parse_args()
	raise SystemExit(asyncio.run(run(limit=args.limit, dry_run=args.dry_run)))


if __name__ == "__main__":
	main()
