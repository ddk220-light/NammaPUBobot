"""The community entity — the multi-tenancy root every new table keys on.

Pure-logic tests against a fake adapter, same pattern as test_migrations.py:
no MySQL involved. bot.community.db and bot.community.cfg are monkeypatched
per test so nothing here touches the real core.database/core.config fakes
that conftest.py installs for every other test file.
"""
import asyncio

import bot.community as community


class _FakeErrors:
	class IntegrityError(Exception):
		pass


class FakeDb:
	errors = _FakeErrors

	def __init__(self):
		self.communities = []  # [{community_id, guild_id, name, retention, created_at}]
		self.community_channels = []  # [{channel_id, community_id, added_at}]
		self.matches = []  # [{match_id, channel_id}] — test fixtures populate this directly
		self.match_replays = []  # [{community_id, match_id, replay_match_id, linked_at}]
		self._next_id = 1
		self.select_calls = 0
		self.update_calls = 0

	def _table(self, table):
		return {
			"communities": self.communities,
			"community_channels": self.community_channels,
			"matches": self.matches,
			"match_replays": self.match_replays,
		}[table]

	async def select_one(self, columns, table, where=None):
		self.select_calls += 1
		where = where or {}
		for row in self._table(table):
			if all(row.get(k) == v for k, v in where.items()):
				return {c: row[c] for c in columns}
		return None

	async def insert(self, table, d, on_duplicate=None):
		if table == "communities":
			community_id = self._next_id
			self._next_id += 1
			self.communities.append({"community_id": community_id, **d})
			return community_id
		if table == "match_replays":
			if on_duplicate == "replace":
				for i, row in enumerate(self.match_replays):
					if row["community_id"] == d["community_id"] and row["match_id"] == d["match_id"]:
						self.match_replays[i] = dict(d)
						return None
			self.match_replays.append(dict(d))
			return None
		self.community_channels.append(dict(d))
		return None

	async def update(self, table, d, keys=None):
		self.update_calls += 1
		keys = keys or {}
		for row in self._table(table):
			if all(row.get(k) == v for k, v in keys.items()):
				row.update(d)


class _Guild:
	def __init__(self, id, name="Test Guild"):
		self.id = id
		self.name = name


def _setup(monkeypatch, flagship_ids=()):
	fake = FakeDb()
	monkeypatch.setattr(community, "db", fake)
	monkeypatch.setattr(community.cfg, "FLAGSHIP_GUILD_IDS", list(flagship_ids))
	community.invalidate_cache()
	return fake


def test_ensure_community_insert_then_select_returns_same_id(monkeypatch):
	fake = _setup(monkeypatch)
	guild = _Guild(111)

	first_id = asyncio.run(community.ensure_community(guild))
	second_id = asyncio.run(community.ensure_community(guild))

	assert first_id == second_id
	assert len(fake.communities) == 1


def test_ensure_community_default_retention_is_lean(monkeypatch):
	fake = _setup(monkeypatch)
	guild = _Guild(222)

	asyncio.run(community.ensure_community(guild))

	assert fake.communities[0]["retention"] == "lean"


def test_ensure_community_flagship_guild_gets_full_retention(monkeypatch):
	fake = _setup(monkeypatch, flagship_ids=(333,))
	guild = _Guild(333)

	asyncio.run(community.ensure_community(guild))

	assert fake.communities[0]["retention"] == "full"


def test_ensure_community_upgrades_existing_lean_row_for_flagship_guild(monkeypatch):
	fake = _setup(monkeypatch)
	guild = _Guild(444)
	asyncio.run(community.ensure_community(guild))
	assert fake.communities[0]["retention"] == "lean"

	# The guild becomes flagship later (e.g. config updated); re-running
	# ensure_community must upgrade the existing row in place.
	monkeypatch.setattr(community.cfg, "FLAGSHIP_GUILD_IDS", [444])
	community.invalidate_cache()
	asyncio.run(community.ensure_community(guild))

	assert len(fake.communities) == 1
	assert fake.communities[0]["retention"] == "full"


def test_ensure_community_never_downgrades_full_to_lean(monkeypatch):
	fake = _setup(monkeypatch, flagship_ids=(555,))
	guild = _Guild(555)
	asyncio.run(community.ensure_community(guild))
	assert fake.communities[0]["retention"] == "full"

	# Guild drops off the flagship list; the row must stay 'full'.
	monkeypatch.setattr(community.cfg, "FLAGSHIP_GUILD_IDS", [])
	community.invalidate_cache()
	asyncio.run(community.ensure_community(guild))

	assert fake.communities[0]["retention"] == "full"


def test_ensure_community_flagship_guild_already_full_is_a_noop(monkeypatch):
	fake = _setup(monkeypatch, flagship_ids=(666,))
	guild = _Guild(666)
	asyncio.run(community.ensure_community(guild))
	assert fake.communities[0]["retention"] == "full"

	# The true no-op branch: the row is already 'full' and the guild is
	# still flagship, so `row["retention"] != "full"` is False and
	# ensure_community must not issue a redundant db.update. This matters
	# because the retention flag gates the stage-4 sweeper — a community
	# wrongly flipped to 'lean' would have its replay detail deleted, and
	# those replays 404 upstream and can never be re-fetched.
	update_calls_before = fake.update_calls
	community_id = asyncio.run(community.ensure_community(guild))

	assert community_id == fake.communities[0]["community_id"]
	assert fake.communities[0]["retention"] == "full"
	assert len(fake.communities) == 1
	assert fake.update_calls == update_calls_before, "already-full row must not trigger db.update"


def test_ensure_community_recovers_from_lost_insert_race(monkeypatch):
	"""Railway rolling deploys run two containers at once, so two concurrent
	ensure_community() calls for a brand-new guild can both see no existing
	row and both attempt the insert — exactly why the migration ledger
	elsewhere uses INSERT IGNORE. With the UNIQUE constraint on
	communities.guild_id, the losing container's insert now raises
	IntegrityError instead of silently creating a duplicate row;
	ensure_community must catch that and re-select so the loser still
	returns the winner's community_id instead of propagating the error."""
	fake = _setup(monkeypatch)
	guild = _Guild(777)
	winner_row = {"community_id": 42, "guild_id": 777, "name": guild.name, "retention": "lean", "created_at": 0}

	real_select_one = fake.select_one
	calls = {"n": 0}

	async def select_one_misses_first_time(columns, table, where=None):
		if table == "communities" and where == {"guild_id": 777}:
			calls["n"] += 1
			if calls["n"] == 1:
				return None  # the race: no row visible yet on our first look
		return await real_select_one(columns, table, where)

	async def insert_loses_the_race(table, d, on_duplicate=None):
		if table == "communities":
			fake.communities.append(winner_row)  # the other container's insert landed first
			raise fake.errors.IntegrityError("duplicate guild_id")
		return await FakeDb.insert(fake, table, d, on_duplicate=on_duplicate)

	monkeypatch.setattr(fake, "select_one", select_one_misses_first_time)
	monkeypatch.setattr(fake, "insert", insert_loses_the_race)

	community_id = asyncio.run(community.ensure_community(guild))

	assert community_id == 42
	assert len(fake.communities) == 1, "the losing container must not create a second row"


def test_attach_channel_is_idempotent(monkeypatch):
	fake = _setup(monkeypatch)

	asyncio.run(community.attach_channel(999, 1))
	asyncio.run(community.attach_channel(999, 1))

	assert len(fake.community_channels) == 1


def test_community_for_channel_returns_none_for_unknown_channel(monkeypatch):
	_setup(monkeypatch)

	result = asyncio.run(community.community_for_channel(123456))

	assert result is None


def test_community_for_channel_caches_a_hit(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(community.attach_channel(42, 7))

	first = asyncio.run(community.community_for_channel(42))
	calls_after_first = fake.select_calls
	second = asyncio.run(community.community_for_channel(42))

	assert first == 7
	assert second == 7
	assert fake.select_calls == calls_after_first, "cached hit must not issue another query"


def test_community_for_channel_does_not_cache_a_miss(monkeypatch):
	"""If a lookup misses right between enrollment's write and its
	invalidate_cache() call — e.g. the write happened in a different
	process/container that never touches this process's cache — caching
	that None would strand it there for the rest of the process's lifetime,
	and every later link_match_replay for that channel would silently skip
	forever. A miss must always re-query instead of sticking."""
	fake = _setup(monkeypatch)

	first = asyncio.run(community.community_for_channel(555))
	assert first is None

	# A write lands without this process's cache being invalidated.
	fake.community_channels.append({"channel_id": 555, "community_id": 9, "added_at": 0})

	second = asyncio.run(community.community_for_channel(555))
	assert second == 9


def test_invalidate_cache_clears_cached_lookups(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(community.attach_channel(1, 1))
	asyncio.run(community.community_for_channel(1))
	calls_before = fake.select_calls

	community.invalidate_cache()
	asyncio.run(community.community_for_channel(1))

	assert fake.select_calls == calls_before + 1


def test_link_match_replay_writes_with_resolved_community(monkeypatch):
	fake = _setup(monkeypatch)
	fake.matches.append({"match_id": 1001, "channel_id": 42})
	asyncio.run(community.attach_channel(42, 7))

	result = asyncio.run(community.link_match_replay(1001, 555000))

	assert result is True
	assert len(fake.match_replays) == 1
	row = fake.match_replays[0]
	assert row["community_id"] == 7
	assert row["match_id"] == 1001
	assert row["replay_match_id"] == 555000
	assert "linked_at" in row


def test_link_match_replay_skips_and_returns_false_when_channel_unenrolled(monkeypatch):
	fake = _setup(monkeypatch)
	fake.matches.append({"match_id": 2002, "channel_id": 99})
	# channel 99 is never attached to any community

	result = asyncio.run(community.link_match_replay(2002, 555001))

	assert result is False
	assert fake.match_replays == []


def test_link_match_replay_skips_and_returns_false_when_match_row_missing(monkeypatch):
	fake = _setup(monkeypatch)
	# no row in fake.matches for match_id 9999

	result = asyncio.run(community.link_match_replay(9999, 555002))

	assert result is False
	assert fake.match_replays == []


def test_link_match_replay_reingest_is_idempotent(monkeypatch):
	fake = _setup(monkeypatch)
	fake.matches.append({"match_id": 3003, "channel_id": 42})
	asyncio.run(community.attach_channel(42, 7))

	first = asyncio.run(community.link_match_replay(3003, 555003))
	second = asyncio.run(community.link_match_replay(3003, 555003))

	assert first is True
	assert second is True
	assert len(fake.match_replays) == 1
