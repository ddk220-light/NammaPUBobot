"""Unit tests for the derived-community refresh job (nammaoe2bot/derived/refresh.py).

Three things shape this file.

1. THE PENDING PREDICATE IS THE FEATURE. Whether a user is refreshed is decided
   by SQL aggregates compared against stored timestamps, so a fake returning
   canned rows would test the Python glue and leave the actual comparison --
   `<=` vs `<`, which profiles a user's history is drawn from, whether an
   unlinked player is in the implied set at all -- completely unguarded. The
   fake adapter here runs the real SQL against an in-memory sqlite3 database
   (stdlib, never a real server), translating only MySQL's %s placeholders and
   its REPLACE/INSERT IGNORE spellings.

2. THE IDENTITY HOOK IS DRIVEN THROUGH nammaoe2bot/features/identity/resolver.py, never simulated by
   writing an `identities` row by hand. The hook IS `bound_at` being stamped by
   link_self/relink/unlink; a test that wrote the column itself would still pass
   with every one of those stamps deleted.

3. There is no pytest-asyncio in this repo, so an `async def test_...` is
   silently SKIPPED and reports as passing. Every test is sync and drives the
   coroutines with asyncio.run().
"""
import asyncio
import importlib
import inspect
import json
import sqlite3

import pytest

import nammaoe2bot.features.identity.resolver as identity
import nammaoe2bot.derived.boards as boards
import nammaoe2bot.derived.civ_stats as civ_stats
import nammaoe2bot.derived.game_stats as game_stats_mod
import nammaoe2bot.derived.refresh as refresh
import nammaoe2bot.derived.rollups as rollups

_SCHEMA = """
CREATE TABLE communities (
	community_id INTEGER PRIMARY KEY, guild_id INTEGER, name TEXT, retention TEXT, created_at INTEGER);
CREATE TABLE community_channels (
	channel_id INTEGER PRIMARY KEY, community_id INTEGER, added_at INTEGER);
CREATE TABLE match_replays (
	community_id INTEGER, match_id INTEGER, replay_match_id INTEGER, linked_at INTEGER,
	PRIMARY KEY (community_id, match_id));
CREATE TABLE identities (
	profile_id INTEGER PRIMARY KEY, user_id INTEGER, aoe2_name TEXT, confidence TEXT NOT NULL,
	first_seen_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL, bound_at INTEGER NOT NULL DEFAULT 0);
CREATE TABLE identity_conflicts (
	id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id INTEGER NOT NULL,
	claimed_user_id INTEGER NOT NULL, source TEXT NOT NULL, noticed_at INTEGER NOT NULL,
	status TEXT NOT NULL DEFAULT 'open');
CREATE UNIQUE INDEX uniq_identity_conflicts_claim
	ON identity_conflicts (profile_id, claimed_user_id, status);
CREATE TABLE game_stats (
	replay_match_id INTEGER, player_number INTEGER, profile_id INTEGER, civ TEXT, team TEXT,
	winner INTEGER, avg_eapm INTEGER, peak_eapm INTEGER, military_medal INTEGER,
	villager_medal INTEGER, has_production INTEGER NOT NULL, top_units TEXT, computed_at INTEGER,
	played_at INTEGER, compute_version INTEGER NOT NULL DEFAULT 0,
	PRIMARY KEY (replay_match_id, player_number));
CREATE TABLE game_labels (
	replay_match_id INTEGER, player_number INTEGER, label TEXT, kind TEXT, evidence TEXT,
	played_at INTEGER, PRIMARY KEY (replay_match_id, player_number, label));
CREATE TABLE replay_players (
	replay_match_id INTEGER, profile_id INTEGER, player_number INTEGER, identity TEXT,
	eapm INTEGER, feudal_s INTEGER, castle_s INTEGER, imperial_s INTEGER, first_tc_s INTEGER,
	villagers INTEGER, vil_pre_feudal INTEGER, vil_pre_castle INTEGER, vil_pre_imperial INTEGER,
	military INTEGER, mil_pre_feudal INTEGER, mil_pre_castle INTEGER, mil_pre_imperial INTEGER,
	PRIMARY KEY (replay_match_id, profile_id));
CREATE TABLE civ_picks (
	id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id INTEGER, replay_match_id INTEGER,
	aoe2_name TEXT, civ TEXT, at INTEGER, bot_match_id INTEGER, user_id INTEGER, nick TEXT,
	team INTEGER, result TEXT);
CREATE TABLE player_rollups (
	community_id INTEGER, user_id INTEGER, games INTEGER NOT NULL, rollup TEXT NOT NULL,
	computed_at INTEGER NOT NULL, PRIMARY KEY (community_id, user_id));
CREATE TABLE metric_boards (
	community_id INTEGER, metric_id TEXT, board TEXT NOT NULL, computed_at INTEGER NOT NULL,
	PRIMARY KEY (community_id, metric_id));
CREATE TABLE civ_stats (
	community_id INTEGER, civ TEXT, games INTEGER NOT NULL, wins INTEGER NOT NULL,
	losses INTEGER NOT NULL, computed_at INTEGER NOT NULL, PRIMARY KEY (community_id, civ));
"""

T0 = 1_700_000_000   # an ordinary epoch, so MAX_AGE arithmetic is realistic


class _SqliteDB:
	"""Stand-in for nammaoe2bot.runtime.database.db backed by in-memory sqlite3. Never touches a
	real database.

	The write helpers below build the same SQL nammaoe2bot/runtime/database/mysql.py's Adapter
	builds (that module cannot be imported here -- it needs aiomysql, which CI
	does not install), so the placeholder shapes and the REPLACE / INSERT IGNORE
	spellings the production code depends on are exercised rather than bypassed.
	"""

	def __init__(self):
		self.conn = sqlite3.connect(":memory:")
		self.conn.row_factory = sqlite3.Row
		self.conn.executescript(_SCHEMA)

	# ── fixture helpers ───────────────────────────────────────────────────
	def add(self, table, **row):
		cols = ",".join(f"`{c}`" for c in row)
		holes = ",".join(["?"] * len(row))
		self.conn.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({holes})",
		                  list(row.values()))

	def rows(self, table, **where):
		sql = f"SELECT * FROM {table}"
		if where:
			sql += " WHERE " + " AND ".join(f"`{c}`=?" for c in where)
		return [dict(r) for r in self.conn.execute(sql, list(where.values())).fetchall()]

	# ── the adapter surface ───────────────────────────────────────────────
	@staticmethod
	def _sql(sql):
		sql = sql.replace("%s", "?")
		if sql.startswith("REPLACE INTO"):
			return "INSERT OR REPLACE INTO" + sql[len("REPLACE INTO"):]
		if sql.startswith("INSERT IGNORE INTO"):
			return "INSERT OR IGNORE INTO" + sql[len("INSERT IGNORE INTO"):]
		return sql

	async def fetchall(self, sql, args=None):
		return [dict(r) for r in self.conn.execute(self._sql(sql), list(args or [])).fetchall()]

	async def fetchone(self, sql, args=None):
		row = self.conn.execute(self._sql(sql), list(args or [])).fetchone()
		return dict(row) if row is not None else None

	async def execute(self, sql, args=None):
		self.conn.execute(self._sql(sql), list(args or []))

	async def select(self, columns, table, where=None, one=False, **_kw):
		conditions = " WHERE " + " AND ".join(f"`{k}`=%s" for k in where) if where else ""
		sql = "SELECT {} FROM `{}`{}".format(", ".join(columns), table, conditions)
		args = list(where.values()) if where else []
		return await (self.fetchone(sql, args) if one else self.fetchall(sql, args))

	async def select_one(self, *args, **kwargs):
		return await self.select(*args, **kwargs, one=True)

	async def insert(self, table, d, on_duplicate=None):
		action = "REPLACE" if on_duplicate == "replace" else "INSERT"
		ignore = " IGNORE" if on_duplicate == "ignore" else ""
		sql = "{}{} INTO {} ({}) VALUES({})".format(
			action, ignore, table, ", ".join(f"`{c}`" for c in d), ", ".join(["%s"] * len(d)))
		await self.execute(sql, list(d.values()))

	async def insert_many(self, table, it, on_duplicate=None):
		for row in it:
			await self.insert(table, row, on_duplicate=on_duplicate)

	async def update(self, table, d, keys=None):
		keys = keys or {}
		where = " WHERE " + " AND ".join(f"`{k}`=%s" for k in keys) if keys else ""
		sql = "UPDATE {} SET {}{}".format(table, ",".join(f"`{c}`=%s" for c in d), where)
		await self.execute(sql, list(d.values()) + list(keys.values()))

	async def delete(self, table, where=None):
		conditions = " WHERE " + " AND ".join(f"`{k}`=%s" for k in where) if where else ""
		await self.execute(f"DELETE FROM {table}{conditions}", list(where.values()) if where else [])


class _ExplodingDB:
	"""Every read and write raises. Used to prove think() cannot carry a failure
	into on_think, which is the property the whole tick depends on."""

	def __getattr__(self, _name):
		async def _boom(*_a, **_k):
			raise RuntimeError("database is down")
		return _boom


class _RecordingLog:
	"""Stand-in for nammaoe2bot.runtime.console.log that keeps what was written, so the tests can
	assert the pass line carries real counts rather than merely being emitted."""

	def __init__(self):
		self.info_lines = []
		self.error_lines = []
		self.debug_lines = []

	def info(self, message):
		self.info_lines.append(message)

	def error(self, message):
		self.error_lines.append(message)

	def debug(self, message):
		self.debug_lines.append(message)


class _Clock:
	def __init__(self, now=T0):
		self.now = now

	def time(self):
		return self.now


_PATCHED_MODULES = (refresh, rollups, boards, civ_stats, identity)


@pytest.fixture(autouse=True)
def _restore_module_adapters():
	"""Put every module's `db` back after each test.

	_install below rebinds a module-level attribute on five shipped modules and
	used to leave them rebound: after this file ran, nammaoe2bot/derived/rollups.py's
	`db` was still this file's sqlite double for the rest of the session, so a
	later file patching only the shared adapter would silently miss it. That is
	the kind of leak that makes a suite pass in one order and fail in another,
	and it cost tests/test_web_repoint.py's install_db an extra patching pass to
	work around. Snapshot-and-restore is not a workaround: it is what
	monkeypatch would have done had _install taken one, and it works for the
	reload test too, which rebinds the same names on a freshly imported module.
	"""
	saved = [(mod, getattr(mod, "db", None), getattr(mod, "log", None)) for mod in _PATCHED_MODULES]
	try:
		yield
	finally:
		for mod, db, log in saved:
			if db is not None:
				mod.db = db
			if log is not None:
				mod.log = log
		identity.invalidate_cache()


def _install(db, log, module=refresh):
	"""Point every module in the refresh path at the fakes.

	Each of them did `from nammaoe2bot.runtime.database import db` at import time and holds its
	own reference, so patching nammaoe2bot.runtime.database alone would miss them -- which is
	exactly what the restart test needs to be able to redo after a reload.
	_restore_module_adapters above undoes this at the end of every test."""
	for mod in (module, rollups, boards, civ_stats, identity):
		mod.db = db
	module.log = log
	identity.invalidate_cache()


def _fresh():
	db, log = _SqliteDB(), _RecordingLog()
	_install(db, log)
	refresh._rollup_attempts.clear()
	refresh._community_attempts.clear()
	return db, log


def _community(db, community_id=1, channel_id=900):
	db.add("communities", community_id=community_id, guild_id=community_id * 7,
	       name=f"C{community_id}", retention="full", created_at=T0)
	db.add("community_channels", channel_id=channel_id, community_id=community_id, added_at=T0)


def _link(db, profile_id, user_id, bound_at=T0):
	"""Seed a binding directly. Only for SETUP -- every test about the identity
	hook itself goes through nammaoe2bot/features/identity/resolver.py's own write paths."""
	db.add("identities", profile_id=profile_id, user_id=user_id, aoe2_name=f"p{profile_id}",
	       confidence="learned", first_seen_at=T0, last_seen_at=T0, bound_at=bound_at)
	identity.invalidate_cache()


def _game(db, replay_match_id, player_number, profile_id, *, community_id=1, match_id=None,
          computed_at=T0, winner=1, top_units=None, military_medal=None, villager_medal=None,
          eapm=100, villagers=50, first_tc_s=None, played_at=T0):
	"""One replay slot: the raw replay_players row, the derived game_stats row,
	and the community link that owns the replay."""
	db.add("match_replays", community_id=community_id, match_id=match_id or replay_match_id,
	       replay_match_id=replay_match_id, linked_at=T0)
	db.add("replay_players", replay_match_id=replay_match_id, profile_id=profile_id,
	       player_number=player_number, identity=f"name{profile_id}", eapm=eapm,
	       villagers=villagers, first_tc_s=first_tc_s)
	db.add("game_stats", replay_match_id=replay_match_id, player_number=player_number,
	       profile_id=profile_id, civ="Franks", team="1", winner=winner, avg_eapm=eapm,
	       peak_eapm=None, military_medal=military_medal, villager_medal=villager_medal,
	       has_production=1, top_units=json.dumps(top_units or []), computed_at=computed_at,
	       played_at=played_at, compute_version=game_stats_mod.COMPUTE_VERSION)


def _label(db, replay_match_id, player_number, label, kind="strategy"):
	db.add("game_labels", replay_match_id=replay_match_id, player_number=player_number,
	       label=label, kind=kind, evidence=None, played_at=T0)


def _rollup_of(db, community_id, user_id):
	rows = db.rows("player_rollups", community_id=community_id, user_id=user_id)
	return rows[0] if rows else None


# ── attribution ───────────────────────────────────────────────────────────────

def test_multi_profile_history_is_aggregated_into_one_rollup():
	"""MUTANT GUARD (a): resolving only the FIRST of a user's profiles. The
	flagship community has genuine multi-account players (`/identity link ...
	additional: True` exists for them), and a half-history is not an error
	anywhere -- it is a smaller, entirely plausible rollup."""
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1)
	_link(db, 22, 1)
	for mid in (1, 2, 3):
		_game(db, mid, 1, 11)
	for mid in (4, 5):
		_game(db, mid, 1, 22)

	asyncio.run(refresh.drain_rollups(T0 + 10))

	row = _rollup_of(db, 1, 1)
	assert row["games"] == 5, "a user's rollup must span every profile they own"
	assert json.loads(row["rollup"])["medal_rates"]["games_ranked"] == 5


def test_both_profiles_feed_one_split_rather_than_two_halves():
	# The failure a first-profile-only resolution actually produces: each half
	# falls under SPLIT_MIN_GAMES and the strategy line disappears entirely,
	# with no error anywhere.
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1)
	_link(db, 22, 1)
	for mid in (1, 2, 3):
		_game(db, mid, 1, 11)
		_label(db, mid, 1, "archer_rush")
	for mid in (4, 5):
		_game(db, mid, 1, 22)
		_label(db, mid, 1, "archer_rush")

	asyncio.run(refresh.drain_rollups(T0 + 10))

	strategies = json.loads(_rollup_of(db, 1, 1)["rollup"])["strategies"]
	assert strategies == [{"key": "archer_rush", "games": 5, "wins": 5}]


def test_a_game_mgz_could_not_call_leaves_the_splits_alone_but_stays_the_players_game():
	"""game_stats.winner is nullable all the way down from replay_players, and a
	SELECT hands compute_rollup a real None for it. Scored through bool() that is
	a LOSS in every split -- so this drives the whole path, storage included,
	rather than trusting the pure unit tests to have covered the column's type."""
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1)
	for mid in range(1, 6):
		_game(db, mid, 1, 11, winner=1)
		_label(db, mid, 1, "archer_rush")
	for mid in range(6, 9):
		_game(db, mid, 1, 11, winner=None)
		_label(db, mid, 1, "archer_rush")

	asyncio.run(refresh.drain_rollups(T0 + 10))

	row = _rollup_of(db, 1, 1)
	assert row["games"] == 8, "every game the player played is still one of their games"
	assert json.loads(row["rollup"])["strategies"] == [
		dict(key="archer_rush", games=5, wins=5)], "only the resolved five are a win rate"


def test_an_unlinked_player_gets_no_row_at_all():
	"""The absence IS the signal stage 5a renders "Statistics pending linking"
	from (identity v2 SS5), so an unlinked player must not produce a row of
	zeros. Both unlinked shapes are covered: no identities row, and a row whose
	user_id is NULL."""
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1)
	db.add("identities", profile_id=33, user_id=None, aoe2_name="p33", confidence="seed",
	       first_seen_at=T0, last_seen_at=T0, bound_at=T0)
	identity.invalidate_cache()
	_game(db, 1, 1, 11)
	_game(db, 1, 2, 33)          # ownerless binding
	_game(db, 1, 3, 44)          # no binding at all

	asyncio.run(refresh.drain_rollups(T0 + 10))

	assert [(r["community_id"], r["user_id"]) for r in db.rows("player_rollups")] == [(1, 1)]


def test_a_game_outside_the_community_does_not_reach_its_rollup():
	db, _log = _fresh()
	_community(db, 1, 900)
	_community(db, 2, 901)
	_link(db, 11, 1)
	_game(db, 1, 1, 11, community_id=1)
	_game(db, 2, 1, 11, community_id=2)

	asyncio.run(refresh.drain_rollups(T0 + 10))

	assert _rollup_of(db, 1, 1)["games"] == 1
	assert _rollup_of(db, 2, 1)["games"] == 1


def test_one_replay_linked_by_two_bot_matches_is_counted_once():
	# match_replays' primary key is (community_id, match_id) and does NOT
	# constrain replay_match_id, so a re-report or a corrected pairing can point
	# two bot matches at one replay. Without the DISTINCT the player's whole
	# game count silently doubles.
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1)
	_game(db, 7, 1, 11, match_id=100)
	db.add("match_replays", community_id=1, match_id=101, replay_match_id=7, linked_at=T0)

	asyncio.run(refresh.drain_rollups(T0 + 10))

	assert _rollup_of(db, 1, 1)["games"] == 1


# ── the identity hook ─────────────────────────────────────────────────────────

def test_a_late_link_makes_an_already_computed_rollup_pending(monkeypatch):
	"""MUTANT GUARD (c): deleting bound_at from nammaoe2bot/features/identity/resolver.py's write paths.

	This is the headline promise of identity v2 -- link late, get your whole
	history -- and it is exactly the case a comparison built on game data alone
	cannot see: the newly-claimed profile's game_stats rows are OLDER than the
	rollup that already exists, and nothing about them changes when the link
	lands."""
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1, bound_at=T0)
	_game(db, 1, 1, 11, computed_at=T0)
	_game(db, 2, 1, 22, computed_at=T0)      # played on an unclaimed profile, long ago

	asyncio.run(refresh.drain_rollups(T0 + 100))
	assert _rollup_of(db, 1, 1)["games"] == 1
	assert asyncio.run(refresh.pending_rollups(T0 + 200)) == [], "converged before the link"

	monkeypatch.setattr(identity, "time", _Clock(T0 + 150))
	assert asyncio.run(identity.link_self(22, 1, "name22")) is True

	assert asyncio.run(refresh.pending_rollups(T0 + 200)) == [(1, 1)]
	asyncio.run(refresh.drain_rollups(T0 + 200))
	assert _rollup_of(db, 1, 1)["games"] == 2, "the link must backfill the whole history"


def test_an_unlink_makes_the_departing_owner_pending(monkeypatch):
	"""The LOSING side of a binding move, which the user's own profiles can no
	longer report: once profile 22 is gone, `identities` holds no trace that user
	1 ever owned it. identity_conflicts does -- nammaoe2bot/features/identity/resolver.py records every
	removal there -- and that is what the watermark's UNION half reads."""
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1, bound_at=T0)
	_link(db, 22, 1, bound_at=T0)
	_game(db, 1, 1, 11, computed_at=T0)
	_game(db, 2, 1, 22, computed_at=T0)

	asyncio.run(refresh.drain_rollups(T0 + 100))
	assert _rollup_of(db, 1, 1)["games"] == 2
	assert asyncio.run(refresh.pending_rollups(T0 + 200)) == []

	monkeypatch.setattr(identity, "time", _Clock(T0 + 150))
	asyncio.run(identity.unlink(22))

	assert asyncio.run(refresh.pending_rollups(T0 + 200)) == [(1, 1)]
	asyncio.run(refresh.drain_rollups(T0 + 200))
	assert _rollup_of(db, 1, 1)["games"] == 1, "the removed profile's games must leave the rollup"


def test_unlinking_a_users_only_profile_removes_the_row_entirely(monkeypatch):
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1, bound_at=T0)
	_game(db, 1, 1, 11, computed_at=T0)
	asyncio.run(refresh.drain_rollups(T0 + 100))
	assert _rollup_of(db, 1, 1) is not None

	monkeypatch.setattr(identity, "time", _Clock(T0 + 150))
	asyncio.run(identity.unlink(11))
	asyncio.run(refresh.drain_rollups(T0 + 200))

	assert _rollup_of(db, 1, 1) is None, "an unlinked player must have NO row, not a row of zeros"
	assert asyncio.run(refresh.pending_rollups(T0 + 300)) == [], "and the removal must converge"


def test_a_relink_moves_the_history_to_the_new_owner(monkeypatch):
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1, bound_at=T0)
	_link(db, 22, 2, bound_at=T0)
	_game(db, 1, 1, 11, computed_at=T0)
	_game(db, 2, 1, 22, computed_at=T0)
	asyncio.run(refresh.drain_rollups(T0 + 100))
	assert _rollup_of(db, 1, 1)["games"] == 1
	assert _rollup_of(db, 1, 2)["games"] == 1

	# Admin decides profile 22 was user 1's all along, alongside profile 11.
	monkeypatch.setattr(identity, "time", _Clock(T0 + 150))
	asyncio.run(identity.relink(22, 1, additional=True))

	pending = asyncio.run(refresh.pending_rollups(T0 + 200))
	assert set(pending) == {(1, 1), (1, 2)}, "both the gaining and the losing owner"
	asyncio.run(refresh.drain_rollups(T0 + 200))
	assert _rollup_of(db, 1, 1)["games"] == 2
	assert _rollup_of(db, 1, 2) is None


def test_a_linked_user_with_no_game_in_this_community_loses_the_row_rather_than_getting_zeros(
		monkeypatch):
	"""MUTANT GUARD: replacing refresh_user's "no stat rows -> delete" with a
	write of an empty rollup.

	The shape the `not profiles` branch cannot reach, and the one
	test_a_relink_moves_the_history_to_the_new_owner does NOT cover: the user is
	still linked -- they own another profile -- but nothing that profile played
	belongs to THIS community. `not profiles` is False, so only the second guard
	stands between them and a stored row of zeros.

	A zeros row is not merely cosmetically wrong. It is stored while the data
	does not imply it, which is pending case (2), so it is rewritten on every
	pass forever; and because _run returns whenever the rollup drain processed
	anything, the community layer behind it is starved permanently. All three
	consequences are asserted below."""
	db, _log = _fresh()
	_community(db, 1, 900)
	_community(db, 2, 901)
	_link(db, 11, 1)
	_link(db, 22, 1)
	_game(db, 1, 1, 11, community_id=1)      # user 1's only game in community 1
	_game(db, 2, 1, 22, community_id=2)      # profile 22 has only ever played in 2
	asyncio.run(refresh.drain_rollups(T0 + 10))
	assert _rollup_of(db, 1, 1)["games"] == 1

	# An admin decides profile 11 was user 2's all along. User 1 keeps profile 22.
	monkeypatch.setattr(identity, "time", _Clock(T0 + 150))
	asyncio.run(identity.relink(11, 2, additional=True))

	asyncio.run(refresh.jobs._run(now=T0 + 200))

	assert _rollup_of(db, 1, 1) is None, "a user with no game here must have NO row, not zeros"
	assert asyncio.run(refresh.pending_rollups(T0 + 300)) == [], (
		"a stored row the data does not imply is pending forever")
	asyncio.run(refresh.jobs._run(now=T0 + 230))
	assert db.rows("metric_boards"), (
		"a row rewritten on every pass would starve the community layer permanently")


def test_a_name_only_observation_does_not_mark_anyone_stale(monkeypatch):
	# learn() bumps last_seen_at on every replay ingest of a known profile and
	# even on a refused claim. Keying staleness on that instead of on bound_at
	# would recompute every player of every ingested match forever.
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1, bound_at=T0)
	_game(db, 1, 1, 11, computed_at=T0)
	asyncio.run(refresh.drain_rollups(T0 + 100))
	assert asyncio.run(refresh.pending_rollups(T0 + 200)) == []

	monkeypatch.setattr(identity, "time", _Clock(T0 + 150))
	assert asyncio.run(identity.learn(11, 1, "learned", aoe2_name="freshname")) is True

	assert asyncio.run(refresh.pending_rollups(T0 + 200)) == []
	assert db.rows("identities", profile_id=11)[0]["last_seen_at"] == T0 + 150
	assert db.rows("identities", profile_id=11)[0]["bound_at"] == T0


def test_reasserting_a_binding_the_owner_already_holds_does_not_mark_them_stale(monkeypatch):
	"""MUTANT GUARD: _overwrite_binding stamping bound_at unconditionally instead
	of only when the owner actually changes.

	_overwrite_binding is ALSO the path for two rewrites that move nothing --
	`/link` re-run on a profile the caller already owns (it is documented as
	idempotent and re-runnable) and an admin relink re-asserting one at `manual`
	-- and both are ordinary things for a real user to do. Stamping them reports a
	binding change that did not happen, and this comparison believes it: the whole
	of that user's history is recomputed for nothing, every time. Confidence and
	aoe2_name moving are deliberately not binding changes either, and the relink
	half below moves the confidence tier to prove it.

	test_a_name_only_observation_does_not_mark_anyone_stale covers learn()'s
	refresh branch, which never reaches _overwrite_binding at all."""
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1, bound_at=T0)          # stored at confidence `learned`
	_game(db, 1, 1, 11, computed_at=T0)
	asyncio.run(refresh.drain_rollups(T0 + 100))
	assert asyncio.run(refresh.pending_rollups(T0 + 200)) == []

	monkeypatch.setattr(identity, "time", _Clock(T0 + 150))
	assert asyncio.run(identity.link_self(11, 1, "name11")) is True   # `/link` re-run

	assert db.rows("identities", profile_id=11)[0]["confidence"] == "self", "the rewrite happened"
	assert db.rows("identities", profile_id=11)[0]["bound_at"] == T0
	assert asyncio.run(refresh.pending_rollups(T0 + 200)) == []

	monkeypatch.setattr(identity, "time", _Clock(T0 + 160))
	asyncio.run(identity.relink(11, 1, additional=True))              # admin re-asserts it

	assert db.rows("identities", profile_id=11)[0]["confidence"] == "manual", "and so did this one"
	assert db.rows("identities", profile_id=11)[0]["bound_at"] == T0
	assert asyncio.run(refresh.pending_rollups(T0 + 200)) == []


def test_a_refused_claim_does_not_mark_the_stored_owner_stale(monkeypatch):
	"""MUTANT GUARD: stamping bound_at on learn()'s REFUSED branch.

	That branch fires on every ingest where a lower-tier source disagrees with the
	stored owner -- an alias the deduction solver keeps re-proposing, say -- and
	nothing about the binding moves: the claim is recorded as a conflict and the
	stored owner stands. Stamping it would mark the profile's owner stale on every
	single ingest, which is the precise failure bound_at exists to avoid and the
	reason last_seen_at was rejected for this job.

	The claimant is marked stale too, through the conflicts half of the watermark
	union, so this one mutation reaches both users at once."""
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1, bound_at=T0)          # user 1 owns it at `learned`
	_game(db, 1, 1, 11, computed_at=T0)
	asyncio.run(refresh.drain_rollups(T0 + 100))
	assert asyncio.run(refresh.pending_rollups(T0 + 200)) == []

	monkeypatch.setattr(identity, "time", _Clock(T0 + 150))
	assert asyncio.run(identity.learn(11, 2, "seed")) is False, "a weaker source must not win"

	assert db.rows("identity_conflicts")[0]["claimed_user_id"] == 2, "the claim was recorded"
	assert db.rows("identities", profile_id=11)[0]["user_id"] == 1, "the owner stands"
	assert db.rows("identities", profile_id=11)[0]["last_seen_at"] == T0 + 150
	assert db.rows("identities", profile_id=11)[0]["bound_at"] == T0
	assert asyncio.run(refresh.pending_rollups(T0 + 200)) == []


# ── staleness, convergence and the backstop ──────────────────────────────────

def test_a_new_game_makes_only_its_own_players_pending():
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1)
	_link(db, 22, 2)
	_game(db, 1, 1, 11, computed_at=T0)
	_game(db, 1, 2, 22, computed_at=T0)
	asyncio.run(refresh.drain_rollups(T0 + 100))
	assert asyncio.run(refresh.pending_rollups(T0 + 200)) == []

	_game(db, 2, 1, 11, computed_at=T0 + 150)

	assert asyncio.run(refresh.pending_rollups(T0 + 200)) == [(1, 1)]


def test_an_input_written_in_the_same_second_as_the_rollup_is_not_lost():
	"""The `<=` in the staleness comparison, which is the whole race guard: the
	job reads a user's rows at second T and stamps second T, and an ingest
	landing during that same second writes computed_at = T too. Under `<` that
	game would be invisible for good."""
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1)
	_game(db, 1, 1, 11, computed_at=T0)

	asyncio.run(refresh.drain_rollups(T0))            # stamped exactly T0
	assert _rollup_of(db, 1, 1)["computed_at"] == T0

	assert asyncio.run(refresh.pending_rollups(T0)) == [(1, 1)]
	asyncio.run(refresh.drain_rollups(T0 + 30))
	assert asyncio.run(refresh.pending_rollups(T0 + 30)) == [], "and it still converges"


def test_a_rollup_older_than_max_age_is_recomputed_with_no_input_change():
	"""The backstop, stateless: instead of a nightly cron and a "did it run?"
	marker, staleness is simply capped. This is what heals an input the
	comparison cannot see -- game_labels carries no computed_at at all, so an
	offline classification retune moves nothing else in the database."""
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1)
	_game(db, 1, 1, 11, computed_at=T0)
	asyncio.run(refresh.drain_rollups(T0 + 100))

	assert asyncio.run(refresh.pending_rollups(T0 + 100 + refresh.MAX_AGE - 1)) == []
	assert asyncio.run(refresh.pending_rollups(T0 + 200 + refresh.MAX_AGE)) == [(1, 1)]


def test_the_pending_set_converges_to_zero():
	db, _log = _fresh()
	_community(db)
	for user_id in range(1, 6):
		_link(db, user_id * 11, user_id)
		_game(db, user_id, 1, user_id * 11)

	asyncio.run(refresh.drain_rollups(T0 + 10))

	assert asyncio.run(refresh.pending_rollups(T0 + 20)) == []
	assert len(db.rows("player_rollups")) == 5


def test_the_drain_is_capped_per_tick():
	db, log = _fresh()
	_community(db)
	wanted = refresh.BATCH * 2 + 3
	for user_id in range(1, wanted + 1):
		_link(db, user_id * 11, user_id)
		_game(db, user_id, 1, user_id * 11)

	processed = asyncio.run(refresh.drain_rollups(T0 + 10))

	assert processed == refresh.BATCH
	assert len(db.rows("player_rollups")) == refresh.BATCH
	assert f"{wanted - refresh.BATCH} still stale" in log.info_lines[-1]


def test_one_user_failing_does_not_abort_the_batch():
	"""MUTANT GUARD (d): removing the per-user try/except. A malformed top_units
	blob raises out of compute_rollup by design (rollups.top_units_of refuses to
	degrade a corrupt row to "no units"), and without the guard the first such
	row costs every user behind it in the batch."""
	db, log = _fresh()
	_community(db)
	for user_id in (1, 2, 3):
		_link(db, user_id * 11, user_id)
		_game(db, user_id, 1, user_id * 11)
	db.conn.execute("UPDATE game_stats SET top_units='{{{ not json' WHERE profile_id=22")

	processed = asyncio.run(refresh.drain_rollups(T0 + 10))

	assert processed == 3
	assert {r["user_id"] for r in db.rows("player_rollups")} == {1, 3}
	assert any("user 2" in line for line in log.error_lines)
	assert "1 failed" in log.info_lines[-1]


def test_a_permanently_failing_user_is_quarantined_and_stops_blocking_the_batch():
	"""MUTANT GUARD: _quarantined() returning an empty set.

	The two obvious assertions -- the attempt counter, and every other user
	eventually getting a rollup -- both hold WITHOUT quarantining, because the
	broken user is only one of BATCH slots and the rest drain anyway. So does
	greping the log for "quarantined in this process": that phrase is emitted
	unconditionally, whatever the count. The two that actually bite are the COUNT
	in the log line, and the number of times the broken user was retried."""
	db, log = _fresh()
	_community(db)
	for user_id in range(1, refresh.BATCH + 3):
		_link(db, user_id * 11, user_id)
		# Stalest-first ordering is by computed_at, so give the broken user the
		# oldest games and it sorts to the front of every batch.
		_game(db, user_id, 1, user_id * 11, computed_at=T0 - (1000 if user_id == 1 else 0))
	db.conn.execute("UPDATE game_stats SET top_units='{{{ not json' WHERE profile_id=11")

	for _pass in range(refresh.MAX_ATTEMPTS + 2):
		asyncio.run(refresh.drain_rollups(T0 + 10))

	assert refresh._rollup_attempts[(1, 1)] >= refresh.MAX_ATTEMPTS
	assert {r["user_id"] for r in db.rows("player_rollups")} == set(range(2, refresh.BATCH + 3))
	assert "1 of those quarantined in this process, 0 attemptable" in log.info_lines[-1], (
		"the log line has to say how many of the stale rows this process has given up on -- "
		"the phrase alone is printed whether the count is 0 or not")
	attempts = [line for line in log.error_lines if "user 1 (attempt" in line]
	assert len(attempts) == refresh.MAX_ATTEMPTS, (
		"a quarantined row must stop being attempted, not merely be counted")


def test_the_log_line_reports_real_counts():
	db, log = _fresh()
	_community(db)
	_link(db, 11, 1)
	_game(db, 1, 1, 11)
	db.add("player_rollups", community_id=1, user_id=2, games=3,
	       rollup=json.dumps({}), computed_at=T0)   # orphan: user 2 owns nothing

	asyncio.run(refresh.drain_rollups(T0 + 10))

	assert "1 written, 1 removed, 0 failed, 0 still stale" in log.info_lines[-1]


# ── restart survival ─────────────────────────────────────────────────────────

def test_the_work_list_survives_a_restart(monkeypatch):
	"""MUTANT GUARD (b): replacing the derived pending set with an in-memory one.

	THE EXACT SCENARIO: a player runs `/link` and a deploy lands seconds later.
	The module is genuinely re-imported here, which is what a deploy does to
	process state. A dirty set held in module memory would satisfy the first
	assertion and come back empty from the second, and that player would never
	get a rollup -- with no error and no log line anywhere saying so. Nothing is
	lost here because there is no state to lose: pendingness is recomputed from
	the database on every pass."""
	db, log = _fresh()
	_community(db)
	_game(db, 1, 1, 11)                                   # played on an unclaimed profile
	monkeypatch.setattr(identity, "time", _Clock(T0 + 5))
	assert asyncio.run(identity.link_self(11, 1, "name11")) is True
	assert asyncio.run(refresh.pending_rollups(T0 + 10)) == [(1, 1)]

	reloaded = importlib.reload(refresh)                  # <- the deploy
	_install(db, log, module=reloaded)

	assert asyncio.run(reloaded.pending_rollups(T0 + 10)) == [(1, 1)]
	asyncio.run(reloaded.drain_rollups(T0 + 10))
	assert _rollup_of(db, 1, 1)["games"] == 1

	importlib.reload(refresh)   # leave the module object the other tests hold


def test_a_restart_mid_batch_loses_no_work():
	# The half-drained state: BATCH users done, the rest still pending. A restart
	# here must resume rather than restart from nothing or redo everything.
	db, log = _fresh()
	_community(db)
	for user_id in range(1, refresh.BATCH + 4):
		_link(db, user_id * 11, user_id)
		_game(db, user_id, 1, user_id * 11)
	asyncio.run(refresh.drain_rollups(T0 + 10))
	done = {r["user_id"] for r in db.rows("player_rollups")}
	assert len(done) == refresh.BATCH

	reloaded = importlib.reload(refresh)
	_install(db, log, module=reloaded)
	asyncio.run(reloaded.drain_rollups(T0 + 20))

	assert {r["user_id"] for r in db.rows("player_rollups")} == set(range(1, refresh.BATCH + 4))
	importlib.reload(refresh)


# ── the community layer ──────────────────────────────────────────────────────

def _boarded(db, community_id=1):
	return {r["metric_id"]: json.loads(r["board"]) for r in db.rows("metric_boards")
	        if r["community_id"] == community_id}


def test_the_community_pass_writes_every_catalogued_board_and_the_civ_stats():
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1)
	for mid in (1, 2, 3):
		_game(db, mid, 1, 11, eapm=100 + mid, first_tc_s=200 + mid)
		db.add("civ_picks", channel_id=900, civ="Franks", at=T0, result="W" if mid < 3 else "L")
	asyncio.run(refresh.drain_rollups(T0 + 10))

	asyncio.run(refresh.drain_communities(T0 + 20))

	assert set(_boarded(db)) == set(boards.METRICS)
	assert _boarded(db)["eapm"]["leaders"] == [{"user_id": 1, "nick": "name11", "avg": 102.0, "n": 3}]
	assert _boarded(db)["first_tc_s"]["direction"] == "low"
	assert db.rows("civ_stats") == [dict(community_id=1, civ="Franks", games=3, wins=2, losses=1,
	                                     computed_at=T0 + 20)]


def test_boards_wait_for_the_per_user_layer_to_settle():
	"""The cadence: the community pass is a whole-community scan, and every one
	run while rollups are still draining is thrown away by the next. Gating it on
	"the rollup drain processed nothing" makes a cold start build the boards once
	instead of once per pass."""
	db, _log = _fresh()
	_community(db)
	for user_id in range(1, refresh.BATCH + 3):
		_link(db, user_id * 11, user_id)
		_game(db, user_id, 1, user_id * 11)

	asyncio.run(refresh.jobs._run(now=T0 + 10))
	assert db.rows("metric_boards") == [], "no board work while the per-user layer is behind"

	asyncio.run(refresh.jobs._run(now=T0 + 20))     # drains the rest
	assert db.rows("metric_boards") == []
	asyncio.run(refresh.jobs._run(now=T0 + 30))     # nothing left to drain -> boards
	assert len(db.rows("metric_boards")) == len(boards.METRICS)


def test_the_community_pass_converges():
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1)
	_game(db, 1, 1, 11)
	asyncio.run(refresh.drain_rollups(T0 + 10))

	asyncio.run(refresh.drain_communities(T0 + 20))

	assert asyncio.run(refresh.pending_communities(T0 + 30)) == []


def test_a_community_with_no_resolved_picks_still_converges():
	"""civ_stats can legitimately store ZERO rows, which is why metric_boards and
	not civ_stats is the freshness marker: a marker that can be empty is
	indistinguishable from one that was never written, and that shape never
	terminates."""
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1)
	_game(db, 1, 1, 11)
	db.add("civ_picks", channel_id=900, civ="Franks", at=T0, result=None)   # unresolved
	asyncio.run(refresh.drain_rollups(T0 + 10))

	asyncio.run(refresh.drain_communities(T0 + 20))

	assert db.rows("civ_stats") == []
	assert asyncio.run(refresh.pending_communities(T0 + 30)) == []


def test_a_match_report_alone_makes_the_community_pending():
	"""A report writes civ_picks and no game_stats -- the replay is ingested
	minutes to hours later, if ever -- so nothing moves a rollup and the
	player_rollups half of the watermark stays put."""
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1)
	_game(db, 1, 1, 11)
	asyncio.run(refresh.drain_rollups(T0 + 10))
	asyncio.run(refresh.drain_communities(T0 + 20))
	assert asyncio.run(refresh.pending_communities(T0 + 30)) == []

	db.add("civ_picks", channel_id=900, civ="Goths", at=T0 + 25, result="W")

	assert asyncio.run(refresh.pending_communities(T0 + 30)) == [1]
	asyncio.run(refresh.drain_communities(T0 + 30))
	assert {r["civ"] for r in db.rows("civ_stats")} == {"Goths"}


def test_a_new_game_makes_the_community_pending_through_its_rollups():
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1)
	_game(db, 1, 1, 11, computed_at=T0)
	asyncio.run(refresh.drain_rollups(T0 + 10))
	asyncio.run(refresh.drain_communities(T0 + 20))
	assert asyncio.run(refresh.pending_communities(T0 + 30)) == []

	_game(db, 2, 1, 11, computed_at=T0 + 25)
	asyncio.run(refresh.drain_rollups(T0 + 30))

	assert asyncio.run(refresh.pending_communities(T0 + 40)) == [1]


def test_a_board_for_a_retired_metric_is_dropped():
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1)
	_game(db, 1, 1, 11)
	db.add("metric_boards", community_id=1, metric_id="tech_click_s",
	       board=json.dumps({}), computed_at=T0)
	asyncio.run(refresh.drain_rollups(T0 + 10))

	asyncio.run(refresh.drain_communities(T0 + 20))

	assert "tech_click_s" not in {r["metric_id"] for r in db.rows("metric_boards")}
	assert asyncio.run(refresh.pending_communities(T0 + 30)) == []


def test_a_metric_added_by_a_deploy_makes_every_community_pending():
	db, _log = _fresh()
	_community(db)
	_link(db, 11, 1)
	_game(db, 1, 1, 11)
	asyncio.run(refresh.drain_rollups(T0 + 10))
	asyncio.run(refresh.drain_communities(T0 + 20))
	assert asyncio.run(refresh.pending_communities(T0 + 30)) == []

	db.conn.execute("DELETE FROM metric_boards WHERE metric_id='eapm'")

	assert asyncio.run(refresh.pending_communities(T0 + 30)) == [1]


def test_a_catalog_entry_naming_a_dead_column_does_not_empty_the_other_boards():
	"""Both queries are generated FROM the catalog, so one bad field makes the
	whole query for its SOURCE unparseable. Degrading that to "no rows" would
	rewrite every board on that source as empty -- a community where nobody has
	ever played -- so every metric on the dead source is failed instead and the
	previous boards stand. The other source and civ_stats are untouched."""
	db, log = _fresh()
	_community(db)
	_link(db, 11, 1)
	_game(db, 1, 1, 11)
	db.add("civ_picks", channel_id=900, civ="Franks", at=T0, result="W")
	asyncio.run(refresh.drain_rollups(T0 + 10))
	on_players = sum(1 for m in boards.METRICS.values() if m["source"] == "replay_players")

	original = boards.METRICS["eapm"]
	boards.METRICS["eapm"] = dict(original, field="no_such_column")
	try:
		written, _dropped, civs, failed = asyncio.run(refresh.refresh_community(1, T0 + 20))
	finally:
		boards.METRICS["eapm"] = original

	assert failed == on_players, "every metric on the unreadable source, and no other"
	assert written == len(boards.METRICS) - on_players
	assert civs == 1, "civ stats must not be collateral damage"
	assert {r["metric_id"] for r in db.rows("metric_boards")} == {
		metric_id for metric_id, meta in boards.METRICS.items() if meta["source"] == "game_stats"
	}, "the unreadable source's boards must be left alone, never rewritten empty"
	assert any("could not read replay_players" in line for line in log.error_lines)


def test_only_one_community_is_refreshed_per_pass():
	db, _log = _fresh()
	for community_id in (1, 2, 3):
		_community(db, community_id, 900 + community_id)
		_link(db, community_id * 11, community_id)
		_game(db, community_id, 1, community_id * 11, community_id=community_id)
	asyncio.run(refresh.drain_rollups(T0 + 10))

	asyncio.run(refresh.drain_communities(T0 + 20))

	assert {r["community_id"] for r in db.rows("metric_boards")} == {1}
	assert len(asyncio.run(refresh.pending_communities(T0 + 30))) == 2


# ── tick isolation ───────────────────────────────────────────────────────────

def test_think_never_raises_into_the_tick():
	"""on_think awaits this directly, so an exception here starves every job
	registered after it -- including the match ticks."""
	db, log = _fresh()
	_install(_ExplodingDB(), log)
	try:
		asyncio.run(_think_and_settle(refresh.jobs, T0 + 1000))
	finally:
		_install(db, log)

	assert refresh.jobs._running is False, "a failed pass must release the overlap guard"
	assert any("ignored" in line for line in log.error_lines)


def test_think_survives_a_broken_frame_time():
	db, log = _fresh()
	refresh.jobs.next_run = 0
	asyncio.run(refresh.jobs.think(None))   # comparison raises inside think()

	assert refresh.jobs._running is False
	assert any("think() error (ignored)" in line for line in log.error_lines)
	_install(db, log)


def test_a_pass_that_cannot_read_the_database_still_attempts_nothing_worse():
	"""A dead database is caught and logged rather than crashing the pass -- and
	the community layer is NOT attempted afterwards. With _ExplodingDB a reached
	drain_communities would raise and log its own line, so the absence of that
	line is the proof."""
	db, log = _fresh()
	_install(_ExplodingDB(), log)
	try:
		asyncio.run(refresh.jobs._run(now=T0))
	finally:
		_install(db, log)

	assert any("rollup drain error (ignored)" in line for line in log.error_lines)
	assert not any("community drain error" in line for line in log.error_lines), (
		"a failed rollup drain must end the pass, not fall through to the community layer")


def test_a_failed_rollup_drain_skips_the_community_layer_for_that_pass(monkeypatch):
	"""The debounce is "the rollup drain PROCESSED nothing", and a drain that
	raised cannot answer that: `processed` is 0 because the count never finished,
	not because there was no work. Running the community layer on that 0 would
	rebuild every board from inputs the per-user layer is still behind on -- the
	exact wasted whole-community scan the debounce exists to prevent -- on a pass
	where the database has already failed once.

	Pinned with stubs rather than a broken fixture so the drains are isolated
	from each other: the community drain here would succeed if it were reached."""
	db, log = _fresh()
	_community(db)
	_link(db, 11, 1)
	_game(db, 1, 1, 11)
	reached = []

	async def _boom(_now):
		raise RuntimeError("database is down")

	async def _record(now):
		reached.append(now)
		return 0

	monkeypatch.setattr(refresh, "drain_rollups", _boom)
	monkeypatch.setattr(refresh, "drain_communities", _record)
	asyncio.run(refresh.jobs._run(now=T0 + 10))

	assert reached == [], "the community drain must not run on a pass whose rollup drain died"
	assert any("rollup drain error (ignored)" in line for line in log.error_lines)

	# ...and the very next pass, with the database back, reaches it -- so the
	# skip is one pass long, not a state the job can get stuck in.
	monkeypatch.undo()
	asyncio.run(refresh.jobs._run(now=T0 + 40))     # drains the one pending rollup
	asyncio.run(refresh.jobs._run(now=T0 + 70))
	assert len(db.rows("metric_boards")) == len(boards.METRICS)


async def _think_and_settle(job, frame_time):
	job.next_run = 0
	await job.think(frame_time)
	for task in list(refresh._tasks):
		await asyncio.gather(task, return_exceptions=True)
	await asyncio.sleep(0)


# ── the two writers this job is the sole caller of ───────────────────────────

def test_rollup_delete_is_idempotent():
	db, _log = _fresh()
	asyncio.run(rollups.delete(1, 1))
	db.add("player_rollups", community_id=1, user_id=1, games=1, rollup="{}", computed_at=T0)
	asyncio.run(rollups.delete(1, 1))
	asyncio.run(rollups.delete(1, 1))

	assert db.rows("player_rollups") == []


def test_delete_uncatalogued_leaves_catalogued_boards_alone():
	db, _log = _fresh()
	db.add("metric_boards", community_id=1, metric_id="eapm", board="{}", computed_at=T0)
	db.add("metric_boards", community_id=1, metric_id="gone", board="{}", computed_at=T0)
	db.add("metric_boards", community_id=2, metric_id="gone", board="{}", computed_at=T0)

	dropped = asyncio.run(boards.delete_uncatalogued(1))

	assert dropped == 1
	assert {(r["community_id"], r["metric_id"]) for r in db.rows("metric_boards")} == {
		(1, "eapm"), (2, "gone")}


def test_the_board_queries_only_read_catalogued_columns():
	# The catalog is the single place that knows which table and column a metric
	# lives on; the SQL is generated from it so a metric can never be added in
	# one place and forgotten in the other.
	players = refresh._players_board_sql()
	stats = refresh._stats_board_sql()
	for metric_id, meta in boards.METRICS.items():
		sql = players if meta["source"] == "replay_players" else stats
		assert f"`{meta['field']}`" in sql, metric_id


def test_the_module_exposes_a_job_singleton_the_tick_can_drive():
	# bot/events.py's on_think awaits `nammaoe2bot.derived.refresh_jobs.think(frame_time)`
	# and nammaoe2bot/derived/__init__.py exports it under that name.
	assert isinstance(refresh.jobs, refresh.DerivedRefresh)
	assert inspect.iscoroutinefunction(refresh.jobs.think)
