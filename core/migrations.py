# -*- coding: utf-8 -*-
"""Startup schema migrations.

Runs in PUBobot2.py AFTER database.db.connect() and BEFORE `import bot`. That
ordering is load-bearing: every bot package declares its tables via
db.ensure_table() at import time, and ensure_table CREATEs any name it does not
find — so a rename that has not happened yet would strand the old table and
spawn an empty new one. Renaming first lets the updated declarations find the
renamed tables.

Every migration is idempotent by construction (existence guards), and the
schema_migrations ledger additionally records what ran, so seeds and drops
execute exactly once. The prod DB is only ever written from inside the deployed
bot — this module is that write path; never run it from a laptop.

For future migration authors: MySQL DDL auto-commits, and there is no
transaction wrapping a migration body together with its ledger write. If a
migration dies partway through, everything it already executed is permanent,
and the next boot re-runs the whole body from the top. That means every single
statement inside a migration body must be individually idempotent on its own —
guarded by `table_exists`/`column_exists` (as `rename_table`/`rename_column`
already do) or written as `IF EXISTS`/`INSERT IGNORE` — not just the migration
as a whole. Do not rely on the ledger to make a half-applied body safe to
re-run; the ledger only records success after the entire body returns.
"""
import os
import time

from core.console import log
from core.identity_seed import CONFIDENCE_ORDER, parse_name_repairs, parse_seed_csv

LEDGER = "schema_migrations"
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# (name, async fn(db)) in execution order. Stage tasks append via @migration.
MIGRATIONS = []


def migration(name):
	def deco(fn):
		MIGRATIONS.append((name, fn))
		return fn
	return deco


async def table_exists(db, name):
	row = await db.fetchone(
		"SELECT 1 AS x FROM information_schema.TABLES "
		"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", [name])
	return row is not None


async def column_exists(db, table, column):
	row = await db.fetchone(
		"SELECT 1 AS x FROM information_schema.COLUMNS "
		"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
		[table, column])
	return row is not None


async def index_exists(db, table, index):
	"""Whether `table` carries an index named `index`. The primary key is called
	`PRIMARY` in information_schema, so this answers "does it have a primary
	key?" too — which is what makes a `DROP PRIMARY KEY` clause guardable
	(dropping one that is not there is an error, not a no-op)."""
	row = await db.fetchone(
		"SELECT 1 AS x FROM information_schema.STATISTICS "
		"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s LIMIT 1",
		[table, index])
	return row is not None


async def rename_table(db, old, new):
	old_there = await table_exists(db, old)
	new_there = await table_exists(db, new)
	if old_there and new_there:
		raise RuntimeError(f"rename {old} -> {new}: both exist; resolve manually before deploying")
	if old_there:
		await db.execute(f"RENAME TABLE `{old}` TO `{new}`")
		log.info(f"migrations: renamed table {old} -> {new}")
	# new-only or neither: nothing to do (already renamed / fresh install).


async def rename_column(db, table, old, new):
	"""Rename one column, idempotently — and refuse, loudly, when BOTH names are
	present.

	The both-exist case is the column-level twin of rename_table's, and it is
	reachable the same way: ensure_table's _ensure_table() ADDs any declared
	column it does not find (unlike a table rename, which it cannot do at all),
	so a code rollback — or a boot that somehow reaches `import bot` against a
	half-renamed table — leaves the old column holding every row's value beside
	a brand-new empty one. Skipping quietly there (what this used to do) would
	make the new column read NULL forever, silently, with a healthy-looking
	bot; raising hands the operator a schema they can still repair by hand."""
	if not await table_exists(db, table):
		return
	old_there = await column_exists(db, table, old)
	new_there = await column_exists(db, table, new)
	if old_there and new_there:
		raise RuntimeError(
			f"rename column {table}.{old} -> {new}: both exist; resolve manually before deploying "
			f"(the data is in `{old}`; `{new}` was almost certainly added empty by an ensure_table "
			"declaration on a rolled-back deploy)")
	if old_there:
		await db.execute(f"ALTER TABLE `{table}` RENAME COLUMN `{old}` TO `{new}`")
		log.info(f"migrations: renamed column {table}.{old} -> {new}")


async def _ledger_ensure(db):
	await db.execute(
		f"CREATE TABLE IF NOT EXISTS {LEDGER} "
		"(name VARCHAR(191) NOT NULL, applied_at BIGINT NOT NULL, PRIMARY KEY (name))")


async def run_all(db):
	await _ledger_ensure(db)
	rows = await db.fetchall(f"SELECT name FROM {LEDGER}")
	done = {r["name"] for r in rows or []}

	# BEFORE the loop, not only after it. The post-conditions describe a
	# database that has to be repaired by dropping the ledger and rebooting, and
	# migrations drop tables — so checking them only at the end means an
	# irreversible body gets to run first, on a database already known to be
	# broken, and the crash that follows tells the operator to re-run a
	# migration whose source data the previous line just destroyed.
	#
	# Concretely, the case review found: a restored backup whose ledger records
	# 001-003 but whose `identities` is missing. 004 runs, DROPS rs_profiles —
	# 003's highest-precedence seed source — records itself, and only then does
	# _assert_identities_seeded crash saying "drop the ledger and reboot so 003
	# re-runs". It does re-run, from the CSVs alone, and every `learned` binding
	# that lived only in rs_profiles is gone, silently, with a healthy-looking
	# bot. The invariant this restores: nothing irreversible executes while the
	# post-conditions would fail.
	#
	# Both checks are ledger-gated, so on a genuinely fresh install (or any
	# database sitting legitimately behind) they are no-ops here and the loop
	# below does its normal work.
	#
	# Consequence for future migration authors: a migration can NOT be the fix
	# for a state a post-condition rejects, because the post-condition now runs
	# first and the migration never gets to execute. Those states are repaired
	# by the runbook (drop `schema_migrations`, reboot), not by new code — which
	# was always the intent, and is now enforced rather than assumed.
	await _assert_boot_post_conditions(db)

	n = 0
	for name, fn in MIGRATIONS:
		if name in done:
			continue
		log.info(f"migrations: applying {name}")
		await fn(db)
		# INSERT IGNORE: Railway rolling deploys can boot two containers at once,
		# both passing the "already applied?" check above before either records
		# it. The rename/column guards make the DDL itself safe to repeat, but
		# without IGNORE the loser's ledger write would crash on a duplicate
		# primary key even though its migration already succeeded harmlessly.
		await db.execute(
			f"INSERT IGNORE INTO {LEDGER} (name, applied_at) VALUES (%s, %s)",
			[name, int(time.time())])
		n += 1
	log.info(f"migrations: {n} applied this boot, {len(MIGRATIONS)} known")
	await _assert_boot_post_conditions(db)


async def _assert_boot_post_conditions(db):
	"""Every boot-time schema invariant, in one place so run_all can assert them
	both before and after the migration loop — see run_all for why "before"
	is the half that matters. Each one is individually ledger-gated and is a
	pure read, so running them twice a boot costs two trivial queries."""
	await _assert_renames_landed(db, _CORE_RENAMES_LEDGER_NAME, _STAGE1_RENAMES)
	await _assert_renames_landed(db, _RAW_RENAMES_LEDGER_NAME, _STAGE3_RENAMES)
	await _assert_identities_seeded(db)


_CORE_RENAMES_LEDGER_NAME = "001_core_renames"
_RAW_RENAMES_LEDGER_NAME = "007_raw_renames"


async def _assert_renames_landed(db, ledger_name, renames):
	"""Post-condition, checked on every boot regardless of what the loop above
	did: if the ledger says `ledger_name` ran, none of its *source* tables may
	still exist.

	Applied to every rename migration (001_core_renames and 007_raw_renames
	today), because the failure it catches is a property of renaming tables at
	all, not of any one stage: a source table that outlives its own rename
	migration is a database that will boot healthy and serve nothing.

	run_all() decides what to run solely from the schema_migrations ledger.
	If an operator restores a pre-deploy database backup, the ledger table
	survives the restore (the dump predates it, and mysqldump does not drop
	tables absent from the dump) while the renamed tables do not — so the
	loop above sees every migration already marked done and skips all of
	them. The bot would then boot straight into `import bot`, whose
	ensure_table() calls CREATE every one of the new names empty, and the
	bot would come up looking healthy while serving no history at all. The
	same thing happens after a code-only rollback that leaves the ledger
	ahead of the schema.

	Crashing here is the intended behaviour: Railway restarts the
	container, and a loud crash is vastly better than a silently empty bot.

	Safe on a genuinely fresh install: at the point run_all() runs (after
	db.connect(), before `import bot`), no old-named table has ever been
	created — ensure_table() only runs later, when bot/ is imported — so
	every table_exists() check below is False and this is a no-op.

	The ledger gate is what makes this callable BEFORE the migration loop as
	well as after it (see run_all): on a database that simply has not been
	migrated yet, the pre-rename tables existing is the ordinary state and not
	a disagreement with anything. It costs nothing after the loop either — the
	only case it excludes is one where the migration has not run, in which case
	the loop either just renamed them (and recorded it) or died trying.
	"""
	applied = await db.fetchone(f"SELECT 1 AS x FROM {LEDGER} WHERE name = %s", [ledger_name])
	if applied is None:
		return  # nothing has claimed to have renamed yet; the loop owns that
	offenders = [old for old, _new in renames if await table_exists(db, old)]
	if offenders:
		raise RuntimeError(
			"migrations: schema/ledger disagreement — these pre-rename tables still "
			f"exist even though the ledger says {ledger_name} already ran: {', '.join(offenders)}. "
			"This almost always means a pre-deploy database backup was restored (mysqldump "
			"keeps schema_migrations but not the renamed tables) or a code-only rollback "
			"happened. Fix: drop the `schema_migrations` table and reboot."
		)


_SEED_LEDGER_NAME = "003_seed_identities"

# The seed CSVs 003_seed_identities reads, as (path relative to _ROOT, kind),
# in the precedence order _m003 reads them. _m003's seeding loop,
# _seed_csv_rows_available() and 004's name repair all resolve their paths from
# here, so there is exactly one place that says where these files live.
_RESOLVED_CSV = "data/profile_resolved.csv"
_SEED_CSVS = ((_RESOLVED_CSV, "resolved"), ("data/player_profile_map.csv", "profile_map"))


def _seed_csv_rows_available():
	"""How many usable seed rows the CSVs shipped in THIS deploy image hold,
	across both files — i.e. the number of `identities` rows
	003_seed_identities is expected to have produced from them.

	Deliberately reads the image, not the database: the image is the one
	input to 003 that a restored database backup cannot take away, which is
	what makes it a usable yardstick for _assert_identities_seeded below.

	Returns 0 for anything it cannot read (file absent, unreadable, header
	only). That direction is deliberate: an unreadable CSV disarms the
	post-condition rather than crashing the boot over it, since a file-system
	quirk here says nothing at all about whether the database is intact.
	"""
	total = 0
	for relpath, kind in _SEED_CSVS:
		path = os.path.join(_ROOT, relpath)
		try:
			if not os.path.exists(path):
				continue
			with open(path, encoding="utf-8") as f:
				total += len(parse_seed_csv(f.read(), kind))
		except Exception as e:
			log.error(f"migrations: could not read {relpath} while checking the identities post-condition: {e}")
	return total


async def _assert_identities_seeded(db):
	"""Post-condition, checked on every boot after the migration loop: if the
	ledger says 003_seed_identities already ran, and this deploy image still
	carries seed CSVs with usable rows in them, then `identities` must not be
	empty (or missing outright).

	003_seed_identities re-raises when every one of its seed sources
	genuinely fails (see _m003), so a failed *first* seed is never recorded
	as done. But the same empty-forever end state is also reachable via the
	rollback runbook: restoring a database backup taken after
	003_seed_identities was recorded in schema_migrations but before (or
	without) `identities` restores the ledger row without restoring the
	table's data — and unlike _assert_renames_landed's renames, no
	old-named table is left behind for that check to catch, because
	`identities` was never renamed from anything. The loop above sees
	003_seed_identities already applied and skips it; `import bot`'s
	ensure_table() then either finds the empty restored table or CREATEs it
	fresh, either way empty. From there the failure is silent: every civ stat
	lookup routes through profiles_for_users(), which just returns {} for an
	empty table, and bot/civ_matcher.py's _find_and_record() treats too few
	mapped players as "nothing to record here", not an error — no exception,
	no retry, no log line, and /health stays 200.

	Crashing here is the intended behaviour, for the same reason as
	_assert_renames_landed: a loud crash beats a silently empty bot.

	Why it is anchored HERE and not on a table (task 2.5.8): this check used
	to key off `rs_profiles` holding rows. 004_identity_v2 drops rs_profiles,
	which would have turned the first line of this function into a permanent,
	silent no-op on every boot after that migration — an assert that quietly
	stops asserting, which is strictly worse than no assert at all. The two
	signals it now uses are the two that survive: the ledger entry that makes
	the claim, and 003's own CSV sources in the deploy image. That pairing is
	also a tighter statement of the actual invariant than the old one was
	("what 003 says it did must be visible"), and it no longer depends on a
	table 003 merely happened to read first.

	Safe on a genuinely fresh install: 003_seed_identities runs in the loop
	just above, seeds those very CSVs, and only then is recorded — so by the
	time this runs, either identities has rows or 003 raised and this is
	never reached. Safe on a partner deployment whose image ships no seed CSV
	at all: nothing was expected, so nothing is asserted (there is no
	replacement signal for that case now that rs_profiles is gone, and
	inventing one out of `matches`/`match_players` would crash-loop the boot
	of any perfectly healthy deployment that simply has no replay history or
	seed data).

	Lifetime: stage 6 deletes both seed CSVs, at which point this check would
	go quiet again — and so would the remedy in its error messages, since
	re-running 003 with no sources left restores nothing. That must be a
	deliberate deletion, not a silent one, so
	tests/test_migrations.py::test_the_identities_post_condition_is_still_armed
	_in_this_repo fails the build the moment the files go, with instructions
	to remove this function along with them.
	"""
	applied = await db.fetchone(f"SELECT 1 AS x FROM {LEDGER} WHERE name = %s", [_SEED_LEDGER_NAME])
	if applied is None:
		return  # nothing has claimed to have seeded yet; the loop above owns that
	expected = _seed_csv_rows_available()
	if expected == 0:
		return  # no seed source in this image -> an empty `identities` is correct
	if not await table_exists(db, "identities"):
		raise RuntimeError(
			f"migrations: identities table is missing while this deploy's seed CSVs hold {expected} "
			"usable row(s) — 003_seed_identities is recorded as applied in schema_migrations but "
			"`identities` itself does not exist. This almost always means a database backup was "
			"restored that kept schema_migrations but predates the identities table. Fix: drop the "
			"`schema_migrations` table and reboot so 003_seed_identities re-runs from scratch."
		)
	id_row = await db.fetchone("SELECT 1 AS x FROM identities LIMIT 1")
	if id_row is not None:
		return
	raise RuntimeError(
		f"migrations: identities table is empty while this deploy's seed CSVs hold {expected} usable "
		"row(s) — 003_seed_identities is recorded as applied in schema_migrations but never actually "
		"seeded anything, most likely because a database backup was restored that kept "
		"schema_migrations but not identities' data. Fix: drop the `schema_migrations` table and "
		"reboot so 003_seed_identities re-runs from scratch."
	)


_STAGE1_RENAMES = [
	("qc_matches", "matches"), ("qc_player_matches", "match_players"),
	("qc_players", "player_ratings"), ("qc_rating_history", "rating_history"),
	("qc_match_id_counter", "match_counter"), ("qc_configs", "channel_settings"),
	("pq_configs", "queue_settings"), ("qc_saved_state", "bot_state"),
	("players", "player_prefs"), ("noadds", "queue_bans"),
	("qc_phrases", "player_phrases"), ("qc_douche", "douche_log"),
	("qc_match_civs", "civ_picks"), ("qc_civ_reconcile", "civ_reconcile"),
	("qc_lobbies", "lobbies"), ("qc_quiz_posts", "quiz_posts"),
	("qc_quiz_answers", "quiz_answers"), ("qc_quiz_config", "quiz_settings"),
	("qc_prediction_posts", "prediction_posts"),
	("qc_prediction_votes", "prediction_votes"),
]


@migration("001_core_renames")
async def _m001(db):
	for old, new in _STAGE1_RENAMES:
		await rename_table(db, old, new)
	await rename_column(db, "matches", "at", "reported_at")


@migration("002_drop_retired")
async def _m002(db):
	for t in ("bot_player_commentary", "disabled_guilds"):
		if await table_exists(db, t):
			await db.execute(f"DROP TABLE `{t}`")
			log.info(f"migrations: dropped retired table {t}")


async def _ensure_identities_table(db):
	"""Minimal, idempotent CREATE TABLE for `identities` — bot/identity.py's
	table, duplicated here rather than created via its db.ensure_table()
	declaration, because db.ensure_table() cannot be reached from this
	module (see this module's docstring for why importing anything under
	bot.* from a migration crashes the boot).

	This CREATE TABLE must stay in sync with bot/identity.py's `identities`
	declaration by hand — that part cannot be shared. The row-parsing logic
	that used to be duplicated alongside it now lives in
	core/identity_seed.py (stdlib-only, safe to import from both here and
	bot/identity.py) precisely so it does not have to be kept in sync by
	hand too.
	"""
	await db.execute(
		"CREATE TABLE IF NOT EXISTS identities ("
		"`profile_id` BIGINT, `user_id` BIGINT, `aoe2_name` VARCHAR(191), "
		"`confidence` VARCHAR(191) NOT NULL, `first_seen_at` BIGINT NOT NULL, "
		# bound_at is carried here as well as in 009's ALTER so a FRESH install
		# creates the column rather than adding it three migrations later. 009
		# still runs and is still needed: it is what gives the column to the
		# databases this CREATE already ran against without it. Its UPDATE is a
		# no-op here (a table created empty has no rows to seed).
		"`last_seen_at` BIGINT NOT NULL, `bound_at` BIGINT NOT NULL DEFAULT '0', "
		"PRIMARY KEY(`profile_id`))"
	)


# The UNIQUE index identity_conflicts dedups on, named so both the CREATE TABLE
# below and 005's ALTER can refer to the same thing, and so 005 can ask
# information_schema whether it is already there.
_CONFLICT_CLAIM_INDEX = "uniq_identity_conflicts_claim"


async def _ensure_identity_conflicts_table(db):
	"""Minimal, idempotent CREATE TABLE for `identity_conflicts` —
	bot/identity.py's table of losing profile_id<->user_id claims, duplicated
	here for the same reason _ensure_identities_table is (see that function's
	and this module's docstrings for why importing bot.identity from here
	crashes the boot). 003_seed_identities is the only writer of this table
	from inside a migration; bot/identity.py's learn() is the other writer,
	at runtime, well after this table already exists.

	The shape is a surrogate `id` primary key plus a UNIQUE index on
	(profile_id, claimed_user_id, status) — see bot/identity.py's declaration
	for why the status column is part of the dedup key and what went wrong
	while it was not. A database created by THIS function therefore never needs
	005_identity_conflict_history; that migration exists for the databases
	created by the earlier version of it.

	Must stay in sync with bot/identity.py's `identity_conflicts` declaration
	by hand — test_the_conflicts_ddl_matches_the_ensure_table_declaration pins it.
	"""
	await db.execute(
		"CREATE TABLE IF NOT EXISTS identity_conflicts ("
		"`id` BIGINT NOT NULL AUTO_INCREMENT, "
		# NOT NULL on both id columns is deliberate and load-bearing now that
		# they are no longer the primary key (which forced it implicitly): a
		# NULL in either defeats the unique index, since MySQL treats NULLs
		# there as never equal. See bot/identity.py's declaration.
		"`profile_id` BIGINT NOT NULL, `claimed_user_id` BIGINT NOT NULL, "
		"`source` VARCHAR(191) NOT NULL, "
		"`noticed_at` BIGINT NOT NULL, `status` VARCHAR(191) NOT NULL DEFAULT 'open', "
		"PRIMARY KEY(`id`), "
		f"UNIQUE KEY `{_CONFLICT_CLAIM_INDEX}` (`profile_id`, `claimed_user_id`, `status`))"
	)


def _record_seed_conflicts(claimed, seed_rows, conflicts, now):
	""" Update `claimed` ({profile_id: user_id}) to mirror what the INSERT
	IGNORE that follows will actually persist (first writer of a profile_id
	sticks — see this migration's docstring), and append a losing claim to
	`conflicts` for every row whose profile_id is already claimed by a
	DIFFERENT user_id. That is the exact silent discard the identity_conflicts
	table exists to stop being silent: without this, a row here would be
	dropped by INSERT IGNORE with zero trace of the disagreement anywhere.

	Two kinds of row are deliberately NOT reported as conflicts:
	  - A row whose own confidence is `manual`: manual rows from
	    data/profile_resolved.csv are unconditionally reasserted after this
	    loop (see the reassertion pass below) regardless of whether INSERT
	    IGNORE blocks them here, so a manual row is never actually discarded
	    — reporting it as a "losing claim" here would be simply wrong, since
	    by the time anyone reads identity_conflicts it will, in fact, be the
	    stored owner.
	  - A row whose user_id agrees with what's already claimed: not a
	    disagreement, just the same fact observed from a second source.
	Rows with user_id=None make no claim at all and are skipped outright. """
	for row in seed_rows:
		pid, uid, source = row["profile_id"], row["user_id"], row["confidence"]
		if uid is None:
			continue
		if pid in claimed:
			if claimed[pid] != uid and source != _MANUAL_CONFIDENCE:
				conflicts.append(dict(
					profile_id=pid, claimed_user_id=uid, source=source, noticed_at=now, status="open"
				))
			continue
		claimed[pid] = uid


# Confidence tiers this migration writes. These are NAMES, not positions: the
# CSV's own `source` column is compared against _MANUAL_CONFIDENCE as a string,
# so what matters is the literal value, never where it sits in the lattice.
# They were briefly derived positionally (CONFIDENCE_ORDER[0]/[1]/[-1]) — that
# is wrong for a name lookup and silently so: adding a tier above `manual` or
# below `learned` would leave this seeding at whatever moved into the slot,
# with no error anywhere. The check below keeps the anti-desync property that
# deriving from the tuple was reaching for, by failing loudly at import (i.e.
# at boot, before any migration runs) if a tier this file names is renamed or
# removed from the lattice.
# This migration never writes `self`; a player's own link postdates seeding.
_SEED_CONFIDENCE = "seed"        # weakest tier
_LEARNED_CONFIDENCE = "learned"
_MANUAL_CONFIDENCE = "manual"    # strongest tier — a human correction

for _tier in (_SEED_CONFIDENCE, _LEARNED_CONFIDENCE, _MANUAL_CONFIDENCE):
	if _tier not in CONFIDENCE_ORDER:
		raise RuntimeError(
			f"core/migrations.py seeds `identities` at confidence {_tier!r}, which is no longer in "
			f"core.identity_seed.CONFIDENCE_ORDER ({CONFIDENCE_ORDER}). Renaming a tier requires "
			f"updating 003_seed_identities (and any already-seeded rows) to match."
		)


def _confidence_for_seed_row(source):
	""" The confidence a CSV-seeded row should get, given that row's own
	`source` value (as returned by parse_seed_csv — already trimmed, or
	None).

	data/profile_resolved.csv carries its own `source` column, and at least
	one row is tagged 'manual' — a deliberate human correction that must
	seed at manual confidence, the same tier bot/identity.py's learn() uses
	for an admin command, so a later automated learn() call can never
	silently overwrite it. Every other value (including the ordinary 'seed'
	rows and every player_profile_map.csv row, which has no `source` column
	at all and so always parses to source=None) lands at seed confidence,
	same as before this row-level distinction existed. Comparison is
	case-insensitive and trims whitespace, since this value comes from a
	hand-edited CSV. """
	if source is not None and source.strip().lower() == _MANUAL_CONFIDENCE:
		return _MANUAL_CONFIDENCE
	return _SEED_CONFIDENCE


@migration("003_seed_identities")
async def _m003(db):
	"""Seed `identities` from the three sources that answer "who is this
	person" today, in precedence order, highest first:

	  1. rs_profiles          (learned from real match ingests) -> 'learned'
	  2. data/profile_resolved.csv                               -> 'seed',
	                             except a row whose own `source` column says
	                             'manual' (case-insensitive, trimmed), which
	                             seeds at -> 'manual' instead
	  3. data/player_profile_map.csv                              -> 'seed'

	Each source's rows are written with INSERT IGNORE, so the first writer of
	a given profile_id wins and every lower-precedence source becomes a
	no-op for any profile_id a higher one already claimed. That, plus each
	source being independently re-derivable (a fresh SELECT / a fresh CSV
	parse), is what makes this migration safe to re-run from the top if a
	crash mid-body forces a retry on the next boot.

	Every source is independently guarded and never allowed to crash the
	boot on its own: rs_profiles may not exist yet on a genuinely fresh
	install (it is declared by bot/replay_stats, only imported AFTER
	migrations run — see this module's docstring), and either CSV may be
	absent from a given deploy image. A missing source (file/table not
	found) is logged and skipped so the others still get a chance to seed —
	that is the ordinary, expected case and never counts as a failure.

	A source that raises partway through (a corrupt CSV, a DB error reading
	rs_profiles) is different: that is genuinely lost data, not an absence.
	One or two such failures still leave the migration free to seed from
	whatever did work, same as a missing source. But if all three raise,
	nothing gets seeded at all — and without checking for that, this
	function would still return normally, run_all() would record
	003_seed_identities as done, and the migration would never be retried:
	`identities` stays empty forever with no further signal (see
	_assert_identities_seeded for the boot-time backstop covering the
	related backup-restore case). So this function tracks per-source
	failures and re-raises if every one of the three failed, leaving the
	migration unrecorded so the next boot retries it from scratch.

	A `manual` row is a deliberate human correction and must win regardless
	of *write order*, not just regardless of confidence — but rs_profiles is
	written before either CSV, and INSERT IGNORE only lets the first writer
	of a profile_id stick. So after all three passes above, every `manual`
	row from data/profile_resolved.csv is re-applied with an explicit UPDATE
	(see the loop below), overwriting whatever `learned`/`seed` row an
	earlier pass left behind for that profile_id. This is intentional: a
	human correction outranks even a `learned` mapping derived from real
	match data. The UPDATE is naturally idempotent (same profile_id, same
	values every re-run), so this migration stays safe to re-run from the
	top.

	Every row INSERT IGNORE silently drops because its profile_id was already
	claimed by a *different* user_id is a genuine source disagreement, not
	just a no-op — and until now it left zero trace anywhere. `claimed` below
	tracks {profile_id: user_id} exactly as INSERT IGNORE would persist it
	(first writer wins, never overwritten within this function), so each
	source's seed rows can be checked against it *before* being written; a
	disagreeing row is appended to `conflicts` and flushed to
	identity_conflicts once the loop finishes — see
	_record_seed_conflicts' docstring for why a `manual` row is exempt from
	being reported as a loser (it never actually loses; the reassertion pass
	below guarantees that regardless of what happened here).
	"""
	await _ensure_identities_table(db)
	await _ensure_identity_conflicts_table(db)
	now = int(time.time())

	# {profile_id: user_id} already committed to `identities` — from a
	# previous boot's partial seed, an earlier pass in this same call, or (in
	# principle) some other writer entirely. See the docstring paragraph
	# above for what this drives.
	claimed = {
		r["profile_id"]: r["user_id"]
		for r in (await db.select(["profile_id", "user_id"], "identities") or [])
	}
	conflicts = []

	# Names of seed sources whose attempt raised (not merely "missing" —
	# see the docstring above). If this ends up covering all three, _m003
	# re-raises at the bottom so run_all() does not record a no-op seed.
	failed_sources = []

	try:
		if await table_exists(db, "rs_profiles"):
			rows = await db.fetchall("SELECT profile_id, user_id, name, last_seen_at FROM rs_profiles")
			seed = [
				dict(
					profile_id=r["profile_id"],
					user_id=r["user_id"],
					aoe2_name=r["name"] or None,
					confidence=_LEARNED_CONFIDENCE,
					first_seen_at=r["last_seen_at"] or now,
					last_seen_at=r["last_seen_at"] or now,
				)
				for r in (rows or [])
			]
			_record_seed_conflicts(claimed, seed, conflicts, now)
			if seed:
				await db.insert_many("identities", seed, on_duplicate="ignore")
			log.info(f"migrations: 003_seed_identities: seeded {len(seed)} row(s) from rs_profiles")
		else:
			log.info("migrations: 003_seed_identities: rs_profiles does not exist yet, skipping")
	except Exception as e:
		log.error(f"migrations: 003_seed_identities: rs_profiles seed step failed, continuing: {e}")
		failed_sources.append("rs_profiles")

	# Rows from data/profile_resolved.csv whose own `source` column says
	# 'manual', captured here so the re-assertion pass below doesn't have to
	# re-open and re-parse the file a second time. Populated inside the loop,
	# only for the "resolved" kind — player_profile_map.csv rows never carry
	# a `source` value (parse_seed_csv always returns source=None for that
	# shape), so they can never be manual.
	manual_rows = []

	for relpath, kind in _SEED_CSVS:
		path = os.path.join(_ROOT, relpath)
		try:
			if not os.path.exists(path):
				log.info(f"migrations: 003_seed_identities: {relpath} not found, skipping")
				continue
			with open(path, encoding="utf-8") as f:
				text = f.read()
			parsed = parse_seed_csv(text, kind)
			if kind == "resolved":
				manual_rows = [r for r in parsed if _confidence_for_seed_row(r["source"]) == _MANUAL_CONFIDENCE]
			seed = [
				dict(
					profile_id=r["profile_id"],
					user_id=r["user_id"],
					aoe2_name=r["aoe2_name"],
					confidence=_confidence_for_seed_row(r["source"]),
					first_seen_at=now,
					last_seen_at=now,
				)
				for r in parsed
			]
			_record_seed_conflicts(claimed, seed, conflicts, now)
			if seed:
				await db.insert_many("identities", seed, on_duplicate="ignore")
			log.info(f"migrations: 003_seed_identities: seeded {len(seed)} row(s) from {relpath}")
		except Exception as e:
			log.error(f"migrations: 003_seed_identities: {relpath} seed step failed, continuing: {e}")
			failed_sources.append(relpath)

	# Flush every losing claim discovered above. Best-effort: a failure here
	# means a disagreement goes unrecorded, which is unfortunate but must
	# never block the actual seeding above (already committed by this
	# point) or the manual reassertion pass below. INSERT IGNORE on
	# identity_conflicts' (profile_id, claimed_user_id) primary key keeps a
	# re-run from duplicating a conflict already on record.
	if conflicts:
		try:
			await db.insert_many("identity_conflicts", conflicts, on_duplicate="ignore")
			log.info(f"migrations: 003_seed_identities: recorded {len(conflicts)} identity conflict(s)")
		except Exception as e:
			log.error(f"migrations: 003_seed_identities: recording identity conflicts failed, continuing: {e}")

	# Manual rows must win regardless of the write order above: rs_profiles
	# is seeded first (as 'learned'), and INSERT IGNORE means whoever writes
	# a profile_id first sticks — so a manual correction that lost that race
	# would otherwise stay stuck behind a lower-precedence row forever. An
	# explicit UPDATE, run unconditionally for every manual row, fixes that:
	# it always lands 'manual' regardless of what an earlier pass wrote.
	# Idempotent by construction (same profile_id, same values on every
	# re-run), and harmless even if the row somehow doesn't exist yet — an
	# UPDATE with no matching row just affects zero rows rather than erroring.
	try:
		for r in manual_rows:
			await db.update(
				"identities",
				dict(user_id=r["user_id"], aoe2_name=r["aoe2_name"], confidence=_MANUAL_CONFIDENCE, last_seen_at=now),
				keys=dict(profile_id=r["profile_id"]),
			)
		if manual_rows:
			log.info(
				f"migrations: 003_seed_identities: reasserted {len(manual_rows)} "
				"manual row(s) from data/profile_resolved.csv"
			)
	except Exception as e:
		log.error(f"migrations: 003_seed_identities: manual reassertion step failed, continuing: {e}")

	if len(failed_sources) == 3:
		raise RuntimeError(
			"migrations: 003_seed_identities: all three seed sources failed "
			f"({', '.join(failed_sources)}); identities was not seeded from any of "
			"them this boot. Not recording this migration as applied so the next "
			"boot retries it — see the errors logged above for what actually failed."
		)


# Tables 004_identity_v2 drops. Every ensure_table declaration for these was
# deleted by earlier tasks in identity v2 — which is the only thing that makes
# dropping them meaningful, since `import bot` would otherwise CREATE any name
# it still finds declared, empty, moments after the drop. That property is
# pinned by tests/test_migrations.py's
# test_m004_dropped_tables_have_no_surviving_declaration rather than left to a
# one-time grep.
_M004_DROPS = ("rs_profiles", "qc_profile_map", "identity_aliases")

# The two schema generations 004's backfill can find itself reading, newest
# first. 004 was written long before 007_raw_renames, but run_all decides what
# to run from the schema_migrations ledger ALONE — so the runbook's one repair
# (drop the ledger, reboot) re-runs 004 against a database 007 has already
# renamed, where `rs_matches` no longer exists. Pinning 004 to the old names
# made that re-run skip the backfill silently, log a line indistinguishable
# from the benign fresh-install case, and leave every historical pairing
# unrebuilt: bot/identity_solver.py then reads an empty match_replays and sees
# no replay evidence at all, forever, with nothing wrong anywhere in the logs.
#
# Editing an already-applied migration is safe here because every read below is
# existence-guarded and the write is INSERT IGNORE — re-running on a database
# that is already correct is a no-op either way.
#
# Table and column are resolved INDEPENDENTLY rather than as a matched pair:
# 007's body renames the tables and only then the columns, so a body that dies
# between the two leaves `replay_matches.aoe2_match_id`, a combination neither
# generation has on its own.
_M004_SOURCE_TABLES = ("replay_matches", "rs_matches")
_M004_MATCH_ID_COLUMNS = ("replay_match_id", "aoe2_match_id")


async def _m004_resolve_backfill_source(db):
	"""`(table, match-id column)` for 004's backfill, either half None if absent.

	Newest names win. The both-exist case — a rolled-back deploy whose
	`ensure_table` recreated the eight empty `rs_*` tables beside the populated
	`replay_*` ones — resolves to `replay_matches`, which is the table holding
	the data; the same boot's 007 then refuses to rename over it and crashes
	loudly (`rename_table`: both exist), which is the intended outcome. Reading
	the authoritative table on the way there is strictly better than reading the
	empty one.
	"""
	source = None
	for name in _M004_SOURCE_TABLES:
		if await table_exists(db, name):
			source = name
			break
	if source is None:
		return None, None
	for name in _M004_MATCH_ID_COLUMNS:
		if await column_exists(db, source, name):
			return source, name
	return source, None


def _m004_backfill_sql(source, match_id_col):
	"""The historical pairings backfill, as one join rather than 1107 round trips.

	LEFT (not inner) joins on purpose: a replay whose bot match is unknown, or
	whose channel was never enrolled in a community, must come back as a row
	with NULLs so it can be counted and logged, instead of silently not being in
	the result set at all.

	`source` and `match_id_col` are always values `_m004_resolve_backfill_source`
	picked out of the two module constants above — never anything a caller or a
	row supplies — which is what makes interpolating them into the SQL safe.
	"""
	return (
		f"SELECT r.bot_match_id AS match_id, r.{match_id_col} AS replay_match_id, "
		"r.parsed_at AS parsed_at, m.match_id AS found_match_id, "
		"m.reported_at AS reported_at, cc.community_id AS community_id "
		f"FROM {source} r "
		"LEFT JOIN matches m ON m.match_id = r.bot_match_id "
		"LEFT JOIN community_channels cc ON cc.channel_id = m.channel_id "
		"WHERE r.bot_match_id IS NOT NULL "
		f"ORDER BY r.{match_id_col}"
	)


async def _m004_repair_polluted_names(db):
	"""004 part (a): un-pollute `identities.aoe2_name`.

	`aoe2_name` is supposed to be what an account is called IN THE GAME. The
	old replay-ingest path preferred the player's Discord nick over the name
	in the replay, and 003_seed_identities then seeded from that
	already-polluted store, so a large share of the flagship's rows hold a
	Discord nick instead. data/profile_resolved.csv carries BOTH columns for
	the profiles it covers, which is what makes the repair decidable at all:
	a stored name equal to that row's `nick` is the pollution, and the row's
	`aoe2_name` is what it should have been.

	Three rules, all deliberate:
	  - Repair only when the stored value equals the row's nick exactly. A
	    stored value equal to the real name is already correct; a stored value
	    matching NEITHER is of unknown provenance (it may be a later, better
	    correction from a replay ingest or an admin) and is left alone.
	  - Compare in Python, not in SQL. MySQL's default collation is
	    case-insensitive, so `WHERE aoe2_name = 'ddk'` would also match a
	    stored 'DDK'; deciding here makes the rule exact and independent of
	    whatever collation the table happens to carry.
	  - Write ONE column. user_id and confidence are untouched, so a stale CSV
	    can never undo an admin's `manual` correction of who a profile is.

	The UPDATE names the old value in its WHERE clause as well as the
	profile_id, making it a compare-and-swap: idempotent on its own (the
	second run matches nothing), and unable to clobber a value some other
	writer changed between the read above and the write.

	Skips silently when the CSV is absent — a partner deployment ships no such
	file, and this repair is flagship-historical data only.
	"""
	path = os.path.join(_ROOT, _RESOLVED_CSV)
	if not os.path.exists(path):
		log.info(f"migrations: 004_identity_v2: {_RESOLVED_CSV} not found, skipping the name repair")
		return
	if not await table_exists(db, "identities"):
		log.info("migrations: 004_identity_v2: identities does not exist yet, skipping the name repair")
		return

	with open(path, encoding="utf-8") as f:
		repairs = parse_name_repairs(f.read())
	stored = {
		r["profile_id"]: r["aoe2_name"]
		for r in (await db.select(["profile_id", "aoe2_name"], "identities") or [])
	}

	repaired = 0
	for row in repairs:
		current = stored.get(row["profile_id"])
		if current is None or current != row["nick"] or row["aoe2_name"] == row["nick"]:
			continue
		await db.update(
			"identities",
			dict(aoe2_name=row["aoe2_name"]),
			keys=dict(profile_id=row["profile_id"], aoe2_name=current),
		)
		repaired += 1
	log.info(
		f"migrations: 004_identity_v2: repaired {repaired} identities.aoe2_name value(s) that held a "
		f"Discord nick, out of {len(repairs)} CSV row(s) carrying both names"
	)


async def _m004_backfill_match_replays(db):
	"""004 part (b): copy every historical replay<->match pairing out of
	`replay_matches.bot_match_id` into the `match_replays` link table.

	The source table is whatever generation of the schema this boot actually
	finds — `replay_matches` after 007_raw_renames, `rs_matches` before it — see
	_M004_SOURCE_TABLES for why 004 must handle both.

	This is load-bearing, and it must run before part (c) and long before
	stage 6 drops `replay_matches.bot_match_id`. bot/replay_stats/store.py only
	dual-writes match_replays going FORWARD (stage 1.6 on), so every pairing
	made before that lives in the single nullable column and nowhere else.
	Those pairings are the identity deduction solver's entire input and the
	join behind every historical per-community replay query.

	The community is resolved the same way bot/community.py's
	link_match_replay does it — matches.match_id -> matches.channel_id ->
	community_channels — but in SQL here, because a migration cannot import
	anything under bot.* (see this module's docstring).

	INSERT IGNORE, not REPLACE: link_match_replay is the authoritative forward
	writer, and a one-shot backfill must never stomp a row it produced. That
	also makes a re-run after a half-finished boot a pure no-op.

	`linked_at` takes the replay's own `parsed_at` when it has one — that is
	when this pairing was actually observed, and it keeps the backfilled rows
	ordered the same way the forward-written ones are. It falls back to the
	match's `reported_at` (the closest surviving stand-in for when the pairing
	happened) and finally to now.

	Skips, loudly, if any table involved is missing — and each skip says WHICH
	case it is, because they have completely different meanings. NEITHER source
	table existing is the fresh install (bot/replay_stats declares the table, and
	that is only imported after migrations run) and is logged at info naming both
	spellings. A source table that exists but carries neither match-id column is
	a schema nobody ships and is logged at error. Missing
	`matches`/`community_channels`/`match_replays` alongside a present source is
	not ordinary either — it means a backup predating stage 1.5/1.6 was restored
	— and is logged at error. All of them only skip rather than raise, because
	those tables can ONLY be created by a boot getting far enough to
	`import bot`; crashing here would make that state permanently unrecoverable.
	Recovery is the runbook's usual one: drop the ledger and reboot once the
	tables exist.
	"""
	source, match_id_col = await _m004_resolve_backfill_source(db)
	if source is None:
		# The genuine fresh install, and the ONLY case where there is nothing to
		# read at all. Both spellings are named so this line can never be
		# confused with "the table is there under the other name".
		log.info(
			"migrations: 004_identity_v2: no source table for the match_replays backfill "
			f"({' / '.join(_M004_SOURCE_TABLES)}) exists yet, skipping it"
		)
		return
	if match_id_col is None:
		log.error(
			f"migrations: 004_identity_v2: {source} exists but carries neither "
			f"{' nor '.join(_M004_MATCH_ID_COLUMNS)} — skipping the match_replays backfill. "
			"This is not a schema any release produces; inspect it by hand."
		)
		return
	if not await column_exists(db, source, "bot_match_id"):
		# Stage 6 drops this column. If the ledger is ever dropped after that,
		# 004 re-runs against a schema that no longer has the source data.
		log.info(
			f"migrations: 004_identity_v2: {source}.bot_match_id is gone (stage 6), "
			"skipping the match_replays backfill"
		)
		return
	missing = [t for t in ("matches", "community_channels", "match_replays") if not await table_exists(db, t)]
	if missing:
		log.error(
			f"migrations: 004_identity_v2: {source} holds pairings but {', '.join(missing)} "
			"do(es) not exist — skipping the match_replays backfill. Drop the `schema_migrations` "
			"table and reboot once those tables exist to run it."
		)
		return

	rows = await db.fetchall(_m004_backfill_sql(source, match_id_col)) or []
	now = int(time.time())
	wanted, no_match, unenrolled, collapsed = {}, 0, 0, 0
	for r in rows:
		if r["found_match_id"] is None:
			no_match += 1
			continue
		if r["community_id"] is None:
			unenrolled += 1
			continue
		key = (r["community_id"], r["match_id"])
		if key in wanted:
			# TWO source rows naming ONE bot match. match_replays' primary
			# key is (community_id, match_id), so only one of them can ever be
			# stored and INSERT IGNORE drops the rest without a word. Counted
			# and named here because the runbook otherwise attributes every gap
			# between "paired source rows" and "match_replays rows" to an
			# unknown match or an unenrolled channel — and this gap has a
			# completely different cause and a completely different fix.
			collapsed += 1
			log.warning(
				f"migrations: 004_identity_v2: bot match {r['match_id']} is claimed by more than one "
				f"{source} row; replay {r['replay_match_id']} cannot be linked because "
				f"{wanted[key]['replay_match_id']} already holds that pairing"
			)
			continue
		wanted[key] = dict(
			community_id=r["community_id"],
			match_id=r["match_id"],
			replay_match_id=r["replay_match_id"],
			linked_at=r["parsed_at"] or r["reported_at"] or now,
		)

	if wanted:
		await db.insert_many("match_replays", list(wanted.values()), on_duplicate="ignore")

	# Read the table back rather than reporting the size of the batch. insert_many
	# returns nothing, INSERT IGNORE is silent by design, and a one-shot backfill
	# whose only record is a log line has to say what LANDED — "backfilled 1107"
	# while 1105 rows exist is exactly the kind of claim that gets believed for a
	# year. `displaced` is the other honest outcome: a row link_match_replay
	# already wrote for this match with a DIFFERENT replay, which IGNORE
	# deliberately leaves alone (that writer is authoritative).
	stored = {
		(r["community_id"], r["match_id"]): r["replay_match_id"]
		for r in (await db.select(["community_id", "match_id", "replay_match_id"], "match_replays") or [])
	}
	verified = sum(1 for key, link in wanted.items() if stored.get(key) == link["replay_match_id"])
	displaced = len(wanted) - verified
	log.info(
		f"migrations: 004_identity_v2: {verified} of {len(wanted)} match_replays pairing(s) verified "
		f"present from {len(rows)} paired {source} row(s) (skipped {no_match} with no matches row, "
		f"{unenrolled} whose channel is not enrolled in a community, {collapsed} sharing a bot match id "
		f"with an already-taken pairing; {displaced} already linked to a different replay)"
	)


@migration("004_identity_v2")
async def _m004(db):
	"""Identity v2's data migration, in three parts that MUST stay in this
	order: repair the polluted names, backfill match_replays, then drop the
	retired tables.

	Ordering: the backfill has to happen while `replay_matches.bot_match_id` is
	still the only home of 1107 historical pairings, and the drops are the one
	irreversible thing here — so they go last, and are skipped entirely if an
	earlier part of the same boot failed. Nothing should take an irreversible
	action in a body that has already gone wrong once.

	Parts (a) and (b) are individually isolated and individually idempotent
	(see their docstrings). A part that raises is logged and does not stop the
	next one from getting its chance in the same boot, but the migration then
	re-raises at the bottom so run_all() does NOT record it in the ledger —
	otherwise a failed part would never be retried and its data would be lost
	silently, which is the one outcome worth failing a deploy over. Railway
	keeps the previous container serving while the new one crash-loops (see
	docs/runbooks/schema-migrations.md), so raising here shows up as a failed
	deploy rather than as a bot outage.
	"""
	failed = []
	for label, part in (
		("name repair", _m004_repair_polluted_names),
		("match_replays backfill", _m004_backfill_match_replays),
	):
		try:
			await part(db)
		except Exception as e:
			log.error(f"migrations: 004_identity_v2: {label} failed, continuing: {e}")
			failed.append(label)

	if failed:
		raise RuntimeError(
			f"migrations: 004_identity_v2: {', '.join(failed)} failed this boot; the retired tables "
			f"({', '.join(_M004_DROPS)}) were deliberately NOT dropped, and this migration is not "
			"being recorded as applied so the next boot retries it — see the errors logged above."
		)

	for t in _M004_DROPS:
		if await table_exists(db, t):
			await db.execute(f"DROP TABLE `{t}`")
			log.info(f"migrations: 004_identity_v2: dropped retired table {t}")


@migration("005_identity_conflict_history")
async def _m005(db):
	"""Widen identity_conflicts' key from (profile_id, claimed_user_id) to
	(profile_id, claimed_user_id, status), on a surrogate primary key.

	WHY, precisely. _record_conflict writes with INSERT IGNORE, so under the old
	primary key the FIRST status recorded for a pair was the only one that could
	ever exist. Trace the case this table exists for: B owns profile 111; an
	admin mis-relinks it to A, which records (111, B, `superseded`); B runs
	`/link 111`, is refused, and is told to ask an admin — and B's `open` row is
	SWALLOWED by that earlier row. `open_conflicts()` filters on status='open',
	so `/identity conflicts` shows nothing and `/identity status` reports zero.
	The correction loop dead-ends in silence at exactly the moment somebody is
	complaining. One row per (pair, status) fixes that while keeping the only
	property the narrow key was buying: a repeated IDENTICAL observation (the
	solver re-deriving the same losing claim on every trigger) still dedups.

	Existing rows are preserved — this is ALTER TABLE, never a rebuild. Nothing
	can violate the new index either: (profile_id, claimed_user_id) being unique
	makes (profile_id, claimed_user_id, status) unique a fortiori.

	ORDER MATTERS. The unique index is added BEFORE the primary key moves, and
	that is not cosmetic: between the two statements the table must never be
	without a uniqueness constraint covering the dedup, or an INSERT IGNORE
	landing in that window (a rolling deploy still serving from the previous
	container) would silently become a plain INSERT and duplicate rows.

	Each statement is guarded on its own information_schema signal rather than
	on this migration's ledger entry, per this module's docstring: a body that
	dies partway through is re-run from the top on the next boot, and MySQL has
	no `ADD COLUMN IF NOT EXISTS`.

	Those guards are check-then-act, so two containers booting simultaneously
	(a Railway rolling deploy) can both pass a check and the loser's ALTER then
	fails on a duplicate key name. MySQL offers no atomic alternative — there is
	no `ADD KEY IF NOT EXISTS`. The loser crashes, Railway restarts it, and the
	retry finds the work already done and does nothing; the previous container
	keeps serving throughout. This is the same tolerance the ledger's INSERT
	IGNORE and _m002's guarded DROP already rely on.

	A table this migration finds ABSENT is created in the new shape and needs
	nothing further — that is a fresh install, where 003 has already created it
	from _ensure_identity_conflicts_table's current DDL.
	"""
	if not await table_exists(db, "identity_conflicts"):
		await _ensure_identity_conflicts_table(db)
		log.info("migrations: 005_identity_conflict_history: created identity_conflicts in the new shape")
		return

	if not await index_exists(db, "identity_conflicts", _CONFLICT_CLAIM_INDEX):
		await db.execute(
			f"ALTER TABLE `identity_conflicts` ADD UNIQUE KEY `{_CONFLICT_CLAIM_INDEX}` "
			"(`profile_id`, `claimed_user_id`, `status`)")
		log.info(
			f"migrations: 005_identity_conflict_history: added the {_CONFLICT_CLAIM_INDEX} unique index")

	if not await column_exists(db, "identity_conflicts", "id"):
		# One ALTER, so the table is never momentarily without a primary key.
		# AUTO_INCREMENT is only legal on a column that IS a key, which is why
		# the PRIMARY KEY clause has to ride along in the same ADD COLUMN.
		clauses = []
		if await index_exists(db, "identity_conflicts", "PRIMARY"):
			clauses.append("DROP PRIMARY KEY")
		clauses.append("ADD COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST")
		await db.execute("ALTER TABLE `identity_conflicts` " + ", ".join(clauses))
		log.info(
			"migrations: 005_identity_conflict_history: moved identity_conflicts onto a surrogate "
			"primary key; the claim key is now (profile_id, claimed_user_id, status)")


# (table, index name, column) for 006_derived_indexes. The names are duplicated in
# bot/classifications/__init__.py's ensure_table declarations — which is the half of
# this that covers a FRESH install, where these tables do not exist yet when
# migrations run — and in utils/classifications/schema.py's raw DDL for the offline
# runner. All three must name the same index on the same column; keep them in step.
# Duplicated rather than imported for the reason in this module's docstring:
# core/migrations.py runs before `import bot` and importing anything under bot.*
# from here drives db.ensure_table's loop.run_until_complete on an already-running
# loop and crashes the boot.
_DERIVED_MATCH_INDEXES = (
	("cls_results", "cls_results_match", "aoe2_match_id"),
	("cls_result_metrics", "cls_result_metrics_match", "aoe2_match_id"),
)


@migration("006_derived_indexes")
async def _m006(db):
	"""Give cls_results and cls_result_metrics a per-match access path.

	Both tables carry only their PRIMARY KEY in the flagship database — the two
	secondary indexes utils/classifications/schema.py declares were never
	actually created there, because the tables predate that DDL and
	`CREATE TABLE IF NOT EXISTS` cannot retrofit a key. Both primary keys lead
	with `key` (`(key, aoe2_match_id, player_number)` and the same plus
	`metric`), and a leading-column mismatch means `WHERE aoe2_match_id=%s`
	cannot use them at all.

	bot/derived/backfill.py issues exactly that lookup, twice per match, and has
	~2,250 of them to do on the first deploy against 32k and 112k rows. Without
	this migration every one of them is a full table scan.

	Idempotent per statement, not merely per migration, per this module's
	docstring: the index_exists guard makes a re-run after a half-applied body a
	no-op, and MySQL has no `CREATE INDEX IF NOT EXISTS`.

	The guard is check-then-act, so two containers booting simultaneously (a
	Railway rolling deploy) can both pass it and the loser's CREATE INDEX then
	fails on a duplicate key name. That is the same tolerance 005 already relies
	on: the loser crashes, Railway restarts it, the retry finds the index present
	and does nothing, and the previous container serves throughout.

	A table that is absent is skipped, not created. That is the fresh-install
	case — bot/classifications declares these tables and is only imported AFTER
	migrations run, so at this point they genuinely do not exist yet — and the
	ensure_table declaration there carries the same `indexes=` entries, so the
	table is created WITH them moments later and needs nothing from here.
	"""
	for table, index, column in _DERIVED_MATCH_INDEXES:
		if not await table_exists(db, table):
			log.info(f"migrations: 006_derived_indexes: {table} does not exist yet, skipping "
			         f"(bot/classifications/__init__.py creates it with {index} already on it)")
			continue
		if await index_exists(db, table, index):
			continue
		await db.execute(f"CREATE INDEX `{index}` ON `{table}` (`{column}`)")
		log.info(f"migrations: 006_derived_indexes: created index {index} on {table}({column})")


# Stage 3b's raw renames. The replay tables were inherited under an `rs_`
# prefix that says nothing about what they hold; `replay_*` is the project's
# own vocabulary for the same data, and matches what the derived-global layer
# (game_stats, game_labels) and the link table (match_replays) already use.
#
# NOT renamed here, deliberately: rs_player_game_tags and rs_player_personas.
# Both are the legacy derived generation, both still have live consumers, and
# both are retired wholesale in stage 6 rather than carried forward under a new
# name — renaming them now would be churn on tables that are about to be
# deleted.
_STAGE3_RENAMES = [
	("rs_matches", "replay_matches"),
	("rs_player_games", "replay_players"),
	("rs_player_units", "replay_units"),
	("rs_player_techs", "replay_techs"),
	("rs_player_buildings", "replay_buildings"),
	("rs_player_events", "replay_events"),
	("rs_player_apm", "replay_apm"),
	("rs_ingest", "replay_ingest"),
]

# Tables whose `aoe2_match_id` column becomes `replay_match_id`: every table
# _STAGE3_RENAMES renames, plus `civ_picks`, which carries the very same value
# under the very same old name. After this the join column is spelled
# identically across raw, link and derived — match_replays, game_stats and
# game_labels were all written as `replay_match_id` from the start precisely so
# this stage would never have to touch them.
#
# NOT included, for the same reason the two tables above are not renamed:
# cls_results, cls_result_metrics, cls_match_ingest and rs_player_game_tags
# keep `aoe2_match_id`. They are stage 6's problem, and their offline
# producers (utils/classifications/, utils/replay_quiz/) keep their own
# schemas in step with them.
_STAGE3_MATCH_ID_TABLES = [new for _old, new in _STAGE3_RENAMES] + ["civ_picks"]


@migration("007_raw_renames")
async def _m007(db):
	"""Rename the raw replay tables and their match-id column, and drop
	rs_config.

	Same job 001_core_renames did for the core tables, and it follows that
	migration's shape exactly: rename_table/rename_column carry their own
	existence guards, so every statement here is individually idempotent and a
	body that dies partway through is safe to re-run from the top on the next
	boot (see this module's docstring).

	ORDER MATTERS in two directions. Within the body, tables are renamed before
	columns, because the column renames name the NEW table names — running them
	first would find nothing on a database that has not been renamed yet, and
	the migration would record itself having renamed no columns at all. Across
	the boot, this whole migration runs before `import bot` for the reason the
	module docstring gives: bot/replay_stats/__init__.py now declares
	`replay_matches` and friends, and ensure_table CREATEs any declared name it
	does not find — so renaming after the import would leave the populated
	rs_* tables stranded beside eight empty replay_* ones.

	`rs_config` is dropped, not renamed. It held exactly one row with exactly
	one boolean (`enabled`), toggled by an admin command — a deployment-wide
	on/off switch that has no business being a table, and which every read had
	to go to the database for. It is now the REPLAY_INGEST_ENABLED config var
	(core/config.py, config.example.cfg, start.py's generated template),
	defaulting to enabled to match the production row this drop removes. The
	drop is guarded, so re-running finds nothing to do; it is also the one
	irreversible statement here, which is why it goes last.
	"""
	for old, new in _STAGE3_RENAMES:
		await rename_table(db, old, new)
	for table in _STAGE3_MATCH_ID_TABLES:
		await rename_column(db, table, "aoe2_match_id", "replay_match_id")
	if await table_exists(db, "rs_config"):
		await db.execute("DROP TABLE `rs_config`")
		log.info("migrations: 007_raw_renames: dropped rs_config — replay ingest is now gated by "
		         "the REPLAY_INGEST_ENABLED config var")


# 008's ADD COLUMN, kept beside the backfill it precedes. The type and the
# NOT NULL must match bot/derived/__init__.py's `has_production` declaration
# exactly — duplicated by hand rather than imported, for the reason in this
# module's docstring — and no DEFAULT, for the reason given there: the sole
# writer supplies the column on every row and validates its whole key set, so a
# DEFAULT could only be reached by a writer that is already a bug, and it would
# turn that bug into a silently unranked player. MySQL fills the existing rows
# with the implicit default (0) as it adds the column; the UPDATE below then
# gives every one of them its real value in the same body.
# test_the_has_production_ddl_matches_the_ensure_table_declaration pins the pair.
_M008_ADD_COLUMN = "ALTER TABLE `game_stats` ADD COLUMN `has_production` TINYINT(1) NOT NULL"

# The backfill. `has_production` is true iff the parser measured this slot's
# production at all — the same predicate bot/derived/game_stats.py's
# compute_game_stats feeds to card_scoring.assign_medals, expressed in SQL
# because a migration may not import bot.* (see this module's docstring).
#
# Correlated per (replay_match_id, player_number), never per match: the flag is
# a fact about ONE slot, and dropping player_number from the correlation would
# make one measured player in a match mark every other slot in it measured too.
#
# MAX(CASE ...) rather than a bare comparison, so the subquery returns exactly
# one row for every driving row. replay_players' primary key is
# (replay_match_id, profile_id) and does NOT constrain player_number — the same
# property bot/derived/backfill.py's DISTINCT exists for — so two source rows
# CAN share a slot, and a plain scalar subquery would raise ER_SUBQUERY_NO_1_ROW
# on the whole 8885-row statement the first time it met one. Aggregating makes
# the result deterministic and total instead of dependent on row order.
#
# COALESCE(..., 0) covers a derived row whose source rows are gone: MAX over no
# rows is NULL, the column is NOT NULL, and "not measured" is the honest reading
# of a slot with no surviving raw row. Such a row is already orphaned and
# bot/derived/backfill.py's set comparison deletes it on its next pass.
#
# Idempotent by construction: it recomputes every row's value from the raw layer
# rather than mutating what is there, so a re-run after a body that died partway
# through (this module's docstring) converges on the same values, and MySQL
# reports zero rows changed the second time.
_M008_BACKFILL = (
	"UPDATE game_stats SET has_production = COALESCE(("
	"SELECT MAX(CASE WHEN COALESCE(rp.villagers, 0) + COALESCE(rp.military, 0) > 0 THEN 1 ELSE 0 END) "
	"FROM replay_players rp "
	"WHERE rp.replay_match_id = game_stats.replay_match_id "
	"AND rp.player_number = game_stats.player_number), 0)"
)


@migration("008_game_stats_has_production")
async def _m008(db):
	"""Store `has_production` on game_stats, and backfill it for every row
	already there.

	WHY THE COLUMN. compute_game_stats has always computed this flag — a player
	with no production is excluded from medal ranking entirely rather than
	ranked last, because showing them bare reads as "played badly" when the
	truth is "not measured" — but it computed it into the payload it hands
	assign_medals and then threw it away. From a stored row, "no medal because
	nobody measured me" and "no medal because I placed fourth" were
	indistinguishable, and player_rollups' medal_rates divides by the number of
	games the player was ELIGIBLE for a medal in. bot/derived/rollups.py used to
	infer that from indirect evidence (a medal on either axis, or a non-empty
	top_units) and silently missed the player who built villagers, no military,
	and placed outside the top three. This migration makes the denominator exact.

	WHY A MIGRATION AND NOT THE RECONCILIATION LOOP. bot/derived/backfill.py
	self-heals game_stats against the raw layer on every tick, and it will NOT
	heal this. Its pending predicate is a set comparison of
	(replay_match_id, player_number) triples between source and derived: adding
	a column changes neither set, so every match compares equal and the loop
	converges to zero work with 8885 rows holding whatever the ALTER put there.
	Teaching it about columns would mean giving it a second, column-level
	predicate — a much worse design than one migration — so this is deliberately
	not attempted there. A future reader wondering why the self-healing loop was
	bypassed: it cannot see this, by construction.

	WHY PURE SQL. This module runs before `import bot` (see the docstring at the
	top of this file), so it cannot call into bot/derived/ to recompute rows —
	db.ensure_table's sync wrapper would drive loop.run_until_complete on an
	already-running loop and crash the boot. The predicate is duplicated into
	SQL instead, which is safe precisely because it is trivial and total:
	`villagers + military > 0`, NULLs read as zero.

	IDEMPOTENT PER STATEMENT, not merely per migration (this module's
	docstring): the ADD COLUMN is guarded on information_schema because MySQL
	has no `ADD COLUMN IF NOT EXISTS`, and the UPDATE recomputes from the raw
	layer rather than mutating in place, so a body that dies between the two is
	safe to re-run from the top on the next boot. The guard is check-then-act,
	so two containers booting simultaneously (a Railway rolling deploy) can both
	pass it and the loser's ALTER fails on a duplicate column name — the same
	tolerance 005 and 006 already rely on: the loser crashes, Railway restarts
	it, the retry finds the column present and only re-runs the harmless UPDATE.

	The UPDATE runs even when the column was already there, deliberately. The
	one state where that matters is a rolled-back deploy: `import bot`'s
	ensure_table ADDs any declared column it does not find, so the column can
	exist holding 0 for every row without this migration ever having run. If
	the ADD's guard also short-circuited the backfill, that database would read
	"nobody has ever been measured" forever — every player's games_ranked 0 and
	every medal rate null — with a healthy-looking bot.

	A missing game_stats is the fresh install and needs nothing: bot/derived
	declares the table and is only imported AFTER migrations run, so the table
	is CREATEd moments later with this column already on it.

	A missing replay_players while game_stats exists RAISES, and that is a
	deliberate departure from 004's "log and skip" for a missing source table.
	004 skips because its data is still sitting in the column it reads and a
	later boot can still copy it; skipping HERE would let run_all record this
	migration as applied while every row kept the placeholder 0, and nothing
	afterwards would ever revisit it — the reconciliation loop cannot see it
	(above), so the wrong denominator would be permanent until somebody noticed
	that every medal rate in the product was null. The state is also not
	reachable in normal operation: both tables are created in the same
	`import bot` phase, and bot/derived/backfill.py queries replay_players every
	POLL_INTERVAL seconds, so a database missing it is already loudly broken.
	Not recording the migration means the next boot retries it; the runbook's
	repair applies as usual.
	"""
	if not await table_exists(db, "game_stats"):
		log.info("migrations: 008_game_stats_has_production: game_stats does not exist yet, skipping "
		         "(bot/derived/__init__.py creates it with has_production already on it)")
		return

	if not await column_exists(db, "game_stats", "has_production"):
		await db.execute(_M008_ADD_COLUMN)
		log.info("migrations: 008_game_stats_has_production: added game_stats.has_production")

	if not await table_exists(db, "replay_players"):
		raise RuntimeError(
			"migrations: 008_game_stats_has_production: game_stats exists but replay_players does not, "
			"so has_production cannot be backfilled and every row would keep the placeholder 0 — i.e. "
			"every player would look unmeasured and every medal rate would render as null. Not "
			"recording this migration as applied so the next boot retries it. This is not a schema any "
			"release produces (both tables are created in the same `import bot` phase); inspect the "
			"database by hand."
		)

	await db.execute(_M008_BACKFILL)
	log.info("migrations: 008_game_stats_has_production: backfilled game_stats.has_production from "
	         "replay_players")


# `identities.bound_at` — WHEN THIS PROFILE'S OWNER LAST CHANGED, as distinct
# from `last_seen_at` ("when was this profile last observed at all"). The two
# genuinely differ: learn() bumps last_seen_at on every replay ingest that sees
# a linked profile, on a name-only refresh, and even on a REFUSED claim, none of
# which move the binding. bot/derived/refresh.py's staleness comparison needs the
# narrow question — see that module's docstring for why an observation timestamp
# cannot answer it without recomputing every rollup on every ingest.
#
# DEFAULT '0' on both this ALTER and bot/identity.py's declaration, quoted
# exactly the way core/DBAdapters/mysql.py's _mysql_column renders a default, so
# the column a fresh install creates and the column this ALTER adds are
# byte-identical (test_the_bound_at_ddl_matches_the_ensure_table_declaration
# pins that). A default is required rather than optional: MySQL's strict mode
# rejects ADD COLUMN ... NOT NULL with no default on a table that already holds
# rows, and `identities` holds every binding in production.
_M009_ADD_COLUMN = "ALTER TABLE `identities` ADD COLUMN `bound_at` BIGINT NOT NULL DEFAULT '0'"

# Existing rows get last_seen_at, which is the tightest UPPER bound available on
# when their binding was last touched: every path that moves a binding also
# bumps last_seen_at, so the real bound_at is at or before it. Erring high is the
# safe direction — a bound_at that is too NEW makes a rollup look stale and get
# recomputed (wasted work, self-correcting on the next pass), while one that is
# too OLD makes a stale rollup look fresh and is never revisited.
#
# Guarded on `bound_at = 0` so a re-run after a body that died partway through
# (this module's docstring) cannot clobber real values the running bot has since
# written. 0 is unambiguous as "never set": every writer stamps int(time.time()).
_M009_BACKFILL = "UPDATE identities SET bound_at = last_seen_at WHERE bound_at = 0"


@migration("009_identities_bound_at")
async def _m009(db):
	"""Give `identities` a binding-mutation timestamp, and seed it for every row
	already there.

	WHY THE COLUMN. bot/derived/refresh.py decides what to recompute by comparing
	each stored derived row's computed_at against the newest INPUT that feeds it —
	the same stateless, converging discipline bot/derived/backfill.py uses for the
	derived-global layer, and for the same reasons (no marker table to leak, and a
	pass that resumes correctly after a restart mid-run). Game data answers that
	comparison through game_stats.computed_at. An IDENTITY change does not: a
	`/link` makes previously-unattributable game_stats rows attributable without
	touching a single one of them, so a comparison built only on game data would
	never notice, and the late-link-backfills-your-whole-history promise that
	identity v2 §5 is built on would silently not happen.

	WHY NOT last_seen_at. It is an OBSERVATION timestamp, not a mutation one.
	learn() bumps it on every replay ingest of an already-known profile, on a
	name-only refresh, and on a claim it REFUSED — so depending on it would both
	mark every player in every ingested match stale for no reason and, worse,
	couple this comparison's correctness to a column whose meaning belongs to a
	different module and could legitimately change. bound_at states the contract
	it is actually being asked for.

	WHY PURE SQL. This module runs before `import bot` (see the docstring at the
	top of this file), so it cannot import bot.identity to reuse its declaration —
	db.ensure_table's sync wrapper would drive loop.run_until_complete on an
	already-running loop and crash the boot. Same constraint 003 works under, and
	the same hand-kept duplication: _ensure_identities_table above also carries
	this column so a fresh install creates it rather than altering it in moments.

	IDEMPOTENT PER STATEMENT, not merely per migration (this module's docstring):
	the ADD COLUMN is guarded on information_schema because MySQL has no
	`ADD COLUMN IF NOT EXISTS`, and the UPDATE only touches rows still holding the
	placeholder 0. The guard is check-then-act, so two containers booting at once
	(a Railway rolling deploy) can both pass it and the loser's ALTER fails on a
	duplicate column name — the same tolerance 005/006/008 already rely on: the
	loser crashes, Railway restarts it, the retry finds the column present and
	re-runs only the harmless UPDATE.

	The UPDATE runs even when the column was already there, deliberately, for the
	same reason 008's does: `import bot`'s ensure_table ADDs any declared column it
	does not find, so the column can exist holding 0 for every row without this
	migration ever having run. Short-circuiting the backfill on the ADD's guard
	would leave that database reading "no binding has ever changed" — which is the
	direction that HIDES staleness rather than the one that over-reports it.

	A missing `identities` is not a state this migration invents a row for: 003
	creates and seeds the table and runs first, so the only way to get here
	without it is a database already loudly broken (_assert_identities_seeded
	fails the boot before the loop even starts). Skipping is correct and is not
	recorded — run_all only records after the body returns, and the body returning
	early here still records, so the guard below deliberately logs rather than
	silently passing.
	"""
	if not await table_exists(db, "identities"):
		log.info("migrations: 009_identities_bound_at: identities does not exist, skipping "
		         "(bot/identity.py creates it with bound_at already on it)")
		return

	if not await column_exists(db, "identities", "bound_at"):
		await db.execute(_M009_ADD_COLUMN)
		log.info("migrations: 009_identities_bound_at: added identities.bound_at")

	await db.execute(_M009_BACKFILL)
	log.info("migrations: 009_identities_bound_at: seeded identities.bound_at from last_seen_at")
