"""Unit tests for the pure player-card aggregation (bot.replay_stats.query.build_card).
Mirrors the quiz filter rules: standard-map games are assumed pre-filtered; age/timing metrics
gate on age_reliable + a real feudal click; timings skip absent values; count averages exclude
games where the player didn't do it."""
from bot.replay_stats.query import build_card


def _game(mid, **kw):
    base = dict(aoe2_match_id=mid, civ="Franks", winner=0, eapm=30, age_reliable=1,
                feudal_s=600, castle_s=1200, imperial_s=2400, first_tc_s=120,
                villagers=100, vil_pre_feudal=20, vil_pre_castle=30, vil_pre_imperial=80,
                military=40, mil_pre_feudal=0, mil_pre_castle=5, mil_pre_imperial=20)
    base.update(kw)
    return base


def _card(games, units=None, techs=None, builds=None, all_games=None, days=90):
    return build_card(games, units or [], techs or [], builds or [],
                      all_games if all_games is not None else len(games), days, "2026-03-25")


def _section(card, key):
    return next((s for s in card["sections"] if s["key"] == key), None)


def _row(card, key, label):
    sec = _section(card, key)
    return next((r for r in sec["rows"] if r[0] == label), None) if sec else None


def test_empty_games_returns_none():
    assert _card([]) is None


def test_header_winrate_eapm_and_civs():
    card = _card([_game(1, winner=1, eapm=20, civ="Mayans"),
                  _game(2, winner=0, eapm=40, civ="Mayans"),
                  _game(3, winner=1, eapm=30, civ="Aztecs")], all_games=5)
    assert card["games"] == 3
    assert card["all_games"] == 5
    assert card["wins"] == 2
    assert card["winrate"] == 67           # round(100*2/3)
    assert card["eapm"] == 30              # mean(20,40,30)
    assert card["civs"][0] == ("Mayans", 2)


def test_villager_total_average():
    card = _card([_game(1, villagers=100), _game(2, villagers=200)])
    assert _row(card, "villagers", "Total / game") == ("Total / game", 150.0, 2)


def test_count_metric_excludes_zero_games():
    # military 40 in one game, 0 in the other -> avg over the single non-zero game.
    card = _card([_game(1, military=40), _game(2, military=0)])
    assert _row(card, "military", "Army / game") == ("Army / game", 40.0, 1)


def test_all_zero_count_row_is_dropped():
    # mil_pre_feudal is 0 in both games -> the row has no data and is omitted.
    card = _card([_game(1), _game(2)])
    assert _row(card, "military", "before Feudal") is None


def test_age_gating_excludes_unreliable_games():
    # only the age-reliable game with a real feudal click counts toward timing/pre splits.
    card = _card([_game(1, age_reliable=1, feudal_s=600, vil_pre_feudal=22),
                  _game(2, age_reliable=0, feudal_s=None, vil_pre_feudal=999)])
    assert card["age_reliable"] == 1
    assert _row(card, "villagers", "before Feudal") == ("before Feudal", 22.0, 1)
    assert _row(card, "age", "Feudal") == ("Feudal", 600, 1)


def test_timing_skips_null_values():
    # imperial_s absent in one age-reliable game -> averaged only over the game that has it.
    card = _card([_game(1, imperial_s=2400), _game(2, imperial_s=None)])
    assert _row(card, "age", "Imperial") == ("Imperial", 2400, 1)


def test_tech_timing_earliest_and_age_gated():
    games = [_game(1, age_reliable=1, feudal_s=600),
             _game(2, age_reliable=0, feudal_s=None)]
    techs = [
        dict(aoe2_match_id=1, tech="Loom", click_s=300),
        dict(aoe2_match_id=2, tech="Loom", click_s=10),     # unreliable game -> excluded
    ]
    card = _card(games, techs=techs)
    assert _row(card, "tech", "Loom") == ("Loom", 300, 1)


def test_unit_by_type_section_excludes_unused():
    games = [_game(1), _game(2)]
    units = [
        dict(aoe2_match_id=1, category="knight_line", total=10),
        dict(aoe2_match_id=2, category="knight_line", total=20),
        dict(aoe2_match_id=1, category="monk", total=0),    # unused -> excluded
    ]
    card = _card(games, units=units)
    sec = _section(card, "by_type")
    labels = {r[0] for r in sec["rows"]}
    assert "Knights" in labels and "Monks" not in labels
    assert _row(card, "by_type", "Knights") == ("Knights", 15.0, 2)


def test_buildings_military_aggregate_and_section():
    games = [_game(1)]
    builds = [
        dict(aoe2_match_id=1, building="Barracks", count=3),
        dict(aoe2_match_id=1, building="Stable", count=2),
        dict(aoe2_match_id=1, building="Town Center", count=4),
    ]
    card = _card(games, builds=builds)
    # military buildings aggregate = Barracks + Stable = 5, listed first
    assert _row(card, "buildings", "Military buildings") == ("Military buildings", 5.0, 1)
    assert _row(card, "buildings", "Town Centers") == ("Town Centers", 4.0, 1)


def test_sections_dropped_when_no_data():
    # games with no units/techs/builds -> those sections absent entirely.
    card = _card([_game(1), _game(2)])
    keys = {s["key"] for s in card["sections"]}
    assert "tech" not in keys
    assert "by_type" not in keys
    assert "buildings" not in keys
    assert "villagers" in keys and "age" in keys
