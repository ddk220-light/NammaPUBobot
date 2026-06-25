"""Early-Castle rush family: shared window + the 5 rush defs (knight/crossbow/cav_archer/camel/ram)."""
from utils.classifications import gamedata as gd
from utils.classifications.defs.knight_rush import CLASSIFICATION as KNIGHT
from utils.classifications.defs.crossbow_rush import CLASSIFICATION as XBOW
from utils.classifications.defs.cav_archer_rush import CLASSIFICATION as CA
from utils.classifications.defs.camel_rush import CLASSIFICATION as CAMEL
from utils.classifications.defs.ram_push import CLASSIFICATION as RAM


def _p(pnum=1, feudal_s=500, castle_s=900, imperial_s=None, tc_build_s=None):
    return {"player_number": pnum, "feudal_s": feudal_s, "castle_s": castle_s,
            "imperial_s": imperial_s, "tc_build_s": tc_build_s or []}


# --- window ------------------------------------------------------------------------------------

def test_window_2nd_additional_tc_ends_window():
    g = {"players": [_p(tc_build_s=[600, 1500, 2000])], "events": [], "techs": []}
    assert gd.early_castle_window(g, 1) == (900, 1500)   # 2nd build at/after feudal(500) = 1500


def test_window_open_when_fewer_than_2_extra_tcs():
    g = {"players": [_p(tc_build_s=[600])], "events": [], "techs": []}
    assert gd.early_castle_window(g, 1) == (900, None)


def test_window_ignores_nomad_dark_age_starting_tc():
    # 60s TC is the Nomad starting TC (pre-Feudal) -> not "additional"; 2nd additional = 2000
    g = {"players": [_p(tc_build_s=[60, 1500, 2000])], "events": [], "techs": []}
    assert gd.early_castle_window(g, 1) == (900, 2000)


def test_window_none_when_never_castled():
    g = {"players": [_p(castle_s=None, tc_build_s=[600, 1500])], "events": [], "techs": []}
    assert gd.early_castle_window(g, 1) == (None, None)


# --- knight_rush -------------------------------------------------------------------------------

KNIGHT_GAME = {
    "players": [_p(tc_build_s=[600, 1500], imperial_s=None)],
    "techs": [{"player_number": 1, "tech": "Cavalier", "click_s": 1100}],
    "events": [
        {"player_number": 1, "category": "knight_line", "name": "Knight", "amount": 25, "t_s": 1000},
        {"player_number": 1, "category": "knight_line", "name": "Knight", "amount": 9, "t_s": 1600},   # after window
        {"player_number": 1, "category": "knight_line", "name": "Teutonic Knight", "amount": 30, "t_s": 1000},  # excluded
    ],
}


def test_knight_trigger_fires():
    assert KNIGHT.trigger(KNIGHT_GAME, 1) is True


def test_knight_excludes_teutonic_and_post_window():
    f = KNIGHT.factors(KNIGHT_GAME, 1)
    assert f["units_in_window"] == 25.0          # 9 after-window + 30 Teutonic both excluded
    assert f["castle_s"] == 900.0
    assert f["built_2nd_tc"] == 1.0 and f["castle_to_2nd_tc_s"] == 600.0
    assert f["sig_upgrade_in_window"] == 1.0 and f["sig_upgrade_click_s"] == 1100.0
    assert f["reached_imperial"] == 0.0


def test_knight_threshold_is_strict_gt_20():
    g = {"players": [_p(tc_build_s=[600, 2000])], "techs": [],
         "events": [{"player_number": 1, "category": "knight_line", "name": "Knight", "amount": 20, "t_s": 1000}]}
    assert KNIGHT.trigger(g, 1) is False         # exactly 20 -> not > 20


# --- crossbow_rush -----------------------------------------------------------------------------

def test_crossbow_counts_foot_archers_excludes_camel_archer():
    g = {"players": [_p(tc_build_s=[600, 1800])], "techs": [],
         "events": [
             {"player_number": 1, "category": "archer_line", "name": "Archer", "amount": 21, "t_s": 1000},
             {"player_number": 1, "category": "archer_line", "name": "Camel Archer", "amount": 30, "t_s": 1000},  # excluded
         ]}
    assert XBOW.trigger(g, 1) is True
    assert XBOW.factors(g, 1)["units_in_window"] == 21.0


# --- cav_archer_rush ---------------------------------------------------------------------------

def test_cav_archer_counts_by_category():
    g = {"players": [_p(tc_build_s=[600, 1800])], "techs": [],
         "events": [{"player_number": 1, "category": "cav_archer", "name": "Cavalry Archer", "amount": 22, "t_s": 1000}]}
    assert CA.trigger(g, 1) is True


# --- camel_rush --------------------------------------------------------------------------------

def test_camel_counts_camel_scout_line_excludes_mameluke_flaming_archer():
    # Camel Scout is the trained base of the camel line (= Camel Rider in Castle Age); it counts.
    # Per request, Mameluke and Flaming Camel are NOT camels here; Camel Archer (ranged) is excluded too.
    g = {"players": [_p(tc_build_s=[600, 1800])], "techs": [],
         "events": [
             {"player_number": 1, "category": "scout", "name": "Camel Scout", "amount": 15, "t_s": 1000},
             {"player_number": 1, "category": "camel_line", "name": "Camel Rider", "amount": 8, "t_s": 1100},
             {"player_number": 1, "category": "unique_other", "name": "Mameluke", "amount": 40, "t_s": 1100},   # excluded
             {"player_number": 1, "category": "camel_line", "name": "Flaming Camel", "amount": 40, "t_s": 1100},  # excluded
             {"player_number": 1, "category": "archer_line", "name": "Camel Archer", "amount": 40, "t_s": 1000},  # excluded
         ]}
    assert CAMEL.trigger(g, 1) is True
    assert CAMEL.factors(g, 1)["units_in_window"] == 23.0   # 15 Camel Scout + 8 Camel Rider


def test_camel_scout_before_castle_not_counted():
    # a feudal Camel Scout (before the Castle click at 900) is scout play, not a castle camel rush
    g = {"players": [_p(tc_build_s=[600, 1800])], "techs": [],
         "events": [{"player_number": 1, "category": "scout", "name": "Camel Scout", "amount": 30, "t_s": 700}]}
    assert CAMEL.trigger(g, 1) is False


# --- ram_push ----------------------------------------------------------------------------------

def test_ram_threshold_gt_3_and_excludes_karambit():
    g = {"players": [_p(tc_build_s=[600, 1800])], "techs": [],
         "events": [
             {"player_number": 1, "category": "siege", "name": "Battering Ram", "amount": 4, "t_s": 1000},
             {"player_number": 1, "category": "unique_other", "name": "Karambit Warrior", "amount": 50, "t_s": 1000},  # 'ram' substring, excluded
         ]}
    assert RAM.trigger(g, 1) is True
    assert RAM.factors(g, 1)["units_in_window"] == 4.0


def test_ram_exactly_3_does_not_fire():
    g = {"players": [_p(tc_build_s=[600, 1800])], "techs": [],
         "events": [{"player_number": 1, "category": "siege", "name": "Capped Ram", "amount": 3, "t_s": 1000}]}
    assert RAM.trigger(g, 1) is False


def test_units_before_castle_click_not_counted():
    # a knight queued in Feudal (before the Castle click at 900) must not count
    g = {"players": [_p(tc_build_s=[600, 1800])], "techs": [],
         "events": [{"player_number": 1, "category": "knight_line", "name": "Knight", "amount": 25, "t_s": 700}]}
    assert KNIGHT.trigger(g, 1) is False
