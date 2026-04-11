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
