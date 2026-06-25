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
