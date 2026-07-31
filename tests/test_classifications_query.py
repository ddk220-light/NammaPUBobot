import asyncio
import json
import time

import pytest

import bot.classifications.query as q
from bot.classifications.query import roster, winners_vs_losers, leaderboard_line, leaderboard_text


def _r(identity, pid, winner, metrics):
    return {"identity": identity, "profile_id": pid, "winner": winner, "metrics": metrics}


RESULTS = [
    _r("Alice", 111, True, {"archers_pre_castle": 17.0, "fletching_pre_castle": 1.0, "castle_s": 1400.0}),
    _r("Alice", 111, False, {"archers_pre_castle": 4.0, "fletching_pre_castle": 0.0, "castle_s": 1300.0}),
    _r("Bob", 222, True, {"archers_pre_castle": 12.0, "fletching_pre_castle": 1.0, "castle_s": 1500.0}),
    _r("Bob", 222, None, {"archers_pre_castle": 20.0, "fletching_pre_castle": 1.0, "castle_s": 1600.0}),
]

SPECS = [
    {"metric": "archers_pre_castle", "label": "Archers before Castle", "kind": "count"},
    {"metric": "fletching_pre_castle", "label": "Got Fletching before Castle", "kind": "percent"},
    {"metric": "castle_s", "label": "Castle click", "kind": "seconds"},
]


def test_roster_counts_and_sort():
    rows = roster(RESULTS)
    by = {r["identity"]: r for r in rows}
    assert by["Alice"]["games"] == 2 and by["Alice"]["wins"] == 1 and by["Alice"]["known"] == 2
    assert by["Alice"]["win_pct"] == 50
    assert by["Bob"]["games"] == 2 and by["Bob"]["wins"] == 1 and by["Bob"]["known"] == 1
    assert by["Bob"]["win_pct"] == 100
    assert [r["identity"] for r in rows] == ["Alice", "Bob"]


def test_winners_vs_losers_averages():
    wl = winners_vs_losers(RESULTS, SPECS)
    assert wl["n_winners"] == 2 and wl["n_losers"] == 1
    f = {x["metric"]: x for x in wl["factors"]}
    assert f["archers_pre_castle"]["winners"] == 14.5
    assert f["archers_pre_castle"]["losers"] == 4.0
    assert f["fletching_pre_castle"]["winners"] == 1.0
    assert f["fletching_pre_castle"]["losers"] == 0.0
    assert f["castle_s"]["kind"] == "seconds"


def test_leaderboard_line_format():
    p = {"identity": "thelivi", "games": 33, "wins": 18, "win_pct": 55}
    line = leaderboard_line(p)
    assert "thelivi" in line and "33" in line and "18" in line and "55%" in line


def test_leaderboard_text_fits_all_when_small():
    board = [{"identity": "A" + str(i), "games": 3, "wins": 1, "win_pct": 33} for i in range(5)]
    text, hidden = leaderboard_text(board, 980)
    assert hidden == 0
    assert text.startswith("```") and text.rstrip().endswith("```")
    assert all("A" + str(i) in text for i in range(5))


def test_leaderboard_text_truncates_when_over_budget():
    board = [{"identity": "Player" + str(i), "games": 3, "wins": 1, "win_pct": 33} for i in range(200)]
    text, hidden = leaderboard_text(board, 400)
    assert hidden > 0
    assert "and {} more".format(hidden) in text


# ── fetch_results: the stage-5c read (game_labels, not cls_*) ────────────
# No pytest-asyncio here, so these drive the coroutine with asyncio.run() -- an
# `async def test_...` would be silently skipped and falsely report as passing.

class _RecordingDB:
    """Records every (sql, params) and answers with canned rows."""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.asked = []

    async def fetchall(self, sql, params=None):
        self.asked.append((sql, list(params) if params else []))
        return list(self.rows)


def _label_row(**kw):
    """One joined row as the SELECT returns it: game_labels + game_stats + the
    replay_players name. `evidence` arrives as the MEDIUMTEXT JSON string."""
    base = dict(replay_match_id=1, player_number=1, profile_id=111, identity="Alice",
                winner=1, evidence=json.dumps({"archers_pre_castle": 17.0}))
    base.update(kw)
    return base


def _fetch(rows, *args, **kwargs):
    db = _RecordingDB(rows)
    original = q.db
    q.db = db
    try:
        out = asyncio.run(q.fetch_results(*args, **kwargs))
    finally:
        q.db = original
    return out, db


def test_fetch_results_builds_player_games_from_the_joined_row():
    out, _ = _fetch([_label_row(),
                     _label_row(replay_match_id=2, player_number=3, profile_id=222,
                                identity="Bob", winner=0,
                                evidence=json.dumps({"archers_pre_castle": 4.0}))],
                    "archer_rush", 30)

    assert [r["identity"] for r in out] == ["Alice", "Bob"]
    assert [r["profile_id"] for r in out] == [111, 222]
    assert [r["winner"] for r in out] == [1, 0]
    assert out[0]["metrics"] == {"archers_pre_castle": 17.0}
    # The leaderboard is built straight off this, so the shape has to survive.
    assert roster(out)[0]["games"] == 1


def test_fetch_results_reads_game_labels_and_not_the_retired_cls_tables():
    _, db = _fetch([], "archer_rush", 30)

    sql = db.asked[0][0]
    assert "FROM game_labels" in sql
    assert "cls_results" not in sql and "cls_result_metrics" not in sql
    # One query, not the old two (rows + a second pass for the metrics table).
    assert len(db.asked) == 1


def test_fetch_results_joins_game_stats_for_the_profile_and_the_result():
    """game_labels stores neither, by design. game_stats' PK is exactly
    game_labels' grain minus the label, so the join can neither drop a labelled
    player nor duplicate one -- which joining replay_players on player_number
    could, since its PK does not constrain that column."""
    _, db = _fetch([], "archer_rush", 30)

    sql = db.asked[0][0]
    assert "JOIN game_stats gs ON gs.replay_match_id=gl.replay_match_id" in sql
    assert "gs.player_number=gl.player_number" in sql
    assert "LEFT JOIN replay_players rp" in sql and "rp.profile_id=gs.profile_id" in sql


def test_fetch_results_constrains_the_kind_the_label_was_stored_under():
    """A strategy leaderboard must not be able to return a spawn row, or vice
    versa. The kind is not passed in -- it is looked up from the same allowlist
    that decided what to store."""
    _, strategy_db = _fetch([], "archer_rush", 30)
    _, spawn_db = _fetch([], "spawn_near_gold", 30)

    sql, params = strategy_db.asked[0]
    assert "gl.label=%s AND gl.kind=%s" in sql
    assert params[:2] == ["archer_rush", "strategy"]
    assert spawn_db.asked[0][1][:2] == ["spawn_near_gold", "spawn"]


def test_a_label_that_is_stored_nowhere_returns_nothing_without_querying():
    """luck_baseline is registered upstream and deliberately never stored (it
    fires for every player in every valid Nomad game). Selecting it would return
    zero rows anyway; not issuing the query is how the caller can tell the
    difference between 'nothing in the window' and 'never recorded'."""
    out, db = _fetch([_label_row()], "luck_baseline", 30)
    assert out == [] and db.asked == []

    out, db = _fetch([_label_row()], "brand_new_thing", 30)
    assert out == [] and db.asked == []


def test_the_window_is_applied_to_the_stored_played_at():
    _, db = _fetch([], "archer_rush", 30)
    since = db.asked[0][1][2]
    assert "gl.played_at >= %s" in db.asked[0][0]
    assert abs(since - (int(time.time()) - 30 * 86400)) <= 2


def test_the_player_filter_narrows_by_the_profile_on_game_stats():
    _, db = _fetch([], "archer_rush", 30, profile_ids=[111, 222])
    sql, params = db.asked[0]
    assert "gs.profile_id IN (%s, %s)" in sql
    assert params[3:] == [111, 222]


def test_evidence_survives_both_shapes_it_can_arrive_in():
    """A SELECT returns the MEDIUMTEXT string; a caller testing against freshly
    computed rows never round-tripped through MySQL."""
    out, _ = _fetch([_label_row(evidence={"castle_s": 1400.0}),
                     _label_row(evidence=None)], "archer_rush", 30)
    assert out[0]["metrics"] == {"castle_s": 1400.0}
    assert out[1]["metrics"] == {}


def test_a_malformed_evidence_blob_raises_rather_than_averaging_without_it():
    """It feeds the winners-vs-losers averages. A row silently contributing no
    metric is not a missing row -- it is a row dropped from a denominator that
    still gets printed as a fact."""
    with pytest.raises(json.JSONDecodeError):
        _fetch([_label_row(evidence="{not json")], "archer_rush", 30)
