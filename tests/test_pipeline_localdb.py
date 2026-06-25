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
    assert localdb.pending_match_ids(conn) == [103, 102, 101]
    localdb.set_status(conn, 102, "unavailable")
    localdb.set_status(conn, 103, "parse_failed", save_version=37.0, error="bad")
    assert localdb.pending_match_ids(conn) == [101]
    r = conn.execute("SELECT status, save_version, error FROM ingest_ledger WHERE aoe2_match_id=103").fetchone()
    assert r == ("parse_failed", 37.0, "bad")
