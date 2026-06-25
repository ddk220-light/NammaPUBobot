import time
from utils.classifications.pipeline import seed


def test_window_sql_uses_since_cutoff():
    sql, args = seed.window_query(days=365)
    assert "qc_match_civs" in sql and "qc_matches" in sql and "GROUP BY" in sql
    assert args[0] <= int(time.time()) - 364 * 86400   # ~365d cutoff
