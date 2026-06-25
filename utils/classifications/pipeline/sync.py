"""Gated sync: push the finished local SQLite cls_* to Railway MySQL in batches, then verify. Only
ever run on explicit go-ahead. Per table: DELETE all, then multi-row INSERT in chunks, then compare
row counts local-vs-remote."""
import argparse
import re

import pymysql

from utils.classifications.pipeline import localdb

TABLES = {
    "cls_results": ["key", "aoe2_match_id", "player_number", "profile_id", "identity", "civ", "team",
                    "winner", "played_at"],
    "cls_result_metrics": ["key", "aoe2_match_id", "player_number", "metric", "value"],
    "cls_classifications": ["key", "title", "trigger_spec", "version", "status", "updated_at"],
    "cls_data_requirements": ["key", "field", "source", "status", "note"],
    "cls_player_totals": ["identity", "games", "wins", "losses"],
}
CHUNK = 1000


def chunked(rows, n):
    return [rows[i:i + n] for i in range(0, len(rows), n)]


def insert_sql(table, cols, nrows):
    one = "(" + ",".join(["%s"] * len(cols)) + ")"
    return "INSERT INTO {} ({}) VALUES {}".format(table, ",".join("`{}`".format(c) for c in cols),
                                                  ",".join([one] * nrows))


def _railway_conn():
    from importlib.machinery import SourceFileLoader
    cfg = SourceFileLoader("cfg", "config.cfg").load_module()
    mm = re.match(r"mysql://([^:]+):([^@]+)@([^:/]+):(\d+)/(.+)", cfg.DB_URI)
    return pymysql.connect(host=mm.group(3), port=int(mm.group(4)), user=mm.group(1),
                           password=mm.group(2), db=mm.group(5), connect_timeout=20, autocommit=False)


def run():
    lconn = localdb.connect()
    rconn = _railway_conn()
    cur = rconn.cursor()
    summary = {}
    for table, cols in TABLES.items():
        rows = [list(r) for r in lconn.execute(
            "SELECT {} FROM {}".format(",".join(cols), table)).fetchall()]
        cur.execute("DELETE FROM `{}`".format(table))
        for chunk in chunked(rows, CHUNK):
            flat = [v for row in chunk for v in row]
            cur.execute(insert_sql(table, cols, len(chunk)), flat)
        summary[table] = len(rows)
    rconn.commit()
    # verify
    ok = True
    for table in TABLES:
        cur.execute("SELECT COUNT(*) FROM `{}`".format(table))
        remote = cur.fetchone()[0]
        local = lconn.execute("SELECT COUNT(*) FROM {}".format(table)).fetchone()[0]
        flag = "OK" if remote == local else "MISMATCH"
        if remote != local:
            ok = False
        print("  {:22} local={} remote={} {}".format(table, local, remote, flag), flush=True)
    rconn.close()
    print("SYNC {}".format("VERIFIED" if ok else "FAILED — counts mismatch"), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    argparse.ArgumentParser().parse_args()
    raise SystemExit(run())
