"""Unit tests for the pure player-card aggregation (bot.replay_stats.query.build_card).
Mirrors the quiz filter rules: standard-map games are assumed pre-filtered; age/timing metrics
gate on age_reliable + a real feudal click; timings skip absent values; count averages exclude
games where the player didn't do it."""
from bot.replay_stats.query import build_card, build_timeline, phase_bucket


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


# ── build-timeline chart data ──────────────────────────────────────────────
def test_phase_bucket_boundaries():
    f, c, i = 600, 1200, 2400
    assert phase_bucket(300, f, c, i) == 0       # before Feudal
    assert phase_bucket(600, f, c, i) == 1       # exactly at Feudal -> next phase
    assert phase_bucket(1000, f, c, i) == 1      # Feudal age
    assert phase_bucket(1500, f, c, i) == 2      # Castle age
    assert phase_bucket(3000, f, c, i) == 3      # post-Imperial
    # missing imperial time pushes the late boundary out
    assert phase_bucket(3000, f, c, None) == 3


def test_build_timeline_means_and_buckets():
    games = [
        dict(feudal_s=600, castle_s=1200, imperial_s=2400, villagers=100,
             vil_pre_feudal=20, vil_pre_castle=30, vil_pre_imperial=80,
             military=50, mil_pre_feudal=0, mil_pre_castle=5, mil_pre_imperial=20),
        dict(feudal_s=700, castle_s=1300, imperial_s=2600, villagers=120,
             vil_pre_feudal=24, vil_pre_castle=34, vil_pre_imperial=90,
             military=70, mil_pre_feudal=0, mil_pre_castle=7, mil_pre_imperial=24),
    ]
    techs = [
        dict(tech="Loom", t=300),            # < feudal(650 avg) -> col 0, eco
        dict(tech="Wheelbarrow", t=1000),    # feudal..castle -> col 1, eco
        dict(tech="Fletching", t=1500),      # castle..imperial -> col 2, military
        dict(tech="Bracer", t=3000),         # post-imperial -> col 3, military
    ]
    tl = build_timeline(games, techs)
    assert tl["n"] == 2
    assert tl["vil"] == [22.0, 32.0, 85.0, 110.0]      # per-phase means
    assert tl["mil"] == [0.0, 6.0, 22.0, 60.0]
    assert tl["ages"] == (650.0, 1250.0, 2500.0)
    assert tl["eco"][0] == [("Loom", 300)]
    assert tl["eco"][1] == [("Wheelbarrow", 1000)]
    assert tl["mil_upg"][2] == [("Fletching", 1500)]
    assert tl["mil_upg"][3] == [("Bracer", 3000)]
    # a tech not in the curated lists is ignored
    assert all("Bracer" not in [n for n, _ in tl["eco"][k]] for k in range(4))


def test_build_timeline_skips_null_age_times():
    games = [dict(feudal_s=600, castle_s=None, imperial_s=None, villagers=90,
                  vil_pre_feudal=20, vil_pre_castle=25, vil_pre_imperial=60,
                  military=30, mil_pre_feudal=0, mil_pre_castle=3, mil_pre_imperial=10)]
    tl = build_timeline(games, [])
    assert tl["ages"][0] == 600
    assert tl["ages"][1] is None and tl["ages"][2] is None
