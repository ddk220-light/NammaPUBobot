import nammaoe2bot.ingest.shape as shape

# Minimal extract_match()-shaped fixture: 1 match, 2 players.
EXTRACTED = {
    "match": {"aoe2_match_id": 999, "map": "Arabia", "save_version": 67.2,
              "duration_s": 1500, "date": "2026-06-20 10:00", "winner_team": None},
    "players": [
        {"player_number": 1, "profile_id": 111, "identity": "Alice", "attribution": "replay",
         "civ": "Mayans", "team": "1", "winner": True, "eapm": 80, "age_reliable": True,
         "tc_relocations": 0, "feudal_s": 600, "castle_s": 1200, "imperial_s": None,
         "first_tc_s": 20, "villagers": 90, "vil_pre_feudal": 20, "vil_pre_castle": 50,
         "vil_pre_imperial": 90, "military": 30, "mil_pre_feudal": 0, "mil_pre_castle": 10,
         "mil_pre_imperial": 30},
        {"player_number": 2, "profile_id": 222, "identity": "Bob", "attribution": "replay",
         "civ": "Franks", "team": "2", "winner": False, "eapm": 70, "age_reliable": True,
         "tc_relocations": 1, "feudal_s": 650, "castle_s": None, "imperial_s": None,
         "first_tc_s": 25, "villagers": 70, "vil_pre_feudal": 18, "vil_pre_castle": 40,
         "vil_pre_imperial": 70, "military": 40, "mil_pre_feudal": 5, "mil_pre_castle": 20,
         "mil_pre_imperial": 40},
    ],
    "units": [{"player_number": 1, "identity": "Alice", "civ": "Mayans", "unit": "Archer",
               "category": "archer_line", "is_military": True, "total": 25, "pre_feudal": 0,
               "pre_castle": 10, "pre_imperial": 25}],
    "techs": [{"player_number": 2, "identity": "Bob", "civ": "Franks", "tech": "Loom",
               "click_s": 120, "phase": "dark"}],
    "buildings": [{"player_number": 1, "identity": "Alice", "civ": "Mayans",
                   "building": "House", "count": 5}],
    "apm": [{"player_number": 1, "minute": 0, "actions": 42},
            {"player_number": 2, "minute": 0, "actions": 31}],
}
PROFMAP = {111: 5001}   # profile 111 -> discord user; 222 unmapped


def test_match_row():
    row = shape.match_row(EXTRACTED["match"], bot_match_id=7, parsed_at=123, parser_version="pv1")
    assert row["replay_match_id"] == 999
    assert row["bot_match_id"] == 7
    assert row["played_at"] == "2026-06-20 10:00"
    assert row["replay_url"] == "https://www.aoe2insights.com/match/999/"
    assert row["parsed_at"] == 123 and row["parser_version"] == "pv1"
    assert "winner_team" not in row   # dropped


def test_pnum_to_profile():
    assert shape.pnum_to_profile(EXTRACTED["players"]) == {1: 111, 2: 222}


def test_player_game_rows_attributes_user_id():
    rows = shape.player_game_rows(999, EXTRACTED["players"], PROFMAP)
    by_pid = {r["profile_id"]: r for r in rows}
    assert by_pid[111]["user_id"] == 5001
    assert by_pid[222]["user_id"] is None        # unmapped -> NULL
    assert by_pid[111]["replay_match_id"] == 999
    assert by_pid[111]["villagers"] == 90


def test_unit_rows_denormalize_profile_id():
    rows = shape.unit_rows(999, EXTRACTED["units"], {1: 111, 2: 222})
    assert rows[0]["replay_match_id"] == 999
    assert rows[0]["player_number"] == 1
    assert rows[0]["profile_id"] == 111
    assert rows[0]["unit"] == "Archer"


def test_the_legacy_profile_upsert_shaper_is_gone():
    """profile_upserts built the rows for the retired replay-side profile table,
    the second store that answered "who is this person". Ingest no longer writes
    it (the identity resolver is the only one), and a shaper left behind is a
    write waiting to be re-added."""
    assert not hasattr(shape, "profile_upserts")


def test_apm_rows_denormalize_profile_id():
    rows = shape.apm_rows(999, EXTRACTED["apm"], shape.pnum_to_profile(EXTRACTED["players"]))
    assert len(rows) == 2
    assert rows[0] == {"replay_match_id": 999, "player_number": 1, "minute": 0,
                       "profile_id": 111, "actions": 42}
    assert rows[1]["profile_id"] == 222


def test_apm_rows_empty():
    assert shape.apm_rows(999, [], {}) == []
