import utils.classifications.gamedata as gd

GAME = {
    "players": [
        {"player_number": 1, "feudal_s": 600, "castle_s": 1200, "winner": True, "eapm": 80},
        {"player_number": 2, "feudal_s": 640, "castle_s": 900, "winner": False, "eapm": 70},
    ],
    "techs": [
        {"player_number": 1, "tech": "Fletching", "click_s": 780, "phase": "feudal"},
        {"player_number": 2, "tech": "Loom", "click_s": 120, "phase": "dark"},
    ],
    "events": [
        {"player_number": 1, "category": "archer_line", "name": "Archer", "amount": 3, "t_s": 720},
        {"player_number": 1, "category": "archer_line", "name": "Archer", "amount": 4, "t_s": 700},
        {"player_number": 1, "category": "skirmisher", "name": "Skirmisher", "amount": 2, "t_s": 710},
        {"player_number": 2, "category": "archer_line", "name": "Archer", "amount": 5, "t_s": 950},
        {"player_number": 1, "category": "archer_line", "name": "Archer", "amount": 1, "t_s": None},
    ],
}


def test_player_lookup():
    assert gd.player(GAME, 1)["eapm"] == 80
    assert gd.player(GAME, 99) is None


def test_archer_queue_events_excludes_skirmishers_and_null_ts_and_sorts():
    evs = gd.archer_queue_events(GAME, 1)
    assert [e["t_s"] for e in evs] == [700, 720]   # skirmisher + null-t_s dropped, time-sorted


def test_tech_click_s():
    assert gd.tech_click_s(GAME, 1, "Fletching") == 780
    assert gd.tech_click_s(GAME, 1, "Loom") is None     # not this player
    assert gd.tech_click_s(GAME, 2, "Loom") == 120
