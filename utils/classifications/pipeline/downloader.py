# utils/classifications/pipeline/downloader.py
"""Downloader (process A): fetch missing replays for ledger ids. Writes replay files and, for
genuinely-unavailable matches, a sibling `<id>.unavail` marker. NEVER writes the DB — the Ingester
reconciles files+markers into the ledger. Resumable: skips ids that already have a file or marker."""
import argparse
import os
import time

from utils.classifications.pipeline import localdb
from utils.replay_quiz import download as dl

REPLAY_DIR = os.path.join(os.path.dirname(localdb.DEFAULT_DB), "replays")


def _paths(mid):
    base = os.path.join(REPLAY_DIR, str(mid))
    return base + ".aoe2record", base + ".unavail"


def run(space=4.0):
    os.makedirs(REPLAY_DIR, exist_ok=True)
    conn = localdb.connect()
    localdb.ensure_schema(conn)
    ids = localdb.pending_match_ids(conn)          # read-only snapshot, newest-first
    conn.close()
    todo = [m for m in ids if not any(os.path.exists(p) for p in _paths(m))]
    print("downloader: {} pending, {} to fetch".format(len(ids), len(todo)), flush=True)
    got = unavail = 0
    for i, mid in enumerate(todo, 1):
        rec, mark = _paths(mid)
        path = None
        try:
            for pid in dl.resolve_profile_ids(mid)[:4]:
                p, _status = dl.download_replay(mid, pid)
                if p and os.path.exists(p):
                    # download_replay writes <id>.aoe2record under REPLAY_DIR already
                    path = p
                    break
        except Exception:
            path = None
        if path:
            got += 1
        else:
            open(mark, "w").close()             # mark unavailable for the Ingester
            unavail += 1
        if i % 25 == 0:
            print("  downloader [{}/{}] got={} unavail={}".format(i, len(todo), got, unavail), flush=True)
        time.sleep(space)                       # pace every attempt (aoe.ms rate-limits hard)
    print("downloader DONE: got={} unavail={}".format(got, unavail), flush=True)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", type=float, default=4.0)
    raise SystemExit(run(ap.parse_args().space))
