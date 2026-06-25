from utils.classifications.defs.maa_rush import trigger, factors

# Player 1: a militia rush -- Militia + Serjeant before the castle click (1200), Man-at-Arms
# upgrade researched. Player 1 also has a Spearman (separate category) and a Flemish Militia
# (imperial Burgundian unit) which must NOT count.
# Player 2: fast castle (clicks Castle at 700); militia only AFTER the click -> not a rush.
# Player 3: never reached Feudal.
GAME = {
    "players": [
        {"player_number": 1, "feudal_s": 600, "castle_s": 1200, "winner": True, "eapm": 80},
        {"player_number": 2, "feudal_s": 600, "castle_s": 700, "winner": False, "eapm": 75},
        {"player_number": 3, "feudal_s": None, "castle_s": None, "winner": False, "eapm": 40},
    ],
    "techs": [{"player_number": 1, "tech": "Man-at-Arms", "click_s": 640, "phase": "feudal"}],
    "events": [
        {"player_number": 1, "category": "militia_line", "name": "Militia", "amount": 3, "t_s": 500},
        {"player_number": 1, "category": "unique_other", "name": "Serjeant", "amount": 2, "t_s": 800},
        {"player_number": 2, "category": "militia_line", "name": "Militia", "amount": 3, "t_s": 760},
        {"player_number": 1, "category": "spearman_line", "name": "Spearman", "amount": 9, "t_s": 520},
        {"player_number": 1, "category": "militia_line", "name": "Flemish Militia", "amount": 7, "t_s": 540},
    ],
}


def test_trigger_fires_for_pre_castle_militia():
    assert trigger(GAME, 1) is True


def test_trigger_skips_fast_castle_militia_after_click():
    # player 2's only militia (t_s 760) is AFTER their castle click (700) -> not a rush
    assert trigger(GAME, 2) is False


def test_trigger_skips_player_who_never_reached_feudal():
    assert trigger(GAME, 3) is False


def test_trigger_ignores_spearmen():
    spear_only = {
        "players": [{"player_number": 1, "feudal_s": 600, "castle_s": 1200}],
        "techs": [],
        "events": [{"player_number": 1, "category": "spearman_line", "name": "Pikeman",
                    "amount": 20, "t_s": 700}],
    }
    assert trigger(spear_only, 1) is False


def test_trigger_ignores_flemish_militia():
    flemish_only = {
        "players": [{"player_number": 1, "feudal_s": 600, "castle_s": None}],
        "techs": [],
        "events": [{"player_number": 1, "category": "militia_line", "name": "Flemish Militia",
                    "amount": 9, "t_s": 700}],
    }
    assert trigger(flemish_only, 1) is False


def test_trigger_serjeant_alone_counts():
    serjeant_only = {
        "players": [{"player_number": 1, "feudal_s": 600, "castle_s": 1200}],
        "techs": [],
        "events": [{"player_number": 1, "category": "unique_other", "name": "Serjeant",
                    "amount": 1, "t_s": 700}],
    }
    assert trigger(serjeant_only, 1) is True


def test_factors():
    f = factors(GAME, 1)
    assert f["militia_pre_castle"] == 5.0   # 3 Militia + 2 Serjeant; Flemish + spearman excluded
    assert f["feudal_s"] == 600.0 and f["castle_s"] == 1200.0
    assert f["reached_castle"] == 1.0
    assert f["feudal_to_castle_s"] == 600.0
    assert f["maa_upgrade_pre_castle"] == 1.0
    assert f["maa_upgrade_click_s"] == 640.0


def test_factors_maa_upgrade_after_castle_not_counted_but_click_recorded():
    game = {
        "players": [{"player_number": 1, "feudal_s": 600, "castle_s": 900}],
        "techs": [{"player_number": 1, "tech": "Man-at-Arms", "click_s": 1000}],   # after castle
        "events": [{"player_number": 1, "category": "militia_line", "name": "Militia",
                    "amount": 3, "t_s": 700}],
    }
    f = factors(game, 1)
    assert f["maa_upgrade_pre_castle"] == 0.0
    assert f["maa_upgrade_click_s"] == 1000.0    # raw click still recorded


def test_factors_never_castled():
    game = {
        "players": [{"player_number": 1, "feudal_s": 600, "castle_s": None}],
        "techs": [],
        "events": [{"player_number": 1, "category": "militia_line", "name": "Militia",
                    "amount": 4, "t_s": 700}],
    }
    f = factors(game, 1)
    assert f["reached_castle"] == 0.0
    assert f["castle_s"] is None and f["feudal_to_castle_s"] is None
    assert f["militia_pre_castle"] == 4.0
    assert f["maa_upgrade_pre_castle"] == 0.0 and f["maa_upgrade_click_s"] is None
