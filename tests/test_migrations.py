"""The startup migration runner.

Pure-logic tests against a fake adapter: the runner must apply each migration
exactly once, record it in the ledger, and make renames idempotent via
existence guards. No MySQL involved.
"""
import asyncio

import core.migrations as mig

# {table: primary key column} for FakeDb.insert_many's INSERT IGNORE
# emulation below. Only tables this suite actually writes via insert_many
# need an entry.
_PRIMARY_KEYS = {"identities": "profile_id"}


class FakeDb:
	def __init__(self, tables=(), applied=(), columns=None, rows=None, raise_on=None):
		self.tables = set(tables)
		self.applied = list(applied)
		self.executed = []
		# {table: {column, ...}}
		self.columns = {t: set(cols) for t, cols in (columns or {}).items()}
		# {table: [row dict, ...]} — seed data fetchall's generic SELECT
		# support reads from, and the destination insert_many writes to.
		self.rows = {t: list(r) for t, r in (rows or {}).items()}
		# Substring: if present in a fetchall's SQL, raise instead of
		# answering — lets a test simulate one seed source failing
		# independently of the others.
		self.raise_on = raise_on

	async def execute(self, sql, args=None):
		self.executed.append(sql)
		if sql.startswith("RENAME TABLE"):
			# `RENAME TABLE `old` TO `new``
			parts = sql.split("`")
			self.tables.discard(parts[1])
			self.tables.add(parts[3])
		if sql.startswith("ALTER TABLE") and "RENAME COLUMN" in sql:
			# `ALTER TABLE `table` RENAME COLUMN `old` TO `new``
			parts = sql.split("`")
			table, old, new = parts[1], parts[3], parts[5]
			cols = self.columns.setdefault(table, set())
			cols.discard(old)
			cols.add(new)
		is_ledger_insert = sql.startswith("INSERT INTO schema_migrations") or sql.startswith(
			"INSERT IGNORE INTO schema_migrations")
		if is_ledger_insert and args[0] not in self.applied:
			self.applied.append(args[0])

	async def fetchone(self, sql, args=None):
		if "information_schema.TABLES" in sql:
			return {"x": 1} if args[0] in self.tables else None
		if "information_schema.COLUMNS" in sql:
			table, column = args[0], args[1]
			return {"x": 1} if column in self.columns.get(table, set()) else None
		return None

	async def fetchall(self, sql, args=None):
		if self.raise_on and self.raise_on in sql:
			raise RuntimeError(f"FakeDb: simulated failure answering {self.raise_on!r}")
		if "FROM schema_migrations" in sql:
			return [{"name": n} for n in self.applied]
		if "FROM rs_profiles" in sql:
			return list(self.rows.get("rs_profiles", []))
		return []

	async def insert_many(self, table, rows, on_duplicate=None):
		""" Models MySQL's INSERT IGNORE (see core/DBAdapters/mysql.py's
		_mysql_insert: on_duplicate="ignore" renders as literal `INSERT
		IGNORE`): the first row written for a given primary key sticks, and
		every later row for that same key — whether from this call or an
		earlier one — is silently dropped rather than overwriting it. """
		dest = self.rows.setdefault(table, [])
		pk = _PRIMARY_KEYS[table]
		seen = {row[pk] for row in dest}
		for row in rows:
			row = dict(row)
			key = row[pk]
			if on_duplicate == "ignore" and key in seen:
				continue
			seen.add(key)
			dest.append(row)


def test_run_all_applies_once_and_records(monkeypatch):
	calls = []
	monkeypatch.setattr(mig, "MIGRATIONS", [("001_test", _make(calls))])
	db = FakeDb()
	asyncio.run(mig.run_all(db))
	asyncio.run(mig.run_all(db))
	assert calls == ["ran"], "second run_all must skip an applied migration"
	assert "001_test" in db.applied


def _make(calls):
	async def fn(db):
		calls.append("ran")
	return fn


def test_rename_table_renames_when_only_old_exists():
	db = FakeDb(tables={"old_t"})
	asyncio.run(mig.rename_table(db, "old_t", "matches"))
	assert "matches" in db.tables and "old_t" not in db.tables


def test_rename_table_skips_when_only_new_exists():
	db = FakeDb(tables={"matches"})
	asyncio.run(mig.rename_table(db, "old_t", "matches"))
	assert not any(s.startswith("RENAME") for s in db.executed)


def test_rename_table_raises_when_both_exist():
	db = FakeDb(tables={"old_t", "matches"})
	try:
		asyncio.run(mig.rename_table(db, "old_t", "matches"))
	except RuntimeError as e:
		assert "both exist" in str(e)
	else:
		raise AssertionError("both-exist must raise, not guess")


def test_migration_decorator_appends_in_order(monkeypatch):
	monkeypatch.setattr(mig, "MIGRATIONS", [])

	@mig.migration("010_a")
	async def a(db):
		pass

	@mig.migration("020_b")
	async def b(db):
		pass

	assert [n for n, _ in mig.MIGRATIONS] == ["010_a", "020_b"]


def test_rename_table_noop_when_neither_exists():
	db = FakeDb(tables=set())
	asyncio.run(mig.rename_table(db, "old_t", "matches"))
	assert not any(s.startswith("RENAME") for s in db.executed)
	assert db.tables == set()


def test_run_all_does_not_record_a_migration_that_raises(monkeypatch):
	calls = []

	async def boom(db):
		calls.append("ran")
		raise RuntimeError("kaboom")

	monkeypatch.setattr(mig, "MIGRATIONS", [("001_boom", boom)])
	db = FakeDb()

	try:
		asyncio.run(mig.run_all(db))
	except RuntimeError:
		pass
	else:
		raise AssertionError("run_all must propagate a migration's exception")

	assert "001_boom" not in db.applied
	assert not any(s.startswith("INSERT") and "schema_migrations" in s for s in db.executed)

	# Next boot must re-attempt the failed migration, not skip it as done.
	try:
		asyncio.run(mig.run_all(db))
	except RuntimeError:
		pass
	else:
		raise AssertionError("run_all must retry the previously-failed migration")

	assert calls == ["ran", "ran"]
	assert "001_boom" not in db.applied


def test_run_all_ledger_write_uses_insert_ignore(monkeypatch):
	monkeypatch.setattr(mig, "MIGRATIONS", [("001_test", _make([]))])
	db = FakeDb()
	asyncio.run(mig.run_all(db))
	assert any(s.startswith("INSERT IGNORE INTO schema_migrations") for s in db.executed)


def test_rename_column_renames_when_old_present():
	db = FakeDb(tables={"matches"}, columns={"matches": {"qc_id"}})
	asyncio.run(mig.rename_column(db, "matches", "qc_id", "channel_id"))
	assert db.columns["matches"] == {"channel_id"}


def test_rename_column_noop_when_new_already_present():
	db = FakeDb(tables={"matches"}, columns={"matches": {"channel_id"}})
	asyncio.run(mig.rename_column(db, "matches", "qc_id", "channel_id"))
	assert not any(s.startswith("ALTER TABLE") for s in db.executed)
	assert db.columns["matches"] == {"channel_id"}


def test_rename_column_noop_when_table_missing():
	db = FakeDb(tables=set())
	asyncio.run(mig.rename_column(db, "matches", "qc_id", "channel_id"))
	assert not any(s.startswith("ALTER TABLE") for s in db.executed)


def test_run_all_raises_when_a_rename_source_table_survives_the_ledger():
	"""Simulates a restored pre-deploy backup: the ledger says every
	migration already ran, but the dump predates the renames, so an
	old-named table is still there. ensure_table would otherwise CREATE the
	new name empty and the bot would boot healthy while serving no history
	— run_all must crash instead."""
	db = FakeDb(tables={"qc_matches"}, applied=["001_core_renames", "002_drop_retired"])
	try:
		asyncio.run(mig.run_all(db))
	except RuntimeError as e:
		assert "qc_matches" in str(e)
	else:
		raise AssertionError("run_all must crash when a rename-source table survives the ledger")


def test_run_all_is_a_noop_on_a_genuinely_fresh_install():
	# No tables (old or new) and no ledger rows at all — the ordinary first
	# boot. The post-condition check must not fire here.
	db = FakeDb()
	asyncio.run(mig.run_all(db))
	assert "001_core_renames" in db.applied


def test_run_all_does_not_raise_once_renames_have_actually_happened():
	old_names = {old for old, _new in mig._STAGE1_RENAMES}
	new_names = {new for _old, new in mig._STAGE1_RENAMES}
	db = FakeDb(tables=old_names)
	asyncio.run(mig.run_all(db))
	assert db.tables == new_names
	assert not (old_names & db.tables), "old-named tables must not survive a normal first deploy"


def test_ensure_identities_table_is_idempotent():
	db = FakeDb()
	asyncio.run(mig._ensure_identities_table(db))
	asyncio.run(mig._ensure_identities_table(db))
	assert db.executed.count(
		"CREATE TABLE IF NOT EXISTS identities ("
		"`profile_id` BIGINT, `user_id` BIGINT, `aoe2_name` VARCHAR(191), "
		"`confidence` VARCHAR(191) NOT NULL, `first_seen_at` BIGINT NOT NULL, "
		"`last_seen_at` BIGINT NOT NULL, PRIMARY KEY(`profile_id`))"
	) == 2


# ─── 003_seed_identities ────────────────────────────────────────────────
# _m003's own precedence and per-source error isolation, not just its
# helpers. FakeDb.insert_many above must model INSERT IGNORE faithfully
# (first writer of a profile_id wins) or these precedence assertions would
# pass against a broken implementation.

def _write_csv(tmp_path, relpath, text):
	path = tmp_path / relpath
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(text, encoding="utf-8")
	return path


def test_m003_rs_profiles_outranks_both_csvs_for_a_shared_profile_id(tmp_path, monkeypatch):
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv",
		"profile_id,user_id,nick,aoe2_name,source,appearances\n"
		"100,111,nickA,ResolvedName,seed,1\n")
	_write_csv(tmp_path, "data/player_profile_map.csv",
		"user_id,nick,aoe2_name,profile_id,country\n"
		"222,nickB,MapName,100,us\n")
	db = FakeDb(
		tables={"rs_profiles"},
		rows={"rs_profiles": [{"profile_id": 100, "user_id": 333, "name": "RSName", "last_seen_at": 999}]},
	)

	asyncio.run(mig._m003(db))

	identities = db.rows["identities"]
	assert len(identities) == 1
	row = identities[0]
	assert row["profile_id"] == 100
	assert row["user_id"] == 333
	assert row["aoe2_name"] == "RSName"
	assert row["confidence"] == "learned"


def test_m003_profile_resolved_csv_wins_over_player_profile_map_csv(tmp_path, monkeypatch):
	"""Both CSVs write at the same 'seed' confidence tier, so precedence
	between them comes only from insertion order (profile_resolved.csv is
	read first in _m003's loop), not from confidence comparison."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv",
		"profile_id,user_id,nick,aoe2_name,source,appearances\n"
		"200,111,nickA,ResolvedName,seed,1\n")
	_write_csv(tmp_path, "data/player_profile_map.csv",
		"user_id,nick,aoe2_name,profile_id,country\n"
		"222,nickB,MapName,200,us\n")
	db = FakeDb(tables=set())  # rs_profiles does not exist

	asyncio.run(mig._m003(db))

	identities = db.rows["identities"]
	assert len(identities) == 1
	row = identities[0]
	assert row["profile_id"] == 200
	assert row["user_id"] == 111
	assert row["aoe2_name"] == "ResolvedName"
	assert row["confidence"] == "seed"


def test_m003_missing_csv_logs_and_continues_seeding_from_the_other_source(tmp_path, monkeypatch):
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	# data/profile_resolved.csv is absent entirely; only the other CSV exists.
	_write_csv(tmp_path, "data/player_profile_map.csv",
		"user_id,nick,aoe2_name,profile_id,country\n"
		"222,nickB,MapName,300,us\n")
	db = FakeDb(tables=set())

	asyncio.run(mig._m003(db))  # must not raise despite the missing file

	identities = db.rows["identities"]
	assert len(identities) == 1
	assert identities[0]["profile_id"] == 300
	assert identities[0]["user_id"] == 222


def test_m003_rs_profiles_failure_does_not_abort_csv_seeding(tmp_path, monkeypatch):
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv",
		"profile_id,user_id,nick,aoe2_name,source,appearances\n"
		"400,111,nickA,ResolvedName,seed,1\n")
	_write_csv(tmp_path, "data/player_profile_map.csv",
		"user_id,nick,aoe2_name,profile_id,country\n"
		"222,nickB,MapName,500,us\n")
	db = FakeDb(tables={"rs_profiles"}, raise_on="FROM rs_profiles")

	asyncio.run(mig._m003(db))  # rs_profiles step raises; must not propagate

	profile_ids = {row["profile_id"] for row in db.rows["identities"]}
	assert profile_ids == {400, 500}


def test_m003_one_csv_source_failure_does_not_abort_the_other(tmp_path, monkeypatch):
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	# profile_resolved.csv exists as a directory rather than a file:
	# os.path.exists is True (so _m003 attempts to read it) but open() raises
	# IsADirectoryError, simulating a present-but-unreadable source.
	(tmp_path / "data").mkdir()
	(tmp_path / "data" / "profile_resolved.csv").mkdir()
	_write_csv(tmp_path, "data/player_profile_map.csv",
		"user_id,nick,aoe2_name,profile_id,country\n"
		"222,nickB,MapName,600,us\n")
	db = FakeDb(tables=set())

	asyncio.run(mig._m003(db))  # must not raise

	identities = db.rows["identities"]
	assert len(identities) == 1
	assert identities[0]["profile_id"] == 600


def test_m003_missing_rs_profiles_table_does_not_crash_on_fresh_install(tmp_path, monkeypatch):
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	# Neither CSV present either — the genuinely-empty fresh-install case.
	db = FakeDb(tables=set())

	asyncio.run(mig._m003(db))  # must not raise

	assert db.rows.get("identities", []) == []


def test_stage1_renames_targets_are_registered_and_sources_are_not_declared():
	"""A typo in a rename pair (e.g. a target that doesn't match any
	ensure_table declaration) is invisible to every other test in this file
	— they all treat table names as opaque strings — but would silently
	rename a production table out from under its declaration, and
	ensure_table would then create the correct name empty. Cross-check the
	pairs against the same declaration scanner test_data_registry.py uses,
	rather than duplicating the walk."""
	from core.data_registry import REGISTRY
	from tests.test_data_registry import _declared_tables

	declared = _declared_tables()
	for old, new in mig._STAGE1_RENAMES:
		assert new in REGISTRY, f"rename target {new!r} has no core.data_registry entry"
		assert old not in declared, f"rename source {old!r} is still declared by an ensure_table call"
