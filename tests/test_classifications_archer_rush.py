from utils.classifications.defs.archer_rush import trigger

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
