"""The startup migration runner.

Pure-logic tests against a fake adapter: the runner must apply each migration
exactly once, record it in the ledger, and make renames idempotent via
existence guards. No MySQL involved.
"""
import asyncio
import re
import types

import core.migrations as mig

# {table: the column(s) an INSERT IGNORE dedups on} for FakeDb.insert_many's
# emulation below. Only tables this suite actually writes via insert_many need
# an entry. A tuple means a composite key. For identity_conflicts that is the
# UNIQUE claim index migration 005 installs — NOT the primary key, which is a
# surrogate `id` the writers never supply (see bot/identity.py's declaration).
_PRIMARY_KEYS = {
	"identities": "profile_id",
	"identity_conflicts": ("profile_id", "claimed_user_id", "status"),
	"match_replays": ("community_id", "match_id"),
}

# `\`name\` TYPE` inside a CREATE TABLE body — how FakeDb learns which columns a
# migration's raw DDL just created, so column_exists() answers the way MySQL
# would for the rest of that boot.
_DDL_COLUMN = re.compile(r"`(\w+)`\s+(?:BIGINT|VARCHAR|TINYINT|FLOAT|MEDIUMTEXT)", re.I)
_DDL_UNIQUE_KEY = re.compile(r"UNIQUE KEY `(\w+)`", re.I)


def _pk_key(row, pk):
	""" The dedup key insert_many's INSERT IGNORE emulation compares by --
	a single value for a single-column primary key, a tuple of values for a
	composite one. """
	return tuple(row[c] for c in pk) if isinstance(pk, tuple) else row[pk]


class FakeDb:
	def __init__(self, tables=(), applied=(), columns=None, rows=None, raise_on=None, indexes=None):
		self.tables = set(tables)
		self.applied = list(applied)
		self.executed = []
		# {table: {index_name, ...}} — the primary key is called "PRIMARY", the
		# same name information_schema.STATISTICS gives it, so a test can model a
		# table that has one and 005 can guard its `DROP PRIMARY KEY` on it.
		self.indexes = {t: set(ix) for t, ix in (indexes or {}).items()}
		# {table: {column, ...}}
		self.columns = {t: set(cols) for t, cols in (columns or {}).items()}
		# {table: [row dict, ...]} — seed data fetchall's generic SELECT
		# support reads from, and the destination insert_many writes to.
		self.rows = {t: list(r) for t, r in (rows or {}).items()}
		# Substring: if present in a fetchall's SQL, raise instead of
		# answering — lets a test simulate one seed source failing
		# independently of the others.
		self.raise_on = raise_on
		# (table, values, keys) for every update() call, so a test can assert
		# a re-run issues no writes at all rather than merely converging on
		# the same values.
		self.updates = []

	async def execute(self, sql, args=None):
		self.executed.append(sql)
		if sql.startswith("CREATE TABLE IF NOT EXISTS "):
			# `CREATE TABLE IF NOT EXISTS name (...)` — a migration that
			# creates a table must leave table_exists() answering True for it
			# afterwards, exactly as MySQL would, or a later post-condition
			# check in the same boot would read a schema that never existed.
			# Its columns and keys are absorbed too, so a later migration in the
			# same boot (005 after 003) sees the table it actually created and
			# does not re-ALTER a shape that is already right.
			table = sql[len("CREATE TABLE IF NOT EXISTS "):].split()[0].split("(")[0]
			self.tables.add(table)
			self.columns.setdefault(table, set()).update(_DDL_COLUMN.findall(sql))
			ix = self.indexes.setdefault(table, set())
			ix.update(_DDL_UNIQUE_KEY.findall(sql))
			if "PRIMARY KEY" in sql.upper():
				ix.add("PRIMARY")
		if sql.startswith("ALTER TABLE") and "ADD UNIQUE KEY" in sql:
			# `ALTER TABLE `t` ADD UNIQUE KEY `ix` (...)`
			self.indexes.setdefault(sql.split("`")[1], set()).update(_DDL_UNIQUE_KEY.findall(sql))
		if sql.startswith("ALTER TABLE") and "ADD COLUMN" in sql:
			# `ALTER TABLE `t` [DROP PRIMARY KEY, ]ADD COLUMN `c` ... [PRIMARY KEY] [FIRST]`
			table = sql.split("`")[1]
			tail = sql.split("ADD COLUMN", 1)[1]
			self.columns.setdefault(table, set()).add(tail.split("`")[1])
			ix = self.indexes.setdefault(table, set())
			if "DROP PRIMARY KEY" in sql:
				ix.discard("PRIMARY")
			if "PRIMARY KEY" in tail.upper():
				ix.add("PRIMARY")
		if sql.startswith("DROP TABLE"):
			# `DROP TABLE `name``
			self.tables.discard(sql.split("`")[1])
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
		if "information_schema.STATISTICS" in sql:
			table, index = args[0], args[1]
			return {"x": 1} if index in self.indexes.get(table, set()) else None
		if "FROM schema_migrations" in sql:
			# `SELECT 1 AS x FROM schema_migrations WHERE name = %s`
			return {"x": 1} if args[0] in self.applied else None
		if "FROM identities" in sql:
			return {"x": 1} if self.rows.get("identities") else None
		return None

	async def fetchall(self, sql, args=None):
		if self.raise_on and self.raise_on in sql:
			raise RuntimeError(f"FakeDb: simulated failure answering {self.raise_on!r}")
		if "FROM schema_migrations" in sql:
			return [{"name": n} for n in self.applied]
		if "FROM rs_profiles" in sql:
			return list(self.rows.get("rs_profiles", []))
		if "FROM rs_matches" in sql:
			return self._backfill_join()
		return []

	def _backfill_join(self):
		""" Emulates 004_identity_v2's backfill SELECT: every rs_matches row
		with a bot_match_id, LEFT JOINed to `matches` on match_id and then to
		`community_channels` on that match's channel_id. LEFT, not inner, is
		the whole point — the migration has to be able to *count* the rows it
		cannot resolve, so an unknown match or an unenrolled channel must come
		back as a row with NULLs rather than not come back at all. """
		matches = {m["match_id"]: m for m in self.rows.get("matches", [])}
		channels = {c["channel_id"]: c["community_id"] for c in self.rows.get("community_channels", [])}
		out = []
		for r in self.rows.get("rs_matches", []):
			if r.get("bot_match_id") is None:
				continue
			m = matches.get(r["bot_match_id"])
			out.append(dict(
				match_id=r["bot_match_id"],
				replay_match_id=r["aoe2_match_id"],
				parsed_at=r.get("parsed_at"),
				found_match_id=m["match_id"] if m else None,
				reported_at=m.get("reported_at") if m else None,
				community_id=channels.get(m["channel_id"]) if m else None,
			))
		return out

	async def insert_many(self, table, rows, on_duplicate=None):
		""" Models MySQL's INSERT IGNORE (see core/DBAdapters/mysql.py's
		_mysql_insert: on_duplicate="ignore" renders as literal `INSERT
		IGNORE`): the first row written for a given primary key sticks, and
		every later row for that same key — whether from this call or an
		earlier one — is silently dropped rather than overwriting it. """
		dest = self.rows.setdefault(table, [])
		pk = _PRIMARY_KEYS[table]
		seen = {_pk_key(row, pk) for row in dest}
		for row in rows:
			row = dict(row)
			key = _pk_key(row, pk)
			if on_duplicate == "ignore" and key in seen:
				continue
			seen.add(key)
			dest.append(row)

	async def select(self, columns, table, where=None, **kwargs):
		""" Generic exact-match SELECT, same shape as
		core/DBAdapters/mysql.py's Adapter.select (used by _m003 to read the
		current `identities` state before comparing incoming seed claims
		against it — see _record_seed_conflicts). """
		where = where or {}
		return [
			{c: row.get(c) for c in columns}
			for row in self.rows.get(table, [])
			if all(row.get(k) == v for k, v in where.items())
		]

	async def update(self, table, d, keys=None):
		""" Models a plain `UPDATE ... WHERE` (core/DBAdapters/mysql.py's
		update): every row matching `keys` is mutated in place with `d`'s
		fields, unconditionally — no primary-key or INSERT-IGNORE semantics
		here, since a real UPDATE always overwrites. A `keys` match with no
		rows present is a harmless no-op, same as a real UPDATE affecting
		zero rows. """
		keys = keys or {}
		self.updates.append((table, dict(d), dict(keys)))
		for row in self.rows.get(table, []):
			if all(row.get(k) == v for k, v in keys.items()):
				row.update(d)


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
	# Subset, not equality: later migrations legitimately CREATE tables of
	# their own (the ledger, `identities`, `identity_conflicts`). What this
	# test is about is that every rename landed and no source name survived.
	assert new_names <= db.tables
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


def test_ensure_identity_conflicts_table_is_idempotent():
	db = FakeDb()
	asyncio.run(mig._ensure_identity_conflicts_table(db))
	asyncio.run(mig._ensure_identity_conflicts_table(db))
	assert db.executed.count(
		"CREATE TABLE IF NOT EXISTS identity_conflicts ("
		"`id` BIGINT NOT NULL AUTO_INCREMENT, "
		"`profile_id` BIGINT NOT NULL, `claimed_user_id` BIGINT NOT NULL, "
		"`source` VARCHAR(191) NOT NULL, "
		"`noticed_at` BIGINT NOT NULL, `status` VARCHAR(191) NOT NULL DEFAULT 'open', "
		"PRIMARY KEY(`id`), "
		"UNIQUE KEY `uniq_identity_conflicts_claim` (`profile_id`, `claimed_user_id`, `status`))"
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


def test_m003_manual_csv_row_wins_over_conflicting_player_profile_map_row(tmp_path, monkeypatch):
	"""data/profile_resolved.csv's own `source` column can tag a row
	'manual' — a deliberate human correction. It must win over a conflicting
	player_profile_map.csv row for the same profile_id even though both CSVs
	seed at nominally the same tier and profile_resolved.csv only happens to
	be read first in the loop; the point of tagging is that it wins by
	design, not by accident of read order."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv",
		"profile_id,user_id,nick,aoe2_name,source,appearances\n"
		"5771336,527532506153615360,aquasama7056,KIT WALKER,manual,\n")
	_write_csv(tmp_path, "data/player_profile_map.csv",
		"user_id,nick,aoe2_name,profile_id,country\n"
		"850996190282776577,bearknightman,KIT WALKER,5771336,\n")
	db = FakeDb(tables=set())  # rs_profiles does not exist

	asyncio.run(mig._m003(db))

	identities = db.rows["identities"]
	assert len(identities) == 1
	row = identities[0]
	assert row["profile_id"] == 5771336
	assert row["user_id"] == 527532506153615360
	assert row["confidence"] == "manual"


def test_m003_manual_csv_row_wins_over_rs_profiles_despite_rs_profiles_seeded_first(tmp_path, monkeypatch):
	"""rs_profiles is seeded before either CSV, and INSERT IGNORE means the
	first writer of a profile_id normally sticks — so without an explicit
	reassertion pass, a 'learned' rs_profiles row would permanently block a
	'manual' CSV correction for the same profile_id. A human correction must
	outrank even a mapping learned from real match data."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv",
		"profile_id,user_id,nick,aoe2_name,source,appearances\n"
		"800,111,nickA,ManualName,manual,1\n")
	db = FakeDb(
		tables={"rs_profiles"},
		rows={"rs_profiles": [{"profile_id": 800, "user_id": 999, "name": "RSName", "last_seen_at": 123}]},
	)

	asyncio.run(mig._m003(db))

	identities = db.rows["identities"]
	assert len(identities) == 1
	row = identities[0]
	assert row["profile_id"] == 800
	assert row["user_id"] == 111
	assert row["aoe2_name"] == "ManualName"
	assert row["confidence"] == "manual"


def test_m003_non_manual_source_value_still_lands_as_seed(tmp_path, monkeypatch):
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv",
		"profile_id,user_id,nick,aoe2_name,source,appearances\n"
		"900,111,nickA,SomeName, Manually-Reviewed ,1\n")
	db = FakeDb(tables=set())

	asyncio.run(mig._m003(db))

	identities = db.rows["identities"]
	assert len(identities) == 1
	assert identities[0]["confidence"] == "seed"


def test_m003_is_idempotent_on_a_manual_vs_rs_profiles_conflict(tmp_path, monkeypatch):
	"""Re-running the whole migration body from the top (as a retried boot
	would) must reach the same final state, not accumulate duplicate rows or
	flip the winner on a second pass."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv",
		"profile_id,user_id,nick,aoe2_name,source,appearances\n"
		"800,111,nickA,ManualName,manual,1\n")
	db = FakeDb(
		tables={"rs_profiles"},
		rows={"rs_profiles": [{"profile_id": 800, "user_id": 999, "name": "RSName", "last_seen_at": 123}]},
	)

	asyncio.run(mig._m003(db))
	asyncio.run(mig._m003(db))

	identities = db.rows["identities"]
	assert len(identities) == 1, "a re-run must not append a duplicate row"
	row = identities[0]
	assert row["user_id"] == 111
	assert row["aoe2_name"] == "ManualName"
	assert row["confidence"] == "manual"


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


# ─── 003_seed_identities: identity_conflicts recording ──────────────────
# Task 2.6: a losing claim used to just vanish once INSERT IGNORE dropped it.
# These pin the real-world case (profile 5771336, see the module docstring)
# and its surrounding rules: the winner is untouched, an agreeing claim
# leaves no trace, and a manual row's own claim is never reported as a loser
# even if it is temporarily blocked before the reassertion pass fixes it up.

def test_m003_records_the_conflicting_lower_precedence_claim_while_the_winner_is_unchanged(tmp_path, monkeypatch):
	"""The real profile_id 5771336 case from data/profile_resolved.csv (manual)
	vs data/player_profile_map.csv (seed): the manual row must keep winning
	exactly as before, and the discarded player_profile_map.csv claim must now
	show up in identity_conflicts instead of vanishing."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv",
		"profile_id,user_id,nick,aoe2_name,source,appearances\n"
		"5771336,527532506153615360,aquasama7056,KIT WALKER,manual,\n")
	_write_csv(tmp_path, "data/player_profile_map.csv",
		"user_id,nick,aoe2_name,profile_id,country\n"
		"850996190282776577,bearknightman,KIT WALKER,5771336,\n")
	db = FakeDb(tables=set())

	asyncio.run(mig._m003(db))

	identities = db.rows["identities"]
	assert len(identities) == 1
	assert identities[0]["user_id"] == 527532506153615360, "the manual row must still win"

	conflicts = db.rows["identity_conflicts"]
	assert len(conflicts) == 1
	row = conflicts[0]
	assert row["profile_id"] == 5771336
	assert row["claimed_user_id"] == 850996190282776577
	assert row["source"] == "seed"
	assert row["status"] == "open"


def test_m003_does_not_record_an_agreeing_claim(tmp_path, monkeypatch):
	"""Both CSVs naming the SAME user for the SAME profile_id is not a
	disagreement -- nothing belongs in identity_conflicts."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv",
		"profile_id,user_id,nick,aoe2_name,source,appearances\n"
		"300,111,nickA,SameName,seed,1\n")
	_write_csv(tmp_path, "data/player_profile_map.csv",
		"user_id,nick,aoe2_name,profile_id,country\n"
		"111,nickB,SameName,300,us\n")
	db = FakeDb(tables=set())

	asyncio.run(mig._m003(db))

	assert db.rows.get("identity_conflicts", []) == []


def test_m003_does_not_report_a_manual_rows_own_claim_as_a_loser(tmp_path, monkeypatch):
	"""profile_id 800: rs_profiles (learned, written first) blocks the manual
	profile_resolved.csv row via INSERT IGNORE mid-loop, but the reassertion
	pass unconditionally fixes that up afterwards -- the manual row is never
	actually discarded, so it must not be reported as though it were."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv",
		"profile_id,user_id,nick,aoe2_name,source,appearances\n"
		"800,111,nickA,ManualName,manual,1\n")
	db = FakeDb(
		tables={"rs_profiles"},
		rows={"rs_profiles": [{"profile_id": 800, "user_id": 999, "name": "RSName", "last_seen_at": 123}]},
	)

	asyncio.run(mig._m003(db))

	identities = db.rows["identities"]
	assert identities[0]["user_id"] == 111, "manual must still win after reassertion"
	assert db.rows.get("identity_conflicts", []) == []


def test_m003_records_conflicting_claims_against_an_already_seeded_learned_row(tmp_path, monkeypatch):
	"""profile_id 100: rs_profiles claims it first (learned); both CSVs then
	separately claim a DIFFERENT user for the same profile_id. Neither CSV
	claim is manual, so both are genuinely, permanently discarded and both
	must be recorded."""
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
	assert identities[0]["user_id"] == 333, "rs_profiles (learned, written first) still wins"

	conflicts = {(row["claimed_user_id"], row["source"]) for row in db.rows["identity_conflicts"]}
	assert conflicts == {(111, "seed"), (222, "seed")}
	assert all(row["profile_id"] == 100 for row in db.rows["identity_conflicts"])


def test_m003_rerun_does_not_duplicate_conflict_rows(tmp_path, monkeypatch):
	"""Re-running the whole migration body from the top (as a retried boot
	would) must not accumulate a second identity_conflicts row for the same
	(profile_id, claimed_user_id) pair."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv",
		"profile_id,user_id,nick,aoe2_name,source,appearances\n"
		"5771336,527532506153615360,aquasama7056,KIT WALKER,manual,\n")
	_write_csv(tmp_path, "data/player_profile_map.csv",
		"user_id,nick,aoe2_name,profile_id,country\n"
		"850996190282776577,bearknightman,KIT WALKER,5771336,\n")
	db = FakeDb(tables=set())

	asyncio.run(mig._m003(db))
	asyncio.run(mig._m003(db))

	conflicts = db.rows["identity_conflicts"]
	assert len(conflicts) == 1, "a re-run must not append a duplicate conflict row"


# ─── 003_seed_identities: all-sources-failed re-raise ───────────────────
# Without this, a boot where every one of the three sources raises would
# still return normally, run_all() would record 003_seed_identities as
# applied, and `identities` would stay empty forever with no retry.

def test_m003_raises_when_all_three_seed_sources_fail(tmp_path, monkeypatch):
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	(tmp_path / "data").mkdir()
	(tmp_path / "data" / "profile_resolved.csv").mkdir()    # open() -> IsADirectoryError
	(tmp_path / "data" / "player_profile_map.csv").mkdir()  # ditto
	db = FakeDb(tables={"rs_profiles"}, raise_on="FROM rs_profiles")

	try:
		asyncio.run(mig._m003(db))
	except RuntimeError as e:
		assert "rs_profiles" in str(e)
		assert "profile_resolved.csv" in str(e)
		assert "player_profile_map.csv" in str(e)
	else:
		raise AssertionError("_m003 must raise when every seed source fails")

	assert db.rows.get("identities", []) == []


def test_m003_does_not_raise_when_only_two_of_three_sources_fail(tmp_path, monkeypatch):
	"""A partial success — even down to just one working source — must still
	succeed, same as today: the whole point is that a real failure (as
	opposed to a merely-missing source) only aborts the ledger write when it
	leaves literally nothing seeded."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	(tmp_path / "data").mkdir()
	(tmp_path / "data" / "profile_resolved.csv").mkdir()  # fails
	_write_csv(tmp_path, "data/player_profile_map.csv",
		"user_id,nick,aoe2_name,profile_id,country\n"
		"222,nickB,MapName,700,us\n")
	db = FakeDb(tables={"rs_profiles"}, raise_on="FROM rs_profiles")  # fails

	asyncio.run(mig._m003(db))  # must not raise — player_profile_map.csv worked

	identities = db.rows["identities"]
	assert len(identities) == 1
	assert identities[0]["profile_id"] == 700


def test_run_all_does_not_record_003_when_every_seed_source_fails(tmp_path, monkeypatch):
	"""Integration check that _m003's raise actually stops run_all() from
	recording the migration — the same guarantee test_run_all_does_not_
	record_a_migration_that_raises proves generically, checked here against
	003_seed_identities specifically."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	monkeypatch.setattr(mig, "MIGRATIONS", [("003_seed_identities", mig._m003)])
	(tmp_path / "data").mkdir()
	(tmp_path / "data" / "profile_resolved.csv").mkdir()
	(tmp_path / "data" / "player_profile_map.csv").mkdir()
	db = FakeDb(tables={"rs_profiles"}, raise_on="FROM rs_profiles")

	try:
		asyncio.run(mig.run_all(db))
	except RuntimeError:
		pass
	else:
		raise AssertionError("run_all must propagate _m003's raise")

	assert "003_seed_identities" not in db.applied


# ─── boot post-condition: _assert_identities_seeded ──────────────────────
# Catches the other route to the same empty-forever failure: a restored
# backup that kept schema_migrations (so the loop skips 003_seed_identities
# entirely) but not identities' data.
#
# Task 2.5.8 re-anchored this check. It used to key off rs_profiles holding
# rows — but 004_identity_v2 DROPS rs_profiles, which would have turned the
# whole check into a permanent silent no-op. It now keys off the two things
# that still exist afterwards: the ledger entry that makes the claim
# (003_seed_identities), and 003's own surviving seed sources (the CSVs
# shipped in the deploy image).

_RESOLVED_CSV_ROW = (
	"profile_id,user_id,nick,aoe2_name,source,appearances\n"
	"100,111,nickA,RealName,seed,1\n"
)


def _seeded(**over):
	row = {"profile_id": 1, "user_id": 2, "aoe2_name": "x",
			"confidence": "learned", "first_seen_at": 1, "last_seen_at": 1}
	row.update(over)
	return row


def test_assert_identities_seeded_noop_when_003_is_not_in_the_ledger(tmp_path, monkeypatch):
	# Nothing has claimed to seed yet, so there is nothing to hold to
	# account — the migration loop itself will run 003 (or crash trying).
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv", _RESOLVED_CSV_ROW)
	db = FakeDb(tables={"identities"}, applied=["001_core_renames"])
	asyncio.run(mig._assert_identities_seeded(db))  # must not raise


def test_assert_identities_seeded_noop_when_no_seed_csv_is_present(tmp_path, monkeypatch):
	# A partner deployment whose image ships neither seed CSV: 003 legitimately
	# seeded nothing, so an empty `identities` is the correct end state.
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	db = FakeDb(tables={"identities"}, applied=["003_seed_identities"])
	asyncio.run(mig._assert_identities_seeded(db))  # must not raise


def test_assert_identities_seeded_noop_when_identities_has_rows(tmp_path, monkeypatch):
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv", _RESOLVED_CSV_ROW)
	db = FakeDb(
		tables={"identities"},
		applied=["003_seed_identities"],
		rows={"identities": [_seeded()]},
	)
	asyncio.run(mig._assert_identities_seeded(db))  # must not raise


def test_assert_identities_seeded_raises_when_identities_is_empty_but_a_seed_csv_has_rows(tmp_path, monkeypatch):
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv", _RESOLVED_CSV_ROW)
	db = FakeDb(tables={"identities"}, applied=["003_seed_identities"])
	try:
		asyncio.run(mig._assert_identities_seeded(db))
	except RuntimeError as e:
		assert "identities" in str(e) and "schema_migrations" in str(e)
	else:
		raise AssertionError("must raise when a seed CSV has rows but identities is empty")


def test_assert_identities_seeded_raises_when_identities_table_is_missing(tmp_path, monkeypatch):
	# The pre-`import bot` window: run_all() runs before ensure_table() ever
	# creates `identities`, so a restored backup that dropped the table
	# outright (rather than merely leaving it empty) must be caught too.
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv", _RESOLVED_CSV_ROW)
	db = FakeDb(tables=set(), applied=["003_seed_identities"])  # no "identities" at all
	try:
		asyncio.run(mig._assert_identities_seeded(db))
	except RuntimeError as e:
		assert "identities" in str(e)
	else:
		raise AssertionError("must raise when identities does not exist but a seed CSV has rows")


def test_assert_identities_seeded_still_fires_once_rs_profiles_is_gone(tmp_path, monkeypatch):
	"""The regression 004_identity_v2 could have introduced: this check used
	to short-circuit on `rs_profiles` not existing, and 004 drops that table.
	Had it stayed anchored there it would have stopped asserting on every
	boot after the drop — silently, which is worse than not having the check
	at all. Deliberately pinned with rs_profiles absent."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv", _RESOLVED_CSV_ROW)
	db = FakeDb(tables={"identities", "matches"}, applied=["003_seed_identities"])
	assert "rs_profiles" not in db.tables
	try:
		asyncio.run(mig._assert_identities_seeded(db))
	except RuntimeError:
		pass
	else:
		raise AssertionError("the post-condition must survive rs_profiles being dropped")


def test_assert_identities_seeded_reads_the_other_seed_csv_too(tmp_path, monkeypatch):
	"""data/player_profile_map.csv is the other source 003 seeds from, and it
	is equally capable of proving `identities` should not be empty."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/player_profile_map.csv",
		"user_id,nick,aoe2_name,profile_id,country\n"
		"222,nickB,MapName,300,us\n")
	db = FakeDb(tables={"identities"}, applied=["003_seed_identities"])
	try:
		asyncio.run(mig._assert_identities_seeded(db))
	except RuntimeError:
		pass
	else:
		raise AssertionError("player_profile_map.csv must arm the check too")


def test_assert_identities_seeded_noop_when_the_csv_holds_no_usable_row(tmp_path, monkeypatch):
	"""A header-only (or entirely unusable) CSV means 003 correctly seeded
	nothing from it, so an empty `identities` is not evidence of anything."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv",
		"profile_id,user_id,nick,aoe2_name,source,appearances\n")
	db = FakeDb(tables={"identities"}, applied=["003_seed_identities"])
	asyncio.run(mig._assert_identities_seeded(db))  # must not raise


def test_the_identities_post_condition_is_still_armed_in_this_repo():
	"""_assert_identities_seeded only asserts anything while 003's seed CSVs
	are actually shipped in the image. Stage 6 deletes those files with the
	other data-file retirements (see docs/superpowers/specs/
	2026-07-30-identity-v2-design.md §6) — and on the boot after that, this
	post-condition would quietly stop asserting, which is the exact failure it
	was re-anchored to avoid in the first place.

	So when this test starts failing, it is not a bug in the test: it means
	003_seed_identities no longer has sources, its documented remedy ("drop
	schema_migrations and reboot so 003 re-runs from scratch") no longer
	restores anything, and _assert_identities_seeded must be DELETED as part
	of the same change rather than left in place doing nothing."""
	assert mig._seed_csv_rows_available() > 0, (
		"no seed CSV left in the image — delete _assert_identities_seeded (and this test) "
		"deliberately instead of letting it silently stop asserting"
	)


def test_run_all_raises_when_identities_seed_did_not_survive_a_restored_backup():
	"""End-to-end reproduction of the scenario _assert_identities_seeded's
	docstring describes: the ledger says every migration (including
	003_seed_identities) already ran, but the restored backup did not bring
	`identities` back with it. No old-named table is involved, so
	_assert_stage1_renames_landed alone would not catch this. Uses the repo's
	real _ROOT, hence the real data/profile_resolved.csv — the same file a
	production image ships."""
	db = FakeDb(
		tables={new for _old, new in mig._STAGE1_RENAMES},
		applied=[name for name, _fn in mig.MIGRATIONS],
	)
	try:
		asyncio.run(mig.run_all(db))
	except RuntimeError as e:
		assert "identities" in str(e)
	else:
		raise AssertionError("run_all must crash when the seed CSV has rows but identities does not")


# ─── 004_identity_v2: (a) repair polluted identities.aoe2_name ──────────
# Production fact this exists for: the old ingest path preferred the Discord
# nick over the game name, and 003 seeded from the already-polluted store, so
# `identities.aoe2_name` holds a Discord nick for a large share of the
# flagship's profiles. data/profile_resolved.csv carries both columns, so it
# can say which stored names are nicks and what the real name is.

# The real rows from data/profile_resolved.csv named in the task brief.
_POLLUTED_CSV = (
	"profile_id,user_id,nick,aoe2_name,source,appearances\n"
	"612690,622810653878648873,ddk,ddk220,seed,17\n"
	"209754,238042803093897216,fenrir05,Fenrir,seed,3\n"
	"1314165,649505118152294421,tyPo Fan,Arkantos12,seed,6\n"
)


def _identity(profile_id, aoe2_name, confidence="seed"):
	return dict(profile_id=profile_id, user_id=1, aoe2_name=aoe2_name,
				confidence=confidence, first_seen_at=1, last_seen_at=1)


def _m004_db(identities, **kwargs):
	rows = dict(kwargs.pop("rows", {}))
	rows["identities"] = identities
	tables = set(kwargs.pop("tables", set())) | {"identities"}
	return FakeDb(tables=tables, rows=rows, **kwargs)


def test_m004_repairs_a_name_polluted_with_the_discord_nick(tmp_path, monkeypatch):
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv", _POLLUTED_CSV)
	db = _m004_db([_identity(612690, "ddk"), _identity(209754, "fenrir05"), _identity(1314165, "tyPo Fan")])

	asyncio.run(mig._m004(db))

	stored = {r["profile_id"]: r["aoe2_name"] for r in db.rows["identities"]}
	assert stored == {612690: "ddk220", 209754: "Fenrir", 1314165: "Arkantos12"}


def test_m004_leaves_an_already_correct_name_alone(tmp_path, monkeypatch):
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv", _POLLUTED_CSV)
	db = _m004_db([_identity(612690, "ddk220")])

	asyncio.run(mig._m004(db))

	assert db.rows["identities"][0]["aoe2_name"] == "ddk220"
	assert db.updates == [], "a correct row must not be rewritten at all"


def test_m004_leaves_an_unrecognised_name_alone(tmp_path, monkeypatch):
	"""A stored name matching neither the nick nor the CSV's game name is of
	unknown provenance — it may well be a later, better correction — so the
	repair must not touch it."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv", _POLLUTED_CSV)
	db = _m004_db([_identity(612690, "SomethingElse")])

	asyncio.run(mig._m004(db))

	assert db.rows["identities"][0]["aoe2_name"] == "SomethingElse"
	assert db.updates == []


def test_m004_is_idempotent_on_a_second_run(tmp_path, monkeypatch):
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv", _POLLUTED_CSV)
	db = _m004_db([_identity(612690, "ddk")])

	asyncio.run(mig._m004(db))
	writes_after_first = len(db.updates)
	asyncio.run(mig._m004(db))

	assert db.rows["identities"][0]["aoe2_name"] == "ddk220"
	assert writes_after_first == 1
	assert len(db.updates) == 1, "a re-run must issue no further repair writes"


def test_m004_skips_the_repair_when_the_csv_is_absent(tmp_path, monkeypatch):
	"""A partner deployment ships no data/profile_resolved.csv; this repair is
	flagship-historical data only and must be a silent no-op there."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	db = _m004_db([_identity(612690, "ddk")])

	asyncio.run(mig._m004(db))  # must not raise

	assert db.rows["identities"][0]["aoe2_name"] == "ddk"
	assert db.updates == []


def test_m004_repair_leaves_confidence_and_user_id_untouched(tmp_path, monkeypatch):
	"""The repair fixes ONE column. Rewriting user_id or confidence would let
	a stale CSV silently undo an admin's `manual` correction."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv", _POLLUTED_CSV)
	db = _m004_db([dict(profile_id=612690, user_id=999, aoe2_name="ddk",
						confidence="manual", first_seen_at=1, last_seen_at=1)])

	asyncio.run(mig._m004(db))

	row = db.rows["identities"][0]
	assert row["aoe2_name"] == "ddk220"
	assert row["user_id"] == 999 and row["confidence"] == "manual"


def test_m004_repair_skips_a_profile_that_is_not_in_identities(tmp_path, monkeypatch):
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	_write_csv(tmp_path, "data/profile_resolved.csv", _POLLUTED_CSV)
	db = _m004_db([])

	asyncio.run(mig._m004(db))

	assert db.rows["identities"] == []
	assert db.updates == []


# ─── 004_identity_v2: (b) backfill match_replays ────────────────────────
# 1107 historical pairings live only in rs_matches.bot_match_id. They are the
# deduction solver's entire input and the join behind every historical
# per-community replay query, and stage 6 drops that column — so this
# backfill must land before then.

def _backfill_db(rs_matches, matches, channels, **kwargs):
	return FakeDb(
		tables={"identities", "rs_matches", "matches", "community_channels", "match_replays"},
		columns={"rs_matches": {"bot_match_id"}},
		rows=dict(rs_matches=rs_matches, matches=matches, community_channels=channels, **kwargs),
	)


def test_m004_backfills_match_replays_from_bot_match_id(tmp_path, monkeypatch):
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	db = _backfill_db(
		rs_matches=[{"aoe2_match_id": 900, "bot_match_id": 10, "parsed_at": 1700}],
		matches=[{"match_id": 10, "channel_id": 55, "reported_at": 1600}],
		channels=[{"channel_id": 55, "community_id": 7}],
	)

	asyncio.run(mig._m004(db))

	assert db.rows["match_replays"] == [
		dict(community_id=7, match_id=10, replay_match_id=900, linked_at=1700)
	]


def test_m004_backfill_skips_an_unenrolled_channel(tmp_path, monkeypatch):
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	db = _backfill_db(
		rs_matches=[{"aoe2_match_id": 900, "bot_match_id": 10, "parsed_at": 1700}],
		matches=[{"match_id": 10, "channel_id": 55, "reported_at": 1600}],
		channels=[],  # channel 55 was never enrolled in a community
	)

	asyncio.run(mig._m004(db))

	assert db.rows.get("match_replays", []) == []


def test_m004_backfill_skips_an_unknown_match(tmp_path, monkeypatch):
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	db = _backfill_db(
		rs_matches=[{"aoe2_match_id": 900, "bot_match_id": 12345, "parsed_at": 1700}],
		matches=[],  # no matches row for bot_match_id 12345
		channels=[{"channel_id": 55, "community_id": 7}],
	)

	asyncio.run(mig._m004(db))

	assert db.rows.get("match_replays", []) == []


def test_m004_backfill_is_idempotent(tmp_path, monkeypatch):
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	db = _backfill_db(
		rs_matches=[{"aoe2_match_id": 900, "bot_match_id": 10, "parsed_at": 1700}],
		matches=[{"match_id": 10, "channel_id": 55, "reported_at": 1600}],
		channels=[{"channel_id": 55, "community_id": 7}],
	)

	asyncio.run(mig._m004(db))
	asyncio.run(mig._m004(db))

	assert len(db.rows["match_replays"]) == 1


def test_m004_backfill_does_not_overwrite_a_link_the_live_path_already_wrote(tmp_path, monkeypatch):
	"""bot/community.py's link_match_replay is the authoritative forward
	writer. INSERT IGNORE (not REPLACE) means this one-shot backfill can never
	stomp a row that writer produced."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	db = _backfill_db(
		rs_matches=[{"aoe2_match_id": 900, "bot_match_id": 10, "parsed_at": 1700}],
		matches=[{"match_id": 10, "channel_id": 55, "reported_at": 1600}],
		channels=[{"channel_id": 55, "community_id": 7}],
		match_replays=[dict(community_id=7, match_id=10, replay_match_id=901, linked_at=9999)],
	)

	asyncio.run(mig._m004(db))

	assert db.rows["match_replays"] == [
		dict(community_id=7, match_id=10, replay_match_id=901, linked_at=9999)
	]


def test_m004_backfill_falls_back_to_the_matches_reported_at(tmp_path, monkeypatch):
	"""linked_at prefers the replay's own parsed_at (when the pairing was
	actually observed); a row predating that column falls back to when the
	match was reported."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	db = _backfill_db(
		rs_matches=[{"aoe2_match_id": 900, "bot_match_id": 10, "parsed_at": None}],
		matches=[{"match_id": 10, "channel_id": 55, "reported_at": 1600}],
		channels=[{"channel_id": 55, "community_id": 7}],
	)

	asyncio.run(mig._m004(db))

	assert db.rows["match_replays"][0]["linked_at"] == 1600


def test_m004_backfill_is_skipped_when_rs_matches_does_not_exist(tmp_path, monkeypatch):
	"""Fresh install: rs_matches is declared by bot/replay_stats, only created
	once `import bot` runs — well after migrations."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	db = FakeDb(tables={"identities", "matches", "community_channels"})

	asyncio.run(mig._m004(db))  # must not raise

	assert db.rows.get("match_replays", []) == []


def test_m004_backfill_is_skipped_once_bot_match_id_is_gone(tmp_path, monkeypatch):
	"""Stage 6 drops rs_matches.bot_match_id. If the ledger is ever dropped
	after that (the runbook's one rollback path), 004 re-runs against a schema
	with no such column — it must skip, not crash the boot."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	db = FakeDb(
		tables={"identities", "rs_matches", "matches", "community_channels"},
		columns={"rs_matches": {"aoe2_match_id"}},  # no bot_match_id
	)

	asyncio.run(mig._m004(db))  # must not raise

	assert db.rows.get("match_replays", []) == []


# ─── 004_identity_v2: (c) drop the retired tables ───────────────────────

def test_m004_drops_the_three_retired_tables(tmp_path, monkeypatch):
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	db = FakeDb(tables={"identities", "rs_profiles", "qc_profile_map", "identity_aliases"})

	asyncio.run(mig._m004(db))

	assert db.tables == {"identities"}
	assert sum(1 for s in db.executed if s.startswith("DROP TABLE")) == 3


def test_m004_drop_is_guarded_when_a_table_is_already_gone(tmp_path, monkeypatch):
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	db = FakeDb(tables={"identities", "qc_profile_map"})

	asyncio.run(mig._m004(db))

	assert db.tables == {"identities"}
	assert sum(1 for s in db.executed if s.startswith("DROP TABLE")) == 1


def test_m004_backfill_is_skipped_when_match_replays_does_not_exist(tmp_path, monkeypatch):
	"""A backup predating stage 1.6: the source pairings are there but the
	destination table is not. Skipping (loudly) rather than raising is
	deliberate — match_replays can only be created by a boot that gets as far
	as `import bot`, so crashing here would make the state unrecoverable."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	db = FakeDb(
		tables={"identities", "rs_matches", "matches", "community_channels"},  # no match_replays
		columns={"rs_matches": {"bot_match_id"}},
		rows=dict(
			rs_matches=[{"aoe2_match_id": 900, "bot_match_id": 10, "parsed_at": 1700}],
			matches=[{"match_id": 10, "channel_id": 55, "reported_at": 1600}],
			community_channels=[{"channel_id": 55, "community_id": 7}],
		),
	)

	asyncio.run(mig._m004(db))  # must not raise

	assert db.rows.get("match_replays", []) == []


def _failing_backfill_db():
	return FakeDb(
		tables={"identities", "rs_matches", "matches", "community_channels", "match_replays",
				"rs_profiles", "qc_profile_map", "identity_aliases"},
		columns={"rs_matches": {"bot_match_id"}},
		raise_on="FROM rs_matches",
	)


def test_m004_does_not_drop_anything_when_an_earlier_part_failed(tmp_path, monkeypatch):
	"""The drops are the only irreversible thing 004 does. A boot in which an
	earlier part already went wrong is exactly the boot not to take an
	irreversible action in — the retry needs the schema it expected."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	db = _failing_backfill_db()

	try:
		asyncio.run(mig._m004(db))
	except RuntimeError as e:
		assert "backfill" in str(e)
	else:
		raise AssertionError("_m004 must re-raise when a part failed, so the ledger is not written")

	assert set(mig._M004_DROPS) <= db.tables, "no table may be dropped after a failed part"
	assert not any(s.startswith("DROP TABLE") for s in db.executed)


def test_run_all_does_not_record_004_when_a_part_failed(tmp_path, monkeypatch):
	"""Integration check that _m004's raise actually stops run_all() from
	recording it: a recorded-but-half-done 004 would never retry, and the 1107
	historical pairings it backfills live nowhere else until stage 6 drops the
	column they are in."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	monkeypatch.setattr(mig, "MIGRATIONS", [("004_identity_v2", mig._m004)])
	db = _failing_backfill_db()

	try:
		asyncio.run(mig.run_all(db))
	except RuntimeError:
		pass
	else:
		raise AssertionError("run_all must propagate _m004's raise")

	assert "004_identity_v2" not in db.applied


def test_m004_dropped_tables_have_no_surviving_declaration():
	"""A DROP of a table whose ensure_table declaration still exists
	accomplishes nothing: the very next `import bot` recreates it empty, and
	nothing anywhere would say so. Pinned as a test rather than a one-time
	grep so re-adding a declaration fails CI instead of silently resurrecting
	the table on the next deploy."""
	from core.data_registry import REGISTRY
	from tests.test_data_registry import _declared_tables

	declared = _declared_tables()
	for name in mig._M004_DROPS:
		assert name not in declared, f"{name!r} is still declared by an ensure_table call"
		assert name not in REGISTRY, f"{name!r} still has a core.data_registry entry"


def test_m004_runs_the_backfill_before_the_drops(tmp_path, monkeypatch):
	"""Ordering is load-bearing in the other direction too: the backfill reads
	nothing from the dropped tables today, but the drops are irreversible
	within a boot, so the parts must stay in the documented order."""
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	db = _backfill_db(
		rs_matches=[{"aoe2_match_id": 900, "bot_match_id": 10, "parsed_at": 1700}],
		matches=[{"match_id": 10, "channel_id": 55, "reported_at": 1600}],
		channels=[{"channel_id": 55, "community_id": 7}],
	)
	db.tables |= {"rs_profiles", "qc_profile_map", "identity_aliases"}

	asyncio.run(mig._m004(db))

	assert len(db.rows["match_replays"]) == 1
	assert not (db.tables & set(mig._M004_DROPS))


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


# ─── boot post-conditions run BEFORE anything irreversible ──────────────
# run_all asserts the post-conditions on both sides of the migration loop. The
# "before" half is the one that matters: the post-conditions describe a
# database whose repair is "drop the ledger and reboot so 003 re-runs", and 004
# DROPS 003's highest-precedence seed source. Checking only afterwards means
# the advice is issued after the data it depends on is already gone.

def _restored_backup_db():
	""" The exact reachable state review found: a restored backup whose ledger
	records 001-003 but whose `identities` did not come back with it, on a
	database that still has rs_profiles. """
	return FakeDb(
		tables={new for _old, new in mig._STAGE1_RENAMES} | {"rs_profiles"},
		applied=["001_core_renames", "002_drop_retired", "003_seed_identities"],
	)


def test_run_all_refuses_to_drop_anything_when_a_post_condition_already_fails():
	db = _restored_backup_db()

	try:
		asyncio.run(mig.run_all(db))
	except RuntimeError as e:
		assert "identities" in str(e)
	else:
		raise AssertionError("run_all must crash on a restored backup that lost identities")

	assert "rs_profiles" in db.tables, (
		"004 must not drop 003's highest-precedence seed source while the post-condition "
		"that tells the operator to re-run 003 is already failing")
	assert "004_identity_v2" not in db.applied
	assert not any(sql.startswith("DROP TABLE") for sql in db.executed)


def test_run_all_still_checks_the_post_conditions_after_the_loop():
	""" The "after" half is not redundant: a migration in the loop can itself
	leave the schema in the state the post-conditions forbid. """
	calls = []

	async def _breaks_it(db):
		db.tables.add("qc_matches")
		calls.append("ran")

	db = FakeDb(applied=["001_core_renames"])
	original = list(mig.MIGRATIONS)
	mig.MIGRATIONS[:] = [("999_breaks_it", _breaks_it)]
	try:
		try:
			asyncio.run(mig.run_all(db))
		except RuntimeError as e:
			assert "qc_matches" in str(e)
		else:
			raise AssertionError("a migration that recreates a pre-rename table must still be caught")
	finally:
		mig.MIGRATIONS[:] = original
	assert calls == ["ran"], "the loop still ran; the check fired afterwards"


def test_the_pre_loop_post_conditions_do_not_fire_on_a_first_ever_deploy():
	""" Ledger-gated, so a database that simply has not been migrated yet is
	untouched by the pre-loop check -- the pre-rename tables existing IS the
	ordinary state there, and crashing would make the first deploy impossible. """
	old_names = {old for old, _new in mig._STAGE1_RENAMES}
	db = FakeDb(tables=old_names)

	asyncio.run(mig.run_all(db))

	assert not (old_names & db.tables)
	assert "004_identity_v2" in db.applied


# ─── 004 (b): the backfill reports what LANDED, not what it meant to ────

class _CapturedLog:
	""" Stands in for mig.log, keeping what was said. conftest.py's real fake
	swallows everything, and a one-shot backfill's log line IS its only output —
	if it is wrong there is nothing else to be right. """

	def __init__(self):
		self.info, self.warning, self.error = [], [], []

	def _record(self, bucket):
		return lambda msg: bucket.append(msg)

	def __getattr__(self, name):
		raise AttributeError(f"migrations logged at an unexpected level: {name}")


def _capture_log(monkeypatch):
	rec = _CapturedLog()
	monkeypatch.setattr(mig, "log", types.SimpleNamespace(
		info=rec._record(rec.info),
		warning=rec._record(rec.warning),
		error=rec._record(rec.error),
	))
	return rec


def test_m004_backfill_counts_a_collapsed_pairing_separately(tmp_path, monkeypatch):
	""" Two rs_matches rows naming ONE bot match: match_replays' primary key is
	(community_id, match_id), so only one can be stored and INSERT IGNORE drops
	the other in silence. The runbook attributes every gap to "unknown match /
	unenrolled channel", so a collapse counted as one of those would be
	misdiagnosed. """
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	rec = _capture_log(monkeypatch)
	db = _backfill_db(
		rs_matches=[
			{"aoe2_match_id": 900, "bot_match_id": 10, "parsed_at": 1700},
			{"aoe2_match_id": 901, "bot_match_id": 10, "parsed_at": 1800},
		],
		matches=[{"match_id": 10, "channel_id": 55, "reported_at": 1600}],
		channels=[{"channel_id": 55, "community_id": 7}],
	)

	asyncio.run(mig._m004_backfill_match_replays(db))

	assert len(db.rows["match_replays"]) == 1
	summary = rec.info[-1]
	assert "1 of 1" in summary, f"report what landed, not the 2 rows it read: {summary}"
	assert "1 sharing a bot match id" in summary, summary
	assert any("more than one" in w for w in rec.warning), "name the match that collapsed"


def test_m004_backfill_does_not_claim_a_row_the_ignore_left_alone(tmp_path, monkeypatch):
	""" link_match_replay is authoritative, so INSERT IGNORE deliberately does
	not overwrite a row it already wrote for this match. The log must not count
	that as backfilled. """
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	rec = _capture_log(monkeypatch)
	db = _backfill_db(
		rs_matches=[{"aoe2_match_id": 900, "bot_match_id": 10, "parsed_at": 1700}],
		matches=[{"match_id": 10, "channel_id": 55, "reported_at": 1600}],
		channels=[{"channel_id": 55, "community_id": 7}],
	)
	db.rows["match_replays"] = [
		dict(community_id=7, match_id=10, replay_match_id=999, linked_at=1)
	]

	asyncio.run(mig._m004_backfill_match_replays(db))

	assert db.rows["match_replays"][0]["replay_match_id"] == 999, "the live writer wins"
	summary = rec.info[-1]
	assert "0 of 1" in summary, f"nothing landed, so do not claim 1: {summary}"
	assert "1 already linked to a different replay" in summary, summary


def test_m004_backfill_verified_count_matches_the_rows_written(tmp_path, monkeypatch):
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	rec = _capture_log(monkeypatch)
	db = _backfill_db(
		rs_matches=[
			{"aoe2_match_id": 900, "bot_match_id": 10, "parsed_at": 1700},
			{"aoe2_match_id": 901, "bot_match_id": 11, "parsed_at": 1800},
		],
		matches=[
			{"match_id": 10, "channel_id": 55, "reported_at": 1600},
			{"match_id": 11, "channel_id": 55, "reported_at": 1601},
		],
		channels=[{"channel_id": 55, "community_id": 7}],
	)

	asyncio.run(mig._m004_backfill_match_replays(db))

	assert len(db.rows["match_replays"]) == 2
	assert "2 of 2" in rec.info[-1]


# ─── 005_identity_conflict_history ──────────────────────────────────────
# Widens identity_conflicts' claim key from (profile_id, claimed_user_id) to
# (profile_id, claimed_user_id, status) on a surrogate primary key. The bug:
# INSERT IGNORE against the narrow key kept whichever status landed FIRST, so
# a refusal recorded after a supersede for the same pair was swallowed and
# `/identity conflicts` showed nothing.

def _pre_005_conflicts_db(**kwargs):
	""" identity_conflicts as 003 created it before this migration existed. """
	return FakeDb(
		tables={"identity_conflicts"},
		columns={"identity_conflicts": {"profile_id", "claimed_user_id", "source", "noticed_at", "status"}},
		indexes={"identity_conflicts": {"PRIMARY"}},
		**kwargs,
	)


def test_m005_adds_the_claim_index_and_moves_the_primary_key(tmp_path, monkeypatch):
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	db = _pre_005_conflicts_db()

	asyncio.run(mig._m005(db))

	assert mig._CONFLICT_CLAIM_INDEX in db.indexes["identity_conflicts"]
	assert "id" in db.columns["identity_conflicts"]
	assert "PRIMARY" in db.indexes["identity_conflicts"], "a surrogate primary key, not none at all"


def test_m005_adds_the_unique_index_before_it_moves_the_primary_key(tmp_path, monkeypatch):
	""" Order is not cosmetic: between the two statements the table must never
	be without a uniqueness constraint covering the dedup, or an INSERT IGNORE
	landing in that window (a rolling deploy still serving from the previous
	container) silently becomes a plain INSERT. """
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	db = _pre_005_conflicts_db()

	asyncio.run(mig._m005(db))

	alters = [sql for sql in db.executed if sql.startswith("ALTER TABLE")]
	assert len(alters) == 2
	assert "ADD UNIQUE KEY" in alters[0], alters
	assert "DROP PRIMARY KEY" in alters[1] and "AUTO_INCREMENT" in alters[1], alters


def test_m005_is_idempotent(tmp_path, monkeypatch):
	""" Every statement is guarded on its own information_schema signal, not on
	the ledger: a body that dies partway through is re-run from the top on the
	next boot, and MySQL has no ADD COLUMN IF NOT EXISTS. """
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	db = _pre_005_conflicts_db()

	asyncio.run(mig._m005(db))
	before = list(db.executed)
	asyncio.run(mig._m005(db))

	assert db.executed == before, "a second run must issue no DDL at all"


def test_m005_creates_the_table_in_the_new_shape_when_it_is_absent(tmp_path, monkeypatch):
	""" A fresh install where 003 has not created it yet (or a database that
	somehow lost it): create it right rather than ALTERing something missing. """
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	db = FakeDb()

	asyncio.run(mig._m005(db))

	assert "identity_conflicts" in db.tables
	assert mig._CONFLICT_CLAIM_INDEX in db.indexes["identity_conflicts"]
	assert "id" in db.columns["identity_conflicts"]
	assert not any(sql.startswith("ALTER TABLE") for sql in db.executed)


def test_m005_does_not_drop_a_primary_key_that_is_not_there(tmp_path, monkeypatch):
	""" DROP PRIMARY KEY on a table with none is an error, not a no-op — so the
	clause is guarded on information_schema rather than assumed. Reachable on a
	half-applied body that got as far as dropping the old key. """
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	db = _pre_005_conflicts_db()
	db.indexes["identity_conflicts"] = {mig._CONFLICT_CLAIM_INDEX}  # no PRIMARY

	asyncio.run(mig._m005(db))

	alters = [sql for sql in db.executed if sql.startswith("ALTER TABLE")]
	assert len(alters) == 1
	assert "DROP PRIMARY KEY" not in alters[0], alters


def test_m005_is_a_noop_after_003_created_the_table_in_the_new_shape(tmp_path, monkeypatch):
	""" On a genuinely fresh install 003 runs first and already builds the new
	shape, so 005 must recognise its own work and do nothing. """
	monkeypatch.setattr(mig, "_ROOT", str(tmp_path))
	db = FakeDb()
	asyncio.run(mig._ensure_identity_conflicts_table(db))
	before = list(db.executed)

	asyncio.run(mig._m005(db))

	assert db.executed == before


def test_the_conflicts_ddl_matches_the_ensure_table_declaration():
	""" identity_conflicts is declared TWICE — bot/identity.py's ensure_table
	and this module's raw CREATE TABLE (a migration cannot import bot.*) — and
	they are kept in sync by hand. Drift is not theoretical: 005 exists because
	the key was wrong, and a fresh install gets its schema from the raw DDL
	while every runtime write assumes the declaration. Compare them here so the
	build fails instead of production diverging.

	Parses bot/identity.py's declaration as text rather than importing it:
	`import bot.identity` executes db.ensure_table() against conftest's fake. """
	import os

	path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot", "identity.py")
	with open(path, encoding="utf-8") as f:
		src = f.read()
	block = src.split('tname="identity_conflicts"', 1)[1].split("))", 1)[0]

	declared_columns = re.findall(r'cname="(\w+)"', block)
	declared_pk = re.findall(r'primary_keys=\[([^\]]*)\]', block)[0]
	declared_unique = re.findall(r'unique_keys=\[\("(\w+)", \[([^\]]*)\]\)\]', block)[0]
	# Nullability is checked too, not just the column list. profile_id and
	# claimed_user_id stopped being primary-key columns in 005, which is what
	# used to force them NOT NULL — and a NULL in either defeats the unique
	# index outright (NULLs never compare equal), so the two declarations
	# drifting on this one attribute would silently disable the dedup on a
	# fresh install while production kept working.
	declared_notnull = [c for c in re.findall(r'cname="(\w+)"[^\n]*notnull=True', block)]

	ddl = []

	class _Collect:
		@staticmethod
		async def execute(sql, args=None):
			ddl.append(sql)

	asyncio.run(mig._ensure_identity_conflicts_table(_Collect))
	sql = ddl[0]

	assert _DDL_COLUMN.findall(sql) == declared_columns, "column list drifted"
	assert re.findall(r"`(\w+)` (?:BIGINT|VARCHAR\(\d+\)) NOT NULL", sql) == declared_notnull, \
		"NOT NULL drifted"
	assert f"PRIMARY KEY({declared_pk.replace(chr(34), '`')})" in sql, "primary key drifted"
	assert declared_unique[0] == mig._CONFLICT_CLAIM_INDEX
	assert f"UNIQUE KEY `{declared_unique[0]}` ({declared_unique[1].replace(chr(34), '`')})" in sql, \
		"unique claim index drifted"
