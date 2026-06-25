from utils.classifications.pipeline import sync


def test_chunk_splits_evenly():
    assert sync.chunked([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert sync.chunked([], 2) == []


def test_multirow_insert_sql():
    sql = sync.insert_sql("cls_player_totals", ["identity", "games"], 3)
    assert sql.count("(%s,%s)") == 3 and sql.startswith("INSERT INTO cls_player_totals")
