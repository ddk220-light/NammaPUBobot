import utils.classifications.shape as shape


def test_result_row():
    player = {"player_number": 1, "profile_id": 111, "identity": "Alice", "civ": "Mayans",
              "team": "1", "winner": True}
    row = shape.result_row("archer_rush", 999, player, played_at=1700000000)
    assert row == {"key": "archer_rush", "aoe2_match_id": 999, "player_number": 1,
                   "profile_id": 111, "identity": "Alice", "civ": "Mayans", "team": "1",
                   "winner": 1, "played_at": 1700000000}


def test_result_row_winner_none_stays_none():
    player = {"player_number": 2, "profile_id": 222, "identity": "Bob", "civ": "Franks",
              "team": "2", "winner": None}
    assert shape.result_row("archer_rush", 999, player, played_at=1)["winner"] is None


def test_metric_rows_skips_none_values():
    factors = {"archers_pre_castle": 12.0, "commit_to_castle_s": None, "reached_castle": 1.0}
    rows = shape.metric_rows("archer_rush", 999, 1, factors)
    by_metric = {r["metric"]: r["value"] for r in rows}
    assert by_metric == {"archers_pre_castle": 12.0, "reached_castle": 1.0}   # None dropped
    assert all(r["key"] == "archer_rush" and r["aoe2_match_id"] == 999 and r["player_number"] == 1
               for r in rows)
