#!/usr/bin/env python3
"""READ-ONLY diagnostic of the bot's live MySQL data state & freshness.

Runs ONLY SELECT / SHOW queries. Reads DB_URI from config.cfg (gitignored).
Never prints credentials. Usage:  python utils/db_state.py
"""
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse, unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db_helpers import load_config  # noqa: E402

try:
	import pymysql
	from pymysql.cursors import DictCursor
except ImportError:
	sys.exit("pymysql not installed. Run: python -m pip install pymysql")


def main():
	cfg = load_config()
	if cfg is None:
		sys.exit("Could not load config.cfg — create it with DB_URI set (see chat instructions).")
	db_uri = getattr(cfg, "DB_URI", "")
	if not db_uri:
		sys.exit("DB_URI is empty in config.cfg.")

	u = urlparse(db_uri)
	host, port = u.hostname or "", u.port or 3306
	user, password, dbname = unquote(u.username or ""), unquote(u.password or ""), (u.path or "").lstrip("/")
	print(f"Connecting: db='{dbname}' host='{host[:10]}...' port={port} user='{user[:3]}...' (password hidden)")
	if host.endswith(".railway.internal"):
		print("WARNING: that's the PRIVATE host — it won't resolve from your PC. Use the PUBLIC proxy URL.")
	try:
		conn = pymysql.connect(host=host, user=user, password=password, db=dbname,
		                       port=port, cursorclass=DictCursor, connect_timeout=20)
	except Exception as e:
		sys.exit(f"Connection failed: {type(e).__name__}: {e}\n"
		         "If this is a private/internal host, enable public networking on the MySQL service and use MYSQL_PUBLIC_URL.")

	cur = conn.cursor()

	def q(sql, p=None):
		cur.execute(sql, p or [])
		return cur.fetchall()

	def scalar(sql, p=None):
		cur.execute(sql, p or [])
		r = cur.fetchone()
		return list(r.values())[0] if r else None

	tables = [list(r.values())[0] for r in q("SHOW TABLES")]
	print(f"\n{len(tables)} tables total. Relevant ones:")
	for t in ['qc_matches', 'qc_player_matches', 'qc_players', 'qc_match_civs', 'qc_rating_history', 'qc_lobbies']:
		print(f"  {t:22s} rows={scalar(f'SELECT COUNT(*) FROM {t}')}" if t in tables else f"  {t:22s} (absent)")

	def epoch(v):
		try:
			return datetime.fromtimestamp(int(v), timezone.utc).isoformat()
		except Exception:
			return str(v)

	if 'qc_matches' in tables:
		mcols = [c['Field'] for c in q("SHOW COLUMNS FROM qc_matches")]
		r = q("SELECT MIN(match_id) mn, MAX(match_id) mx, MAX(`at`) amax, COUNT(DISTINCT channel_id) ch FROM qc_matches")[0]
		print(f"\nqc_matches: match_id {r['mn']}..{r['mx']} | latest at={r['amax']} ({epoch(r['amax'])} UTC) | channels={r['ch']}")
		if 'ranked' in mcols:
			print(f"qc_matches: ranked={scalar('SELECT COUNT(*) FROM qc_matches WHERE ranked=1')} / total={scalar('SELECT COUNT(*) FROM qc_matches')}")

	if 'qc_match_civs' in tables:
		cols = [c['Field'] for c in q("SHOW COLUMNS FROM qc_match_civs")]
		print(f"\nqc_match_civs columns: {cols}")
		rows = scalar("SELECT COUNT(*) FROM qc_match_civs")
		mcol = 'bot_match_id' if 'bot_match_id' in cols else ('aoe2_match_id' if 'aoe2_match_id' in cols else None)
		if mcol:
			print(f"qc_match_civs: {rows} rows | {scalar(f'SELECT COUNT(DISTINCT {mcol}) FROM qc_match_civs')} distinct {mcol} "
			      f"({scalar(f'SELECT MIN({mcol}) FROM qc_match_civs')}..{scalar(f'SELECT MAX({mcol}) FROM qc_match_civs')})")
		for dc in ('date', 'created_at', 'at', 'recorded_at'):
			if dc in cols:
				print(f"qc_match_civs: {dc} {scalar(f'SELECT MIN(`{dc}`) FROM qc_match_civs')} .. {scalar(f'SELECT MAX(`{dc}`) FROM qc_match_civs')}")
				break
		if mcol == 'bot_match_id' and 'qc_matches' in tables:
			covered = scalar("SELECT COUNT(DISTINCT m.match_id) FROM qc_matches m JOIN qc_match_civs c ON c.bot_match_id=m.match_id")
			print(f"qc_match_civs covers {covered} / {scalar('SELECT COUNT(*) FROM qc_matches')} qc_matches "
			      f"| newest match WITHOUT civs: {scalar('SELECT MAX(match_id) FROM qc_matches m WHERE NOT EXISTS (SELECT 1 FROM qc_match_civs c WHERE c.bot_match_id=m.match_id)')}")

	conn.close()
	print("\n(read-only; connection closed)")


if __name__ == "__main__":
	main()
