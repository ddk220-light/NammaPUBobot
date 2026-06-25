"""Castle placement: forward_castle / safe_castle (shared _castle_placement logic + gamedata)."""
from utils.classifications import gamedata as gd
from utils.classifications.defs.forward_castle import CLASSIFICATION as FWD
from utils.classifications.defs.safe_castle import CLASSIFICATION as SAFE


def _pl(pnum, team="1", feudal_s=500, castle_s=1000, imperial_s=None,
        tc_builds=None, start_tc_xy=None, castle_builds=None):
    tcb = tc_builds or []
    return {"player_number": pnum, "team": team, "feudal_s": feudal_s, "castle_s": castle_s,
            "imperial_s": imperial_s, "tc_build_s": sorted(b["t_s"] for b in tcb),
            "tc_builds": tcb, "start_tc_xy": start_tc_xy, "castle_builds": castle_builds or []}


def _game(*players):
    return {"players": list(players), "events": [], "techs": []}


# p1 home TC (50,50), enemy p2 home TC (200,200); p1's castle at (180,180) -> next to the enemy.
FWD_GAME = _game(
    _pl(1, team="1", tc_builds=[{"x": 50, "y": 50, "t_s": 300}], castle_builds=[{"x": 180, "y": 180, "t_s": 1100}]),
    _pl(2, team="2", tc_builds=[{"x": 200, "y": 200, "t_s": 320}]),
)


def test_forward_castle_fires_safe_does_not():
    assert FWD.trigger(FWD_GAME, 1) is True
    assert SAFE.trigger(FWD_GAME, 1) is False


def test_forward_factors():
    f = FWD.factors(FWD_GAME, 1)
    assert f["castle_placed_s"] == 1100.0
    assert f["dist_to_enemy_tc"] < f["dist_to_own_tc"]
    assert f["enemy_over_own_ratio"] < 1.0


def test_safe_castle_fires_forward_does_not():
    # same layout but the castle (60,55) sits next to the player's own home TC (50,50)
    g = _game(
        _pl(1, team="1", tc_builds=[{"x": 50, "y": 50, "t_s": 300}], castle_builds=[{"x": 60, "y": 55, "t_s": 1100}]),
        _pl(2, team="2", tc_builds=[{"x": 200, "y": 200, "t_s": 320}]),
    )
    assert SAFE.trigger(g, 1) is True
    assert FWD.trigger(g, 1) is False


def test_home_tc_last_before_castle_then_starting_fallback():
    g = _game(_pl(1, tc_builds=[{"x": 10, "y": 10, "t_s": 200}, {"x": 90, "y": 90, "t_s": 400}]))
    assert gd.home_tc_xy(g, 1) == (90, 90)              # delete->replace: last placed wins
    g2 = _game(_pl(1, tc_builds=[], start_tc_xy={"x": 33, "y": 44}))
    assert gd.home_tc_xy(g2, 1) == (33, 44)             # fallback to pre-placed starting TC


def test_primary_castle_requires_castle_before_any_castle_age_tc():
    # a TC is built in Castle Age (1050) BEFORE the castle (1100) -> castle is not the primary building
    g = _game(
        _pl(1, team="1", tc_builds=[{"x": 50, "y": 50, "t_s": 300}, {"x": 70, "y": 70, "t_s": 1050}],
            castle_builds=[{"x": 180, "y": 180, "t_s": 1100}]),
        _pl(2, team="2", tc_builds=[{"x": 200, "y": 200, "t_s": 320}]),
    )
    assert gd.primary_castle(g, 1) is None
    assert FWD.trigger(g, 1) is False and SAFE.trigger(g, 1) is False


def test_no_opponent_no_classification():
    g = _game(
        _pl(1, team="1", tc_builds=[{"x": 50, "y": 50, "t_s": 300}], castle_builds=[{"x": 180, "y": 180, "t_s": 1100}]),
        _pl(2, team="1", tc_builds=[{"x": 200, "y": 200, "t_s": 320}]),   # same team -> not an opponent
    )
    assert FWD.trigger(g, 1) is False and SAFE.trigger(g, 1) is False


def test_no_castle_not_triggered():
    g = _game(
        _pl(1, team="1", tc_builds=[{"x": 50, "y": 50, "t_s": 300}], castle_builds=[]),
        _pl(2, team="2", tc_builds=[{"x": 200, "y": 200, "t_s": 320}]),
    )
    assert FWD.trigger(g, 1) is False and SAFE.trigger(g, 1) is False
