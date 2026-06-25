"""Late-castle family + the phase-exclusivity cascade (feudal -> early -> late)."""
from utils.classifications import gamedata as gd
from utils.classifications.defs.late_knight import CLASSIFICATION as LKNIGHT
from utils.classifications.defs.late_ram import CLASSIFICATION as LRAM
from utils.classifications.defs.late_unique import CLASSIFICATION as LUNIQUE
from utils.classifications.defs.knight_rush import CLASSIFICATION as KNIGHT


def _pl(pnum=1, feudal_s=500, castle_s=1000, imperial_s=2500, tc_build_s=None):
    return {"player_number": pnum, "feudal_s": feudal_s, "castle_s": castle_s, "imperial_s": imperial_s,
            "tc_build_s": tc_build_s if tc_build_s is not None else [600, 1100, 1400]}


def _game(events, **pkw):
    return {"players": [_pl(**pkw)], "events": events, "techs": []}


# default player: castle 1000, 3 additional TCs (600/1100/1400) -> early window [1000,1400),
# late window [1400, imp=2500)
LATE_KNIGHTS = [{"player_number": 1, "category": "knight_line", "name": "Knight", "amount": 35, "t_s": 1500}]


# --- late window -------------------------------------------------------------------------------

def test_late_window_is_3rd_tc_to_imperial():
    assert gd.late_castle_window(_game([]), 1) == (1400, 2500)


def test_late_window_none_without_3rd_tc():
    assert gd.late_castle_window(_game([], tc_build_s=[600, 1100]), 1) == (None, None)


def test_late_window_open_when_no_imperial():
    assert gd.late_castle_window(_game([], imperial_s=None), 1) == (1400, None)


# --- late_knight -------------------------------------------------------------------------------

def test_late_knight_fires():
    assert LKNIGHT.trigger(_game(LATE_KNIGHTS), 1) is True


def test_late_knight_factors():
    f = LKNIGHT.factors(_game(LATE_KNIGHTS), 1)
    assert f["units_in_window"] == 35.0
    assert f["third_tc_s"] == 1400.0 and f["imperial_s"] == 2500.0
    assert f["late_window_s"] == 1100.0
    assert f["reached_imperial"] == 1.0


def test_late_knight_threshold_strict_gt_30():
    g = _game([{"player_number": 1, "category": "knight_line", "name": "Knight", "amount": 30, "t_s": 1500}])
    assert LKNIGHT.trigger(g, 1) is False


def test_late_knights_before_3rd_tc_not_counted():
    # knights at 1300 fall in the EARLY window (1000-1400), not the late window
    g = _game([{"player_number": 1, "category": "knight_line", "name": "Knight", "amount": 35, "t_s": 1300}])
    assert LKNIGHT.trigger(g, 1) is False


# --- exclusivity cascade -----------------------------------------------------------------------

def test_late_excluded_for_feudal_rusher():
    # a Scout Cavalry before Castle -> scout_rush -> feudal rusher -> excluded from late
    ev = LATE_KNIGHTS + [{"player_number": 1, "category": "scout", "name": "Scout Cavalry", "amount": 5, "t_s": 700}]
    assert LKNIGHT.trigger(_game(ev), 1) is False


def test_late_excluded_for_early_castle_pusher():
    # 25 knights in the early window (1000-1400) -> early-castle rush -> excluded from late
    ev = LATE_KNIGHTS + [{"player_number": 1, "category": "knight_line", "name": "Knight", "amount": 25, "t_s": 1100}]
    assert LKNIGHT.trigger(_game(ev), 1) is False


def test_early_castle_knight_now_excludes_feudal_rusher():
    early = [{"player_number": 1, "category": "knight_line", "name": "Knight", "amount": 25, "t_s": 1100}]
    assert KNIGHT.trigger(_game(early), 1) is True                       # plain early-castle rush -> fires
    scout = [{"player_number": 1, "category": "scout", "name": "Scout Cavalry", "amount": 5, "t_s": 700}]
    assert KNIGHT.trigger(_game(early + scout), 1) is False              # feudal rusher -> not a knight rush


# --- late_ram / late_unique --------------------------------------------------------------------

def test_late_ram_threshold_gt_10():
    assert LRAM.trigger(_game([{"player_number": 1, "category": "siege", "name": "Battering Ram", "amount": 11, "t_s": 1500}]), 1) is True
    assert LRAM.trigger(_game([{"player_number": 1, "category": "siege", "name": "Battering Ram", "amount": 10, "t_s": 1500}]), 1) is False


def test_late_unique_counts_unique_other():
    assert LUNIQUE.trigger(_game([{"player_number": 1, "category": "unique_other", "name": "Mangudai", "amount": 31, "t_s": 1500}]), 1) is True
