"""Unit tests for bot/replay_stats/store.py's identity-resolver read and
write paths.

Ingest reads and writes identity in exactly one place now: the resolver
(bot/identity.py). load_profile_user_map is the read, scoped to the profile_ids
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
    monkeypatch.setattr(store, "identity", fake)

    result = asyncio.run(store.load_profile_user_map([101, 102, 103]))

    assert result == {101: 10, 103: 30}
    assert fake.calls == [101, 102, 103]


def test_load_profile_user_map_omits_profiles_with_no_known_owner(monkeypatch):
    fake = _FakeIdentity({})
    monkeypatch.setattr(store, "identity", fake)

    assert asyncio.run(store.load_profile_user_map([1, 2])) == {}


def test_load_profile_user_map_empty_input_makes_no_calls(monkeypatch):
    fake = _FakeIdentity({1: 2})
    monkeypatch.setattr(store, "identity", fake)

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
    monkeypatch.setattr(store, "identity", fake)
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
    monkeypatch.setattr(store, "identity", fake)
    players = [{"profile_id": 101, "identity": "PlayerA"}, {"profile_id": 999, "identity": "Unknown"}]
    profmap = {101: 10}

    asyncio.run(store._learn_from_ingest(players, profmap))

    assert fake.learn_calls == [(101, 10, "learned", "PlayerA")]


def test_learn_from_ingest_treats_blank_identity_as_no_name(monkeypatch):
    fake = _FakeIdentityWithLearn()
    monkeypatch.setattr(store, "identity", fake)
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
    monkeypatch.setattr(store, "identity", fake)
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
