"""The startup migration runner.

Pure-logic tests against a fake adapter: the runner must apply each migration
exactly once, record it in the ledger, and make renames idempotent via
existence guards. No MySQL involved.
"""
import asyncio

import core.migrations as mig

# {table: primary key column(s)} for FakeDb.insert_many's INSERT IGNORE
# emulation below. Only tables this suite actually writes via insert_many
# need an entry. A tuple means a composite primary key.
_PRIMARY_KEYS = {"identities": "profile_id", "identity_conflicts": ("profile_id", "claimed_user_id")}


def _pk_key(row, pk):
	""" The dedup key insert_many's INSERT IGNORE emulation compares by --
	a single value for a single-column primary key, a tuple of values for a
	composite one. """
	return tuple(row[c] for c in pk) if isinstance(pk, tuple) else row[pk]


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
		if "FROM rs_profiles" in sql:
			return {"x": 1} if self.rows.get("rs_profiles") else None
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
		return []

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


def test_ensure_identity_conflicts_table_is_idempotent():
	db = FakeDb()
	asyncio.run(mig._ensure_identity_conflicts_table(db))
	asyncio.run(mig._ensure_identity_conflicts_table(db))
	assert db.executed.count(
		"CREATE TABLE IF NOT EXISTS identity_conflicts ("
		"`profile_id` BIGINT, `claimed_user_id` BIGINT, `source` VARCHAR(191) NOT NULL, "
		"`noticed_at` BIGINT NOT NULL, `status` VARCHAR(191) NOT NULL DEFAULT 'open', "
		"PRIMARY KEY(`profile_id`, `claimed_user_id`))"
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
# entirely) but not identities' data, while rs_profiles (real match
# history) survived the restore.

def test_assert_identities_seeded_noop_when_rs_profiles_does_not_exist():
	# Genuinely fresh install: rs_profiles is declared by bot/replay_stats,
	# only created by `import bot` — well after run_all() returns.
	db = FakeDb(tables=set())
	asyncio.run(mig._assert_identities_seeded(db))  # must not raise


def test_assert_identities_seeded_noop_when_rs_profiles_is_empty():
	# rs_profiles exists (a prior boot got as far as `import bot`) but no
	# replay has been ingested yet -- identities being empty alongside it is
	# unremarkable, not a symptom of the backup-restore failure mode.
	db = FakeDb(tables={"rs_profiles", "identities"})
	asyncio.run(mig._assert_identities_seeded(db))  # must not raise


def test_assert_identities_seeded_noop_when_both_have_rows():
	db = FakeDb(
		tables={"rs_profiles", "identities"},
		rows={
			"rs_profiles": [{"profile_id": 1, "user_id": 2, "name": "x", "last_seen_at": 1}],
			"identities": [{"profile_id": 1, "user_id": 2, "aoe2_name": "x",
							"confidence": "learned", "first_seen_at": 1, "last_seen_at": 1}],
		},
	)
	asyncio.run(mig._assert_identities_seeded(db))  # must not raise


def test_assert_identities_seeded_raises_when_identities_is_empty_but_rs_profiles_has_rows():
	db = FakeDb(
		tables={"rs_profiles", "identities"},
		rows={"rs_profiles": [{"profile_id": 1, "user_id": 2, "name": "x", "last_seen_at": 1}]},
	)
	try:
		asyncio.run(mig._assert_identities_seeded(db))
	except RuntimeError as e:
		assert "identities" in str(e) and "rs_profiles" in str(e)
	else:
		raise AssertionError("must raise when rs_profiles has rows but identities is empty")


def test_assert_identities_seeded_raises_when_identities_table_is_missing_but_rs_profiles_has_rows():
	# The pre-`import bot` window: run_all() runs before ensure_table() ever
	# creates `identities`, so a restored backup that dropped the table
	# outright (rather than merely leaving it empty) must be caught too.
	db = FakeDb(
		tables={"rs_profiles"},  # no "identities" here at all
		rows={"rs_profiles": [{"profile_id": 1, "user_id": 2, "name": "x", "last_seen_at": 1}]},
	)
	try:
		asyncio.run(mig._assert_identities_seeded(db))
	except RuntimeError as e:
		assert "identities" in str(e)
	else:
		raise AssertionError("must raise when identities table does not exist but rs_profiles has rows")


def test_run_all_raises_when_identities_seed_did_not_survive_a_restored_backup():
	"""End-to-end reproduction of the scenario _assert_identities_seeded's
	docstring describes: the ledger says every migration (including
	003_seed_identities) already ran, but the restored backup did not bring
	`identities` back with it while rs_profiles (real match history) is
	intact. No old-named table is involved, so _assert_stage1_renames_landed
	alone would not catch this."""
	db = FakeDb(
		tables={"rs_profiles"} | {new for _old, new in mig._STAGE1_RENAMES},
		applied=[name for name, _fn in mig.MIGRATIONS],
		rows={"rs_profiles": [{"profile_id": 1, "user_id": 2, "name": "x", "last_seen_at": 1}]},
	)
	try:
		asyncio.run(mig.run_all(db))
	except RuntimeError as e:
		assert "identities" in str(e)
	else:
		raise AssertionError("run_all must crash when rs_profiles has rows but identities does not")


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
