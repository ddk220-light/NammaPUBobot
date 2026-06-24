"""Unit tests for the pure aggregations in bot.replay_stats. build_timeline buckets upgrades into
phases (quiz-style); build_growth_curve averages each game's cumulative villager/military count
onto a common time grid over the games still live at each t, with a 95% CI and per-point n;
event_rows (shape) turns the per-action timeline into rs_player_events rows with a per-player seq."""
from bot.replay_stats.query import build_growth_curve, build_timeline, phase_bucket
from bot.replay_stats.shape import event_rows


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


def test_build_growth_curve_means_and_decreasing_n():
    games = [
        dict(duration_s=240, feudal_s=120, castle_s=None, imperial_s=None,
             vil=[(0, 2), (60, 2), (120, 2)], mil=[(60, 1), (180, 3)]),
        dict(duration_s=120, feudal_s=60, castle_s=None, imperial_s=None,
             vil=[(0, 4), (120, 2)], mil=[(120, 2)]),
    ]
    c = build_growth_curve(games, grid_step=60)
    # grid runs to the P95-capped duration (234s) -> last grid point 180 (240 > cap, excluded),
    # which guarantees every point has n>=1 (no empty trailing point that would render as None)
    assert c["grid"] == [0, 60, 120, 180]
    assert c["n"] == 2
    # cumulative villager mean per grid point; military likewise
    assert c["vil_mean"] == [3.0, 4.0, 6.0, 6.0]
    assert c["mil_mean"] == [0.0, 0.5, 1.5, 4.0]
    # n decreases as the shorter game ends (only the 240s game is live past 120s)
    assert c["vil_n"] == [2, 2, 2, 1]
    assert c["vil_n"] == c["mil_n"]            # n is keyed on still-live games, same for both series
    # ages average over games (castle/imperial never reached -> None)
    assert c["ages"] == (90.0, None, None)


def test_build_growth_curve_ci_band():
    games = [
        dict(duration_s=120, feudal_s=None, castle_s=None, imperial_s=None, vil=[(0, 2)], mil=[]),
        dict(duration_s=120, feudal_s=None, castle_s=None, imperial_s=None, vil=[(0, 4)], mil=[]),
    ]
    c = build_growth_curve(games, grid_step=60)
    # at t=0: villager values {2,4}, mean 3, a real 95% band straddling the mean
    assert c["vil_mean"][0] == 3.0
    assert c["vil_lo"][0] < 3.0 < c["vil_hi"][0]
    # identical-value military (all 0) -> zero spread -> band collapses onto the mean
    assert c["mil_lo"][0] == c["mil_hi"][0] == c["mil_mean"][0] == 0.0


def test_build_growth_curve_empty():
    assert build_growth_curve([]) is None
    assert build_growth_curve([dict(duration_s=0, vil=[], mil=[])]) is None


def test_build_growth_curve_few_games_full_grid_no_none():
    # Regression for the chart-collapse/None-crash bugs: with < N_MIN games, build returns the FULL
    # grid (truncation is the chart's job), every point has n>=1, and no mean is None -> the chart
    # renders the whole low-confidence curve instead of a 30-second sliver / a crash.
    games = [
        dict(duration_s=1800, feudal_s=600, castle_s=1200, imperial_s=None, vil=[(0, 50)], mil=[(700, 20)]),
        dict(duration_s=1200, feudal_s=650, castle_s=None, imperial_s=None, vil=[(0, 40)], mil=[(800, 10)]),
        dict(duration_s=900, feudal_s=700, castle_s=None, imperial_s=None, vil=[(0, 30)], mil=[]),
    ]
    c = build_growth_curve(games, grid_step=60)
    assert c["n"] == 3 and len(c["grid"]) >= 2
    assert all(x is not None for x in c["vil_mean"])         # no None -> fill_between/plot can't crash
    assert all(x is not None for x in c["mil_mean"])
    assert c["vil_n"][0] == 3 and c["vil_n"][-1] >= 1        # starts below N_MIN, never drops to 0
    assert all(c["vil_n"][i] >= c["vil_n"][i + 1] for i in range(len(c["vil_n"]) - 1))   # non-increasing


def test_event_rows_assigns_per_player_seq_in_time_order():
    events = [
        dict(player_number=2, kind="queue", name="Scout Cavalry", category="scout",
             is_military=True, amount=1, t_s=300),
        dict(player_number=1, kind="queue", name="Villager", category="villager",
             is_military=False, amount=1, t_s=20),
        dict(player_number=1, kind="queue", name="Archer", category="archer_line",
             is_military=True, amount=3, t_s=400),
        dict(player_number=1, kind="queue", name="Villager", category="villager",
             is_military=False, amount=1, t_s=5),
    ]
    rows = event_rows(99, events, {1: 111, 2: 222})
    assert len(rows) == 4
    p1 = [r for r in rows if r["player_number"] == 1]
    assert [r["seq"] for r in p1] == [0, 1, 2]                 # seq is dense, per-player
    assert [r["t_s"] for r in p1] == [5, 20, 400]             # ordered by time
    assert all(r["aoe2_match_id"] == 99 for r in rows)
    assert {r["profile_id"] for r in p1} == {111}
    assert p1[2]["name"] == "Archer" and p1[2]["amount"] == 3
    p2 = [r for r in rows if r["player_number"] == 2][0]
    assert p2["seq"] == 0 and p2["profile_id"] == 222          # seq resets per player
