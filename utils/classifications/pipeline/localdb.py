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
