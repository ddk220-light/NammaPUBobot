"""Pytest bootstrap — install minimal fakes for the `core.*` modules so
test files can import the bot's parser helpers without a live MySQL
connection, a Discord client, or a real ``config.cfg``.

Why this is needed: the bot's module-load wiring is aggressive.
``bot/elo_sync.py`` does ``from core.database import db`` at module
load, and ``core/database.py`` in turn does
``db = init_db(cfg.DB_URI)`` at module load — constructing the DB
adapter from ``config.cfg`` the moment anything reaches into the core
layer. That's fine in production but means the first line of any
unit-test run would blow up trying to read ``config.cfg`` (which
doesn't exist in CI) and dial MySQL.

pytest imports ``conftest.py`` before it imports any test module, so
as long as the fakes here land in ``sys.modules`` before
``from bot.elo_sync import ...`` executes, every downstream ``import``
of the real module is short-circuited and gets the fake back.

This file intentionally does NOT import from the real ``core`` package
— doing so would defeat the whole point (it would trigger the same
config-and-db chain we're trying to avoid).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path


# Make the repo root importable so ``from bot.elo_sync import ...``
# works regardless of which directory pytest was invoked from.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(_REPO_ROOT))


# ─── core.config ─────────────────────────────────────────────────────
# The real module loads config.cfg via SourceFileLoader and exposes it
# as `cfg`. Tests only need a tiny subset of attributes — whatever the
# parsers happen to look up. Use SimpleNamespace so getattr-with-default
# works naturally.
_fake_core_config = types.ModuleType('core.config')
_fake_core_config.cfg = types.SimpleNamespace(
	DB_URI='mysql://test:test@localhost:3306/test',
	DC_OWNER_ID=0,
	PUBOBOT_USER_ID=0,
	LOBBYBOT_USER_ID=0,
	STATUS='',
	HELP='',
	FLAGSHIP_GUILD_IDS=[],
	# bot/replay_stats/store.is_enabled() reads this (it replaced the single-row
	# ops table 007_raw_renames dropped). Its own getattr default is True, so this
	# is here to make the test environment's answer explicit, not incidental.
	REPLAY_INGEST_ENABLED=True,
)
sys.modules['core.config'] = _fake_core_config


# ─── core.console ────────────────────────────────────────────────────
# log.info / log.error / log.debug / log.warning are called from many
# places in the parsers. A null logger that swallows every call is all
# we need for unit tests. `alive` is a bool attribute `think()` reads
# but no parser touches it — kept here for completeness in case future
# tests reach for it.
class _NullLog:
	def __getattr__(self, _name):
		def _noop(*_a, **_k):
			return None
		return _noop


_fake_core_console = types.ModuleType('core.console')
_fake_core_console.log = _NullLog()
_fake_core_console.alive = True
sys.modules['core.console'] = _fake_core_console


# ─── core.database ───────────────────────────────────────────────────
# Every DB method raises — unit tests must not hit the database. If a
# test needs to exercise a function that writes to DB, it should either
# monkeypatch the method or use a proper integration-test harness.
# `types` mimics the MySQL adapter's type registry that `bot/stats/*`
# references at import time for ensure_table() schema definitions.
class _RaisingDB:
	class types:
		int = 'BIGINT'
		bool = 'TINYINT(1)'
		str = 'VARCHAR(191)'
		text = 'VARCHAR(2000)'
		float = 'FLOAT'
		dict = 'MEDIUMTEXT'

	def ensure_table(self, *_a, **_k):
		# No-op: bot/stats/stats.py calls this at import time. In a
		# production boot this actually creates tables; in tests we just
		# let the call pass so the module-level statements succeed.
		return None

	async def _unexpected(self, *_a, **_k):
		raise RuntimeError(
			'core.database.db method called during unit test — mock it '
			'in the test, or move the function under test to a pure '
			'helper that does not touch the DB.'
		)

	select_one = _unexpected
	select = _unexpected
	insert = _unexpected
	insert_many = _unexpected
	update = _unexpected
	delete = _unexpected
	execute = _unexpected
	executemany = _unexpected
	fetchone = _unexpected
	fetchall = _unexpected


_fake_core_database = types.ModuleType('core.database')
_fake_core_database.db = _RaisingDB()
sys.modules['core.database'] = _fake_core_database


# ─── aiohttp (stub) ──────────────────────────────────────────────────
# bot/civ_matcher.py does `import aiohttp` at module load, but only USES it
# (ClientSession, ClientError, ...) inside async functions the unit tests never
# run. CI's pytest job installs only pytest, so a bare stub lets civ_matcher
# import for its pure-helper tests (_load_profile_uid_map) without pulling in
# the full aiohttp runtime dependency. Same trick as the core.* fakes above.
_fake_aiohttp = types.ModuleType('aiohttp')
sys.modules['aiohttp'] = _fake_aiohttp


# ─── aiohttp.web (stub) ──────────────────────────────────────────────
# `bot/web.py` does `from aiohttp import web` and every handler in it ends in
# `web.json_response(...)`. Until stage 5d the only way to test that file was to
# parse it with `ast` and assert on its source (see test_web_identity.py), which
# is how a web endpoint could read a retired table for two stages without a test
# noticing: a source-level assertion cannot tell you what an endpoint RETURNS.
#
# json_response here keeps the payload and the status on the object it returns
# instead of serialising them, so a test can drive a handler and assert on the
# actual response. That is the whole point — these are the real handlers, the
# real SQL and the real payload shaping, with only the transport faked.
class _FakeResponse:
	def __init__(self, payload=None, status=200, text=None, content_type=None):
		self.payload = payload
		self.status = status
		self.text = text
		self.content_type = content_type


class _FakeRouter:
	def __init__(self):
		self.routes = []

	def add_get(self, path, handler):
		self.routes.append(('GET', path, handler))

	def add_post(self, path, handler):
		self.routes.append(('POST', path, handler))


class _FakeApplication:
	def __init__(self):
		self.router = _FakeRouter()


_fake_aiohttp_web = types.ModuleType('aiohttp.web')
_fake_aiohttp_web.json_response = lambda payload=None, status=200: _FakeResponse(payload=payload, status=status)
_fake_aiohttp_web.Response = _FakeResponse
_fake_aiohttp_web.Application = _FakeApplication
_fake_aiohttp_web.AppRunner = object
_fake_aiohttp_web.TCPSite = object


class _FakeHTTPError(Exception):
	def __init__(self, *a, **kw):
		super().__init__(kw.get('text') or (a[0] if a else ''))
		self.location = kw.get('location')


_fake_aiohttp_web.HTTPBadRequest = _FakeHTTPError
_fake_aiohttp_web.HTTPFound = _FakeHTTPError
_fake_aiohttp_web.HTTPForbidden = _FakeHTTPError
_fake_aiohttp_web.HTTPNotFound = _FakeHTTPError
_fake_aiohttp_web.HTTPUnauthorized = _FakeHTTPError
sys.modules['aiohttp.web'] = _fake_aiohttp_web
_fake_aiohttp.web = _fake_aiohttp_web


# ─── nextcord (stub) ─────────────────────────────────────────────────
# Reached only through core/cfg_factory.py (`from nextcord import Guild`, for an
# isinstance check) and core/utils.py (Embed + three utils helpers), both of
# which bot/web.py imports. Permissive on purpose: nothing under test calls into
# it, so a stand-in class that accepts anything is enough, and pinning a fuller
# shape would be inventing an API contract this repo does not own.
class _NextcordStub:
	def __init__(self, *_a, **_k):
		pass

	def __getattr__(self, _name):
		return _NextcordStub()


class _FakeEmbed:
	""" Faithful enough to assert on, unlike the permissive stub above.

	This one is NOT a rubber stamp on purpose. core/utils.py's error_embed /
	ok_embed build an Embed at import-of-core.utils time from whatever
	sys.modules['nextcord'] holds, and core.utils is now imported (via
	core.cfg_factory, via bot/web.py) during collection — before any test's own
	nextcord fake is installed. A stub that swallowed the kwargs would make
	`embed.title` a stub object, and every copy assertion in
	tests/test_identity.py would compare a stub to a string and fail. Keep the
	attribute names in step with tests/test_identity.py's own _FakeEmbed. """

	def __init__(self, title=None, description=None, colour=None, color=None, **_kw):
		self.title = title
		self.description = description
		self.colour = colour if colour is not None else color
		self.color = self.colour
		self.fields = []

	def add_field(self, name=None, value=None, inline=True):
		self.fields.append(dict(name=name, value=value, inline=inline))
		return self

	def set_footer(self, **_kw):
		return self


_fake_nextcord = types.ModuleType('nextcord')
for _name in ('Guild', 'Member', 'TextChannel', 'Role', 'Client', 'Intents'):
	setattr(_fake_nextcord, _name, _NextcordStub)
_fake_nextcord.Embed = _FakeEmbed
_fake_nextcord.Colour = lambda value=0: value
_fake_nextcord.Color = _fake_nextcord.Colour
_fake_nextcord_utils = types.ModuleType('nextcord.utils')
_fake_nextcord_utils.get = lambda *_a, **_k: None
_fake_nextcord_utils.find = lambda *_a, **_k: None
_fake_nextcord_utils.escape_markdown = lambda s: s
_fake_nextcord.utils = _fake_nextcord_utils
sys.modules['nextcord'] = _fake_nextcord
sys.modules['nextcord.utils'] = _fake_nextcord_utils

# core/cfg_factory.py's only other third-party import.
_fake_emoji = types.ModuleType('emoji')
_fake_emoji.emojize = lambda s, **_k: s
_fake_emoji.demojize = lambda s, **_k: s
_fake_emoji.EMOJI_DATA = {}
sys.modules['emoji'] = _fake_emoji


# ─── core.client (stub) ──────────────────────────────────────────────
# The real module subclasses nextcord.Client and builds an Intents object at
# import time, so it is faked outright rather than run against the permissive
# nextcord stub above. bot/web.py reaches `dc` for three things only: looking up
# a user's avatar, walking the guild list, and the readiness flag the health
# endpoint reports. All three answer "nothing" here, which is the state a test
# with no Discord connection is actually in.
class _FakeDiscordClient:
	guilds = ()

	def get_user(self, _user_id):
		return None

	def get_channel(self, _channel_id):
		return None

	def is_ready(self):
		return False


_fake_core_client = types.ModuleType('core.client')
_fake_core_client.dc = _FakeDiscordClient()
sys.modules['core.client'] = _fake_core_client


# ─── bot (package shim) ──────────────────────────────────────────────
# Importing any submodule of `bot` normally runs `bot/__init__.py`,
# which pulls in nextcord and dozens of other runtime-only deps. The
# parsers (bot.elo_sync.parse_elo_message, bot.civ_sync.parse_*) don't
# need any of that, but Python doesn't know.
#
# Trick: pre-register an empty `bot` package in sys.modules with an
# explicit __path__. Now `from bot.elo_sync import X` sees `bot`
# already-imported (so __init__.py is skipped) but can still resolve
# submodules via the __path__ list. This is the same shim pattern
# numpy/scipy use in their own test suites for heavy import graphs.
_fake_bot = types.ModuleType('bot')
_fake_bot.__path__ = [str(_REPO_ROOT / 'bot')]
sys.modules['bot'] = _fake_bot
