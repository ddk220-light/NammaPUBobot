"""The identity resolver — the single answer to "who is this person" that
later tasks re-point the four fragmented readers (player_profile_map.csv,
profile_resolved.csv, rs_profiles, qc_profile_map) at.

Pure-logic tests against a fake adapter, same pattern as test_community.py:
no MySQL involved. bot.identity.db is monkeypatched per test so nothing here
touches the real core.database fake conftest.py installs for every other
test file.
"""
import asyncio

import bot.identity as identity


class FakeDb:
	def __init__(self):
		self.identities = []  # [{profile_id, user_id, aoe2_name, confidence, first_seen_at, last_seen_at}]
		self.identity_aliases = []  # [{community_id, user_id, nick, updated_at}]
		self.select_calls = 0

	def _table(self, table):
		return {
			"identities": self.identities,
			"identity_aliases": self.identity_aliases,
		}[table]

	async def select_one(self, columns, table, where=None):
		self.select_calls += 1
		where = where or {}
		for row in self._table(table):
			if all(row.get(k) == v for k, v in where.items()):
				return {c: row.get(c) for c in columns}
		return None

	async def select(self, columns, table, where=None, **_kwargs):
		self.select_calls += 1
		where = where or {}
		return [
			{c: row.get(c) for c in columns}
			for row in self._table(table)
			if all(row.get(k) == v for k, v in where.items())
		]

	async def insert(self, table, d, on_duplicate=None):
		rows = self._table(table)
		if on_duplicate == "replace":
			pk = {"identities": ("profile_id",), "identity_aliases": ("community_id", "user_id")}[table]
			for i, row in enumerate(rows):
				if all(row.get(k) == d.get(k) for k in pk):
					rows[i] = dict(d)
					return None
		rows.append(dict(d))
		return None

	async def update(self, table, d, keys=None):
		keys = keys or {}
		for row in self._table(table):
			if all(row.get(k) == v for k, v in keys.items()):
				row.update(d)


def _setup(monkeypatch):
	fake = FakeDb()
	monkeypatch.setattr(identity, "db", fake)
	identity.invalidate_cache()
	return fake


# ─── parse_seed_csv ─────────────────────────────────────────────────────

def test_parse_seed_csv_profile_map_shape():
	text = (
		"user_id,nick,aoe2_name,profile_id,country\n"
		"238042803093897216,fenrir05,Fenrir,209754,us\n"
	)
	rows = identity.parse_seed_csv(text, "profile_map")
	assert rows == [dict(profile_id=209754, user_id=238042803093897216, aoe2_name="Fenrir")]


def test_parse_seed_csv_resolved_shape():
	text = (
		"profile_id,user_id,nick,aoe2_name,source,appearances\n"
		"12297184,786488329864478751,guruGreatest,GuruGreatest,seed,31\n"
	)
	rows = identity.parse_seed_csv(text, "resolved")
	assert rows == [dict(profile_id=12297184, user_id=786488329864478751, aoe2_name="GuruGreatest")]


def test_parse_seed_csv_keeps_row_with_empty_user_id():
	text = (
		"profile_id,user_id,nick,aoe2_name,source,appearances\n"
		"24413606,,,SomeName,unmapped,1\n"
	)
	rows = identity.parse_seed_csv(text, "resolved")
	assert rows == [dict(profile_id=24413606, user_id=None, aoe2_name="SomeName")]


def test_parse_seed_csv_skips_row_with_non_numeric_profile_id():
	text = (
		"user_id,nick,aoe2_name,profile_id,country\n"
		"695309066771365970,thelivi,Mr.Livi / Mr. X,17841676 / 2885693,in\n"
	)
	rows = identity.parse_seed_csv(text, "profile_map")
	assert rows == []


def test_parse_seed_csv_skips_row_with_non_numeric_user_id():
	text = (
		"user_id,nick,aoe2_name,profile_id,country\n"
		"not-a-number,fenrir05,Fenrir,209754,us\n"
	)
	rows = identity.parse_seed_csv(text, "profile_map")
	assert rows == []


def test_parse_seed_csv_skips_row_with_missing_profile_id():
	text = (
		"user_id,nick,aoe2_name,profile_id,country\n"
		"284337490712592386,srinimama,,,\n"
	)
	rows = identity.parse_seed_csv(text, "profile_map")
	assert rows == []


def test_parse_seed_csv_returns_empty_list_for_header_only_text():
	text = "user_id,nick,aoe2_name,profile_id,country\n"
	assert identity.parse_seed_csv(text, "profile_map") == []


# ─── learn: precedence ──────────────────────────────────────────────────

def test_learn_inserts_new_row(monkeypatch):
	fake = _setup(monkeypatch)

	asyncio.run(identity.learn(111, 222, "learned", aoe2_name="Foo"))

	assert len(fake.identities) == 1
	row = fake.identities[0]
	assert row["profile_id"] == 111
	assert row["user_id"] == 222
	assert row["aoe2_name"] == "Foo"
	assert row["confidence"] == "learned"
	assert row["first_seen_at"] == row["last_seen_at"]


def test_learn_does_not_lower_confidence(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "manual"))

	asyncio.run(identity.learn(111, 333, "seed"))

	assert fake.identities[0]["confidence"] == "manual"


def test_learn_does_not_overwrite_manual_mapping_with_learned(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "manual", aoe2_name="RealName"))

	asyncio.run(identity.learn(111, 999, "learned", aoe2_name="WrongGuess"))

	row = fake.identities[0]
	assert row["user_id"] == 222
	assert row["aoe2_name"] == "RealName"
	assert row["confidence"] == "manual"


def test_learn_does_overwrite_seed_mapping_with_manual(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "seed", aoe2_name="Guess"))

	asyncio.run(identity.learn(111, 999, "manual", aoe2_name="Confirmed"))

	row = fake.identities[0]
	assert row["user_id"] == 999
	assert row["aoe2_name"] == "Confirmed"
	assert row["confidence"] == "manual"


def test_learn_always_bumps_last_seen_at_even_when_blocked(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "manual"))
	fake.identities[0]["last_seen_at"] = 1

	asyncio.run(identity.learn(111, 333, "seed"))

	assert fake.identities[0]["last_seen_at"] != 1


def test_learn_same_confidence_updates_user_id(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "learned"))

	asyncio.run(identity.learn(111, 333, "learned"))

	assert fake.identities[0]["user_id"] == 333
	assert fake.identities[0]["confidence"] == "learned"


def test_learn_preserves_aoe2_name_when_not_provided(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "seed", aoe2_name="Original"))

	asyncio.run(identity.learn(111, 222, "learned"))

	assert fake.identities[0]["aoe2_name"] == "Original"


# ─── profiles_for_users / user_for_profile ──────────────────────────────

def test_profiles_for_users_groups_multiple_profiles_under_one_user(monkeypatch):
	_setup(monkeypatch)
	asyncio.run(identity.learn(1, 555, "seed"))
	asyncio.run(identity.learn(2, 555, "seed"))
	asyncio.run(identity.learn(3, 666, "seed"))

	result = asyncio.run(identity.profiles_for_users([555, 666]))

	assert sorted(result[555]) == [1, 2]
	assert result[666] == [3]


def test_profiles_for_users_omits_users_with_no_known_profile(monkeypatch):
	_setup(monkeypatch)
	asyncio.run(identity.learn(1, 555, "seed"))

	result = asyncio.run(identity.profiles_for_users([555, 12345]))

	assert 12345 not in result


def test_user_for_profile_returns_none_for_unknown_profile(monkeypatch):
	_setup(monkeypatch)

	assert asyncio.run(identity.user_for_profile(999999)) is None


def test_user_for_profile_returns_known_user(monkeypatch):
	_setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "seed"))

	assert asyncio.run(identity.user_for_profile(111)) == 222


# ─── cache invalidation ─────────────────────────────────────────────────

def test_cache_is_invalidated_by_a_write(monkeypatch):
	_setup(monkeypatch)
	asyncio.run(identity.learn(1, 555, "seed"))
	first = asyncio.run(identity.profiles_for_users([555, 666]))
	assert 666 not in first

	asyncio.run(identity.learn(2, 666, "seed"))
	second = asyncio.run(identity.profiles_for_users([555, 666]))

	assert second[666] == [2]


def test_invalidate_cache_forces_a_requery(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(1, 555, "seed"))
	asyncio.run(identity.profiles_for_users([555]))
	calls_after_first = fake.select_calls

	identity.invalidate_cache()
	asyncio.run(identity.profiles_for_users([555]))

	assert fake.select_calls > calls_after_first


# ─── set_nick / nick_for ────────────────────────────────────────────────

def test_nick_for_returns_none_when_unset(monkeypatch):
	_setup(monkeypatch)

	assert asyncio.run(identity.nick_for(1, 555)) is None


def test_set_nick_then_nick_for_round_trips(monkeypatch):
	_setup(monkeypatch)

	asyncio.run(identity.set_nick(1, 555, "Foo"))

	assert asyncio.run(identity.nick_for(1, 555)) == "Foo"


def test_set_nick_is_scoped_per_community(monkeypatch):
	_setup(monkeypatch)
	asyncio.run(identity.set_nick(1, 555, "Foo"))

	asyncio.run(identity.set_nick(2, 555, "Bar"))

	assert asyncio.run(identity.nick_for(1, 555)) == "Foo"
	assert asyncio.run(identity.nick_for(2, 555)) == "Bar"


def test_set_nick_overwrites_existing_nick_for_same_community_and_user(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(identity.set_nick(1, 555, "Foo"))

	asyncio.run(identity.set_nick(1, 555, "Renamed"))

	assert len(fake.identity_aliases) == 1
	assert asyncio.run(identity.nick_for(1, 555)) == "Renamed"
