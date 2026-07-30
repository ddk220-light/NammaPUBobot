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
import csv
import io
import os
import time

from core.console import log

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
	declaration.

	Why duplicated instead of imported: `import bot.identity` would run that
	module's own (synchronous) `db.ensure_table()` calls as an unavoidable
	side effect of the import statement itself — Python always executes a
	module's top-level code exactly once, in full, the first time anything
	imports it, regardless of which names you actually want from it.
	`ensure_table()`'s sync wrapper internally does
	`self.loop.run_until_complete(...)` on the SAME loop object this
	migration is already running under (run_all() is driven by
	`loop.run_until_complete(migrations.run_all(db))` in PUBobot2.py) —
	asyncio forbids a loop from entering `run_until_complete` re-entrantly,
	so that import would raise "This event loop is already running" and
	crash the boot on the very first deploy after this migration ships.
	Importing bot.identity from here isn't fixable by only pulling in the
	pure functions either — the whole module body still runs once, ensure_table
	calls included.

	Keep this schema in sync with bot/identity.py's `identities` declaration
	by hand (their divergence would only matter for a boot that hits this
	table before `import bot`'s own ensure_table() call gets to self-heal
	it — see the note above the `identities` ensure_table call in
	bot/identity.py). `identity_aliases` needs no such duplicate: nothing
	seeds it before `import bot` runs.
	"""
	await db.execute(
		"CREATE TABLE IF NOT EXISTS identities ("
		"`profile_id` BIGINT, `user_id` BIGINT, `aoe2_name` VARCHAR(191), "
		"`confidence` VARCHAR(191) NOT NULL, `first_seen_at` BIGINT NOT NULL, "
		"`last_seen_at` BIGINT NOT NULL, PRIMARY KEY(`profile_id`))"
	)


def _legacy_seed_csv_to_int(value):
	if value is None:
		return None
	value = str(value).strip()
	if not value:
		return None
	try:
		return int(value)
	except ValueError:
		return None


def _parse_legacy_seed_csv(text):
	"""Parses data/profile_resolved.csv and data/player_profile_map.csv into
	{profile_id, user_id, aoe2_name} dicts. Deliberately mirrors
	bot.identity.parse_seed_csv's row rules byte-for-byte instead of calling
	it — see _ensure_identities_table's docstring for why this migration
	cannot import anything from bot/. tests/test_migrations.py's
	test_parse_legacy_seed_csv_matches_bot_identity_parse_seed_csv pins the
	two copies together so a change to one without the other fails CI
	instead of silently drifting.

	Both real CSVs carry `profile_id`, `user_id`, and `aoe2_name` columns by
	NAME (just in different order, with different extra columns alongside)
	— csv.DictReader keys off the header row, so one parse handles both
	shapes; nothing here needs to know which file it's reading.

	profile_id missing or not an int -> row skipped (unusable). user_id
	missing/empty -> a legitimate identity with an unknown Discord owner,
	kept with user_id=None. user_id present but not an int -> malformed,
	whole row skipped rather than guessed at.
	"""
	rows = []
	for r in csv.DictReader(io.StringIO(text)):
		profile_id = _legacy_seed_csv_to_int(r.get("profile_id"))
		if profile_id is None:
			continue

		user_id_raw = (r.get("user_id") or "").strip()
		if user_id_raw == "":
			user_id = None
		else:
			user_id = _legacy_seed_csv_to_int(user_id_raw)
			if user_id is None:
				continue  # present but malformed -> the whole row is unusable

		aoe2_name = (r.get("aoe2_name") or "").strip() or None
		rows.append(dict(profile_id=profile_id, user_id=user_id, aoe2_name=aoe2_name))
	return rows


@migration("003_seed_identities")
async def _m003(db):
	"""Seed `identities` from the three sources that answer "who is this
	person" today, in precedence order, highest first:

	  1. rs_profiles          (learned from real match ingests) -> 'learned'
	  2. data/profile_resolved.csv                               -> 'seed'
	  3. data/player_profile_map.csv                              -> 'seed'

	Each source's rows are written with INSERT IGNORE, so the first writer of
	a given profile_id wins and every lower-precedence source becomes a
	no-op for any profile_id a higher one already claimed. That, plus each
	source being independently re-derivable (a fresh SELECT / a fresh CSV
	parse), is what makes this migration safe to re-run from the top if a
	crash mid-body forces a retry on the next boot.

	Every source is independently guarded and never allowed to crash the
	boot: rs_profiles may not exist yet on a genuinely fresh install (it is
	declared by bot/replay_stats, only imported AFTER migrations run — see
	this module's docstring), and either CSV may be absent from a given
	deploy image. A missing/failed source is logged and skipped so the
	others still get a chance to seed.
	"""
	await _ensure_identities_table(db)
	now = int(time.time())

	try:
		if await table_exists(db, "rs_profiles"):
			rows = await db.fetchall("SELECT profile_id, user_id, name, last_seen_at FROM rs_profiles")
			seed = [
				dict(
					profile_id=r["profile_id"],
					user_id=r["user_id"],
					aoe2_name=r["name"] or None,
					confidence="learned",
					first_seen_at=r["last_seen_at"] or now,
					last_seen_at=r["last_seen_at"] or now,
				)
				for r in (rows or [])
			]
			if seed:
				await db.insert_many("identities", seed, on_duplicate="ignore")
			log.info(f"migrations: 003_seed_identities: seeded {len(seed)} row(s) from rs_profiles")
		else:
			log.info("migrations: 003_seed_identities: rs_profiles does not exist yet, skipping")
	except Exception as e:
		log.error(f"migrations: 003_seed_identities: rs_profiles seed step failed, continuing: {e}")

	for relpath in ("data/profile_resolved.csv", "data/player_profile_map.csv"):
		path = os.path.join(_ROOT, relpath)
		try:
			if not os.path.exists(path):
				log.info(f"migrations: 003_seed_identities: {relpath} not found, skipping")
				continue
			with open(path, encoding="utf-8") as f:
				text = f.read()
			parsed = _parse_legacy_seed_csv(text)
			seed = [
				dict(
					profile_id=r["profile_id"],
					user_id=r["user_id"],
					aoe2_name=r["aoe2_name"],
					confidence="seed",
					first_seen_at=now,
					last_seen_at=now,
				)
				for r in parsed
			]
			if seed:
				await db.insert_many("identities", seed, on_duplicate="ignore")
			log.info(f"migrations: 003_seed_identities: seeded {len(seed)} row(s) from {relpath}")
		except Exception as e:
			log.error(f"migrations: 003_seed_identities: {relpath} seed step failed, continuing: {e}")
