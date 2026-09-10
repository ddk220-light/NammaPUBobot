#!/usr/bin/env python3
"""Census or purge the five bulky replay-detail tables.

Dry-run is the default.  Applying is intentionally awkward: replay ingestion
and replay dashboard reads must both be explicitly disabled in the environment,
the operator must supply a readable gzip-tested SQL backup, and the confirmation
phrase must match.  The compact match/player spine and every derived summary are
left untouched.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import os
from pathlib import Path
from urllib.parse import unquote, urlparse


TARGET_TABLES = (
	"replay_events",
	"replay_techs",
	"replay_buildings",
	"replay_units",
	"replay_apm",
)
CONFIRMATION = "PURGE_REPLAY_DETAIL"


def _false_env(name: str) -> bool:
	value = os.environ.get(name)
	return value is not None and value.strip().lower() in {"0", "false", "no", "off"}


def connection_settings() -> dict:
	uri = os.environ.get("DB_URI") or os.environ.get("MYSQL_URL")
	if uri:
		parsed = urlparse(uri)
		if parsed.scheme not in {"mysql", "mysql+pymysql"}:
			raise SystemExit("DB_URI/MYSQL_URL must use the mysql:// scheme")
		return {
			"host": parsed.hostname,
			"port": parsed.port or 3306,
			"user": unquote(parsed.username or ""),
			"password": unquote(parsed.password or ""),
			"database": unquote(parsed.path.lstrip("/")),
		}
	return {
		"host": os.environ.get("MYSQLHOST"),
		"port": int(os.environ.get("MYSQLPORT", "3306")),
		"user": os.environ.get("MYSQLUSER"),
		"password": os.environ.get("MYSQLPASSWORD", ""),
		"database": os.environ.get("MYSQLDATABASE"),
	}


def verify_backup(path: Path) -> str:
	if not path.is_file() or path.suffix != ".gz":
		raise SystemExit("--backup must name an existing .sql.gz dump outside the database volume")
	digest = hashlib.sha256()
	try:
		with path.open("rb") as raw:
			for chunk in iter(lambda: raw.read(1024 * 1024), b""):
				digest.update(chunk)
		# Reading through EOF verifies the gzip trailer/CRC, not merely its header.
		with gzip.open(path, "rb") as stream:
			for _chunk in iter(lambda: stream.read(1024 * 1024), b""):
				pass
	except (OSError, EOFError) as exc:
		raise SystemExit(f"backup gzip integrity check failed: {exc}") from exc
	return digest.hexdigest()


def census(conn) -> list[dict]:
	holes = ",".join(["%s"] * len(TARGET_TABLES))
	with conn.cursor() as cur:
		cur.execute(
			"SELECT table_name, table_rows, data_length, index_length "
			"FROM information_schema.tables "
			"WHERE table_schema=DATABASE() AND table_name IN (" + holes + ") "
			"ORDER BY data_length+index_length DESC",
			TARGET_TABLES,
		)
		return list(cur.fetchall() or ())


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--apply", action="store_true", help="actually truncate the target tables")
	parser.add_argument("--backup", type=Path, help="verified .sql.gz backup required with --apply")
	parser.add_argument("--confirm", help=f"required phrase with --apply: {CONFIRMATION}")
	args = parser.parse_args()

	settings = connection_settings()
	missing = [key for key in ("host", "user", "database") if not settings.get(key)]
	if missing:
		raise SystemExit("missing database settings: " + ", ".join(missing))

	if args.apply:
		if args.confirm != CONFIRMATION:
			raise SystemExit(f"--apply requires --confirm {CONFIRMATION}")
		if args.backup is None:
			raise SystemExit("--apply requires --backup /path/to/dump.sql.gz")
		if not _false_env("REPLAY_INGEST_ENABLED") or not _false_env("REPLAY_DASHBOARD_ENABLED"):
			raise SystemExit(
				"refusing: set REPLAY_INGEST_ENABLED=False and "
				"REPLAY_DASHBOARD_ENABLED=False explicitly")
		sha256 = verify_backup(args.backup)
		print(f"Verified backup {args.backup} sha256={sha256}")

	# PyMySQL is installed transitively by the production aiomysql dependency.
	# Keep the import here so --help and the safety helpers remain usable in the
	# deliberately dependency-light test environment.
	import pymysql

	conn = pymysql.connect(
		**settings, charset="utf8mb4", autocommit=True,
		cursorclass=pymysql.cursors.DictCursor)
	try:
		rows = census(conn)
		total = 0
		for row in rows:
			bytes_used = int(row.get("data_length") or 0) + int(row.get("index_length") or 0)
			total += bytes_used
			print(
				f"{row['table_name']}: estimated_rows={int(row.get('table_rows') or 0)} "
				f"allocated_mib={bytes_used / 1024 / 1024:.2f}")
		print(f"Total replay detail allocation: {total / 1024 / 1024:.2f} MiB")

		if not args.apply:
			print("DRY RUN: nothing changed")
			return 0

		for table in TARGET_TABLES:
			with conn.cursor() as cur:
				cur.execute(f"TRUNCATE TABLE `{table}`")
			print(f"Purged {table}")
		print("Replay detail purge complete; compact spine and summaries were preserved.")
		return 0
	finally:
		conn.close()


if __name__ == "__main__":
	raise SystemExit(main())
