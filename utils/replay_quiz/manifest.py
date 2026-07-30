"""Manifest + replay-cache bookkeeping for the replay-quiz download pipeline.

Two stores, with very different lifetimes:

  data/replay_manifest.csv  git-tracked   — one row per attempted match
  data/replays/*.aoe2record gitignored    — the actual replays, machine-local

Because only the first is committed, a fresh checkout has a full manifest and an
empty cache. Anything deciding "is this match already done?" must therefore ask
BOTH stores; `pending_ids` is that single decision point.

Kept to the standard library on purpose — no `requests`, no `mgz` — so the
resume policy is importable (and unit-testable) without the mgz fork installed.
"""
import csv
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(ROOT, "data", "replays")
MANIFEST = os.path.join(ROOT, "data", "replay_manifest.csv")
FIELDS = ["aoe2_match_id", "profile_id", "bytes", "save_version", "body_ops",
          "minutes", "body_parse_ok", "error"]


def replay_path(gid, cache_dir=None):
    return os.path.join(cache_dir or CACHE_DIR, f"{gid}.aoe2record")


def is_cached(gid, cache_dir=None):
    """True only for a non-empty file: a 0-byte leftover is a failed download."""
    path = replay_path(gid, cache_dir)
    return os.path.exists(path) and os.path.getsize(path) > 0


def is_done(gid, manifest, cache_dir=None):
    row = manifest.get(str(gid))
    if row is None:
        return False
    # Rows read back from CSV hold the string "True"; rows written during the
    # current run still hold a real bool.
    return str(row.get("body_parse_ok")) == "True" and is_cached(gid, cache_dir)


def pending_ids(gids, manifest, cache_dir=None):
    """The subset of `gids` still to attempt, in the order given."""
    return [g for g in gids if not is_done(g, manifest, cache_dir)]


def load_manifest(path=None):
    rows = {}
    path = path or MANIFEST
    if os.path.exists(path):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                rows[r["aoe2_match_id"]] = r
    return rows


def write_manifest(rows, path=None):
    with open(path or MANIFEST, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for k in sorted(rows, key=lambda x: -int(x)):
            w.writerow({c: rows[k].get(c, "") for c in FIELDS})
