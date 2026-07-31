"""Unit tests for the Match Cards read layer (bot/replay_stats/card_query.py)."""

import asyncio

import bot.replay_stats.card_query as cq


class _FakeDB:
    """Returns canned rows per SQL fragment; records every query it was asked."""

    def __init__(self, responses=None, fail_on=None):
        self.responses = responses or {}
        self.fail_on = fail_on or ()
        self.seen = []
        self.asked = []      # (sql, params) -- the params matter as much as the SQL

    async def fetchall(self, sql, params=None):
        self.seen.append(sql)
        self.asked.append((sql, list(params) if params else []))
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
    db = _FakeDB({"replay_buildings": [
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
    sql = next(s for s in db.seen if "replay_buildings" in s)
    assert "building IN" in sql


_STRATEGY_SQL = "l.label AS ckey"
_SPAWN_SQL = "label AS ckey FROM game_labels"


def test_every_strategy_that_fired_is_returned(monkeypatch):
    db = _FakeDB({_STRATEGY_SQL: [
        {"player_number": 1, "ckey": "knight_rush", "title": "Knight Rush"},
        {"player_number": 1, "ckey": "safe_castle", "title": "Safe Castle"},
    ]})
    out = _run(monkeypatch, db)
    assert out["strategies"][1] == ["Knight Rush", "Safe Castle"]


def test_strategy_falls_back_to_a_title_cased_key_when_the_registry_is_missing(monkeypatch):
    db = _FakeDB({_STRATEGY_SQL: [
        {"player_number": 1, "ckey": "cav_archer_rush", "title": None},
    ]})
    out = _run(monkeypatch, db)
    assert out["strategies"][1] == ["Cav Archer Rush"]


def test_strategies_come_from_game_labels_constrained_by_the_stored_kind(monkeypatch):
    """Stage 5c: the chips read the derived table, and `kind` is the allowlist.
    game_labels holds strategy and spawn rows in one namespace, so an unconstrained
    query would render 'spawned alone' as somebody's strategy -- and the card no
    longer carries its own 17-key copy of the list to constrain by."""
    db = _FakeDB()
    _run(monkeypatch, db)
    sql = next(s for s in db.seen if _STRATEGY_SQL in s)
    assert "FROM game_labels" in sql
    assert "cls_results" not in sql
    assert "l.kind=%s" in sql
    assert "'strategy'" not in sql        # the kind is bound as a param, never inlined
    assert not hasattr(cq, "STRATEGY_KEYS")


def test_the_strategy_kind_is_the_one_actually_bound(monkeypatch):
    """The parameter, not just the placeholder: 'kind=%s' with 'spawn' bound would
    satisfy every assertion above and render spawn phrases as strategies."""
    db = _FakeDB()
    _run(monkeypatch, db)
    params = next(p for s, p in db.asked if _STRATEGY_SQL in s)
    assert params == [1, "strategy"]


def test_spawn_keys_map_to_phrases_with_enemy_taking_priority(monkeypatch):
    db = _FakeDB({_SPAWN_SQL: [
        {"player_number": 1, "ckey": "spawn_isolated"},
        {"player_number": 2, "ckey": "spawn_near_ally"},
        {"player_number": 2, "ckey": "spawn_near_enemy"},
    ]})
    out = _run(monkeypatch, db)
    assert out["spawn"][1] == "spawned alone"
    assert out["spawn"][2] == "spawned next to enemy"


def test_spawn_asks_game_labels_for_the_three_sayable_keys_of_the_eleven_stored(monkeypatch):
    """SPAWN_PHRASES stays a DISPLAY subset: kind='spawn' picks the stored category,
    the key list narrows it to what the card can put in a sentence. Merging the two
    concepts in either direction is the failure this pins."""
    from bot.derived import game_labels

    db = _FakeDB()
    _run(monkeypatch, db)
    sql, params = next((s, p) for s, p in db.asked if _SPAWN_SQL in s)
    assert "kind=%s" in sql and "label IN (%s" in sql
    assert params == [1, "spawn", "spawn_near_enemy", "spawn_isolated", "spawn_near_ally"]
    assert len(game_labels.SPAWN_KEYS) == 11


def test_a_player_with_no_spawn_key_gets_no_phrase(monkeypatch):
    out = _run(monkeypatch, _FakeDB())
    assert out["spawn"] == {}


def test_peak_eapm_is_the_max_bucket_per_player(monkeypatch):
    db = _FakeDB({"replay_apm": [
        {"player_number": 1, "peak": 89},
        {"player_number": 2, "peak": 71},
    ]})
    out = _run(monkeypatch, db)
    assert out["peak_eapm"] == {1: 89, 2: 71}


def test_peak_eapm_uses_max_not_an_average(monkeypatch):
    """Bucket rows are sparse, so any average over them is wrong."""
    db = _FakeDB()
    _run(monkeypatch, db)
    sql = next(s for s in db.seen if "replay_apm" in s)
    assert "MAX(actions)" in sql
    assert "AVG" not in sql


def test_an_empty_apm_table_yields_no_peaks_rather_than_zeros(monkeypatch):
    out = _run(monkeypatch, _FakeDB())
    assert out["peak_eapm"] == {}


def test_a_null_peak_is_dropped_rather_than_rendered_as_zero(monkeypatch):
    db = _FakeDB({"replay_apm": [{"player_number": 1, "peak": None}]})
    assert _run(monkeypatch, db)["peak_eapm"] == {}


def test_one_failing_query_does_not_break_the_others(monkeypatch):
    db = _FakeDB(
        {"replay_buildings": [{"player_number": 1, "building": "Farm", "count": 9}]},
        fail_on=("replay_apm",))
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


# ── swept sources ────────────────────────────────────────────────────────
# bot/derived/sweeper.py deletes replay_events / replay_buildings / replay_apm rows
# for a lean community once its derived summaries exist. These pin what the card
# does then: the swept signals go quiet, the retained ones (strategies and spawn,
# now on the forever-retained game_labels) still render, and nothing raises. The
# sweeper ships with DRY_RUN = True and this is part of what has to hold before
# anyone flips it.

_SWEPT_TABLES = ("replay_events", "replay_buildings", "replay_apm")


def test_a_swept_community_loses_the_raw_signals_and_keeps_the_derived_ones(monkeypatch):
    db = _FakeDB({
        _STRATEGY_SQL: [{"player_number": 1, "ckey": "knight_rush", "title": "Knight Rush"}],
        _SPAWN_SQL: [{"player_number": 1, "ckey": "spawn_isolated"}],
    })
    out = _run(monkeypatch, db)

    # Swept: empty, not absent and not zero -- the caller omits each element.
    assert out["buildings"] == {} and out["clicks"] == {} and out["composition"] == {}
    assert out["peak_eapm"] == {}
    # Retained: game_labels is derived-global, retention="forever".
    assert out["strategies"][1] == ["Knight Rush"]
    assert out["spawn"][1] == "spawned alone"


def test_a_fully_swept_match_returns_the_whole_shape_rather_than_raising(monkeypatch):
    out = _run(monkeypatch, _FakeDB())
    assert set(out) == {"buildings", "clicks", "composition", "strategies", "spawn", "peak_eapm"}
    assert all(v == {} for v in out.values())


def test_the_strategy_and_spawn_reads_touch_no_sweepable_table(monkeypatch):
    """The claim above, checked against the SQL rather than trusted: if either chip
    query ever joined back to a sweepable table for a name or a count, a swept
    community would silently lose its chips too."""
    db = _FakeDB()
    _run(monkeypatch, db)
    for fragment in (_STRATEGY_SQL, _SPAWN_SQL):
        sql = next(s for s in db.seen if fragment in s)
        assert not any(t in sql for t in _SWEPT_TABLES), sql


def test_clicks_are_grouped_by_player_in_time_order(monkeypatch):
    db = _FakeDB({"replay_events": [
        {"player_number": 1, "t_s": 300},
        {"player_number": 1, "t_s": 100},
        {"player_number": 2, "t_s": 50},
    ]})
    out = _run(monkeypatch, db)
    assert out["clicks"][1] == [100, 300]
    assert out["clicks"][2] == [50]
