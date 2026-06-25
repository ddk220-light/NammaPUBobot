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


def test_player_totals_merge_case_insensitive_nicks(tmp_path):
    # MySQL's identity PK is case-insensitive; the local aggregate must merge 'Thiru'/'thiru' into
    # ONE row so a sync doesn't hit a duplicate-PK error. (regression for the sync 1062 error)
    conn = localdb.connect(str(tmp_path / "ci.db"))
    localdb.ensure_schema(conn)
    localdb.write_match(conn, 1, [], [], [(1, 1, "Thiru", 1)])
    localdb.write_match(conn, 2, [], [], [(2, 1, "thiru", 0)])
    localdb.rebuild_player_totals(conn)
    rows = conn.execute("SELECT identity, games, wins, losses FROM cls_player_totals").fetchall()
    assert len(rows) == 1
    assert (rows[0][1], rows[0][2], rows[0][3]) == (2, 1, 1)   # merged: 2 games, 1 win, 1 loss
