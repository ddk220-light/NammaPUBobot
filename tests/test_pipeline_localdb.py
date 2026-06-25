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
