from utils.classifications.defs import luck


def _p(n, team, winner, settle, **extra):
    d = {"player_number": n, "team": team, "winner": winner, "settle_tc_xy": settle}
    d.update(extra)
    return d


def _valid_game(**p1_extra):
    # 8 players, 4 winners, Land Nomad -> valid. P1 carries the metric under test.
    players = [_p(1, "A", True, {"x": 0.0, "y": 0.0}, **p1_extra)]
    players += [_p(i, "A" if i <= 4 else "B", i <= 4, {"x": float(i * 100), "y": 0.0}) for i in range(2, 9)]
    return {"match": {"map": "Land Nomad"}, "players": players}


def _by_key():
    return {c.key: c for c in luck.CLASSIFICATIONS}


def test_twelve_luck_classifications_all_category_luck():
    assert len(luck.CLASSIFICATIONS) == 12
    assert all(c.category == "luck" for c in luck.CLASSIFICATIONS)
    assert "luck_baseline" in _by_key()


def test_near_gold_fires_below_threshold_only():
    c = _by_key()["spawn_near_gold"]            # near_gold < 7
    assert c.trigger(_valid_game(spawn_gold_d=5.0), 1) is True
    assert c.trigger(_valid_game(spawn_gold_d=9.0), 1) is False


def test_gold_poor_fires_above_threshold_only():
    c = _by_key()["spawn_gold_poor"]            # gold_poor > 17
    assert c.trigger(_valid_game(spawn_gold_d=20.0), 1) is True
    assert c.trigger(_valid_game(spawn_gold_d=10.0), 1) is False


def test_luck_trigger_noop_on_invalid_game():
    c = _by_key()["spawn_near_gold"]
    g = _valid_game(spawn_gold_d=5.0)
    g["match"]["map"] = "Arabia"                # now invalid
    assert c.trigger(g, 1) is False


def test_baseline_fires_for_every_player_in_valid_game():
    c = _by_key()["luck_baseline"]
    assert c.trigger(_valid_game(spawn_gold_d=5.0), 1) is True
    assert c.trigger(_valid_game(spawn_gold_d=5.0), 5) is True


def test_isolated_uses_proximity_metric():
    c = _by_key()["spawn_isolated"]             # nearest any > 63; P1's nearest other is 200 here
    assert c.trigger(_valid_game(), 1) is True
