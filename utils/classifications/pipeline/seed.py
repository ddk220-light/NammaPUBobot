"""Seed the local ingest_ledger from the Railway match list (READ-ONLY on prod). Run once before
(or alongside) the Downloader/Ingester. Reuses config.cfg DB_URI."""
import argparse
import time

from utils.classifications.pipeline import localdb
from utils.db_helpers import parse_db_uri


def window_query(days):
    since = int(time.time()) - days * 86400
    # civ_picks.replay_match_id since 007_raw_renames, aliased back to the name
    # this pipeline's own SQLite ledger uses (see localdb.py).
    sql = ("SELECT mc.replay_match_id AS aoe2_match_id, MAX(m.reported_at) AS played_at "
           "FROM civ_picks mc JOIN matches m ON m.match_id = mc.bot_match_id "
           "WHERE mc.replay_match_id IS NOT NULL AND m.reported_at >= %s GROUP BY mc.replay_match_id")
    return sql, [since]


def _railway_conn():
    import pymysql

    from importlib.machinery import SourceFileLoader
    cfg = SourceFileLoader("cfg", "config.cfg").load_module()
    kwargs = parse_db_uri(cfg.DB_URI)
    return pymysql.connect(**kwargs, connect_timeout=20)


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
