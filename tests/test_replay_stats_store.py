"""Unit tests for bot/replay_stats/store.py's identity-resolver read and
write paths.

Ingest reads and writes identity in exactly one place now: the resolver
(nammaoe2bot/features/identity/resolver.py). load_profile_user_map is the read, scoped to the profile_ids
in the match being written; _learn_from_ingest is the write, feeding back the
in-game name and last-seen time this replay just observed. The legacy
replay-side profile table that ingest used to upsert alongside them is no
longer written or declared, and the one-time CSV seeder is gone — migration
003_seed_identities owns seeding.

The name _learn_from_ingest passes is the player's own name out of the REPLAY
(extract_match's `identity`), never a Discord nickname; that substitution is
what polluted the resolver's aoe2_name column in production, and
tests/test_extract_identity.py pins the fixed expression.
"""
import asyncio

from bot.replay_stats import store


class _FakeIdentity:
    def __init__(self, owners=None):
        self.owners = dict(owners or {})  # profile_id -> user_id
        self.calls = []

    async def user_for_profile(self, profile_id):
        self.calls.append(profile_id)
        return self.owners.get(profile_id)


def test_load_profile_user_map_consults_identity_resolver(monkeypatch):
    fake = _FakeIdentity({101: 10, 103: 30})
    monkeypatch.setattr(store, "resolver", fake)

    result = asyncio.run(store.load_profile_user_map([101, 102, 103]))

    assert result == {101: 10, 103: 30}
    assert fake.calls == [101, 102, 103]


def test_load_profile_user_map_omits_profiles_with_no_known_owner(monkeypatch):
    fake = _FakeIdentity({})
    monkeypatch.setattr(store, "resolver", fake)

    assert asyncio.run(store.load_profile_user_map([1, 2])) == {}


def test_load_profile_user_map_empty_input_makes_no_calls(monkeypatch):
    fake = _FakeIdentity({1: 2})
    monkeypatch.setattr(store, "resolver", fake)

    assert asyncio.run(store.load_profile_user_map([])) == {}
    assert fake.calls == []


def test_csv_seeder_is_gone():
    # migration 003_seed_identities now owns seeding; the one-time CSV loader
    # is retired along with the path/csv-module plumbing it alone needed.
    assert not hasattr(store, "seed_profiles_from_csv")


# ─── _learn_from_ingest ───────────────────────────────────────────────────

class _FakeIdentityWithLearn(_FakeIdentity):
    """Extends _FakeIdentity with a learn() that records calls, and can be
    told to raise for specific profile_ids to exercise the best-effort
    per-player error handling."""

    def __init__(self, owners=None, learn_error_for=None):
        super().__init__(owners)
        self.learn_calls = []  # [(profile_id, user_id, source, aoe2_name), ...]
        self._learn_error_for = set(learn_error_for or ())

    async def learn(self, profile_id, user_id, source, aoe2_name=None):
        if profile_id in self._learn_error_for:
            raise RuntimeError(f"simulated learn() failure for profile_id={profile_id}")
        self.learn_calls.append((profile_id, user_id, source, aoe2_name))


class _FakeLog:
    def __init__(self):
        self.error_calls = []

    def error(self, msg):
        self.error_calls.append(msg)


def test_learn_from_ingest_learns_every_resolved_profile(monkeypatch):
    fake = _FakeIdentityWithLearn()
    monkeypatch.setattr(store, "resolver", fake)
    players = [
        {"profile_id": 101, "identity": "PlayerA"},
        {"profile_id": 102, "identity": "PlayerB"},
    ]
    profmap = {101: 10, 102: 20}

    asyncio.run(store._learn_from_ingest(players, profmap))

    assert fake.learn_calls == [
        (101, 10, "learned", "PlayerA"),
        (102, 20, "learned", "PlayerB"),
    ]


def test_learn_from_ingest_skips_profiles_with_no_resolved_owner(monkeypatch):
    # profmap only knows about profiles the identity resolver already
    # resolved (see load_profile_user_map above) -- a profile_id absent from
    # it is not "learned" from thin air.
    fake = _FakeIdentityWithLearn()
    monkeypatch.setattr(store, "resolver", fake)
    players = [{"profile_id": 101, "identity": "PlayerA"}, {"profile_id": 999, "identity": "Unknown"}]
    profmap = {101: 10}

    asyncio.run(store._learn_from_ingest(players, profmap))

    assert fake.learn_calls == [(101, 10, "learned", "PlayerA")]


def test_learn_from_ingest_treats_blank_identity_as_no_name(monkeypatch):
    fake = _FakeIdentityWithLearn()
    monkeypatch.setattr(store, "resolver", fake)
    players = [{"profile_id": 101, "identity": ""}]
    profmap = {101: 10}

    asyncio.run(store._learn_from_ingest(players, profmap))

    assert fake.learn_calls == [(101, 10, "learned", None)]


def test_learn_from_ingest_is_best_effort_across_players(monkeypatch):
    """A single player's identity.learn() failing must not stop the rest
    from being learned, and must never propagate -- replay ingest's raw
    parse output is irreplaceable (replays 404 upstream once they expire),
    so an identity write failing can never be allowed to break ingest."""
    fake = _FakeIdentityWithLearn(learn_error_for={101})
    fake_log = _FakeLog()
    monkeypatch.setattr(store, "resolver", fake)
    monkeypatch.setattr(store, "log", fake_log)
    players = [
        {"profile_id": 101, "identity": "PlayerA"},
        {"profile_id": 102, "identity": "PlayerB"},
    ]
    profmap = {101: 10, 102: 20}

    asyncio.run(store._learn_from_ingest(players, profmap))  # must not raise

    assert fake.learn_calls == [(102, 20, "learned", "PlayerB")]
    assert len(fake_log.error_calls) == 1
    assert "101" in fake_log.error_calls[0]


# ─── write_match's best-effort derived-layer guard ────────────────────────
# game_stats is computed and written inside write_match, behind a try/except
# that logs and continues. That guard is the whole reason the derived layer can
# be added to the ingest path at all: the raw parse write_match has already
# committed is irreplaceable (replays 404 upstream once they expire), so a bug
# in the medal maths must never be able to cost it. The equivalent guard on the
# game_labels side is covered by tests/test_replay_classification_sync.py; this
# is its counterpart, and it exists because replacing the log.error below with a
# bare `raise` previously passed the entire suite.

class _FakeDB:
    """Records writes and answers reads with nothing. Every optional post-step
    in write_match is independently guarded, so the ones this test does not care
    about degrade to logged errors on their own -- which is exactly the shape
    production has when a downstream table is missing."""

    def __init__(self):
        self.inserted = []

    async def execute(self, sql, args=None):
        return None

    async def insert(self, table, row, on_duplicate=None):
        self.inserted.append(table)

    async def insert_many(self, table, rows, on_duplicate=None):
        self.inserted.append(table)

    async def fetchall(self, sql, args=None):
        return []

    async def fetchone(self, sql, args=None):
        return None

    async def select_one(self, columns, table, where=None):
        return None


def _extracted():
    return {
        "match": {"aoe2_match_id": 4242, "map": "Nomad", "duration_s": 1800, "date": "2026-01-01"},
        "players": [
            {"player_number": 1, "profile_id": 101, "identity": "A", "civ": "Franks",
             "team": "1", "winner": True, "villagers": 90, "military": 20, "eapm": 40},
            {"player_number": 2, "profile_id": 102, "identity": "B", "civ": "Goths",
             "team": "2", "winner": False, "villagers": 80, "military": 30, "eapm": 50},
        ],
        "units": [], "techs": [], "buildings": [], "events": [], "apm": [],
    }


def _run_write_match(monkeypatch, game_stats_write, played_at_epoch=None):
    """write_match with everything stubbed except the game_stats step under test."""
    from bot.derived import game_stats

    fake_db = _FakeDB()
    fake_log = _FakeLog()
    monkeypatch.setattr(store, "db", fake_db)
    monkeypatch.setattr(store, "log", fake_log)
    monkeypatch.setattr(store, "resolver", _FakeIdentityWithLearn())
    monkeypatch.setattr(game_stats, "write", game_stats_write)
    written = asyncio.run(store.write_match(_extracted(), None, 1700000000, "v1",
                                            played_at_epoch=played_at_epoch))
    return written, fake_db, fake_log


def test_write_match_survives_a_game_stats_write_failure(monkeypatch):
    async def _boom(replay_match_id, rows):
        raise RuntimeError("simulated game_stats write failure")

    written, fake_db, fake_log = _run_write_match(monkeypatch, _boom)

    # The raw parse still landed and write_match still reported its player count.
    assert written == 2
    assert "replay_matches" in fake_db.inserted
    assert "replay_players" in fake_db.inserted
    # ...and the failure was reported rather than swallowed silently.
    assert any("game_stats write failed" in m for m in fake_log.error_calls)


def test_write_match_writes_game_stats_for_the_match_it_just_parsed(monkeypatch):
    seen = []

    async def _record(replay_match_id, rows):
        seen.append((replay_match_id, [r["player_number"] for r in rows]))

    written, _fake_db, fake_log = _run_write_match(monkeypatch, _record)

    assert written == 2
    assert seen == [(4242, [1, 2])]
    assert not any("game_stats write failed" in m for m in fake_log.error_calls)


# ─── the cls_results writer is load-bearing, not vestigial ────────────────
# Three separate comments — here, in classification_sync.py, and in
# nammaoe2bot/runtime/data_registry.py — warn that deleting this one call arms a data-loss
# path. Prose does not fail a build: deleting the call outright passed the
# entire suite green, while stage 6 is aimed straight at that call site. This
# is the executable half of those warnings.

def _run_write_match_recording_classifications(monkeypatch, write_extracted_match):
    from bot.derived import game_stats
    from bot.replay_stats import classifications

    async def _noop_game_stats(_replay_match_id, _rows):
        return None

    monkeypatch.setattr(classifications, "write_extracted_match", write_extracted_match)
    monkeypatch.setattr(game_stats, "write", _noop_game_stats)

    fake_db = _FakeDB()
    fake_log = _FakeLog()
    monkeypatch.setattr(store, "db", fake_db)
    monkeypatch.setattr(store, "log", fake_log)
    monkeypatch.setattr(store, "resolver", _FakeIdentityWithLearn())
    written = asyncio.run(store.write_match(_extracted(), None, 1700000000, "v1",
                                            played_at_epoch=1699999999))
    return written, fake_log


def test_the_ingest_path_still_writes_cls_results_for_every_match(monkeypatch):
    """ DO NOT DELETE THIS TEST OR THE CALL IT PINS without first moving
    bot/derived/backfill.py off cls_results.

    write_match calling classifications.write_extracted_match is the ONLY thing
    that populates cls_results for a freshly ingested match. bot/derived/
    backfill.py reconciles game_labels against cls_results on every pass, so a
    match with game_labels and no cls_results is permanently pending — and the
    reconciler's answer to a pending match is to rewrite it from its source,
    i.e. to DELETE the labels the live sync just wrote. backfill.py now refuses
    to act on an unverifiable empty source, which turns that from silent
    permanent data loss into a quarantined match and a log line; the refusal is
    a backstop, not a licence to remove this write. Every new match would sit
    quarantined and unreconciled.
    """
    seen = []

    async def _record(extracted, played_at_epoch=None, rebuild_totals=True):
        seen.append((extracted["match"]["aoe2_match_id"], played_at_epoch))
        return 0

    written, fake_log = _run_write_match_recording_classifications(monkeypatch, _record)

    assert written == 2
    assert seen == [(4242, 1699999999)], (
        "write_match must call classifications.write_extracted_match with the match it "
        "just parsed and that match's played_at epoch — it is the sole writer of "
        "cls_results, which bot/derived/backfill.py reconciles game_labels against")
    assert not any("classification write failed" in m for m in fake_log.error_calls)


def test_a_failing_cls_results_write_is_logged_and_does_not_break_the_ingest(monkeypatch):
    """ The guard around it is correct and stays: the raw parse write_match has
    already committed is irreplaceable (replays 404 upstream once they expire).
    What was missing is that the failure has consequences downstream, so it has
    to be reported rather than swallowed. """
    async def _boom(extracted, played_at_epoch=None, rebuild_totals=True):
        raise RuntimeError("simulated cls_results write failure")

    written, fake_log = _run_write_match_recording_classifications(monkeypatch, _boom)

    assert written == 2
    assert any("classification write failed" in m and "4242" in m for m in fake_log.error_calls)
