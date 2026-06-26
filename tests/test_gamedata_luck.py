from utils.classifications import gamedata as gd


def _p(n, team, winner, settle=None, **extra):
    d = {"player_number": n, "team": team, "winner": winner, "settle_tc_xy": settle}
    d.update(extra)
    return d


def _game(players, map_name="Land Nomad"):
    return {"match": {"map": map_name}, "players": players}


def test_spawn_proximity_ally_enemy_any():
    # P1 at origin; ally P2 at (10,0); enemies P3 at (3,4)->5, P4 at (30,0)
    g = _game([
        _p(1, "1+2", True, {"x": 0.0, "y": 0.0}),
        _p(2, "1+2", True, {"x": 10.0, "y": 0.0}),
        _p(3, "3+4", False, {"x": 3.0, "y": 4.0}),
        _p(4, "3+4", False, {"x": 30.0, "y": 0.0}),
    ])
    d_ally, d_enemy, d_any = gd.spawn_proximity(g, 1)
    assert round(d_ally, 1) == 10.0
    assert round(d_enemy, 1) == 5.0
    assert round(d_any, 1) == 5.0


def test_spawn_proximity_none_without_settle():
    g = _game([_p(1, "1", True, None), _p(2, "2", False, {"x": 1.0, "y": 1.0})])
    assert gd.spawn_proximity(g, 1) == (None, None, None)


def test_is_valid_luck_game_balanced_nomad():
    players = [_p(i, "A" if i <= 4 else "B", i <= 4, {"x": float(i), "y": 0.0}) for i in range(1, 9)]
    assert gd.is_valid_luck_game(_game(players)) is True


def test_is_valid_luck_game_rejects_no_winner():
    players = [_p(i, "A" if i <= 4 else "B", False, {"x": float(i), "y": 0.0}) for i in range(1, 9)]
    assert gd.is_valid_luck_game(_game(players)) is False


def test_is_valid_luck_game_rejects_wrong_map_and_count():
    players8 = [_p(i, "A" if i <= 4 else "B", i <= 4, {"x": float(i), "y": 0.0}) for i in range(1, 9)]
    assert gd.is_valid_luck_game(_game(players8, map_name="Arabia")) is False
    players7 = players8[:7]
    assert gd.is_valid_luck_game(_game(players7)) is False


def test_spawn_metric_reads_player_field():
    g = _game([_p(1, "A", True, {"x": 0.0, "y": 0.0}, spawn_gold_d=4.2)])
    assert gd.spawn_metric(g, 1, "spawn_gold_d") == 4.2
