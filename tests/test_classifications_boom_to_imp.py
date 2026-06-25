from utils.classifications.defs.boom_to_imp import CLASSIFICATION as BOOM


def _pl(feudal_s=500, castle_s=1000, imperial_s=2000, tc_build_s=None, vil_pre_imperial=100):
    return {"player_number": 1, "feudal_s": feudal_s, "castle_s": castle_s, "imperial_s": imperial_s,
            "tc_build_s": tc_build_s if tc_build_s is not None else [600, 1100, 1400],
            "vil_pre_imperial": vil_pre_imperial}


def _game(mil_amount, **pkw):
    ev = [{"player_number": 1, "category": "spearman_line", "name": "Spearman",
           "amount": mil_amount, "t_s": 1500, "is_military": True}] if mil_amount else []
    return {"players": [_pl(**pkw)], "events": ev, "techs": []}


def test_boom_fires():
    assert BOOM.trigger(_game(5), 1) is True            # 3 extra TCs, 5 military, reached imp


def test_factors():
    f = BOOM.factors(_game(5), 1)
    assert f["extra_tcs"] == 3.0
    assert f["military_before_imp"] == 5.0
    assert f["villagers_before_imp"] == 100.0
    assert f["imperial_s"] == 2000.0
    assert f["castle_to_imp_s"] == 1000.0


def test_needs_2_extra_tcs():
    assert BOOM.trigger(_game(5, tc_build_s=[600]), 1) is False      # only 1 extra TC


def test_too_much_military_excluded():
    assert BOOM.trigger(_game(25), 1) is False                       # 25 military >= 20


def test_must_reach_imperial():
    assert BOOM.trigger(_game(5, imperial_s=None), 1) is False


def test_military_after_imp_not_counted():
    # 25 spearmen but all AFTER the imperial click (2000) -> military_before_imp is 0 -> boom
    g = {"players": [_pl()], "techs": [],
         "events": [{"player_number": 1, "category": "spearman_line", "name": "Spearman",
                     "amount": 25, "t_s": 2100, "is_military": True}]}
    assert BOOM.trigger(g, 1) is True
    assert BOOM.factors(g, 1)["military_before_imp"] == 0.0


def test_nomad_starting_tc_not_counted_as_extra():
    # a Dark-Age TC at 60 (before Feudal 500) is the Nomad start, not an "extra" TC
    g = _game(5, tc_build_s=[60, 1100])                              # only 1 extra (1100) >= feudal
    assert BOOM.trigger(g, 1) is False