# utils/classifications/pipeline/ingester.py
"""Ingester (process B, sole DB writer): for each ledger match, reconcile the filesystem produced by
the Downloader — `<id>.aoe2record` -> parse + classify + write SQLite; `<id>.unavail` -> mark
unavailable. Streams (re-scans for newly-arrived files) and rebuilds cls_player_totals periodically.
Exits when no ledger row is still 'pending'/'downloaded' AND no download is in progress (an idle
sweep finds nothing new). Run with PYTHONPATH=.replay_scratch."""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".replay_scratch"))

from utils.classifications.pipeline import classify, localdb
from utils.classifications.pipeline.downloader import REPLAY_DIR, _paths
from utils.classifications.registry import REGISTRY

CACHE_DIR = os.path.join(os.path.dirname(localdb.DEFAULT_DB), ".replay_extract_cache")
EXTRACT_VERSION = "v3"


def _cache_path(mid):
    return os.path.join(CACHE_DIR, "{}.{}.json".format(mid, EXTRACT_VERSION))


def _extract(path, mid, resolved, date_map):
    import json
    cp = _cache_path(mid)
    if os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            return json.load(f)
    from utils.replay_quiz.extract import extract_match
    data = extract_match(path, resolved, date_map)
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def _ingest_one(conn, mid, resolved, date_map):
    rec, mark = _paths(mid)
    if os.path.exists(rec):
        try:
            game = _extract(rec, mid, resolved, date_map)
        except Exception as e:
            sv = None
            try:
                from utils.replay_quiz.download import read_save_version
                sv = read_save_version(rec)
            except Exception:
                pass
            localdb.set_status(conn, mid, "parse_failed", save_version=sv, error=str(e)[:200])
            return "failed"
        rr, mr, pr = classify.classify_game(game, mid, localdb.played_at(conn, mid) or 0)
        localdb.write_match(conn, mid, rr, mr, pr)
        localdb.set_status(conn, mid, "ingested")
        return "ingested"
    if os.path.exists(mark):
        localdb.set_status(conn, mid, "unavailable")
        return "unavailable"
    return "waiting"


def run(idle_exits=3, poll=10.0):
    conn = localdb.connect()
    localdb.ensure_schema(conn)
    for c in REGISTRY.values():
        localdb.upsert_classification(conn, c)
    from utils.replay_quiz.extract import load_resolved, load_date_map
    resolved, date_map = load_resolved(), load_date_map()
    idle = 0
    done_marker = os.path.join(REPLAY_DIR, ".done")
    while True:
        pend = localdb.pending_match_ids(conn)
        if not pend:
            break
        progressed = 0
        for mid in pend:
            r = _ingest_one(conn, mid, resolved, date_map)
            if r in ("ingested", "failed", "unavailable"):
                progressed += 1
        localdb.rebuild_player_totals(conn)
        done = conn.execute("SELECT COUNT(*) FROM ingest_ledger WHERE status='ingested'").fetchone()[0]
        fail = conn.execute("SELECT COUNT(*) FROM ingest_ledger WHERE status='parse_failed'").fetchone()[0]
        na = conn.execute("SELECT COUNT(*) FROM ingest_ledger WHERE status='unavailable'").fetchone()[0]
        print("ingester: ingested={} parse_failed={} unavailable={} pending={}".format(
            done, fail, na, len(localdb.pending_match_ids(conn))), flush=True)
        if progressed == 0:
            if os.path.exists(done_marker):   # only count idle once the Downloader has finished
                idle += 1
                if idle >= idle_exits:
                    break
            time.sleep(poll)
        else:
            idle = 0
    print("ingester DONE.", flush=True)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--idle-exits", type=int, default=3)
    ap.add_argument("--poll", type=float, default=10.0)
    a = ap.parse_args()
    raise SystemExit(run(a.idle_exits, a.poll))
