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
# game_labels.write() honours the SAME db_adapter sync_match's cls_* writes
# use. That matters: a caller passing an adapter is writing one match through
# one connection, and a derived write that ignored it would land this match's
# cls_* rows in the caller's database and its game_labels rows in the bot's.
# So these tests read the game_labels calls off the db_adapter they passed.


def _calls_for(fake, table):
    """Every recorded call naming `table`, in order. Both a DELETE (whose table
    is inside the SQL) and an insert_many (whose table is an argument)."""
    return [
        call for call in fake.calls
        if (call[0] == "execute" and table in call[1])
        or (call[0] == "insert_many" and call[1] == table)
    ]


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
    asyncio.run(classification_sync.sync_match(_archer_rush_extracted(), 456, fake))

    calls = _calls_for(fake, "game_labels")
    assert calls[0] == ("execute", "DELETE FROM game_labels WHERE replay_match_id=%s", [99])
    assert calls[1][0] == "insert_many"
    inserted = calls[1][2]
    assert any(r["label"] == "archer_rush" and r["kind"] == "strategy" for r in inserted)
    # replay_match_id stamped, played_at threaded through from sync_match's arg.
    assert all(r["replay_match_id"] == 99 and r["played_at"] == 456 for r in inserted)


def test_sync_match_sends_the_derived_write_to_the_db_adapter_it_was_given():
    # The split-brain guard: with an adapter passed, NOTHING may reach the
    # module-global db. gl_recorder stands in for that global and must stay
    # untouched, while the adapter sees the full DELETE + INSERT.
    fake = FakeDB()
    gl_recorder = FakeDB()
    original_db = game_labels.db
    game_labels.db = gl_recorder
    try:
        asyncio.run(classification_sync.sync_match(_archer_rush_extracted(), 456, fake))
    finally:
        game_labels.db = original_db

    assert gl_recorder.calls == []
    assert len(_calls_for(fake, "game_labels")) == 2


def test_sync_match_still_uses_the_module_global_db_when_given_no_adapter(monkeypatch):
    # And the other half: with no adapter, the module-global is still the writer,
    # so the ordinary ingest path is unchanged.
    gl_recorder = FakeDB()
    monkeypatch.setattr(game_labels, "db", gl_recorder)
    monkeypatch.setattr(classification_sync, "db", FakeDB())

    asyncio.run(classification_sync.sync_match(_archer_rush_extracted(), 456))

    assert [c[0] for c in gl_recorder.calls] == ["execute", "insert_many"]


def test_sync_match_survives_a_game_labels_write_failure():
    # A bug in the derived-layer mapping/write must never cost the cls_*
    # write that already succeeded -- best-effort per the task brief.
    class _FailsOnGameLabels(FakeDB):
        async def insert_many(self, table, rows, on_duplicate=None):
            if table == "game_labels":
                raise RuntimeError("simulated derived-layer write failure")
            await super().insert_many(table, rows, on_duplicate)

    fake = _FailsOnGameLabels()
    counts = asyncio.run(classification_sync.sync_match(_archer_rush_extracted(), 456, fake))

    assert counts[0] >= 1
    # cls_* rows were still inserted despite game_labels blowing up.
    assert fake.calls[2][0] == "insert_many"
    assert fake.calls[2][1] == "cls_results"
    assert _calls_for(fake, "game_labels")[-1][0] == "execute"  # the insert never landed
