from utils.classifications.defs.scout_rush import trigger, factors

# Player 1: a clear scout rush -- Scout Cavalry + Camel Scout before the castle click (1200).
# Player 2: fast castle (clicks Castle at 700); scouts only AFTER the click -> not a rush.
# Player 3: never reached Feudal.
# Player 1 also has Eagle Scout (Meso infantry) and Champi Scout (modded civ) which must NOT count.
GAME = {
    "players": [
        {"player_number": 1, "feudal_s": 600, "castle_s": 1200, "winner": True, "eapm": 80},
        {"player_number": 2, "feudal_s": 600, "castle_s": 700, "winner": False, "eapm": 75},
        {"player_number": 3, "feudal_s": None, "castle_s": None, "winner": False, "eapm": 40},
    ],
    "techs": [{"player_number": 1, "tech": "Bloodlines", "click_s": 900, "phase": "feudal"}],
    "events": [
        {"player_number": 1, "category": "scout", "name": "Scout Cavalry", "amount": 2, "t_s": 650},
        {"player_number": 1, "category": "scout", "name": "Camel Scout", "amount": 1, "t_s": 800},
        {"player_number": 2, "category": "scout", "name": "Scout Cavalry", "amount": 3, "t_s": 760},
        {"player_number": 1, "category": "scout", "name": "Eagle Scout", "amount": 5, "t_s": 660},
        {"player_number": 1, "category": "scout", "name": "Champi Scout", "amount": 4, "t_s": 670},
    ],
}


def test_trigger_fires_for_pre_castle_scouts():
    assert trigger(GAME, 1) is True


def test_trigger_skips_fast_castle_scouts_after_click():
    # player 2's only scout (t_s 760) is AFTER their castle click (700) -> not a rush
    assert trigger(GAME, 2) is False


def test_trigger_skips_player_who_never_reached_feudal():
    assert trigger(GAME, 3) is False


def test_trigger_ignores_eagle_and_champi_scouts():
    meso_and_mod_only = {
        "players": [{"player_number": 1, "feudal_s": 600, "castle_s": 1200}],
        "techs": [],
        "events": [
            {"player_number": 1, "category": "scout", "name": "Eagle Scout", "amount": 9, "t_s": 700},
            {"player_number": 1, "category": "scout", "name": "Champi Scout", "amount": 9, "t_s": 720},
        ],
    }
    assert trigger(meso_and_mod_only, 1) is False


def test_factors():
    f = factors(GAME, 1)
    assert f["scouts_pre_castle"] == 3.0   # 2 Scout Cavalry + 1 Camel Scout; eagle/champi excluded
    assert f["feudal_s"] == 600.0 and f["castle_s"] == 1200.0
    assert f["reached_castle"] == 1.0
    assert f["feudal_to_castle_s"] == 600.0
    assert f["bloodlines_pre_castle"] == 1.0
    assert f["bloodlines_click_s"] == 900.0


def test_factors_bloodlines_after_castle_not_counted_but_click_recorded():
    game = {
        "players": [{"player_number": 1, "feudal_s": 600, "castle_s": 900}],
        "techs": [{"player_number": 1, "tech": "Bloodlines", "click_s": 1000}],   # after castle
        "events": [{"player_number": 1, "category": "scout", "name": "Scout Cavalry",
                    "amount": 3, "t_s": 700}],
    }
    f = factors(game, 1)
    assert f["bloodlines_pre_castle"] == 0.0
    assert f["bloodlines_click_s"] == 1000.0    # raw click still recorded


def test_factors_never_castled():
    game = {
        "players": [{"player_number": 1, "feudal_s": 600, "castle_s": None}],
        "techs": [],
        "events": [{"player_number": 1, "category": "scout", "name": "Scout Cavalry",
                    "amount": 4, "t_s": 700}],
    }
    f = factors(game, 1)
    assert f["reached_castle"] == 0.0
    assert f["castle_s"] is None and f["feudal_to_castle_s"] is None
    assert f["scouts_pre_castle"] == 4.0
    assert f["bloodlines_pre_castle"] == 0.0 and f["bloodlines_click_s"] is None
