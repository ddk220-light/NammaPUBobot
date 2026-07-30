"""The startup migration runner.

Pure-logic tests against a fake adapter: the runner must apply each migration
exactly once, record it in the ledger, and make renames idempotent via
existence guards. No MySQL involved.
"""
import asyncio

import core.migrations as mig


class FakeDb:
	def __init__(self, tables=(), applied=(), columns=None):
		self.tables = set(tables)
		self.applied = list(applied)
		self.executed = []
		# {table: {column, ...}}
		self.columns = {t: set(cols) for t, cols in (columns or {}).items()}

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
		if "FROM schema_migrations" in sql:
			return [{"name": n} for n in self.applied]
		return []


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
