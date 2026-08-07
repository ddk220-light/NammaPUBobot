"""Unit tests for the civ-pool reader (nammaoe2bot/features/civs/pools.py) after stage 5c moved it off
its CSV seed and onto the derived-community civ_stats table.

Not to be confused with tests/test_derived_civ_stats.py, which covers the module that
WRITES that table (nammaoe2bot/derived/civ_stats.py). The two modules share a name and nothing
else.

No pytest-asyncio in this repo, so the async reads are driven with asyncio.run() from
sync tests -- an `async def test_...` would be silently skipped and falsely pass.
"""
import asyncio

import pytest

import nammaoe2bot.features.civs.pools as civ_stats


class _RecordingDB:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.asked = []

    async def fetchall(self, sql, params=None):
        self.asked.append((sql, list(params) if params else []))
        return list(self.rows)


def _winrates(rows, community_id=1):
    db = _RecordingDB(rows)
    original = civ_stats.db
    civ_stats.db = db
    try:
        out = asyncio.run(civ_stats.civ_winrates(community_id))
    finally:
        civ_stats.db = original
    return out, db


# ── the read comes from the table ────────────────────────────────────────

def test_winrates_are_computed_from_the_stored_tally():
    out, _ = _winrates([
        {"civ": "Franks", "games": 100, "wins": 55},
        {"civ": "Goths", "games": 80, "wins": 20},
    ])
    assert out["Franks"] == {"civ": "Franks", "games": 100, "winrate": 0.55}
    assert out["Goths"]["winrate"] == 0.25


def test_the_read_is_scoped_to_one_community_and_gated_by_the_sample_floor():
    """civ_stats is keyed (community_id, civ): two communities sharing a player
    must be able to disagree about their civ meta, which an unscoped read would
    silently merge."""
    _, db = _winrates([], community_id=7)
    sql, params = db.asked[0]
    assert "FROM civ_stats" in sql
    assert "community_id=%s" in sql and "games >= %s" in sql
    assert params == [7, civ_stats.MIN_CIV_GAMES]


def test_the_read_does_not_aggregate_civ_picks_live():
    """That GROUP BY was this module's other retired source. It scanned every
    channel in the database regardless of community, which is the bug the derived
    table exists to fix -- so falling back to it would restore the bug."""
    _, db = _winrates([])
    assert "civ_picks" not in db.asked[0][0]
    assert "GROUP BY" not in db.asked[0][0]


def test_an_empty_table_yields_no_pool_rather_than_a_stale_snapshot():
    """The whole point of deleting the CSV seed: a community with no computed
    stats yet gets nothing, and the caller renders no pool. It must NOT get last
    April's frozen numbers presented as its own."""
    out, _ = _winrates([])
    assert out == {}


def test_a_zero_games_row_is_skipped_rather_than_dividing_by_zero():
    out, _ = _winrates([{"civ": "Franks", "games": 0, "wins": 0},
                        {"civ": "Goths", "games": 60, "wins": 30}])
    assert set(out) == {"Goths"}


# ── no CSV path remains ──────────────────────────────────────────────────
# Asserted against the module's namespace rather than its text: the docstring
# still explains what was deleted and why, and must be free to say "CSV".

def test_no_csv_seed_survives_anywhere_in_the_module():
    for gone in ("csv", "Path", "_ELO_DATA_PATH", "_civ_elo_data",
                 "load_civ_elo_stats", "get_all_civs", "civ_elo_from_db"):
        assert not hasattr(civ_stats, gone), f"{gone} is back in nammaoe2bot/features/civs/pools.py"


def test_pick_balanced_teams_has_no_data_source_of_its_own():
    """`civ_data` is required, not defaulted. A pure function that can silently
    fall back to a module-global seed is exactly how the CSV stayed live for a
    year after the DB copy existed."""
    with pytest.raises(TypeError):
        civ_stats.pick_balanced_teams()


# ── the pool itself ──────────────────────────────────────────────────────

def _civs(n):
    return {f"Civ{i}": {"civ": f"Civ{i}", "games": 100, "winrate": 0.40 + i / 100}
            for i in range(n)}


def test_no_data_means_no_pool():
    assert civ_stats.pick_balanced_teams({}) is None


def test_two_teams_of_five_are_drafted_from_the_supplied_data():
    team_a, team_b = civ_stats.pick_balanced_teams(_civs(15))
    assert len(team_a) == 5 and len(team_b) == 5
    names = [c["civ"] for c in team_a + team_b]
    assert len(set(names)) == 10
    assert [c["winrate"] for c in team_a] == sorted(
        (c["winrate"] for c in team_a), reverse=True)


def test_excluded_civs_are_left_out_when_the_pool_is_wide_enough():
    out = civ_stats.pick_balanced_teams(_civs(15), excluded_civs=["civ0", "CIV1"])
    names = {c["civ"] for c in out[0] + out[1]}
    assert "Civ0" not in names and "Civ1" not in names


# ── get_today_civs still asks its own question ───────────────────────────

def test_today_civs_reads_civ_picks_and_not_the_lifetime_tally():
    """A per-channel, same-day question. civ_stats is a per-community lifetime
    tally with neither a channel nor a time dimension, so it cannot answer it."""
    class _Chan:
        id = 42

    db = _RecordingDB([{"civ": "Franks"}, {"civ": None}])
    original = civ_stats.db
    civ_stats.db = db
    try:
        out = asyncio.run(civ_stats.get_today_civs(_Chan()))
    finally:
        civ_stats.db = original

    assert out == {"Franks"}
    sql, params = db.asked[0]
    assert "FROM civ_picks" in sql and "channel_id=%s" in sql
    assert params[0] == 42
