from utils.classifications.defs.archer_rush import trigger, factors

# A clear feudal archer rush (player 1): archers queued before the castle click (1200).
# A fast-castle player (player 2): clicks Castle early (700); archers only AFTER the click.
GAME = {
    "players": [
        {"player_number": 1, "feudal_s": 600, "castle_s": 1200, "winner": True, "eapm": 80},
        {"player_number": 2, "feudal_s": 600, "castle_s": 700, "winner": False, "eapm": 75},
        {"player_number": 3, "feudal_s": None, "castle_s": None, "winner": False, "eapm": 40},
    ],
    "techs": [{"player_number": 1, "tech": "Fletching", "click_s": 780, "phase": "feudal"}],
    "events": [
        {"player_number": 1, "category": "archer_line", "name": "Archer", "amount": 3, "t_s": 700},
        {"player_number": 1, "category": "archer_line", "name": "Archer", "amount": 4, "t_s": 760},
        {"player_number": 2, "category": "archer_line", "name": "Archer", "amount": 3, "t_s": 760},
        {"player_number": 1, "category": "skirmisher", "name": "Skirmisher", "amount": 9, "t_s": 720},
    ],
}


def test_trigger_fires_for_pre_castle_archers():
    assert trigger(GAME, 1) is True


def test_trigger_skips_fast_castle_archers_after_click():
    # player 2's only archer (t_s 760) is AFTER their castle click (700) -> not a rush
    assert trigger(GAME, 2) is False


def test_trigger_skips_player_who_never_reached_feudal():
    assert trigger(GAME, 3) is False


def test_trigger_ignores_skirmishers():
    skirmisher_only = {
        "players": [{"player_number": 1, "feudal_s": 600, "castle_s": 1200}],
        "techs": [],
        "events": [{"player_number": 1, "category": "skirmisher", "name": "Skirmisher",
                    "amount": 20, "t_s": 700}],
    }
    assert trigger(skirmisher_only, 1) is False


def test_factors_new_set():
    f = factors(GAME, 1)
    assert f["archers_pre_castle"] == 7.0
    assert f["feudal_s"] == 600.0 and f["castle_s"] == 1200.0
    assert f["reached_castle"] == 1.0
    assert f["feudal_to_castle_s"] == 600.0
    assert f["fletching_pre_castle"] == 1.0
    assert f["fletching_click_s"] == 780.0
    # dropped metrics are gone
    for k in ("commit_to_castle_s", "eapm", "first_archer_after_feudal_s", "archers_within_3min_of_feudal"):
        assert k not in f


def test_factors_fletching_after_castle_not_counted_but_click_recorded():
    game = {
        "players": [{"player_number": 1, "feudal_s": 600, "castle_s": 900}],
        "techs": [{"player_number": 1, "tech": "Fletching", "click_s": 1000}],   # after castle
        "events": [{"player_number": 1, "category": "archer_line", "name": "Archer",
                    "amount": 3, "t_s": 700}],
    }
    f = factors(game, 1)
    assert f["fletching_pre_castle"] == 0.0
    assert f["fletching_click_s"] == 1000.0    # raw click still recorded


def test_factors_never_castled():
    game = {
        "players": [{"player_number": 1, "feudal_s": 600, "castle_s": None}],
        "techs": [],
        "events": [{"player_number": 1, "category": "archer_line", "name": "Archer",
                    "amount": 4, "t_s": 700}],
    }
    f = factors(game, 1)
    assert f["reached_castle"] == 0.0
    assert f["castle_s"] is None and f["feudal_to_castle_s"] is None
    assert f["archers_pre_castle"] == 4.0
    assert f["fletching_pre_castle"] == 0.0 and f["fletching_click_s"] is None
