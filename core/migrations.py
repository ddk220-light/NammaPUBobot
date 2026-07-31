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
	if not await table_exists(db, table):
		return
	if await column_exists(db, table, old) and not await column_exists(db, table, new):
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
	await _assert_stage1_renames_landed(db)
	await _assert_identities_seeded(db)


async def _assert_stage1_renames_landed(db):
	"""Post-condition, checked on every boot regardless of what the loop above
	did: none of the _STAGE1_RENAMES *source* tables may still exist.

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
	"""
	offenders = [old for old, _new in _STAGE1_RENAMES if await table_exists(db, old)]
	if offenders:
		raise RuntimeError(
			"migrations: schema/ledger disagreement — these pre-rename tables still "
			f"exist even though the ledger says the rename already ran: {', '.join(offenders)}. "
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
	table's data — and unlike _assert_stage1_renames_landed's renames, no
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
	_assert_stage1_renames_landed: a loud crash beats a silently empty bot.

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
		"`last_seen_at` BIGINT NOT NULL, PRIMARY KEY(`profile_id`))"
	)


async def _ensure_identity_conflicts_table(db):
	"""Minimal, idempotent CREATE TABLE for `identity_conflicts` —
	bot/identity.py's table of losing profile_id<->user_id claims, duplicated
	here for the same reason _ensure_identities_table is (see that function's
	and this module's docstrings for why importing bot.identity from here
	crashes the boot). 003_seed_identities is the only writer of this table
	from inside a migration; bot/identity.py's learn() is the other writer,
	at runtime, well after this table already exists.

	Must stay in sync with bot/identity.py's `identity_conflicts` declaration
	by hand.
	"""
	await db.execute(
		"CREATE TABLE IF NOT EXISTS identity_conflicts ("
		"`profile_id` BIGINT, `claimed_user_id` BIGINT, `source` VARCHAR(191) NOT NULL, "
		"`noticed_at` BIGINT NOT NULL, `status` VARCHAR(191) NOT NULL DEFAULT 'open', "
		"PRIMARY KEY(`profile_id`, `claimed_user_id`))"
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

# The historical pairings backfill, as one join rather than 1107 round trips.
# LEFT (not inner) joins on purpose: a replay whose bot match is unknown, or
# whose channel was never enrolled in a community, must come back as a row with
# NULLs so it can be counted and logged, instead of silently not being in the
# result set at all.
_M004_BACKFILL_SQL = (
	"SELECT r.bot_match_id AS match_id, r.aoe2_match_id AS replay_match_id, "
	"r.parsed_at AS parsed_at, m.match_id AS found_match_id, "
	"m.reported_at AS reported_at, cc.community_id AS community_id "
	"FROM rs_matches r "
	"LEFT JOIN matches m ON m.match_id = r.bot_match_id "
	"LEFT JOIN community_channels cc ON cc.channel_id = m.channel_id "
	"WHERE r.bot_match_id IS NOT NULL "
	"ORDER BY r.aoe2_match_id"
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
	`rs_matches.bot_match_id` into the `match_replays` link table.

	This is load-bearing, and it must run before part (c) and long before
	stage 6 drops `rs_matches.bot_match_id`. bot/replay_stats/store.py only
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

	Skips, loudly, if any table involved is missing. On a fresh install
	rs_matches does not exist at migration time at all (bot/replay_stats
	declares it, and that is only imported after migrations run), which is the
	ordinary case and logged at info. Missing `matches`/`community_channels`/
	`match_replays` alongside a present rs_matches is not ordinary — it means a
	backup predating stage 1.5/1.6 was restored — and is logged at error. It
	still only skips rather than raising, because those tables can ONLY be
	created by a boot getting far enough to `import bot`; crashing here would
	make that state permanently unrecoverable. Recovery is the runbook's usual
	one: drop the ledger and reboot once the tables exist.
	"""
	if not await table_exists(db, "rs_matches"):
		log.info("migrations: 004_identity_v2: rs_matches does not exist yet, skipping the match_replays backfill")
		return
	if not await column_exists(db, "rs_matches", "bot_match_id"):
		# Stage 6 drops this column. If the ledger is ever dropped after that,
		# 004 re-runs against a schema that no longer has the source data.
		log.info(
			"migrations: 004_identity_v2: rs_matches.bot_match_id is gone (stage 6), "
			"skipping the match_replays backfill"
		)
		return
	missing = [t for t in ("matches", "community_channels", "match_replays") if not await table_exists(db, t)]
	if missing:
		log.error(
			f"migrations: 004_identity_v2: rs_matches holds pairings but {', '.join(missing)} "
			"do(es) not exist — skipping the match_replays backfill. Drop the `schema_migrations` "
			"table and reboot once those tables exist to run it."
		)
		return

	rows = await db.fetchall(_M004_BACKFILL_SQL) or []
	now = int(time.time())
	links, no_match, unenrolled = [], 0, 0
	for r in rows:
		if r["found_match_id"] is None:
			no_match += 1
			continue
		if r["community_id"] is None:
			unenrolled += 1
			continue
		links.append(dict(
			community_id=r["community_id"],
			match_id=r["match_id"],
			replay_match_id=r["replay_match_id"],
			linked_at=r["parsed_at"] or r["reported_at"] or now,
		))

	if links:
		await db.insert_many("match_replays", links, on_duplicate="ignore")
	log.info(
		f"migrations: 004_identity_v2: backfilled {len(links)} match_replays row(s) from "
		f"{len(rows)} paired rs_matches row(s) (skipped {no_match} with no matches row, "
		f"{unenrolled} whose channel is not enrolled in a community)"
	)


@migration("004_identity_v2")
async def _m004(db):
	"""Identity v2's data migration, in three parts that MUST stay in this
	order: repair the polluted names, backfill match_replays, then drop the
	retired tables.

	Ordering: the backfill has to happen while `rs_matches.bot_match_id` is
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
