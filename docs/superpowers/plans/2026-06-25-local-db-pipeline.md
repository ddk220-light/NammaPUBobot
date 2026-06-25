# Local-DB-First Classification Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build/maintain the `cls_*` classification dataset on a local SQLite DB via a parallel download→mgz-parse→classify pipeline over the last-365-days replays, then sync to Railway MySQL only on go-ahead via a fast batched verified write.

**Architecture:** Local SQLite (`data/analysis.db`) is the working source of truth. Two filesystem-coordinated processes run in parallel — a Downloader (produces replay files) and an Ingester (the sole DB writer: parses, classifies, writes SQLite). A gated `sync.py` pushes the finished local `cls_*` to Railway in batches and verifies counts.

**Tech Stack:** Python 3.11, `sqlite3` (stdlib, WAL mode), `pymysql` (Railway read/write), vendored `mgz` fork (parse, via `PYTHONPATH=.replay_scratch`), existing `utils.classifications.registry`/`shape` and `utils.replay_quiz.extract`/`download`.

**Conventions:** `utils/` uses **4-space** indent. Run tests with `pytest tests/ -q`. Pure tests need no DB/network; SQLite tests use a temp file. Reuse — do not reimplement — `registry`, `shape`, `extract`, `download`.

---

### Task 1: SQLite schema + connection (`localdb.py` core)

**Files:**
- Create: `utils/classifications/pipeline/__init__.py` (empty)
- Create: `utils/classifications/pipeline/localdb.py`
- Test: `tests/test_pipeline_localdb.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_localdb.py
from utils.classifications.pipeline import localdb


def test_ensure_schema_creates_all_tables(tmp_path):
    conn = localdb.connect(str(tmp_path / "a.db"))
    localdb.ensure_schema(conn)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"cls_results", "cls_result_metrics", "cls_classifications",
            "cls_data_requirements", "cls_player_totals", "ingest_ledger",
            "ingest_players"} <= names


def test_connect_is_wal(tmp_path):
    conn = localdb.connect(str(tmp_path / "a.db"))
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline_localdb.py -q`
Expected: FAIL (ModuleNotFoundError: utils.classifications.pipeline.localdb)

- [ ] **Step 3: Write minimal implementation**

```python
# utils/classifications/pipeline/localdb.py
"""SQLite data layer for the local-first classification pipeline: the working copy of the cls_*
tables plus an ingest ledger and a per-player-game record (ingest_players, the source of
cls_player_totals). Opened WAL so the Downloader can read while the Ingester writes."""
import os
import sqlite3
import time

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "data", "analysis.db")

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS cls_results (
        key TEXT NOT NULL, aoe2_match_id INTEGER NOT NULL, player_number INTEGER NOT NULL,
        profile_id INTEGER, identity TEXT, civ TEXT, team TEXT, winner INTEGER, played_at INTEGER,
        PRIMARY KEY (key, aoe2_match_id, player_number))""",
    """CREATE TABLE IF NOT EXISTS cls_result_metrics (
        key TEXT NOT NULL, aoe2_match_id INTEGER NOT NULL, player_number INTEGER NOT NULL,
        metric TEXT NOT NULL, value REAL,
        PRIMARY KEY (key, aoe2_match_id, player_number, metric))""",
    """CREATE TABLE IF NOT EXISTS cls_classifications (
        key TEXT PRIMARY KEY, title TEXT, trigger_spec TEXT, version INTEGER, status TEXT,
        updated_at INTEGER)""",
    """CREATE TABLE IF NOT EXISTS cls_data_requirements (
        key TEXT NOT NULL, field TEXT NOT NULL, source TEXT, status TEXT, note TEXT,
        PRIMARY KEY (key, field))""",
    """CREATE TABLE IF NOT EXISTS cls_player_totals (
        identity TEXT PRIMARY KEY, games INTEGER, wins INTEGER, losses INTEGER)""",
    """CREATE TABLE IF NOT EXISTS ingest_ledger (
        aoe2_match_id INTEGER PRIMARY KEY, played_at INTEGER, status TEXT NOT NULL,
        save_version REAL, error TEXT, ingested_at INTEGER)""",
    # one row per ingested player-game (categorized or not) -> the source of cls_player_totals
    """CREATE TABLE IF NOT EXISTS ingest_players (
        aoe2_match_id INTEGER NOT NULL, player_number INTEGER NOT NULL, identity TEXT,
        winner INTEGER, PRIMARY KEY (aoe2_match_id, player_number))""",
]


def connect(path=DEFAULT_DB):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def ensure_schema(conn):
    for ddl in _SCHEMA:
        conn.execute(ddl)
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pipeline_localdb.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add utils/classifications/pipeline/__init__.py utils/classifications/pipeline/localdb.py tests/test_pipeline_localdb.py
git commit -m "feat(pipeline): local SQLite schema + WAL connection"
```

---

### Task 2: Ingest-ledger operations (`localdb.py`)

**Files:**
- Modify: `utils/classifications/pipeline/localdb.py` (append functions)
- Test: `tests/test_pipeline_localdb.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_pipeline_localdb.py
def _seeded(tmp_path):
    conn = localdb.connect(str(tmp_path / "l.db"))
    localdb.ensure_schema(conn)
    localdb.seed_ledger(conn, [(101, 1700), (102, 1800), (103, 1900)])
    return conn


def test_seed_is_idempotent_and_pending(tmp_path):
    conn = _seeded(tmp_path)
    localdb.seed_ledger(conn, [(101, 1700), (104, 2000)])  # re-seed: 101 kept, 104 added
    rows = dict(conn.execute("SELECT aoe2_match_id, status FROM ingest_ledger").fetchall())
    assert rows == {101: "pending", 102: "pending", 103: "pending", 104: "pending"}


def test_pending_ids_and_status_setters(tmp_path):
    conn = _seeded(tmp_path)
    assert localdb.pending_match_ids(conn) == [101, 102, 103]
    localdb.set_status(conn, 102, "unavailable")
    localdb.set_status(conn, 103, "parse_failed", save_version=37.0, error="bad")
    assert localdb.pending_match_ids(conn) == [101]
    r = conn.execute("SELECT status, save_version, error FROM ingest_ledger WHERE aoe2_match_id=103").fetchone()
    assert r == ("parse_failed", 37.0, "bad")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline_localdb.py -q`
Expected: FAIL (AttributeError: module has no attribute 'seed_ledger')

- [ ] **Step 3: Write minimal implementation**

```python
# append to utils/classifications/pipeline/localdb.py
def seed_ledger(conn, matches):
    """matches: iterable of (aoe2_match_id, played_at). New ids -> status 'pending'; existing
    ids are left untouched (idempotent re-seed)."""
    conn.executemany(
        "INSERT OR IGNORE INTO ingest_ledger (aoe2_match_id, played_at, status) "
        "VALUES (?, ?, 'pending')", [(int(m), int(p or 0)) for m, p in matches])
    conn.commit()


def pending_match_ids(conn):
    """Ledger ids still awaiting a terminal state (newest-first by played_at)."""
    return [r[0] for r in conn.execute(
        "SELECT aoe2_match_id FROM ingest_ledger WHERE status IN ('pending','downloaded') "
        "ORDER BY played_at DESC").fetchall()]


def set_status(conn, mid, status, save_version=None, error=None):
    conn.execute(
        "UPDATE ingest_ledger SET status=?, save_version=?, error=?, ingested_at=? "
        "WHERE aoe2_match_id=?", [status, save_version, error, int(time.time()), int(mid)])
    conn.commit()


def played_at(conn, mid):
    r = conn.execute("SELECT played_at FROM ingest_ledger WHERE aoe2_match_id=?", [int(mid)]).fetchone()
    return r[0] if r else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pipeline_localdb.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add utils/classifications/pipeline/localdb.py tests/test_pipeline_localdb.py
git commit -m "feat(pipeline): ingest-ledger seed + status ops"
```

---

### Task 3: cls_* + ingest_players writer and player-totals compute (`localdb.py`)

**Files:**
- Modify: `utils/classifications/pipeline/localdb.py` (append)
- Test: `tests/test_pipeline_localdb.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_pipeline_localdb.py
def test_write_match_then_player_totals(tmp_path):
    conn = localdb.connect(str(tmp_path / "w.db"))
    localdb.ensure_schema(conn)
    results = [{"key": "archer_rush", "aoe2_match_id": 9, "player_number": 1, "profile_id": 5,
                "identity": "Al", "civ": "Mayans", "team": "1", "winner": 1, "played_at": 100}]
    metrics = [{"key": "archer_rush", "aoe2_match_id": 9, "player_number": 1,
                "metric": "archers_pre_castle", "value": 7.0}]
    players = [(9, 1, "Al", 1), (9, 2, "Bo", 0)]              # all player-games this match
    localdb.write_match(conn, 9, results, metrics, players)
    assert conn.execute("SELECT COUNT(*) FROM cls_results").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM ingest_players").fetchone()[0] == 2
    # re-writing the same match replaces, never duplicates
    localdb.write_match(conn, 9, results, metrics, players)
    assert conn.execute("SELECT COUNT(*) FROM cls_results").fetchone()[0] == 1
    localdb.rebuild_player_totals(conn)
    assert dict(conn.execute("SELECT identity, games FROM cls_player_totals").fetchall()) == {"Al": 1, "Bo": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline_localdb.py -q`
Expected: FAIL (AttributeError: 'write_match')

- [ ] **Step 3: Write minimal implementation**

```python
# append to utils/classifications/pipeline/localdb.py
def write_match(conn, mid, result_rows, metric_rows, player_rows):
    """Replace ALL stored data for one match: delete its cls_results / cls_result_metrics /
    ingest_players, then insert fresh. result_rows / metric_rows are shape.* dicts; player_rows are
    (aoe2_match_id, player_number, identity, winner) tuples for EVERY player-game in the match."""
    mid = int(mid)
    conn.execute("DELETE FROM cls_results WHERE aoe2_match_id=?", [mid])
    conn.execute("DELETE FROM cls_result_metrics WHERE aoe2_match_id=?", [mid])
    conn.execute("DELETE FROM ingest_players WHERE aoe2_match_id=?", [mid])
    if result_rows:
        cols = ["key", "aoe2_match_id", "player_number", "profile_id", "identity", "civ", "team",
                "winner", "played_at"]
        conn.executemany(
            "INSERT INTO cls_results ({}) VALUES ({})".format(",".join(cols), ",".join(["?"] * len(cols))),
            [[r.get(c) for c in cols] for r in result_rows])
    if metric_rows:
        cols = ["key", "aoe2_match_id", "player_number", "metric", "value"]
        conn.executemany(
            "INSERT INTO cls_result_metrics ({}) VALUES ({})".format(",".join(cols), ",".join(["?"] * len(cols))),
            [[r.get(c) for c in cols] for r in metric_rows])
    conn.executemany(
        "INSERT INTO ingest_players (aoe2_match_id, player_number, identity, winner) VALUES (?,?,?,?)",
        player_rows)
    conn.commit()


def rebuild_player_totals(conn):
    """cls_player_totals = aggregate of ingest_players (every scanned player-game)."""
    conn.execute("DELETE FROM cls_player_totals")
    conn.execute(
        "INSERT INTO cls_player_totals (identity, games, wins, losses) "
        "SELECT identity, COUNT(*), SUM(winner=1), SUM(winner=0) FROM ingest_players "
        "GROUP BY identity")
    conn.commit()


def upsert_classification(conn, c):
    """Registry row + data-requirements ledger for one Classification (mirrors the MySQL side)."""
    conn.execute(
        "INSERT OR REPLACE INTO cls_classifications (key, title, trigger_spec, version, status, updated_at) "
        "VALUES (?,?,?,?,?,?)", [c.key, c.title, c.trigger_spec, c.version, c.status, int(time.time())])
    conn.execute("DELETE FROM cls_data_requirements WHERE key=?", [c.key])
    conn.executemany(
        "INSERT INTO cls_data_requirements (key, field, source, status, note) VALUES (?,?,?,?,?)",
        [(c.key, r["field"], r["source"], r["status"], r["note"]) for r in c.requirements])
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pipeline_localdb.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add utils/classifications/pipeline/localdb.py tests/test_pipeline_localdb.py
git commit -m "feat(pipeline): cls_* + ingest_players writer, player-totals rebuild"
```

---

### Task 4: `classify_game` — parsed game → rows (`classify.py`)

**Files:**
- Create: `utils/classifications/pipeline/classify.py`
- Test: `tests/test_pipeline_classify.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_classify.py
from utils.classifications.pipeline import classify


def test_classify_game_emits_rows_and_players():
    # an archer rush (player 1) in a 2-player game
    game = {"players": [
        {"player_number": 1, "feudal_s": 600, "castle_s": 1200, "winner": True,
         "profile_id": 5, "identity": "Al", "civ": "Mayans", "team": "1"},
        {"player_number": 2, "feudal_s": 600, "castle_s": 700, "winner": False,
         "profile_id": 6, "identity": "Bo", "civ": "Franks", "team": "2"}],
        "techs": [], "events": [
            {"player_number": 1, "category": "archer_line", "name": "Archer", "amount": 5, "t_s": 700}]}
    result_rows, metric_rows, player_rows = classify.classify_game(game, 999, played_at=123)
    assert any(r["key"] == "archer_rush" and r["player_number"] == 1 for r in result_rows)
    assert player_rows == [(999, 1, "Al", 1), (999, 2, "Bo", 0)]   # ALL players, winner as 1/0/None
    assert all(r["aoe2_match_id"] == 999 for r in result_rows)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline_classify.py -q`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Write minimal implementation**

```python
# utils/classifications/pipeline/classify.py
"""Turn a parsed game (extract_match output) into rows for the local DB. Reuses the registry +
shape exactly as the runner does, so local and prod classification logic stay identical."""
from utils.classifications import shape
from utils.classifications.registry import REGISTRY


def _winner_int(w):
    return 1 if w in (1, True) else 0 if w in (0, False) else None


def classify_game(game, mid, played_at):
    """-> (result_rows, metric_rows, player_rows). player_rows = (mid, pnum, identity, winner) for
    EVERY player-game (the cls_player_totals source); result/metric rows only for matched triggers."""
    result_rows, metric_rows = [], []
    player_rows = []
    for p in game.get("players", []):
        pnum = p["player_number"]
        player_rows.append((int(mid), pnum, p.get("identity") or "?", _winner_int(p.get("winner"))))
    for key, c in REGISTRY.items():
        for p in game.get("players", []):
            pnum = p["player_number"]
            if not c.trigger(game, pnum):
                continue
            result_rows.append(shape.result_row(key, mid, p, played_at))
            metric_rows.extend(shape.metric_rows(key, mid, pnum, c.factors(game, pnum)))
    return result_rows, metric_rows, player_rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pipeline_classify.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add utils/classifications/pipeline/classify.py tests/test_pipeline_classify.py
git commit -m "feat(pipeline): classify_game (reuses registry + shape)"
```

---

### Task 5: Seed CLI — Railway match list → ledger (`seed.py`)

**Files:**
- Create: `utils/classifications/pipeline/seed.py`
- Test: `tests/test_pipeline_seed.py`

- [ ] **Step 1: Write the failing test** (test the pure window-SQL builder; the network call is exercised by the real run)

```python
# tests/test_pipeline_seed.py
import time
from utils.classifications.pipeline import seed


def test_window_sql_uses_since_cutoff():
    sql, args = seed.window_query(days=365)
    assert "qc_match_civs" in sql and "qc_matches" in sql and "GROUP BY" in sql
    assert args[0] <= int(time.time()) - 364 * 86400   # ~365d cutoff
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline_seed.py -q`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Write minimal implementation**

```python
# utils/classifications/pipeline/seed.py
"""Seed the local ingest_ledger from the Railway match list (READ-ONLY on prod). Run once before
(or alongside) the Downloader/Ingester. Reuses config.cfg DB_URI."""
import argparse
import re
import sys
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pipeline_seed.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add utils/classifications/pipeline/seed.py tests/test_pipeline_seed.py
git commit -m "feat(pipeline): seed ledger from Railway match list (read-only)"
```

---

### Task 6: Downloader CLI (`downloader.py`)

**Files:**
- Create: `utils/classifications/pipeline/downloader.py`

This is an I/O loop (no unit test; validated by the real run in Task 9). It reads the ledger READ-ONLY for the id list, writes only files + `.unavail` markers under `data/replays/`.

- [ ] **Step 1: Write the implementation**

```python
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
```

> NOTE: `dl.download_replay` writes to `utils/replay_quiz/download.CACHE_DIR` (= `data/replays/`). Confirm `REPLAY_DIR` above equals that dir; both resolve to `<repo>/data/replays`. If they ever diverge, prefer importing `dl.CACHE_DIR`.

- [ ] **Step 2: Sanity-run against 2 ids** (manual, no assertion — just confirm no crash + files/markers appear)

Run: `PYTHONPATH=.replay_scratch python -c "from utils.classifications.pipeline import downloader; import utils.classifications.pipeline.localdb as l; c=l.connect(); l.ensure_schema(c); l.seed_ledger(c,[(487119341,1),(402545695,1)]); c.close(); downloader.run(space=2)"`
Expected: prints `downloader DONE: got=… unavail=…`; `data/replays/487119341.aoe2record` exists (recent id) and `data/replays/402545695.unavail` exists (old id).

- [ ] **Step 3: Commit**

```bash
git add utils/classifications/pipeline/downloader.py
git commit -m "feat(pipeline): downloader process (files + .unavail markers, paced)"
```

---

### Task 7: Ingester CLI (`ingester.py`)

**Files:**
- Create: `utils/classifications/pipeline/ingester.py`

I/O loop, sole DB writer. Reconciles files/markers → parse → classify → SQLite → ledger; rebuilds player_totals; exits when the Downloader is done and nothing is left.

- [ ] **Step 1: Write the implementation**

```python
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
            idle += 1
            if idle >= idle_exits:      # nothing new across N sweeps -> downloader is done
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
```

- [ ] **Step 2: Sanity-run** (after Task 6's sanity-run left a real + an unavail file)

Run: `PYTHONPATH=.replay_scratch python -m utils.classifications.pipeline.ingester --idle-exits 1`
Expected: prints `ingester: ingested=1 … unavailable=1 …` then `ingester DONE.`; `data/analysis.db` `cls_results` has rows for the parsed match.

- [ ] **Step 3: Commit**

```bash
git add utils/classifications/pipeline/ingester.py
git commit -m "feat(pipeline): ingester process (parse+classify+write SQLite, sole writer)"
```

---

### Task 8: Batched, verified sync to Railway (`sync.py`)

**Files:**
- Create: `utils/classifications/pipeline/sync.py`
- Test: `tests/test_pipeline_sync.py`

- [ ] **Step 1: Write the failing test** (the pure chunker; the MySQL write is exercised on go-ahead)

```python
# tests/test_pipeline_sync.py
from utils.classifications.pipeline import sync


def test_chunk_splits_evenly():
    assert sync.chunked([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert sync.chunked([], 2) == []


def test_multirow_insert_sql():
    sql = sync.insert_sql("cls_player_totals", ["identity", "games"], 3)
    assert sql.count("(%s,%s)") == 3 and sql.startswith("INSERT INTO cls_player_totals")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline_sync.py -q`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Write minimal implementation**

```python
# utils/classifications/pipeline/sync.py
"""Gated sync: push the finished local SQLite cls_* to Railway MySQL in batches, then verify. Only
ever run on explicit go-ahead. Per table: DELETE all, then multi-row INSERT in chunks, then compare
row counts local-vs-remote."""
import argparse
import re
import sys

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pipeline_sync.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add utils/classifications/pipeline/sync.py tests/test_pipeline_sync.py
git commit -m "feat(pipeline): gated batched verified sync to Railway"
```

---

### Task 9: Orchestrate the real run + full-suite check

**Files:** none new (operational task)

- [ ] **Step 1: Full test suite is green**

Run: `pytest tests/ -q` and `ruff check utils/classifications/pipeline`
Expected: all pass, ruff clean.

- [ ] **Step 2: Seed the ledger** (READ-ONLY on prod)

Run: `python -m utils.classifications.pipeline.seed --days 365`
Expected: `seeded ~1092 matches from last 365d (~735 pending)`. **Milestone report: seed done.**

- [ ] **Step 3: Launch Downloader and Ingester in parallel** (two background processes)

Run (A): `PYTHONPATH=.replay_scratch python -m utils.classifications.pipeline.downloader --space 4`
Run (B): `PYTHONPATH=.replay_scratch python -m utils.classifications.pipeline.ingester --idle-exits 3 --poll 15`
Expected: disk `data/replays/` climbs; `data/analysis.db` `cls_results` distinct matches climb. **Milestone reports: download X/Y, ingest X/Y + mgz-failures by `save_version` (query `SELECT save_version, COUNT(*) FROM ingest_ledger WHERE status='parse_failed' GROUP BY save_version`).**

- [ ] **Step 4: On completion, report final local state** (do NOT sync yet)

Run: `python -c "import utils.classifications.pipeline.localdb as l; c=l.connect(); print('matches', c.execute('SELECT COUNT(DISTINCT aoe2_match_id) FROM cls_results').fetchone()[0]); print('players', c.execute('SELECT COUNT(*) FROM cls_player_totals').fetchone()[0]); print(c.execute('SELECT status, COUNT(*) FROM ingest_ledger GROUP BY status').fetchall())"`
Expected: matches/players up from the 352-game baseline; ledger statuses tally. **Milestone: ingest complete — await go-ahead.**

- [ ] **Step 5: ONLY after explicit go-ahead — sync to Railway**

Run: `python -m utils.classifications.pipeline.sync`
Expected: per-table `local=… remote=… OK` and `SYNC VERIFIED`. **Milestone: prod synced + verified.**

---

## Notes for the implementer

- `data/analysis.db`, `data/replays/`, `data/.replay_extract_cache/` are under the gitignored `data/` — never commit replays or the DB.
- The bot's read path is unchanged; the synced schema matches `utils/classifications/schema.py`.
- Keep prod READ-ONLY in every step except Task 9 Step 5.
