"""The community entity — the multi-tenancy root every new table keys on.

Pure-logic tests against a fake adapter, same pattern as test_migrations.py:
no MySQL involved. bot.community.db and bot.community.cfg are monkeypatched
per test so nothing here touches the real core.database/core.config fakes
that conftest.py installs for every other test file.
"""
import asyncio

import bot.community as community


class FakeDb:
	def __init__(self):
		self.communities = []  # [{community_id, guild_id, name, retention, created_at}]
		self.community_channels = []  # [{channel_id, community_id, added_at}]
		self._next_id = 1
		self.select_calls = 0
		self.update_calls = 0

	def _table(self, table):
		return self.communities if table == "communities" else self.community_channels

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


def test_retention_for_returns_stored_value(monkeypatch):
	_setup(monkeypatch, flagship_ids=(1,))
	guild = _Guild(1)
	community_id = asyncio.run(community.ensure_community(guild))

	assert asyncio.run(community.retention_for(community_id)) == "full"


def test_invalidate_cache_clears_cached_lookups(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(community.attach_channel(1, 1))
	asyncio.run(community.community_for_channel(1))
	calls_before = fake.select_calls

	community.invalidate_cache()
	asyncio.run(community.community_for_channel(1))

	assert fake.select_calls == calls_before + 1
