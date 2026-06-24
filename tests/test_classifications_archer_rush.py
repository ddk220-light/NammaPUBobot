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


def test_factors_counts_and_timing():
    f = factors(GAME, 1)
    assert f["archers_pre_castle"] == 7.0                 # 3 + 4 (skirmishers excluded)
    assert f["feudal_s"] == 600.0 and f["castle_s"] == 1200.0
    assert f["reached_castle"] == 1.0
    assert f["feudal_to_castle_s"] == 600.0
    assert f["first_archer_s"] == 700.0
    assert f["first_archer_after_feudal_s"] == 100.0
    assert f["archers_within_3min_of_feudal"] == 7.0      # both queues within 600+180=780
    assert f["fletching_pre_castle"] == 1.0
    assert f["fletching_after_feudal_s"] == 180.0         # 780 - 600


def test_factors_commit_to_castle_none_when_under_ten_archers():
    # only 7 archers (<10) -> commit_to_castle_s undefined
    assert factors(GAME, 1)["commit_to_castle_s"] is None


def test_factors_commit_to_castle_when_committed():
    game = {
        "players": [{"player_number": 1, "feudal_s": 600, "castle_s": 1400, "eapm": 90}],
        "techs": [{"player_number": 1, "tech": "Fletching", "click_s": 800}],
        "events": [
            {"player_number": 1, "category": "archer_line", "name": "Archer", "amount": 6, "t_s": 700},
            {"player_number": 1, "category": "archer_line", "name": "Archer", "amount": 6, "t_s": 900},
        ],
    }
    f = factors(game, 1)
    assert f["archers_pre_castle"] == 12.0
    # 10th archer reached at the 900 queue; commit = max(900, fletch 800) = 900; 1400-900 = 500
    assert f["commit_to_castle_s"] == 500.0


def test_factors_fletching_after_castle_does_not_count():
    game = {
        "players": [{"player_number": 1, "feudal_s": 600, "castle_s": 900, "eapm": 50}],
        "techs": [{"player_number": 1, "tech": "Fletching", "click_s": 1000}],   # after castle
        "events": [{"player_number": 1, "category": "archer_line", "name": "Archer",
                    "amount": 3, "t_s": 700}],
    }
    f = factors(game, 1)
    assert f["fletching_pre_castle"] == 0.0
    assert f["fletching_after_feudal_s"] is None


def test_factors_commit_to_castle_none_without_fletching():
    game = {
        "players": [{"player_number": 1, "feudal_s": 600, "castle_s": 1400, "eapm": 90}],
        "techs": [],   # no Fletching at all
        "events": [
            {"player_number": 1, "category": "archer_line", "name": "Archer", "amount": 6, "t_s": 700},
            {"player_number": 1, "category": "archer_line", "name": "Archer", "amount": 6, "t_s": 900},
        ],
    }
    f = factors(game, 1)
    assert f["archers_pre_castle"] == 12.0
    assert f["commit_to_castle_s"] is None   # Fletching is a required co-signal
