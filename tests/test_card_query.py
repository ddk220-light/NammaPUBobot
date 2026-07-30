"""Unit tests for the Match Cards read layer (bot/replay_stats/card_query.py)."""

import asyncio

import bot.replay_stats.card_query as cq


class _FakeDB:
    """Returns canned rows per SQL fragment; records every query it was asked."""

    def __init__(self, responses=None, fail_on=None):
        self.responses = responses or {}
        self.fail_on = fail_on or ()
        self.seen = []

    async def fetchall(self, sql, params=None):
        self.seen.append(sql)
        for fragment in self.fail_on:
            if fragment in sql:
                raise RuntimeError(f"simulated failure for {fragment}")
        for fragment, rows in self.responses.items():
            if fragment in sql:
                return rows
        return []


def _run(monkeypatch, db, match_end_s=600):
    # Object form: the string form cannot resolve core.database (namespace
    # package), and card_query bound its own `db` reference at import.
    monkeypatch.setattr(cq, "db", db)
    return asyncio.run(cq.fetch_card_signals(1, match_end_s))


def test_buildings_are_split_into_farms_and_tcs(monkeypatch):
    db = _FakeDB({"rs_player_buildings": [
        {"player_number": 1, "building": "Farm", "count": 14},
        {"player_number": 1, "building": "Town Center", "count": 3},
        {"player_number": 2, "building": "Farm", "count": 8},
    ]})
    out = _run(monkeypatch, db)
    assert out["buildings"][1] == {"farms": 14, "tcs": 3}
    assert out["buildings"][2] == {"farms": 8, "tcs": 0}


def test_building_query_asks_only_for_farms_and_town_centers(monkeypatch):
    db = _FakeDB()
    _run(monkeypatch, db)
    sql = next(s for s in db.seen if "rs_player_buildings" in s)
    assert "building IN" in sql


def test_every_strategy_that_fired_is_returned(monkeypatch):
    db = _FakeDB({"cls_results c": [
        {"player_number": 1, "ckey": "knight_rush", "title": "Knight Rush"},
        {"player_number": 1, "ckey": "safe_castle", "title": "Safe Castle"},
    ]})
    out = _run(monkeypatch, db)
    assert out["strategies"][1] == ["Knight Rush", "Safe Castle"]


def test_strategy_falls_back_to_a_title_cased_key_when_the_registry_is_missing(monkeypatch):
    db = _FakeDB({"cls_results c": [
        {"player_number": 1, "ckey": "cav_archer_rush", "title": None},
    ]})
    out = _run(monkeypatch, db)
    assert out["strategies"][1] == ["Cav Archer Rush"]


def test_strategy_query_constrains_by_the_key_allowlist(monkeypatch):
    """cls_results holds luck/spawn keys in the same table with no category
    column, so an unconstrained query would render 'All valid spawns' as a
    strategy label."""
    db = _FakeDB()
    _run(monkeypatch, db)
    sql = next(s for s in db.seen if "cls_results c" in s)
    assert "knight_rush" not in sql  # keys are bound as params, never inlined
    assert "IN (%s" in sql
    assert len(cq.STRATEGY_KEYS) == 17
    assert "luck_baseline" not in cq.STRATEGY_KEYS


def test_spawn_keys_map_to_phrases_with_enemy_taking_priority(monkeypatch):
    db = _FakeDB({"`key` AS ckey FROM cls_results": [
        {"player_number": 1, "ckey": "spawn_isolated"},
        {"player_number": 2, "ckey": "spawn_near_ally"},
        {"player_number": 2, "ckey": "spawn_near_enemy"},
    ]})
    out = _run(monkeypatch, db)
    assert out["spawn"][1] == "spawned alone"
    assert out["spawn"][2] == "spawned next to enemy"


def test_a_player_with_no_spawn_key_gets_no_phrase(monkeypatch):
    out = _run(monkeypatch, _FakeDB())
    assert out["spawn"] == {}


def test_peak_eapm_is_the_max_bucket_per_player(monkeypatch):
    db = _FakeDB({"rs_player_apm": [
        {"player_number": 1, "peak": 89},
        {"player_number": 2, "peak": 71},
    ]})
    out = _run(monkeypatch, db)
    assert out["peak_eapm"] == {1: 89, 2: 71}


def test_peak_eapm_uses_max_not_an_average(monkeypatch):
    """Bucket rows are sparse, so any average over them is wrong."""
    db = _FakeDB()
    _run(monkeypatch, db)
    sql = next(s for s in db.seen if "rs_player_apm" in s)
    assert "MAX(actions)" in sql
    assert "AVG" not in sql


def test_an_empty_apm_table_yields_no_peaks_rather_than_zeros(monkeypatch):
    out = _run(monkeypatch, _FakeDB())
    assert out["peak_eapm"] == {}


def test_a_null_peak_is_dropped_rather_than_rendered_as_zero(monkeypatch):
    db = _FakeDB({"rs_player_apm": [{"player_number": 1, "peak": None}]})
    assert _run(monkeypatch, db)["peak_eapm"] == {}


def test_one_failing_query_does_not_break_the_others(monkeypatch):
    db = _FakeDB(
        {"rs_player_buildings": [{"player_number": 1, "building": "Farm", "count": 9}]},
        fail_on=("rs_player_apm",))
    out = _run(monkeypatch, db)
    assert out["buildings"][1]["farms"] == 9
    assert out["peak_eapm"] == {}


def test_every_signal_failing_still_returns_the_full_shape(monkeypatch):
    db = _FakeDB(fail_on=("SELECT",))
    out = _run(monkeypatch, db)
    assert set(out) == {"buildings", "clicks", "composition", "strategies",
                        "spawn", "peak_eapm"}
    assert all(v == {} for v in out.values())


def test_composition_totals_and_post_imperial_split(monkeypatch):
    db = _FakeDB({"e.category": [
        {"player_number": 1, "category": "knight_line", "total": 80, "post_imp": 60},
        {"player_number": 1, "category": "siege", "total": 20, "post_imp": 20},
        {"player_number": 2, "category": "scout", "total": 30, "post_imp": 0},
    ]})
    out = _run(monkeypatch, db)
    assert out["composition"][1]["composition"] == {"knight_line": 80, "siege": 20}
    assert out["composition"][1]["post_imperial"] == 80
    assert out["composition"][2]["post_imperial"] == 0


def test_composition_joins_on_player_number_and_filters_to_military(monkeypatch):
    db = _FakeDB()
    _run(monkeypatch, db)
    sql = next(s for s in db.seen if "e.category" in s)
    assert "g.player_number=e.player_number" in sql
    assert "is_military=1" in sql
    assert "profile_id" not in sql


def test_clicks_are_grouped_by_player_in_time_order(monkeypatch):
    db = _FakeDB({"rs_player_events": [
        {"player_number": 1, "t_s": 300},
        {"player_number": 1, "t_s": 100},
        {"player_number": 2, "t_s": 50},
    ]})
    out = _run(monkeypatch, db)
    assert out["clicks"][1] == [100, 300]
    assert out["clicks"][2] == [50]
