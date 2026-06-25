"""Seed the local ingest_ledger from the Railway match list (READ-ONLY on prod). Run once before
(or alongside) the Downloader/Ingester. Reuses config.cfg DB_URI."""
import argparse
import re
import time

import pymysql

from utils.classifications.pipeline import localdb


def window_query(days):
    since = int(time.time()) - days * 86400
    sql = ("SELECT mc.aoe2_match_id AS aoe2_match_id, MAX(m.at) AS played_at "
           "FROM qc_match_civs mc JOIN qc_matches m ON m.match_id = mc.bot_match_id "
           "WHERE mc.aoe2_match_id IS NOT NULL AND m.at >= %s GROUP BY mc.aoe2_match_id")
    return sql, [since]


def _railway_conn():
    from importlib.machinery import SourceFileLoader
    cfg = SourceFileLoader("cfg", "config.cfg").load_module()
    mm = re.match(r"mysql://([^:]+):([^@]+)@([^:/]+):(\d+)/(.+)", cfg.DB_URI)
    return pymysql.connect(host=mm.group(3), port=int(mm.group(4)), user=mm.group(1),
                           password=mm.group(2), db=mm.group(5), connect_timeout=20)


def run(days=365):
    rc = _railway_conn()
    sql, args = window_query(days)
    with rc.cursor() as cur:
        cur.execute(sql, args)
        matches = [(r[0], r[1]) for r in cur.fetchall() if r[0] is not None]
    rc.close()
    conn = localdb.connect()
    localdb.ensure_schema(conn)
    localdb.seed_ledger(conn, matches)
    pending = len(localdb.pending_match_ids(conn))
    print("seeded {} matches from last {}d ({} pending)".format(len(matches), days, pending), flush=True)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    raise SystemExit(run(ap.parse_args().days))
