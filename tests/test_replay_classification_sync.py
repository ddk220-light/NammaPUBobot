"""Unit tests for the live classify -> game_labels ingest path
(nammaoe2bot/ingest/classification_sync.py).

No pytest-asyncio in this repo, so every test here is a plain sync test driving the
coroutine with asyncio.run() -- never `async def test_...`, which pytest would silently
skip and falsely report as passing.
"""
import asyncio

import pytest

from nammaoe2bot.derived import game_labels
from nammaoe2bot.ingest import classification_sync


class FakeDB:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, args):
        self.calls.append(("execute", sql, args))

    async def insert_many(self, table, rows, on_duplicate=None):
        self.calls.append(("insert_many", table, list(rows), on_duplicate))


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


# ── the stage-5c cutover: game_labels yes, cls_* no ──────────────────────
# These two are the pair. The first would pass just as happily with the retired
# cls_* dual-write still in place, so it cannot be the only test of the cutover;
# the second is the one that fails the moment the write comes back.

def test_sync_match_writes_game_labels_from_the_live_classifier():
    fake = FakeDB()
    written = asyncio.run(classification_sync.sync_match(_archer_rush_extracted(), 456, fake))

    calls = _calls_for(fake, "game_labels")
    assert calls[0] == ("execute", "DELETE FROM game_labels WHERE replay_match_id=%s", [99])
    assert calls[1][0] == "insert_many"
    inserted = calls[1][2]
    assert any(r["label"] == "archer_rush" and r["kind"] == "strategy" for r in inserted)
    # replay_match_id stamped, played_at threaded through from sync_match's arg.
    assert all(r["replay_match_id"] == 99 and r["played_at"] == 456 for r in inserted)
    # The return value counts the rows STORED, not the triggers that fired.
    assert written == len(inserted)


def test_sync_match_no_longer_writes_the_cls_tables():
    """Stage 5c stopped the dual-write. cls_results/cls_result_metrics are still
    written on the same ingest by nammaoe2bot/ingest/classifications.py's
    write_extracted_match (store.write_match calls it, and nammaoe2bot/derived/backfill.py
    still reconciles game_labels against cls_results) -- but not from HERE, and not
    a second time."""
    fake = FakeDB()
    asyncio.run(classification_sync.sync_match(_archer_rush_extracted(), 456, fake))

    assert _calls_for(fake, "cls_results") == []
    assert _calls_for(fake, "cls_result_metrics") == []
    # Nothing else either: the whole call is one DELETE + one INSERT of game_labels.
    assert [(c[0], c[1] if c[0] == "insert_many" else "sql") for c in fake.calls] == [
        ("execute", "sql"), ("insert_many", "game_labels")]


def test_write_classification_rows_is_gone():
    """The cls_* writer function itself, not just its call site. Leaving it behind
    as an unused public function invites a caller back."""
    assert not hasattr(classification_sync, "write_classification_rows")


# ── the db_adapter thread ────────────────────────────────────────────────
# game_labels.write() honours the SAME db_adapter sync_match was given: a caller
# passing an adapter is writing one match through one connection, and a derived
# write that ignored it would land the row in a different database.

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

    asyncio.run(classification_sync.sync_match(_archer_rush_extracted(), 456))

    assert [c[0] for c in gl_recorder.calls] == ["execute", "insert_many"]


# ── failure surfaces ─────────────────────────────────────────────────────

def test_a_failing_write_raises_rather_than_reporting_zero_labels():
    """There is no cls_* write left to protect, so the best-effort guard that used
    to swallow this is gone: nammaoe2bot/ingest/jobs.py wraps the call, logs the
    failure against the match id, and the reconciler rewrites the match on its next
    pass. A guard here could only turn the failure into a silent '0 labels' line."""
    class _FailsOnGameLabels(FakeDB):
        async def insert_many(self, table, rows, on_duplicate=None):
            raise RuntimeError("simulated derived-layer write failure")

    with pytest.raises(RuntimeError, match="simulated derived-layer write failure"):
        asyncio.run(classification_sync.sync_match(_archer_rush_extracted(), 456,
                                                   _FailsOnGameLabels()))


def test_a_match_that_classifies_to_nothing_still_clears_its_old_labels():
    """A re-ingest whose triggers no longer fire must not leave the previous run's
    labels behind -- write() deletes first, and sync_match reports 0."""
    empty = {"match": {"aoe2_match_id": 42}, "players": [], "techs": [], "events": []}
    fake = FakeDB()

    written = asyncio.run(classification_sync.sync_match(empty, 456, fake))

    assert written == 0
    assert fake.calls == [("execute", "DELETE FROM game_labels WHERE replay_match_id=%s", [42])]
