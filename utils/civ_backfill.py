#!/usr/bin/env python3
"""Backfill historical civ data from data/match_civ_details.csv into the
qc_match_civs MySQL table.

SAFETY:
  - DRY-RUN by default (no writes). Pass --apply to actually insert.
  - Idempotent: skips any (bot_match_id, user_id) already present.
  - Never updates or deletes existing rows.
  - Maps nick -> user_id authoritatively via qc_player_matches (match_id, nick),
    so we never guess identities. Rows whose (match, nick) isn't in
    qc_player_matches are reported and skipped, not invented.

Reads DB_URI from config.cfg (gitignored).
Usage:
    python utils/civ_backfill.py            # dry-run report
    python utils/civ_backfill.py --apply    # write (after you've seen the dry-run)
"""
import os
import sys
import csv
import argparse
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db_helpers import load_config  # noqa: E402

import pymysql  # noqa: E402
from pymysql.cursors import DictCursor  # noqa: E402

CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "match_civ_details.csv")
IST = timezone(timedelta(hours=5, minutes=30))


def connect():
	cfg = load_config()
	if cfg is None or not getattr(cfg, "DB_URI", ""):
		sys.exit("config.cfg / DB_URI missing.")
	u = urlparse(cfg.DB_URI)
	return pymysql.connect(host=u.hostname, port=u.port or 3306, user=unquote(u.username or ""),
	                       password=unquote(u.password or ""), db=(u.path or "").lstrip("/"),
	                       cursorclass=DictCursor, connect_timeout=20)


def csv_date_to_epoch(s):
	for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
		try:
			return int(datetime.strptime(s.strip(), fmt).replace(tzinfo=IST).timestamp())
		except (ValueError, AttributeError):
			continue
	return None


def load_profile_nick_to_uid():
	"""nick -> Discord user_id from data/player_profile_map.csv (uses the stable user_id column)."""
	path = os.path.join(os.path.dirname(CSV_PATH), "player_profile_map.csv")
	m = {}
	try:
		with open(path, newline="", encoding="utf-8") as f:
			for row in csv.DictReader(f):
				uid = (row.get("user_id") or "").strip()
				nick = row.get("nick")
				if nick and uid.isdigit():
					m[nick] = int(uid)
	except FileNotFoundError:
		pass
	return m


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("--apply", action="store_true", help="actually write (default is dry-run)")
	args = ap.parse_args()

	with open(CSV_PATH, newline="", encoding="utf-8") as f:
		rows = list(csv.DictReader(f))
	matches_in_csv = {r["bot_match_id"] for r in rows}
	dates = sorted(r["date"] for r in rows if r.get("date"))
	print(f"CSV: {len(rows)} rows across {len(matches_in_csv)} bot matches | date {dates[0]} .. {dates[-1]}")

	conn = connect()
	cur = conn.cursor()

	def q(sql, p=None):
		cur.execute(sql, p or [])
		return cur.fetchall()

	channels = [r["channel_id"] for r in q("SELECT DISTINCT channel_id FROM qc_matches")]
	print(f"channels in qc_matches: {channels}")
	channel_id = channels[0] if len(channels) == 1 else None

	# Authoritative (match_id, nick) -> (user_id, channel_id) and match_id -> at
	pm = {}    # (match_id, nick) -> (user_id, channel_id)
	pm2 = {}   # (match_id, user_id) -> (nick, channel_id) -- for nick-change recovery
	for r in q("SELECT match_id, nick, user_id, channel_id FROM qc_player_matches"):
		pm[(str(r["match_id"]), r["nick"])] = (r["user_id"], r["channel_id"])
		pm2[(str(r["match_id"]), r["user_id"])] = (r["nick"], r["channel_id"])
	at_by_match = {str(r["match_id"]): r["at"] for r in q("SELECT match_id, `at` FROM qc_matches")}
	prof = load_profile_nick_to_uid()
	print(f"loaded qc_player_matches keys={len(pm)} | qc_matches at-map={len(at_by_match)} | profile nick->uid={len(prof)}")

	# Existing civ rows (dedup + format check)
	existing = {(str(r["bot_match_id"]), r["user_id"]) for r in
	            q("SELECT bot_match_id, user_id FROM qc_match_civs WHERE bot_match_id IS NOT NULL")}
	print(f"existing qc_match_civs (bot_match_id,user_id) keys: {len(existing)}")
	sample_existing = q("SELECT channel_id, aoe2_match_id, aoe2_name, civ, `at`, bot_match_id, user_id, nick, team, result "
	                    "FROM qc_match_civs WHERE bot_match_id IS NOT NULL LIMIT 3")
	print("sample EXISTING qc_match_civs rows:")
	for r in sample_existing:
		print("   ", {k: r[k] for k in ('bot_match_id', 'user_id', 'nick', 'civ', 'team', 'result', 'at')})

	# Classify CSV rows
	to_insert, unmappable, dup, recovered = [], [], 0, 0
	for r in rows:
		bmid, nick = r["bot_match_id"], r["nick"]
		hit, used_nick = pm.get((bmid, nick)), nick
		if hit is None:
			# Nick-change recovery: profile map nick->user_id, then verify they actually played this match.
			uid = prof.get(nick)
			if uid is not None and (bmid, uid) in pm2:
				auth_nick, ch = pm2[(bmid, uid)]
				hit, used_nick, recovered = (uid, ch), auth_nick, recovered + 1
		if hit is None:
			unmappable.append(r)
			continue
		user_id, ch = hit
		if (bmid, user_id) in existing:
			dup += 1
			continue
		at = at_by_match.get(bmid) or csv_date_to_epoch(r.get("date", ""))
		to_insert.append((
			ch or channel_id, int(r["aoe2_match_id"]) if r.get("aoe2_match_id") else None, None,
			r["civ"], at, int(bmid), user_id, used_nick, int(r["team"]) if r.get("team") not in (None, "") else None, r["result"]
		))

	add_matches = {t[5] for t in to_insert}
	print("\n================ DRY-RUN SUMMARY ================")
	print(f"  mappable & NEW (to insert): {len(to_insert)} rows across {len(add_matches)} matches (incl. {recovered} recovered via nick-change)")
	print(f"  already present (skipped):  {dup} rows")
	print(f"  UNMAPPABLE (no qc_player_matches (match,nick); skipped): {len(unmappable)} rows")
	if unmappable:
		from collections import Counter
		bad = Counter(r["nick"] for r in unmappable)
		print("    top unmapped nicks:", bad.most_common(8))
		print("    sample unmapped rows:", [(r["bot_match_id"], r["nick"], r["civ"]) for r in unmappable[:5]])
	print("  sample rows that WOULD be inserted (channel,aoe2_mid,aoe2_name,civ,at,bot_mid,user_id,nick,team,result):")
	for t in to_insert[:5]:
		print("    ", t)
	print(f"\n  qc_match_civs match coverage: {len({k[0] for k in existing})} -> "
	      f"{len({k[0] for k in existing} | {str(m) for m in add_matches})} bot matches (of 3131)")

	if not args.apply:
		print("\nDRY-RUN ONLY — no rows written. Re-run with --apply to insert.")
		conn.close()
		return

	# APPLY
	cols = "channel_id, aoe2_match_id, aoe2_name, civ, `at`, bot_match_id, user_id, nick, team, result"
	cur.executemany(f"INSERT INTO qc_match_civs ({cols}) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", to_insert)
	conn.commit()
	print(f"\nAPPLIED: inserted {cur.rowcount} rows. New total = "
	      f"{q('SELECT COUNT(*) c FROM qc_match_civs')[0]['c']}")
	conn.close()


if __name__ == "__main__":
	main()
