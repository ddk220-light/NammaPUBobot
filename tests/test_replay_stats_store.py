"""Unit tests for bot/replay_stats/store.py's identity-resolver read path.

load_profile_user_map used to run a full-table
`SELECT profile_id, user_id FROM rs_profiles WHERE user_id IS NOT NULL` on
every match write — a read now superseded by the identity resolver
(bot/identity.py), so task 2.3 re-points it there, scoped to just the
profile_ids in the match being written. The rs_profiles WRITE (write_match's
insert_many at the end of the function) is untouched — that table keeps being
written at ingest until a later stage drops it. The one-time
profile_resolved.csv seeder is deleted outright: migration 003_seed_identities
now owns seeding.
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
    # into rs_profiles is retired along with the path/csv-module plumbing it
    # alone needed.
    assert not hasattr(store, "seed_profiles_from_csv")
