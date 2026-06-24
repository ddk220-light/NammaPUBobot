"""Unit tests for the pure build-timeline aggregation (bot.replay_stats.query). Mirrors the quiz
filter approach: games are pre-filtered to age-reliable standard-map games; each tracked upgrade
lands in the phase where it's researched on average (bucketed by avg click vs avg age-up times)."""
from bot.replay_stats.query import build_timeline, phase_bucket


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
    # a military upgrade never lands in the eco buckets
    assert all("Bracer" not in [n for n, _ in tl["eco"][k]] for k in range(4))


def test_build_timeline_skips_null_age_times():
    games = [dict(feudal_s=600, castle_s=None, imperial_s=None, villagers=90,
                  vil_pre_feudal=20, vil_pre_castle=25, vil_pre_imperial=60,
                  military=30, mil_pre_feudal=0, mil_pre_castle=3, mil_pre_imperial=10)]
    tl = build_timeline(games, [])
    assert tl["ages"][0] == 600
    assert tl["ages"][1] is None and tl["ages"][2] is None
