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
from core.identity_seed import CONFIDENCE_ORDER, parse_seed_csv

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


async def _assert_identities_seeded(db):
	"""Post-condition, checked on every boot after the migration loop: if
	rs_profiles holds rows (real match history has already been ingested)
	while `identities` is empty (or missing outright), 003_seed_identities'
	ledger entry does not reflect reality.

	003_seed_identities now re-raises when every one of its three seed
	sources genuinely fails (see _m003), so a failed *first* seed no longer
	gets recorded as done. But the same empty-forever end state is also
	reachable via the rollback runbook: restoring a database backup taken
	after 003_seed_identities was recorded in schema_migrations but before
	(or without) `identities` itself restores the ledger row without
	restoring the table's data — and unlike _assert_stage1_renames_landed's
	renames, no old-named table is left behind for that check to catch,
	because `identities` was never renamed from anything. The loop above
	sees 003_seed_identities already applied and skips it; `import bot`'s
	ensure_table() then either finds the empty restored table or CREATEs it
	fresh, either way empty. From there the failure is silent: every civ
	stat lookup routes through profiles_for_users(), which just returns {}
	for an empty table, and bot/civ_matcher.py's _find_and_record() treats
	too few mapped players as "nothing to record here", not an error — no
	exception, no retry, no log line, and /health stays 200.

	Crashing here is the intended behaviour, for the same reason as
	_assert_stage1_renames_landed: a loud crash beats a silently empty bot.

	Safe on a genuinely fresh install, including one that has since booted
	at least once but ingested nothing: rs_profiles is declared by
	bot/replay_stats, only imported (and hence only CREATEd) after `import
	bot` — well after run_all() returns — so on a brand-new install this
	function's first table_exists() check is False and it is a no-op. Once
	rs_profiles does exist, it legitimately has zero rows until the first
	replay is ingested; identities being empty alongside an equally-empty
	rs_profiles is exactly as unremarkable, so both must be checked, not
	just one.
	"""
	if not await table_exists(db, "rs_profiles"):
		return
	rs_row = await db.fetchone("SELECT 1 AS x FROM rs_profiles LIMIT 1")
	if rs_row is None:
		return
	if not await table_exists(db, "identities"):
		raise RuntimeError(
			"migrations: identities table is missing while rs_profiles holds rows — "
			"003_seed_identities is recorded as applied in schema_migrations but "
			"`identities` itself does not exist. This almost always means a database "
			"backup was restored that kept schema_migrations but predates the "
			"identities table. Fix: drop the `schema_migrations` table and reboot so "
			"003_seed_identities re-runs from scratch."
		)
	id_row = await db.fetchone("SELECT 1 AS x FROM identities LIMIT 1")
	if id_row is not None:
		return
	raise RuntimeError(
		"migrations: identities table is empty while rs_profiles holds rows — "
		"003_seed_identities is recorded as applied in schema_migrations but never "
		"actually seeded anything, most likely because a database backup was "
		"restored that kept schema_migrations but not identities' data. Fix: drop "
		"the `schema_migrations` table and reboot so 003_seed_identities re-runs "
		"from scratch."
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
	hand too. `identity_aliases` needs no such duplicate CREATE TABLE:
	nothing seeds it before `import bot` runs.
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


# Confidence tiers this migration writes, taken from core.identity_seed's
# CONFIDENCE_ORDER (rather than the literal strings) so a rename there can't
# silently desync from here.
_SEED_CONFIDENCE, _LEARNED_CONFIDENCE, _MANUAL_CONFIDENCE = CONFIDENCE_ORDER


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

	for relpath, kind in (
		("data/profile_resolved.csv", "resolved"),
		("data/player_profile_map.csv", "profile_map"),
	):
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
