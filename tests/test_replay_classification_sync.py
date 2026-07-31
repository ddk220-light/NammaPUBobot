import asyncio

from bot.derived import game_labels
from bot.replay_stats import classification_sync


class FakeDB:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, args):
        self.calls.append(("execute", sql, args))

    async def insert_many(self, table, rows, on_duplicate=None):
        self.calls.append(("insert_many", table, list(rows), on_duplicate))


def test_write_classification_rows_replaces_match_rows():
    fake = FakeDB()
    result_rows = [{
        "key": "knight_rush",
        "aoe2_match_id": 77,
        "player_number": 1,
        "profile_id": 5,
        "identity": "Al",
        "civ": "Franks",
        "team": "1",
        "winner": 1,
        "played_at": 123,
    }]
    metric_rows = [{
        "key": "knight_rush",
        "aoe2_match_id": 77,
        "player_number": 1,
        "metric": "knights_pre_imperial",
        "value": 9.0,
    }]

    asyncio.run(classification_sync.write_classification_rows(77, result_rows, metric_rows, fake))

    assert fake.calls[0] == ("execute", "DELETE FROM cls_result_metrics WHERE aoe2_match_id=%s", [77])
    assert fake.calls[1] == ("execute", "DELETE FROM cls_results WHERE aoe2_match_id=%s", [77])
    assert fake.calls[2] == ("insert_many", "cls_results", result_rows, "replace")
    assert fake.calls[3] == ("insert_many", "cls_result_metrics", metric_rows, "replace")


def test_sync_match_uses_live_classifier_shape():
    fake = FakeDB()
    extracted = {
        "match": {"aoe2_match_id": 88},
        "players": [
            {
                "player_number": 1,
                "profile_id": 10,
                "identity": "Archer",
                "civ": "Mayans",
                "team": "1",
                "winner": True,
                "feudal_s": 600,
                "castle_s": 1200,
            },
            {
                "player_number": 2,
                "profile_id": 11,
                "identity": "Boomer",
                "civ": "Franks",
                "team": "2",
                "winner": False,
                "feudal_s": 600,
                "castle_s": 700,
            },
        ],
        "techs": [],
        "events": [
            {"player_number": 1, "category": "archer_line", "name": "Archer", "amount": 5, "t_s": 700}
        ],
    }

    counts = asyncio.run(classification_sync.sync_match(extracted, 456, fake))

    assert counts[0] >= 1
    inserted_results = fake.calls[2][2]
    assert any(r["key"] == "archer_rush" and r["aoe2_match_id"] == 88 for r in inserted_results)


# ── sync_match's game_labels dual-write ──────────────────────────────────
# game_labels.write() always goes through bot.derived.game_labels.db (the
# same shape as bot/derived/game_stats.py -- see that module's docstring for
# why), never the `db_adapter` sync_match's cls_* writes use. So these tests
# monkeypatch game_labels.db directly rather than passing a db_adapter.

def _archer_rush_extracted():
    return {
        "match": {"aoe2_match_id": 99},
        "players": [
            {
                "player_number": 1, "profile_id": 10, "identity": "Archer",
                "civ": "Mayans", "team": "1", "winner": True,
                "feudal_s": 600, "castle_s": 1200,
            },
            {
                "player_number": 2, "profile_id": 11, "identity": "Boomer",
                "civ": "Franks", "team": "2", "winner": False,
                "feudal_s": 600, "castle_s": 700,
            },
        ],
        "techs": [],
        "events": [
            {"player_number": 1, "category": "archer_line", "name": "Archer", "amount": 5, "t_s": 700}
        ],
    }


def test_sync_match_writes_game_labels_from_the_same_result_and_metric_rows():
    fake = FakeDB()
    gl_recorder = FakeDB()
    original_db = game_labels.db
    game_labels.db = gl_recorder
    try:
        asyncio.run(classification_sync.sync_match(_archer_rush_extracted(), 456, fake))
    finally:
        game_labels.db = original_db

    assert gl_recorder.calls[0] == ("execute", "DELETE FROM game_labels WHERE replay_match_id=%s", [99])
    assert gl_recorder.calls[1][0] == "insert_many"
    assert gl_recorder.calls[1][1] == "game_labels"
    inserted = gl_recorder.calls[1][2]
    assert any(r["label"] == "archer_rush" and r["kind"] == "strategy" for r in inserted)
    # replay_match_id stamped, played_at threaded through from sync_match's arg.
    assert all(r["replay_match_id"] == 99 and r["played_at"] == 456 for r in inserted)


def test_sync_match_survives_a_game_labels_write_failure():
    # A bug in the derived-layer mapping/write must never cost the cls_*
    # write that already succeeded -- best-effort per the task brief.
    fake = FakeDB()
    original_db = game_labels.db
    game_labels.db = None  # any attribute access inside write() raises AttributeError
    try:
        counts = asyncio.run(classification_sync.sync_match(_archer_rush_extracted(), 456, fake))
    finally:
        game_labels.db = original_db

    assert counts[0] >= 1
    # cls_* rows were still inserted despite game_labels blowing up.
    assert fake.calls[2][0] == "insert_many"
    assert fake.calls[2][1] == "cls_results"
