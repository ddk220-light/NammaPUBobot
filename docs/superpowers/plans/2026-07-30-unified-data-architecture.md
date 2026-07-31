# Unified Data Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the data layer into core / raw / links / derived with a
first-class community (Discord server) entity, plain-English names, one
dedicated writer per table, per-community retention, and every laptop pipeline
replaced by a bot job — per `docs/superpowers/specs/2026-07-30-unified-data-layer-design.md` (v4, approved).

**Architecture:** Six stages, one deploy each, strictly ordered. A startup
migration runner (new) executes renames/creates/seeds in the only safe slot:
after `database.db.connect()` and **before** `import bot`, because every bot
package auto-CREATEs its declared tables at import and the adapter cannot
rename. Old write paths run in parallel with new ones until the consuming
stage cuts over, then die in stage 6.

**Tech Stack:** Python 3.11, nextcord, aiomysql/MySQL (Railway), pytest 8.3.3.

## Plan maintenance rule (read first)

Stage 1 is fully elaborated (TDD steps + complete code) and executes against
today's tree. Stages 2–6 are **binding contracts** — schemas, names,
signatures, file lists, retirements are fixed here — but their step-level code
is written against files earlier stages will have changed. **The first task of
each of stages 2–6 is: re-read the design doc + this plan + `git log` since the
prior stage, then elaborate that stage's tasks into full TDD steps in this file
(same rigor as stage 1) before implementing.** Do not improvise schema or
naming changes during elaboration; those require the design doc to change
first.

## Global Constraints

- **Indentation:** `bot/`, `core/`, `tests/` use TABS; `utils/` uses 4 spaces. Never mix within a file.
- **Tests:** NO `pytest-asyncio` — an `async def test_` silently SKIPS and false-passes. Drive async code from a sync `def test_` via `asyncio.run(...)`.
- **Patching:** `core/` is a namespace package — use object-form `monkeypatch.setattr(mod, "attr", ...)`, and always patch the **consuming** module's `db`, never `core.database.db`.
- **CI installs only pytest** — no nextcord/aiomysql/aiohttp. `tests/conftest.py` stubs them and makes `ensure_table` a no-op. Any new module that must import under CI keeps heavy imports lazy (inside functions), matching `bot/post_game.py`.
- **Local runs:** `python3.11 -m pytest tests/ -q` (system `python3` is 3.14 with no pytest). Scripts needing aiomysql: `./.venv-db/bin/python`. NEVER pipe pytest into `tail`/`head` in a command that gates a commit — the pipe masks the exit code.
- **Lint:** `ruff check .` must pass (config `ruff.toml`, line length 120, tabs).
- **Prod DB from the local machine is SELECT-only** (standing grant). All writes — including every migration in this plan — execute inside the deployed bot on Railway via the startup runner. Never run a migration from a laptop.
- **Deploys:** per stage: `bash scripts/backup_db.sh` → merge → `export PATH="/opt/homebrew/bin:$PATH" && railway up --ci` (watch for a skipped build) → verify: `railway ssh "sha256sum <changed files>"` matches local, `/health` returns 200 with `db_connected: true`, `railway logs -n 40` shows queue channels init and NO `migrations:` error lines.
- **Migration slot:** `PUBobot2.py` between `database.db.connect()` (line 48) and `import bot` (line 51). Nothing else may run DDL.
- **Commits:** one per task, message style `feat(data): ...` / `refactor(naming): ...`, ending with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **New config vars** must be added to `config.example.cfg` AND `start.py`'s template in the same task.
- Legacy interactive migration scripts `update_db.py` (root) and `utils/update_db.py` are superseded by the runner and get deleted in Stage 1.

## Stage map

| stage | delivers | deploy gate |
| --- | --- | --- |
| 1 | migration runner, data registry, core+feature renames, `communities`/`community_channels`/`match_replays`, easy retirements, `on_dublicate` fix | bot boots, queues/ratings/report all work on renamed tables |
| 2 | `identities` + `identity_aliases`, CSV seeds, all identity readers cut over | civ matching + lobby linking work off the DB, CSVs unused at runtime *(gate met only partially — closed by 2.5)* |
| 2.5 | identity v2 (`2026-07-30-identity-v2-design.md`): player `/link` with API validation, atomic admin relink + `/identity unlink` + `/identity status`, pairing/deduction solver, refresh-time attribution, `aoe2_name` repair, drop `rs_profiles`/`qc_profile_map`/`identity_aliases`, delete every runtime CSV read, audit fixes | `/link` works end-to-end against prod API; solver binds a synthetic 2-game fixture; no runtime CSV reads remain; audit ledger items closed |
| 3 | `game_stats` + `game_labels` written at ingest; `rs_*` → `replay_*` renames; cards read stored medals | next parsed replay produces rows in both; cards render identically |
| 4 | `player_rollups`, `metric_boards`, `civ_stats`, refresh job, retention sweeper | rollup rows appear after a match; sweeper dry-run logs correct candidates |
| 5 | consumers cut over: scouting report → quiz player bank → cards/insights → web repoint; second parser + SQLite deleted | each consumer serves from derived only |
| 6 | drop `cls_*`, `rs_player_game_tags`, `rs_player_personas`, `rs_profiles`, `qc_profile_map`, dead modules, CSVs; final naming guard | registry test green; no old name anywhere |

---

# Stage 1 — Runner, registry, community, renames, retirements

### Task 1.1: Migration runner

**Files:**
- Create: `core/migrations.py`
- Modify: `PUBobot2.py:48-51`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `async run_all(db)`, `migration(name)` decorator appending to
  `MIGRATIONS`, helpers `async table_exists(db, name)`,
  `async column_exists(db, table, column)`, `async rename_table(db, old, new)`,
  `async rename_column(db, table, old, new)`. Ledger table `schema_migrations`
  (name PK, applied_at).
- Consumes: the adapter's `db.execute/fetchone/fetchall` only.

- [ ] **Step 1: Write the failing tests** (`tests/test_migrations.py`, TABS)

```python
"""The startup migration runner.

Pure-logic tests against a fake adapter: the runner must apply each migration
exactly once, record it in the ledger, and make renames idempotent via
existence guards. No MySQL involved.
"""
import asyncio

import core.migrations as mig


class FakeDb:
	def __init__(self, tables=(), applied=()):
		self.tables = set(tables)
		self.applied = list(applied)
		self.executed = []

	async def execute(self, sql, args=None):
		self.executed.append(sql)
		if sql.startswith("RENAME TABLE"):
			# `RENAME TABLE `old` TO `new``
			parts = sql.split("`")
			self.tables.discard(parts[1])
			self.tables.add(parts[3])
		if sql.startswith("INSERT INTO schema_migrations"):
			self.applied.append(args[0])

	async def fetchone(self, sql, args=None):
		if "information_schema.TABLES" in sql:
			return {"x": 1} if args[0] in self.tables else None
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
	db = FakeDb(tables={"qc_matches"})
	asyncio.run(mig.rename_table(db, "qc_matches", "matches"))
	assert "matches" in db.tables and "qc_matches" not in db.tables


def test_rename_table_skips_when_only_new_exists():
	db = FakeDb(tables={"matches"})
	asyncio.run(mig.rename_table(db, "qc_matches", "matches"))
	assert not any(s.startswith("RENAME") for s in db.executed)


def test_rename_table_raises_when_both_exist():
	db = FakeDb(tables={"qc_matches", "matches"})
	try:
		asyncio.run(mig.rename_table(db, "qc_matches", "matches"))
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
```

- [ ] **Step 2: Run** `python3.11 -m pytest tests/test_migrations.py -v` — expected: FAIL, `ModuleNotFoundError: core.migrations`.

- [ ] **Step 3: Implement** `core/migrations.py` (TABS):

```python
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
"""
import time

from core.console import log

LEDGER = "schema_migrations"

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
	for name, fn in MIGRATIONS:
		if name in done:
			continue
		log.info(f"migrations: applying {name}")
		await fn(db)
		await db.execute(
			f"INSERT INTO {LEDGER} (name, applied_at) VALUES (%s, %s)",
			[name, int(time.time())])
	log.info(f"migrations: up to date ({len(MIGRATIONS)} known, {len(MIGRATIONS) - len([n for n, _ in MIGRATIONS if n in done])} applied this boot)")
```

- [ ] **Step 4: Wire the slot** in `PUBobot2.py` — replace:

```python
loop.run_until_complete(database.db.connect())

# Load bot
import bot
```

with:

```python
loop.run_until_complete(database.db.connect())

# Schema migrations MUST run before `import bot`: bot packages auto-CREATE
# their declared tables at import, and the adapter cannot rename — see
# core/migrations.py for why this ordering is load-bearing.
from core import migrations
loop.run_until_complete(migrations.run_all(database.db))

# Load bot
import bot
```

- [ ] **Step 5: Run** `python3.11 -m pytest tests/test_migrations.py -v` — expected: all PASS. Then the full suite: `python3.11 -m pytest tests/ -q` — expected: no regressions.

- [ ] **Step 6: Commit** — `feat(data): startup migration runner with idempotent rename guards`

### Task 1.2: Data registry

**Files:**
- Create: `core/data_registry.py`
- Test: `tests/test_data_registry.py`

**Interfaces:**
- Produces: `REGISTRY: dict[str, dict]` with keys `layer`
  (`core|raw|link|derived|ops`), `tenancy` (`global|community|channel`),
  `writers` (**tuple** of module paths — empty tuple if nothing writes it),
  `retention` (`forever|sweepable`). Constant `ALL_TABLES = frozenset(REGISTRY)`.

> **CORRECTION (applied during execution).** The Step-3 REGISTRY literal below
> was hand-audited and got the writer wrong for **9 of 43 tables** — it assumed
> a single writer where several exist (`qc_matches` is written by
> `bot/stats/stats.py` AND `bot/elo_sync.py`; `qc_players` by those two plus
> `bot/events.py`; likewise `qc_player_matches`, `qc_rating_history`,
> `qc_match_civs`, `qc_lobbies`, `rs_ingest`, `cls_results`,
> `cls_result_metrics`), and gave `bot_player_commentary` a bogus `"offline"`
> sentinel. Hence `writers` is a tuple, not a string, and records what writes
> each table **today** — the one-dedicated-writer rule of design §4 is the
> target that later stages converge on, not a description of the present.
> **Do not re-derive this list by hand; grep for `db.insert(`/`insert_many(`/
> `update(`/`delete(` and raw INSERT/UPDATE/DELETE/REPLACE across `bot/` and
> `core/` only.** The authoritative version is the committed
> `core/data_registry.py`.
- The test is the enforcement: every `tname="..."` / `FactoryTable(name=...)`
  declaration in `bot/` + `core/` must be registered, and vice versa. This test
  is what keeps §3/§7 of the design doc true through every later stage — each
  stage updates REGISTRY in the same commit as its schema change.

- [ ] **Step 1: Write the failing test** (`tests/test_data_registry.py`, TABS)

```python
"""The data registry is the single source of truth for table contracts.

Scans every ensure_table/FactoryTable declaration under bot/ and core/ and
asserts exact two-way agreement with core.data_registry.REGISTRY. A table
added without a registry entry (or an entry whose table was dropped) fails CI.
"""
import os
import re

from core.data_registry import REGISTRY

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DECL = re.compile(r"""(?:tname|name)\s*=\s*['"]([A-Za-z0-9_]+)['"]""")
_SCAN_HINTS = ("ensure_table", "FactoryTable")


def _declared_tables():
	found = set()
	for base in ("bot", "core"):
		for dirpath, _dirs, files in os.walk(os.path.join(_ROOT, base)):
			if "__pycache__" in dirpath:
				continue
			for f in files:
				if not f.endswith(".py"):
					continue
				src = open(os.path.join(dirpath, f), encoding="utf-8").read()
				if not any(h in src for h in _SCAN_HINTS):
					continue
				# Only lines inside declaration blocks matter; tname= is unique
				# to them, and FactoryTable(name=...) is caught by the same rx.
				for line in src.splitlines():
					if "tname=" in line or "FactoryTable(name" in line:
						m = _DECL.search(line)
						if m and m.group(1) not in ("None",):
							found.add(m.group(1))
	return found


def test_every_declared_table_is_registered_and_vice_versa():
	declared = _declared_tables()
	declared.discard("schema_migrations")  # runner-owned, created raw
	registered = set(REGISTRY)
	assert declared - registered == set(), f"declared but unregistered: {sorted(declared - registered)}"
	assert registered - declared == set(), f"registered but undeclared: {sorted(registered - declared)}"


def test_registry_entries_are_complete():
	for name, meta in REGISTRY.items():
		assert meta.get("layer") in ("core", "raw", "link", "derived", "ops"), name
		assert meta.get("tenancy") in ("global", "community", "channel"), name
		assert meta.get("writer"), name
		assert meta.get("retention") in ("forever", "sweepable"), name
```

- [ ] **Step 2: Run it** — expected: FAIL (`core.data_registry` missing).

- [ ] **Step 3: Implement** `core/data_registry.py` (TABS). Initial content =
  the **pre-rename** world (this task lands before Task 1.4's renames; 1.4
  updates the keys in the same commit as the declarations move). Every current
  declaration from the inventory:

```python
# -*- coding: utf-8 -*-
"""Single source of truth for every table's contract: layer (core/raw/link/
derived/ops), tenancy, sole writer, and retention class. Names say WHAT a
table is; this registry says HOW it is treated. tests/test_data_registry.py
enforces two-way agreement with the ensure_table declarations."""

REGISTRY = {
	# core — irreplaceable
	"qc_matches": dict(layer="core", tenancy="channel", writer="bot/stats/stats.py", retention="forever"),
	"qc_player_matches": dict(layer="core", tenancy="channel", writer="bot/stats/stats.py", retention="forever"),
	"qc_players": dict(layer="core", tenancy="channel", writer="bot/stats/stats.py", retention="forever"),
	"qc_rating_history": dict(layer="core", tenancy="channel", writer="bot/stats/rating.py", retention="forever"),
	"qc_match_id_counter": dict(layer="core", tenancy="global", writer="bot/stats/stats.py", retention="forever"),
	"qc_configs": dict(layer="core", tenancy="channel", writer="core/cfg_factory.py", retention="forever"),
	"pq_configs": dict(layer="core", tenancy="channel", writer="core/cfg_factory.py", retention="forever"),
	"qc_saved_state": dict(layer="core", tenancy="global", writer="bot/main.py", retention="forever"),
	"players": dict(layer="core", tenancy="global", writer="bot/commands/misc.py", retention="forever"),
	"noadds": dict(layer="core", tenancy="channel", writer="bot/stats/noadds.py", retention="forever"),
	"qc_phrases": dict(layer="core", tenancy="channel", writer="bot/stats/noadds.py", retention="forever"),
	"qc_douche": dict(layer="core", tenancy="channel", writer="bot/douche.py", retention="forever"),
	"disabled_guilds": dict(layer="core", tenancy="global", writer="bot/stats/stats.py", retention="forever"),
	# feature state (core contract)
	"qc_quiz_posts": dict(layer="core", tenancy="channel", writer="bot/quiz/store.py", retention="forever"),
	"qc_quiz_answers": dict(layer="core", tenancy="channel", writer="bot/quiz/store.py", retention="forever"),
	"qc_quiz_config": dict(layer="core", tenancy="channel", writer="bot/quiz/store.py", retention="forever"),
	"qc_prediction_posts": dict(layer="core", tenancy="channel", writer="bot/predictions/store.py", retention="forever"),
	"qc_prediction_votes": dict(layer="core", tenancy="channel", writer="bot/predictions/store.py", retention="forever"),
	# raw — append-only observations
	"qc_match_civs": dict(layer="raw", tenancy="community", writer="bot/civ_matcher.py", retention="forever"),
	"qc_civ_reconcile": dict(layer="ops", tenancy="community", writer="bot/civ_reconcile.py", retention="forever"),
	"qc_lobbies": dict(layer="raw", tenancy="community", writer="bot/lobby/jobs.py", retention="forever"),
	"qc_profile_map": dict(layer="raw", tenancy="global", writer="bot/lobby/profile_map.py", retention="forever"),
	"rs_config": dict(layer="ops", tenancy="global", writer="bot/replay_stats/store.py", retention="forever"),
	"rs_matches": dict(layer="raw", tenancy="global", writer="bot/replay_stats/store.py", retention="forever"),
	"rs_player_games": dict(layer="raw", tenancy="global", writer="bot/replay_stats/store.py", retention="forever"),
	"rs_player_units": dict(layer="raw", tenancy="global", writer="bot/replay_stats/store.py", retention="sweepable"),
	"rs_player_techs": dict(layer="raw", tenancy="global", writer="bot/replay_stats/store.py", retention="sweepable"),
	"rs_player_buildings": dict(layer="raw", tenancy="global", writer="bot/replay_stats/store.py", retention="sweepable"),
	"rs_player_events": dict(layer="raw", tenancy="global", writer="bot/replay_stats/store.py", retention="sweepable"),
	"rs_player_apm": dict(layer="raw", tenancy="global", writer="bot/replay_stats/store.py", retention="sweepable"),
	"rs_ingest": dict(layer="ops", tenancy="global", writer="bot/replay_stats/jobs.py", retention="forever"),
	"rs_profiles": dict(layer="raw", tenancy="global", writer="bot/replay_stats/store.py", retention="forever"),
	# derived — rebuildable (legacy generation, retired across stages 3-6)
	"rs_player_game_tags": dict(layer="derived", tenancy="global", writer="bot/replay_stats/player_tags.py", retention="forever"),
	"rs_player_personas": dict(layer="derived", tenancy="global", writer="bot/replay_stats/persona_store.py", retention="forever"),
	"cls_classifications": dict(layer="derived", tenancy="global", writer="bot/replay_stats/classifications.py", retention="forever"),
	"cls_data_requirements": dict(layer="derived", tenancy="global", writer="bot/replay_stats/classifications.py", retention="forever"),
	"cls_results": dict(layer="derived", tenancy="global", writer="bot/replay_stats/classification_sync.py", retention="forever"),
	"cls_result_metrics": dict(layer="derived", tenancy="global", writer="bot/replay_stats/classification_sync.py", retention="forever"),
	"cls_player_totals": dict(layer="derived", tenancy="global", writer="bot/replay_stats/classifications.py", retention="forever"),
	"cls_match_ingest": dict(layer="derived", tenancy="global", writer="bot/replay_stats/classifications.py", retention="forever"),
	"bot_player_commentary": dict(layer="derived", tenancy="global", writer="offline", retention="forever"),
	# ops/web
	"web_sessions": dict(layer="ops", tenancy="global", writer="bot/web.py", retention="forever"),
	"web_oauth_states": dict(layer="ops", tenancy="global", writer="bot/web.py", retention="forever"),
}

ALL_TABLES = frozenset(REGISTRY)
```

- [ ] **Step 4: Run** the registry test + full suite — expected: PASS. If the
  scan finds a declaration this list missed, add it to REGISTRY (the test
  output names it) — do not weaken the test.

- [ ] **Step 5: Commit** — `feat(data): table registry with contract enforcement test`

### Task 1.3: Fix the `on_dublicate` typo (48 call sites)

**Files:**
- Modify: `core/DBAdapters/mysql.py` (parameter name), plus every caller —
  find with `grep -rln "on_dublicate" --include="*.py" bot/ core/ utils/`.
- Test: **create** `tests/test_naming.py` here with the typo guard as its only
  test. Task 1.4 adds the old-table-name test to the same file.

- [ ] **Step 1:** Mechanical rename `on_dublicate` → `on_duplicate` in the
  adapter signature and all call sites (sed or scripted edit; TAB files stay TAB).
- [ ] **Step 2: Guard test**

```python
def test_the_dublicate_typo_never_returns():
	import os
	root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	hits = []
	for base in ("bot", "core", "utils"):
		for dirpath, _d, files in os.walk(os.path.join(root, base)):
			if "__pycache__" in dirpath:
				continue
			for f in files:
				if f.endswith(".py") and "on_dublicate" in open(os.path.join(dirpath, f), encoding="utf-8").read():
					hits.append(os.path.join(dirpath, f))
	assert hits == []
```

- [ ] **Step 3:** `python3.11 -m pytest tests/ -q` full suite + `ruff check .` — PASS.
- [ ] **Step 4: Commit** — `refactor(db): on_dublicate -> on_duplicate everywhere`

### Task 1.4: Migration 001 — core + feature renames, and the code sweep

**Files:**
- Modify: `core/migrations.py` (append migration), every declaration/SQL site below.
- Test: create `tests/test_naming.py`.

**The rename map (binding; from design §8.1):**

| old | new | declaration file |
| --- | --- | --- |
| qc_matches | matches | bot/stats/stats.py:57 |
| qc_player_matches | match_players | bot/stats/stats.py:83 |
| qc_players | player_ratings | bot/stats/stats.py:22 |
| qc_rating_history | rating_history | bot/stats/stats.py:40 |
| qc_match_id_counter | match_counter | bot/stats/stats.py:76 |
| qc_configs | channel_settings | bot/queue_channel.py:36 (FactoryTable) |
| pq_configs | queue_settings | bot/queues/pickup_queue.py (FactoryTable) |
| qc_saved_state | bot_state | bot/main.py:20 |
| players | player_prefs | bot/stats/stats.py:11 |
| noadds | queue_bans | bot/stats/noadds.py:8 |
| qc_phrases | player_phrases | bot/stats/noadds.py:25 |
| qc_douche | douche_log | bot/douche.py:17 |
| qc_match_civs | civ_picks | bot/civ_sync.py:16 |
| qc_civ_reconcile | civ_reconcile | bot/civ_reconcile.py:32 |
| qc_lobbies | lobbies | bot/lobby/__init__.py:27 |
| qc_quiz_posts | quiz_posts | bot/quiz/__init__.py:15 |
| qc_quiz_answers | quiz_answers | bot/quiz/__init__.py:39 |
| qc_quiz_config | quiz_settings | bot/quiz/__init__.py:56 |
| qc_prediction_posts | prediction_posts | bot/predictions/__init__.py:23 |
| qc_prediction_votes | prediction_votes | bot/predictions/__init__.py:45 |

Column rename in the same migration: `matches.at` → `reported_at`.
NOT renamed here: `qc_profile_map` (absorbed stage 2), `rs_*`/`cls_*` (stage 3),
`web_*` (already clean), `bot_player_commentary`/`disabled_guilds` (dropped in 1.6).

- [ ] **Step 1:** Append to `core/migrations.py`:

```python
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
```

- [ ] **Step 2: The sweep.** Update every declaration and every SQL/`db.select`
  string. Reference file lists (from the audited reference map — re-grep each
  old name to catch drift before editing):
  - `qc_matches` (26 files): bot/civ_reconcile.py, bot/commands/stats.py, bot/context/slash/commands.py, bot/elo_sync.py, bot/player_profile.py, bot/post_game.py, bot/replay_stats/persona_store.py, bot/replay_stats/player_tags.py, bot/replay_stats/store.py, bot/stats/stats.py, bot/team_insights.py, bot/web.py, tests/test_elo_sync.py, tests/test_pipeline_seed.py, utils/analyze_matches.py, utils/backfill_strategy_tags.py, utils/civ_analysis.py, utils/civ_backfill.py, utils/classifications/dbio.py, utils/classifications/pipeline/seed.py, utils/compute_alt_ratings.py (deleted in 1.6 — skip), utils/db_state.py, utils/import_pubobot_export.py, utils/insights_explore.py, utils/preview_insights.py, utils/update_db.py (deleted in 1.6 — skip)
  - `qc_player_matches` (20), `qc_players` (16), `qc_rating_history` (11), `qc_match_civs` (15), `qc_lobbies` (7), quiz/prediction/douche/noadds/phrases singles — same procedure; grep-driven checklist per table.
  - `matches.at` column: every query using `m.at` / `ORDER BY at` / `cur.at` in the files above switches to `reported_at`.
  - `bot/main.py` save/load state SQL → `bot_state`; CfgFactory `FactoryTable(name=...)` in queue_channel.py + pickup_queue.py.
- [ ] **Step 3:** `tests/test_naming.py` — the growing guard (TABS):

```python
"""Old table names must never reappear in live code. The allowlist is
core/migrations.py (renames reference old names forever) and this test."""
import os

# Only names that cannot collide with ordinary identifiers. `players` and
# `noadds` are deliberately absent: both are live command/module names
# ("/noadds", bot/stats/noadds.py, team['players']), so a substring guard on
# them fires on legitimate code. Their renames are enforced by
# tests/test_data_registry.py instead, which compares actual declarations.
OLD_NAMES = [
	"qc_matches", "qc_player_matches", "qc_players", "qc_rating_history",
	"qc_match_id_counter", "qc_configs", "pq_configs", "qc_saved_state",
	"qc_phrases", "qc_douche", "qc_match_civs", "qc_civ_reconcile",
	"qc_lobbies", "qc_quiz_posts", "qc_quiz_answers", "qc_quiz_config",
	"qc_prediction_posts", "qc_prediction_votes", "on_dublicate",
]
_ALLOW = ("core/migrations.py", "tests/test_naming.py")


def test_no_old_table_names_in_live_code():
	root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	hits = []
	for base in ("bot", "core", "utils", "tests"):
		for dirpath, _d, files in os.walk(os.path.join(root, base)):
			if "__pycache__" in dirpath:
				continue
			for f in files:
				if not f.endswith(".py"):
					continue
				path = os.path.join(dirpath, f)
				rel = os.path.relpath(path, root)
				if rel in _ALLOW:
					continue
				src = open(path, encoding="utf-8").read()
				for name in OLD_NAMES:
					if name in src:
						hits.append(f"{rel}: {name}")
	assert hits == [], "old names in live code:\n" + "\n".join(hits)
```

  Note `players` is not in OLD_NAMES (the bare word is everywhere as a variable);
  its rename is enforced by the registry test instead.
- [ ] **Step 4:** Update `core/data_registry.py` keys to the new names (writers
  unchanged). Run: registry test, naming test, full suite, ruff — all PASS.
- [ ] **Step 5: Commit** — `refactor(naming): rename core + feature tables to plain-English names`

### Task 1.5: Community entity + auto-enroll

**Files:**
- Create: `bot/community.py` — declares `communities` and `community_channels`
  with `db.ensure_table` (NOT a migration: ensure_table creates them on first
  import, and the registry test's scanner needs a declaration to find).
- Modify: `bot/events.py` (on_ready enroll hook), `config.example.cfg` +
  `start.py` (add `FLAGSHIP_GUILD_IDS = []`), `core/data_registry.py`
- Test: `tests/test_community.py`

**Interfaces (Produces — binding for stages 2-5):**

```python
# bot/community.py — TABS. Declarations:
# communities: community_id (int, autoincrement, pk), guild_id (int),
#              name (str), retention (str, default 'lean'), created_at (int)
# community_channels: channel_id (int, pk), community_id (int), added_at (int)
async def ensure_community(guild) -> int          # select-by-guild_id or insert; flagship guilds get retention='full'
async def attach_channel(channel_id: int, community_id: int) -> None   # idempotent
async def community_for_channel(channel_id: int) -> int | None         # cached; None if unenrolled
async def retention_for(community_id: int) -> str                      # 'full' | 'lean'
def invalidate_cache() -> None
```

Enroll hook in `bot/events.py` `on_ready`, after queue channels init: for each
`bot.queue_channels` value, resolve the Discord channel, `ensure_community(
channel.guild)` + `attach_channel`. Guild lookups are runtime-only — this is
why enrollment is a hook, not a migration.

- [ ] Steps: failing tests (FakeDb pattern from test_migrations; cases:
  insert-once/select-second returns same id; flagship guild gets
  `retention='full'` and an existing lean row is upgraded; attach idempotent;
  `community_for_channel` caches) → implement → suite → registry update
  (`communities`, `community_channels` under core) → commit
  `feat(data): community entity with on_ready auto-enroll`.

### Task 1.6: `match_replays` link table + dual-write

**Files:**
- Create: declaration + writer helper in `bot/community.py` (link is
  community-owned): table `match_replays`: community_id (int), match_id (int),
  replay_match_id (int), linked_at (int), pk (community_id, match_id).
- Modify: `bot/replay_stats/store.py` `write_match` — after the `rs_matches`
  upsert, when `bot_match_id` is set: `SELECT channel_id FROM matches WHERE
  match_id=%s` → `community_for_channel` → insert link (`on_duplicate`
  replace). Missing community → `log.info` skip (unenrolled channel).
  `rs_matches.bot_match_id` keeps being written in parallel until stage 5.
- Test: `tests/test_match_replays.py` (fake db: link written with resolved
  community; skip-and-log when channel unenrolled; idempotent on re-ingest).
- [ ] Steps: failing tests → implement → suite → registry (`match_replays`,
  layer=link, tenancy=community, writer=bot/replay_stats/store.py,
  retention=forever) → commit `feat(data): match_replays link table, dual-written at ingest`.

### Task 1.7: Easy retirements

**Files:**
- Delete: `bot/commentary/` (whole package), `bot/alt_ratings.py`,
  `utils/compute_alt_ratings.py`, `data/alt_ratings.csv`,
  `tests/test_alt_ratings.py`, `update_db.py`, `utils/update_db.py`
- Modify: `bot/__init__.py:29` (drop commentary import),
  `bot/commands/stats.py` (drop the commentary tier in the scouting report —
  the `commentary_query` import, the `player_commentary` call and the
  `c = (commentary or {})...` block; the persona line and generated-scout
  fallback STAY until stage 5), `bot/commands/stats.py` `leaderboard_alternate`
  handler + `__all__` entry, `bot/context/slash/commands.py:647-654` (the
  `/leaderboard_alternate` registration), `bot/stats/stats.py:95`
  (disabled_guilds declaration), `CLAUDE.md` (fix the stale `namma_` line; note
  `core/data_registry.py` as the table-contract source of truth)
- Modify: `core/migrations.py` — migration 002:

```python
@migration("002_drop_retired")
async def _m002(db):
	for t in ("bot_player_commentary", "disabled_guilds"):
		if await table_exists(db, t):
			await db.execute(f"DROP TABLE `{t}`")
			log.info(f"migrations: dropped retired table {t}")
```

- Registry: remove `bot_player_commentary`, `disabled_guilds`.
- Tests: remove `bot_player_commentary` uses; naming test gains
  `"bot_player_commentary"`, `"disabled_guilds"`, `"leaderboard_alternate"`,
  `"alt_ratings"` in OLD_NAMES.
- [ ] Steps: delete + edit → full suite + ruff → commit
  `feat(data): retire commentary, alt ratings, disabled_guilds, legacy migration scripts`.

### Task 1.8: Stage-1 deploy + verify

- [ ] `python3.11 -m pytest tests/ -q` (expect: prior count minus removed tests, all green) and `ruff check .`
- [ ] `bash scripts/backup_db.sh` — confirm a fresh dump exists before the first migration deploy.
- [ ] Merge to main (`--no-ff`), re-run suite on merged result, push.
- [ ] `export PATH="/opt/homebrew/bin:$PATH" && railway up --ci` — watch the build run (no watch-path skip).
- [ ] Verify: `railway logs -n 60` shows `migrations: applying 001_core_renames`, `002_drop_retired`, then normal boot (`Init channel ... successful`, state load). `/health` 200 with `db_connected: true`. `railway ssh "sha256sum core/migrations.py bot/community.py"` matches local.
- [ ] Live smoke: `/who`, `/rank`, and one queue add/remove in the flagship channel; confirm `communities` has one row with `retention='full'` (read-only SELECT from local is fine) and `community_channels` has the channel.
- [ ] Record stage completion in `.superpowers/sdd/progress.md`.

---

# Stage 2 — Identity

**Elaboration-first rule applies** (see header): elaborate tasks 2.2–2.5
against the post-stage-1 tree before implementing.

**Binding schemas:**

```
identities        profile_id (int, pk), user_id (int, null), aoe2_name (str, null),
                  confidence (str: 'seed'|'learned'|'manual'), first_seen_at (int), last_seen_at (int)
identity_aliases  community_id (int), user_id (int), nick (str),
                  updated_at (int), pk (community_id, user_id)
```

**Binding API (`bot/identity.py`):**

```python
async def profiles_for_users(user_ids) -> dict[int, list[int]]
async def user_for_profile(profile_id) -> int | None
async def learn(profile_id, user_id, source, aoe2_name=None) -> None   # source: 'lobby'|'ingest'|'manual'; never downgrades 'manual'
async def set_nick(community_id, user_id, nick) -> None
```

**Measured starting state (2026-07-30, read-only):** `rs_profiles` has 89 rows,
48 with a `user_id`. `qc_profile_map` is empty. `data/player_profile_map.csv`
columns: `user_id,nick,aoe2_name,profile_id,country`.
`data/profile_resolved.csv` columns: `profile_id,user_id,nick,aoe2_name,source,appearances`.

**Precedence when sources disagree about a `profile_id → user_id` mapping:**
`rs_profiles` (learned at ingest from real matches) beats `profile_resolved.csv`
(generated) beats `player_profile_map.csv` (hand-maintained, oldest). Later
`learn(source='manual')` beats everything and is never overwritten.

### Task 2.1: Elaborate this stage — DONE (this section)

### Task 2.2: `bot/identity.py`, tables, seed migration

**Files:** create `bot/identity.py`; modify `core/migrations.py` (migration 003),
`core/data_registry.py`; test `tests/test_identity.py`.

**Produces (binding for 2.3–2.5):**
```python
async def profiles_for_users(user_ids) -> dict[int, list[int]]
async def user_for_profile(profile_id: int) -> int | None
async def learn(profile_id, user_id, source, aoe2_name=None) -> None
async def set_nick(community_id, user_id, nick) -> None
async def nick_for(community_id, user_id) -> str | None
def parse_seed_csv(text: str, kind: str) -> list[dict]   # pure; kind='profile_map'|'resolved'
def invalidate_cache() -> None
```
`CONFIDENCE_ORDER = ("seed", "learned", "manual")` — `learn` never lowers a
row's confidence and never overwrites a `manual` mapping with a non-manual one.

- [ ] Steps: failing tests first (`parse_seed_csv` on both real header shapes
  including a row with an empty `user_id`; precedence — a `manual` row survives a
  `learned` write; `profiles_for_users` groups multiple profiles per user;
  `user_for_profile` returns None for unknown) → implement → migration 003 seeding
  `identities` from `rs_profiles` first, then the two CSVs, `INSERT IGNORE` so
  earlier (higher-precedence) rows win → registry rows (`identities`
  layer=raw/tenancy=global, `identity_aliases` layer=link/tenancy=community,
  both `writers=("bot/identity.py",)`, retention=forever) → full suite → commit.

### Task 2.3: Cut over every identity reader

**Files:** `bot/civ_matcher.py`, `bot/lobby/profile_map.py`,
`bot/replay_stats/store.py`, `bot/web.py`; tests as noted.

Four readers, each replaced with the `bot/identity.py` API:
1. `bot/civ_matcher.py:38,56` — `_load_profile_map()` / `_load_profile_uid_map()`
   read `data/player_profile_map.csv` at runtime on **every** civ-match attempt.
   Replace both with `profiles_for_users`. Delete `_PROFILE_MAP_PATH` and both
   loaders. Note `_find_and_record` currently falls back from user_id to *nick*;
   preserve that fallback via `nick_for` or drop it only if no caller depends on it.
2. `bot/lobby/profile_map.py:32,46` — `known_for()` / `link()` wrap the empty
   `qc_profile_map`. Re-point at `user_for_profile` / `learn(source='lobby')`.
   Keep `eliminate()` (pure inference, unrelated). `qc_profile_map` stops being
   read here; it is dropped in stage 6.
3. `bot/replay_stats/store.py:85,158` — `SELECT profile_id, user_id FROM rs_profiles`
   and the `profile_resolved.csv` seeder. Reads move to `identity`; the CSV
   seeder is deleted (migration 003 now owns seeding). `rs_profiles` keeps being
   **written** at ingest (dropped in stage 6) — do not remove the write.
4. `bot/web.py:682` — `_mapped_player_identity` → `identity`.

- [ ] Steps: per reader, write a failing test asserting it consults `identity`
  (fake the module), implement, run the reader's own existing tests
  (`tests/test_civ_matcher.py`, `tests/test_lobby_*.py`) → full suite → commit.

### Task 2.4: Admin identity commands

**Files:** `bot/commands/admin.py`, `bot/context/slash/groups.py`,
`bot/context/slash/commands.py`; test `tests/test_identity.py`.

`/namma_admin identity link <member> <profile_id>` → `learn(source='manual')`;
`/namma_admin identity show <member>` → the member's profiles and current nick.
Follow the existing admin subcommand-group pattern exactly.

- [ ] Steps: failing test on the handler's pure path → implement → suite → commit.

### Task 2.5: Deploy + verify

- [ ] Follow `docs/runbooks/schema-migrations.md`: verified backup, merge,
  suite on the merged result, `railway up --ci`.
- [ ] Verify by query, not `/health` alone: `identities` row count ≥ 89 and every
  `rs_profiles` row with a `user_id` has a matching `identities` row with the same
  `user_id`; ledger contains `003_seed_identities`.
- [ ] Smoke: next reported match still resolves civs — `railway logs | grep "Civ match:"`.

---

# Stage 2.5 — Identity v2

**Authority:** `docs/superpowers/specs/2026-07-30-identity-v2-design.md`
(APPROVED 2026-07-30). Added after the post-stage-2 audit, whose findings are
recorded in `.superpowers/sdd/progress.md` ("POST-STAGE-2 AUDIT") — this stage
closes all of them. **Elaboration-first rule applies.**

**Binding schema/lattice deltas:**

- `identities.confidence` lattice becomes `seed < learned < self < manual`
  (`CONFIDENCE_ORDER` in `core/identity_seed.py` gains `"self"` between
  `learned` and `manual`).
- `learn()` tightened: equal tier + different user no longer overwrites — it
  records an `open` conflict and keeps the existing binding. Strictly-higher
  tier overwrites and records the losing claim as `superseded`.
- `identity_conflicts.status` values: `open | superseded | unlinked`.
- Migration `004_identity_v2`: (a) repair `identities.aoe2_name` from
  `data/profile_resolved.csv`'s `aoe2_name` column for rows whose stored name
  matches that CSV's `nick` (the 30 polluted rows — idempotent, guarded);
  (b) `DROP TABLE` `rs_profiles`, `qc_profile_map`, `identity_aliases`;
  (c) registry + OLD_NAMES updated in the same commit.

**Binding API deltas (`bot/identity.py`):**

```python
async def unlink(profile_id, removed_by_user_id) -> None
    # user_id -> NULL, confidence -> 'seed', claim recorded status='unlinked'
async def relink(profile_id, user_id, additional=False) -> None
    # manual tier; supersedes the profile's previous owner AND (unless
    # additional) the member's previous profiles, all recorded 'superseded'
async def link_self(profile_id, user_id, observed_name) -> bool
    # 'self' tier; False (no write + open conflict) if profile already owned
```

`set_nick` / `nick_for` are deleted with `identity_aliases`.

**New module `bot/identity_solver.py`** — the pairing/deduction solver:
one pure function `deduce(paired_matches, known) -> list[(user_id,
profile_id)]` implementing participation + team/outcome constraint
intersection with the ≥2-paired-games floor and contradiction detection
(contradiction → conflict rows, no writes), plus an async wrapper that runs
after each paired ingest and after every self/manual link. Replaces
`profile_map.eliminate()` and the watcher's inference (both deleted).

**Commands:** bare `/link` (three behaviors per spec §2; profile-id validation
via a new `fetch_profile` in `bot/lobby/api.py` — pin the aoe2companion
endpoint during elaboration and verify it live before coding against it);
admin `/identity link` gains `additional` flag and atomic-relink semantics;
new `/identity unlink` and `/identity status`. Copy for gated analysis
surfaces is exactly **"Statistics pending linking"**.

**Kill list (all verified live in the audit):** `bot/replay_stats/jobs.py`'s
`_load_resolved` + the `utils/replay_quiz/extract.py` resolved-CSV plumbing it
calls; `bot/civ_sync.py` `load_profile_map()` / `_auto_add_profile_mappings()`
(elo-sync lobby pairing re-keyed on time + player-count + map, identity-free);
`bot/replay_stats/store.py`'s `rs_profiles` writes (`shape.profile_upserts`);
`bot/web.py` `_mapped_profiles_by_user()` three-store union → `identities`.
Also ride-along audit fixes: registry `writers` for `identities`,
`identity_conflicts`, `player_ratings`; swap `store.py`'s
rs_profiles-write/learn ordering (moot once the write dies, verify).

## Task 2.5.1 — Elaboration (DONE; facts pinned live 2026-07-30)

**API, verified by curl (do not re-derive):**
`GET https://data.aoe2companion.com/api/profiles/{id}` — 200 →
`{"name":"ddk220","profileId":612690,"country":"us","games":"2647",...}`
(`name` is the REAL in-game name); 404 → `{"success":false,"error":"profile
couldn't be found","profileId":N}`; 400 → validation error (non-numeric).
`/link` MUST distinguish 404 (bad id → error + instructions) from
network/timeout/5xx (transient → "try again later"), writing on neither.
Human verify URL: `https://www.aoe2insights.com/user/relic/{profile_id}/` —
format proven in production by `bot/civ_sync.py:295`, which parses exactly
this shape out of LobbyBOT embeds. aoe2insights is Cloudflare-protected:
fine for humans, unusable as an API.

**Solver thresholds — CALIBRATED ON REAL DATA, do not invent new numbers:**
`MIN_GAMES = 3`, `MIN_RATIO = 0.90`, `MIN_MARGIN = 0.50`. Prototyped over the
1101 usable paired matches: binds 11 of 39 unlinked profiles, all with
ratio 0.93–1.00 and margin 0.76–1.00. There is a natural gap in the real
distribution between margin 0.76 (weakest accepted) and 0.33 (strongest
rejected) — MIN_MARGIN=0.50 sits in that gap. Independent name-corroboration
of the 11: ~8 show visible name overlap; the other 3 have no name
relationship at all, which is the population this solver exists to serve.

**Strict intersection was tried and REJECTED**: it produced 127 contradictions
and 11 empty candidate sets on real data (substitutes and lobby guests mean a
profile's owner is not on the matching side in literally every game). Scoring
with a margin is the design; do not "simplify" it back to intersection.

**Ordering constraint (binding):** migration 004's DROPs must land in commits
AFTER the declarations/writers are removed, else `ensure_table` recreates the
tables at import. Hence the task order below puts the kill list before the
migration.

### Task 2.5.2 — Lattice + write rules in `bot/identity.py`

Files: `bot/identity.py`, `core/identity_seed.py`, `tests/test_identity.py`.
- `CONFIDENCE_ORDER = ("seed", "learned", "self", "manual")`.
- `learn()` tightening: equal rank + DIFFERENT user no longer overwrites —
  keep the binding, record an `open` conflict. Equal rank + SAME user still
  refreshes name/`last_seen_at`. Strictly-higher rank overwrites and records
  the displaced owner as `superseded` (only when the owner actually changes).
- New: `link_self(profile_id, user_id, observed_name) -> bool` (False + `open`
  conflict when the profile is owned by someone else); `unlink(profile_id)`
  (user_id→NULL, confidence→`seed`, prior claim recorded `unlinked`);
  `relink(profile_id, user_id, additional=False)` — `manual` tier, atomic:
  supersedes the profile's prior owner and, unless `additional`, every OTHER
  profile currently owned by that member.
- DELETE `set_nick`, `nick_for`, and the `identity_aliases` ensure_table
  block. Update `identity_show` to drop its Nick field in this same commit.
- Tests to ADD (names binding): `test_learn_equal_rank_different_user_no_longer_overwrites`,
  `test_learn_equal_rank_same_user_still_refreshes`,
  `test_link_self_binds_an_unowned_profile`,
  `test_link_self_refuses_and_records_when_owned_by_another`,
  `test_link_self_is_idempotent_for_the_same_owner`,
  `test_unlink_clears_owner_and_records_the_removed_claim`,
  `test_relink_supersedes_the_previous_owner_atomically`,
  `test_relink_additional_keeps_the_members_other_profiles`,
  `test_relink_without_additional_releases_the_members_other_profiles`.
  DELETE the four `set_nick`/`nick_for` tests.
- MIRROR any column change into `core/migrations.py`'s
  `_ensure_identities_table` / `_ensure_identity_conflicts_table` (they cannot
  import `bot.identity`).

### Task 2.5.3 — Profile validation client

Files: `bot/lobby/api.py` (+ `tests/test_lobby_api.py`).
Add `async def fetch_profile(profile_id)` beside `fetch_match_by_id`, same
lazy-aiohttp/UA/never-raises shape, but it MUST distinguish outcomes — return
`("ok", {"profile_id": int, "name": str})` / `("not_found", None)` /
`("unavailable", None)`. A bare `None` cannot express the difference `/link`
needs. Pure-parser test on a captured 200 body + a captured 404 body; no
network in tests.

### Task 2.5.4 — Player `/link`

Files: `bot/commands/misc.py` (or a new `bot/commands/identity.py` — add to
`__all__`), `bot/context/slash/commands.py` (bare top-level, after the
`# root commands` marker at ~line 461, `**guild_kwargs`, single-line
`): await run_slash(...)` body), `tests/test_identity.py`.
Three behaviors per spec §2: already-linked → view-only (profile id, observed
name, insights URL, "only an admin can change this") regardless of argument;
unlinked + no id → instructions; unlinked + id → validate, then bind via
`link_self` or refuse. Copy for the gated case elsewhere is exactly
**"Statistics pending linking"**.
Tests drive the handler with a fake validation client: bad id → no write +
instructions; transient → no write + retry copy; valid → `link_self` called
with the API's `name`; already-linked → view-only, `link_self` NOT called;
owned-by-other → refusal + conflict.

### Task 2.5.5 — Admin commands + `/identity status`

`/identity link` gains `additional` (keep `force` semantics folded into the
atomic relink); new `/identity unlink <member> <profile_id>`; new
`/identity status` (MODERATOR) reporting linked/total for players seen in the
community in the last 90 days plus which analysis features are below floor.

### Task 2.5.6 — The solver

Files: create `bot/identity_solver.py` (TABS) + `tests/test_identity_solver.py`.
Pure core, no I/O:
```python
def deduce(matches, known, min_games=3, min_ratio=0.90, min_margin=0.50)
# matches: [{"profiles": {profile_id: won_bool}, "users": {user_id: won_bool}}]
# -> {profile_id: (user_id, games, ratio, margin)}
```
Rules: skip a match whose two rosters differ in size (roster-divergence guard
— fired on 6 of 1107 real matches); within a match exclude users already
bound to another profile present in that same match; score a (profile, user)
pair when both are on the same outcome side; bind only when
`games >= min_games and ratio >= min_ratio and margin >= min_margin`.
Async wrapper loads the community's paired matches (via `match_replays`,
which 2.5.7's backfill fills) and writes each binding through
`identity.learn(..., "learned")`, best-effort per binding. Runs after a paired
ingest and after every self/manual link.
Tests: unanimous evidence binds; a lone 50/50 game does not; roster-size
mismatch is skipped; a substitute-style contradiction lowers ratio below floor
and blocks the write; the within-match exclusion prevents double-attribution;
multi-account users (5 exist in prod) are not broken by the exclusion.
DELETE `profile_map.eliminate` + its watcher call site and
`tests/test_lobby_profile_map.py`'s five eliminate tests.

### Task 2.5.7 — Kill list + web cutover

Delete `bot/replay_stats/jobs.py::_load_resolved` and its
`utils/replay_quiz/extract.py` resolved-CSV plumbing (ingest names come from
the replay itself); delete `civ_sync.load_profile_map` +
`_auto_add_profile_mappings` and re-key `find_matching_lobby*` on time +
player-count + map (identity-free; pair nothing when two candidates tie);
delete `shape.profile_upserts` + the `rs_profiles` write; repoint
`web._mapped_profiles_by_user` at `identities` and delete `_csv_profile_rows`.
Registry entries and `writers` fixes (`identities`, `identity_conflicts`,
`player_ratings`) land here.

### Task 2.5.8 — Migration 004 (drops LAST) + repair + backfill

```python
@migration("004_identity_v2")
async def _m004(db):
    # (a) repair aoe2_name polluted with Discord nicks (30 of 54 rows)
    # (b) backfill match_replays from rs_matches.bot_match_id — 1107 historical
    #     pairings that stage 6's column drop would otherwise destroy
    # (c) DROP rs_profiles, qc_profile_map, identity_aliases
```
Order inside the body matters: (b) must precede (c). All three guarded by
`table_exists` and individually idempotent.

### Task 2.5.9 — Deploy + verify

Runbook backup → merge → `railway up --ci`. Verify by query, not `/health`:
repaired names correct (spot-check profile 612690 = `ddk220`),
`match_replays` ≈ 1107 rows, the three tables gone, ledger has
`004_identity_v2`, solver log line shows its binding count. Live smoke:
`/link` with a bad id, a valid id, and as an already-linked player.

---

# Stage 3 — Derived-global + raw renames

**Binding schemas:**

```
game_stats   replay_match_id (int), player_number (int), profile_id (int, null),
             civ (str, null), team (str, null), winner (bool, null),
             avg_eapm (int, null), peak_eapm (int, null),
             military_medal (int, null: 1|2|3), villager_medal (int, null: 1|2|3),
             top_units (dict: [{unit, category, total}] top 3 by total),
             computed_at (int), pk (replay_match_id, player_number)
game_labels  replay_match_id (int), player_number (int), label (str),
             kind (str: 'strategy'|'spawn'),
             evidence (dict, null), played_at (int, null),
             pk (replay_match_id, player_number, label)
```

**No `user_id` columns** (changed by identity v2, spec §5): derived-global keys
on `profile_id` only; every consumer resolves profile → user through
`identities` at refresh/read time. That is what makes a late `/link` backfill
the player's whole history with no backfill job.

```
```

**Binding decisions:**
- Writer: `bot/derived/game_stats.py`, called from `store.write_match`
  immediately after raw writes, one pure `compute_game_stats(players, units,
  apm_rows) -> rows` function (medal logic reuses
  `card_scoring.assign_medals` — moved call, unchanged math) + one
  `async write(...)`.
- Labels: `bot/replay_stats/classification_sync.py` maps classifier output →
  `game_labels` using `card_query.STRATEGY_KEYS` (kind='strategy') and
  `SPAWN_PHRASES` keys + `spawn_near_food/gold/stone`, `spawn_*_poor`,
  `scattered_villagers`, `tight_villagers` (kind='spawn'). `luck_baseline` is
  dropped at the source. **cls_ dual-write continues** (insights + web still
  read it) until stage 5.
- Match cards read stored medals from `game_stats` (join on
  (replay_match_id, player_number)) instead of calling `assign_medals` at
  render; the pure function remains for the ingest writer.
- Raw renames, migration 005 (004 is identity v2): `rs_matches→replay_matches`,
  `rs_player_games→replay_players`, `rs_player_units→replay_units`,
  `rs_player_techs→replay_techs`, `rs_player_buildings→replay_buildings`,
  `rs_player_events→replay_events`, `rs_player_apm→replay_apm`,
  `rs_ingest→replay_ingest`; `rs_config` DROPPED — its single `enabled` flag
  becomes config var `REPLAY_INGEST_ENABLED` (config.example.cfg + start.py).
  Column rename across all replay_* + civ_picks: `aoe2_match_id` →
  `replay_match_id`. Sweep + OLD_NAMES additions + registry update ride the
  same commit.
- `rs_player_game_tags` and `rs_player_personas` keep their writers running
  (their consumers are still live) — names unchanged until they drop in 6.

**Tasks:** 3.1 elaborate → 3.2 `game_stats` writer (TDD on the pure function:
medals match `assign_medals` output; top_units top-3 by total; apm avg/peak
from buckets; age_reliable gating identical to cards) → 3.3 `game_labels`
writer + allowlist tests (luck_baseline dropped; kinds correct) → 3.4 cards
read stored medals (existing card tests updated to seed `game_stats` fakes) →
3.5 raw renames migration + sweep + guards → 3.6 deploy + verify (post one
match end-to-end on flagship: raw rows AND game_stats/game_labels rows appear;
cards render with medals).

---

# Stage 4 — Derived-community + retention

**Binding schemas:**

```
player_rollups  community_id (int), user_id (int), games (int),
                rollup (dict — see contract below), computed_at (int),
                pk (community_id, user_id)
metric_boards   community_id (int), metric_id (str), board (dict:
                {label, unit, direction, leaders: [{user_id, nick, avg, n}],
                 top_games: [{user_id, nick, value, replay_match_id}]}),
                computed_at (int), pk (community_id, metric_id)
civ_stats       community_id (int), civ (str), games (int), wins (int),
                losses (int), computed_at (int), pk (community_id, civ)
```

**Rollup dict contract (consumed by stage 5a scouting report — binding):**

```json
{
  "medal_rates": {"military": 0.34, "villager": 0.18, "games_ranked": 41},
  "apm": {"median_avg": 62, "median_peak": 91, "games": 38},
  "strategies": [{"key": "scout_rush", "games": 12, "wins": 9}],
  "spawns":     [{"key": "spawn_near_enemy", "games": 8, "wins": 3}],
  "units":      [{"unit": "Knight", "games": 15, "wins": 10}]
}
```

**Binding decisions:**
- Metrics catalog for `metric_boards`: ONLY fields that survive the sweep —
  from `replay_players` scalars (villagers, vil_pre_* , military, mil_pre_*,
  feudal_s/castle_s/imperial_s, first_tc_s, eapm) and `game_stats`
  (peak_eapm, medals, top_units). Tech-timing and building-count metrics from
  the old quiz catalog are dropped.
- Refresh: `bot/derived/refresh.py` — dirty-set of (community_id, user_id)
  marked by ingest/link/report hooks; `on_think` drains ≤N per tick; full
  per-user recompute (≤600 rows each) — no incremental deltas, rebuild-per-user
  is the simple correct unit. Nightly full-community pass as backstop.
  Attribution happens HERE (identity v2 §5): the refresh resolves the user's
  profiles via `identities` and aggregates profile-keyed `game_stats`/
  `game_labels` rows — which is why an identity link hook marking the user
  dirty backfills their entire history for free. Unlinked profiles simply
  aggregate into no rollup until they resolve.
- Sample floors (constants in `bot/derived/refresh.py`, calibrated on flagship
  data during stage 5): `SPLIT_MIN_GAMES = 5`, `BOARD_MIN_GAMES = 3`.
- Sweeper: `bot/derived/sweeper.py`, daily on_think slot. Deletes from
  `replay_events/techs/buildings/units/apm` rows whose replay_match_id is
  linked ONLY to lean communities, `linked_at < now - RETENTION_DAYS (30)`,
  and whose every linked community has `rollups computed_at > linked_at`.
  Never touches a replay linked to any `full` community. First deploy runs it
  in `DRY_RUN = True` (logs candidate counts, deletes nothing); flipping to
  live is a follow-up commit after the log is inspected.

**Tasks:** 4.1 elaborate → 4.2 rollup writer (TDD: pure aggregation from fake
game_stats/labels/links rows; floors honored; win splits correct) → 4.3
metric boards (pure: leaders + top_games per metric) → 4.4 civ_stats (from
civ_picks) → 4.5 refresh job wiring + dirty hooks → 4.6 sweeper with DRY_RUN →
4.7 deploy + verify (rollup row for a flagship player appears after next
match; sweeper log lists 0 candidates — no lean communities exist yet).

---

# Stage 5 — Consumers cut over (one deploy per substage)

- **5a Scouting report:** `/rank` + `/rank_detailed` scouting section reads
  `player_rollups` (via `community_for_channel(ctx.channel.id)`). Renders:
  medal rates line, APM medians line, top strategy/spawn/unit each with its
  win split (floors honored — below floor, the split line is omitted, never
  shown with a warning). Persona line + generated scout read DELETED;
  `web.player_overview_snapshot` deleted; persona refresh calls removed from
  ingest (`persona_store.refresh_match_users`). `rs_player_personas` stops
  being written; dropped in 6.
- **5b Quiz:** `bot/quiz/player_bank.py` generates player-source questions
  live from `metric_boards` at post time (day-parity source alternation keeps
  today's player/game rhythm; game days keep reading the committed
  `data/quiz_bank.json` schedule). DELETE `utils/replay_quiz/` (parser,
  SQLite, build_questions, weekly), `utils/quiz_gen/convert_player_bank.py`,
  `data/replay_quiz.db`, `data/question_bank.json`, `data/quiz_bank_player.json`,
  `data/replay_manifest.csv`, `data/profile_resolved.csv` (identity owns it
  since stage 2), and the player-source branches of
  `utils/quiz_gen/build_schedule.py`.
- **5c Cards + insights + civ stats:** `/insights` reads `game_labels`;
  classification dual-write to `cls_*` STOPS; `bot/civ_stats.py` reads
  `civ_stats` table (CSV seed fallback deleted); card strategy chips read
  `game_labels`.
- **5d Web repoint (minimal):** player API → `player_rollups` + `game_labels`;
  strategies page → `game_labels`; civ-stats API → `civ_stats`; luck page and
  any cls_-backed endpoint removed. Viewing-layer breakage beyond these
  repoints is accepted per the design; the community-first web redesign is a
  separate future project.
- Each substage: elaborate → TDD → deploy → verify checklist → progress note.

# Stage 6 — Final retirements

- Migration 006 drops: `cls_classifications`, `cls_data_requirements`,
  `cls_results`, `cls_result_metrics`, `cls_player_totals`, `cls_match_ingest`,
  `rs_player_game_tags`, `rs_player_personas` (`rs_profiles`, `qc_profile_map`,
  `identity_aliases` already dropped in 2.5);
  column drop `replay_matches.bot_match_id` (link table is sole authority;
  `civ_picks.bot_match_id` STAYS — it is that table's join key to `matches`).
- Delete modules: `bot/replay_stats/persona.py`, `persona_store.py`,
  `player_tags.py`, `scoring.py` (verify no remaining consumer first — its
  last known consumers die in 5), `bot/classifications/` read-side,
  `utils/backfill_personas.py`, `utils/persona_calibration.py`,
  `utils/backfill_player_game_tags.py`, `utils/backfill_strategy_tags.py`,
  `utils/tag_calibration.py`, `utils/classifications/` offline pipeline
  (classifier logic that classification_sync still imports moves into
  `bot/replay_stats/` first), `data/player_profile_map.csv`,
  `data/civ_elo_stats.csv`, `data/player_civ_stats.csv`,
  `data/match_civ_details.csv`, `data/match_id_map.csv`,
  `data/player_civ_weekly.csv`, `data/qc_*.csv` exports.
- OLD_NAMES gains every dropped name; registry shrinks to the target §3 set;
  CLAUDE.md architecture section rewritten to describe the new layers.
- Deploy + verify + close-out notes.

---

## Self-review record (kept per writing-plans)

- Spec coverage: §1 dispositions → tasks 1.4/1.7/3.5/5b/5c/6; §2.5 → 3.2/3.3/5a/6;
  §3 layers/entities → 1.2/1.5/1.6/2.2/3.x/4.x; §4 writers → enforced by
  registry + one writer module per new table; §5 stages → stage map; §7
  retention → 4.6; §8 naming → 1.3/1.4/3.5/6 + test_naming.py. No uncovered
  section found.
- Placeholder scan: stages 2–6 contain contracts by design (see Plan
  maintenance rule) — schemas, APIs, file lists and decisions are concrete;
  step-level code is deliberately deferred to stage-opening elaboration, which
  is itself a checkboxed task, not a TODO.
- Type consistency: `community_for_channel` (1.5) is the resolver used in 1.6,
  4.x and 5a; `replay_match_id` naming is uniform from 3 onward; rollup dict
  keys in 4 match the 5a renderer contract.
