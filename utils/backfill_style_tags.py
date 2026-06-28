#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backfill derived player role tags from rs_* replay tables into cls_results."""
import argparse
import os
import sys
import time
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
	sys.path.insert(0, str(ROOT))

from utils.db_helpers import parse_db_uri
from utils.player_style_tags import STYLE_TAG_LABELS, style_tag_rows


def _db_uri():
	return os.environ.get("DB_URI") or os.environ.get("MYSQL_URL")


def _rows(conn, sql, args=()):
	with conn.cursor() as cur:
		cur.execute(sql, args)
		return list(cur.fetchall())


def _execute(conn, sql, args=()):
	with conn.cursor() as cur:
		cur.execute(sql, args)


def _insert_dicts(conn, table, rows):
	if not rows:
		return
	cols = list(rows[0].keys())
	sql = "INSERT INTO `{}` ({}) VALUES ({})".format(
		table,
		",".join("`{}`".format(c) for c in cols),
		",".join(["%s"] * len(cols)),
	)
	with conn.cursor() as cur:
		cur.executemany(sql, [[r.get(c) for c in cols] for r in rows])


def _game(conn, match_id):
	return {
		"match": _rows(conn, "SELECT aoe2_match_id, map, duration_s FROM rs_matches WHERE aoe2_match_id=%s", [match_id])[0],
		"players": _rows(conn, """
			SELECT player_number, profile_id, identity, civ, team, winner, castle_s, imperial_s,
			       vil_pre_castle, mil_pre_castle, military
			FROM rs_player_games WHERE aoe2_match_id=%s
		""", [match_id]),
		"units": _rows(conn, """
			SELECT player_number, category, unit, total, pre_castle, pre_imperial
			FROM rs_player_units WHERE aoe2_match_id=%s
		""", [match_id]),
		"techs": _rows(conn, """
			SELECT player_number, tech, click_s, phase
			FROM rs_player_techs WHERE aoe2_match_id=%s
		""", [match_id]),
	}


def run(days=None):
	uri = _db_uri()
	if not uri:
		raise SystemExit("Set DB_URI first.")
	conn = pymysql.connect(**parse_db_uri(uri), autocommit=False, cursorclass=pymysql.cursors.DictCursor)
	try:
		where = ""
		args = []
		if days is not None:
			where = "WHERE COALESCE(qm.at, rm.parsed_at) >= %s"
			args.append(int(time.time()) - int(days) * 86400)
		matches = _rows(conn, """
			SELECT rm.aoe2_match_id, COALESCE(qm.at, rm.parsed_at) AS played_at
			FROM rs_matches rm
			LEFT JOIN qc_matches qm ON qm.match_id=rm.bot_match_id
			{}
			ORDER BY COALESCE(qm.at, rm.parsed_at) DESC
		""".format(where), args)
		keys = list(STYLE_TAG_LABELS)
		total = 0
		for m in matches:
			mid = int(m["aoe2_match_id"])
			game = _game(conn, mid)
			rows = style_tag_rows(game, mid, int(m.get("played_at") or 0))
			_execute(
				conn,
				"DELETE FROM cls_results WHERE aoe2_match_id=%s AND `key` IN (" + ",".join(["%s"] * len(keys)) + ")",
				[mid, *keys],
			)
			_insert_dicts(conn, "cls_results", rows)
			total += len(rows)
		conn.commit()
		print("processed={} style_tags={}".format(len(matches), total))
	finally:
		conn.close()


if __name__ == "__main__":
	parser = argparse.ArgumentParser()
	parser.add_argument("--days", type=int, default=None)
	run(parser.parse_args().days)
