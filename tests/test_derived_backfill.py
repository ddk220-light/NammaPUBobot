"""Unit tests for the derived-global reconciliation loop (bot/derived/backfill.py).

Two things shape this file.

1. The pending predicate IS a SQL count comparison. A fake that returns canned
   rows would test the Python glue and leave `>` vs `>=` — the difference
   between converging and looping forever — completely unguarded. So the fake
   adapter here runs the real SQL against an in-memory sqlite3 database
   (stdlib, never a real server), translating only the %s placeholders. Every
   convergence claim below is therefore executed, not asserted about.

2. There is no pytest-asyncio in this repo, so an `async def test_...` is
   silently SKIPPED and reports as passing. Every test is sync and drives the
   coroutines with asyncio.run().
"""
import asyncio
import json
import sqlite3

import bot.replay_stats  # noqa: F401  — backfill's compute lazily imports card_scoring from it
import bot.derived.backfill as backfill
import bot.derived.game_labels as game_labels
import bot.derived.game_stats as game_stats

_SCHEMA = """
CREATE TABLE replay_matches (replay_match_id INTEGER PRIMARY KEY, played_at TEXT);
CREATE TABLE replay_players (
	replay_match_id INTEGER, profile_id INTEGER, player_number INTEGER, civ TEXT, team TEXT,
	winner INTEGER, eapm INTEGER, villagers INTEGER, military INTEGER,
	PRIMARY KEY (replay_match_id, profile_id));
CREATE TABLE replay_units (
	replay_match_id INTEGER, player_number INTEGER, unit TEXT, category TEXT,
	is_military INTEGER, total INTEGER,
	PRIMARY KEY (replay_match_id, player_number, unit));
CREATE TABLE replay_apm (
	replay_match_id INTEGER, player_number INTEGER, minute INTEGER, actions INTEGER,
	PRIMARY KEY (replay_match_id, player_number, minute));
CREATE TABLE cls_results (
	`key` TEXT, aoe2_match_id INTEGER, player_number INTEGER, played_at INTEGER,
	PRIMARY KEY (`key`, aoe2_match_id, player_number));
CREATE TABLE cls_result_metrics (
	`key` TEXT, aoe2_match_id INTEGER, player_number INTEGER, metric TEXT, value REAL,
	PRIMARY KEY (`key`, aoe2_match_id, player_number, metric));
CREATE TABLE game_stats (
	replay_match_id INTEGER, player_number INTEGER, profile_id INTEGER, civ TEXT, team TEXT,
	winner INTEGER, avg_eapm INTEGER, peak_eapm INTEGER, military_medal INTEGER,
	villager_medal INTEGER, has_production INTEGER NOT NULL, top_units TEXT,
	computed_at INTEGER,
	PRIMARY KEY (replay_match_id, player_number));
CREATE TABLE game_labels (
	replay_match_id INTEGER, player_number INTEGER, label TEXT, kind TEXT, evidence TEXT,
	played_at INTEGER,
	PRIMARY KEY (replay_match_id, player_number, label));
"""


class _SqliteDB:
	"""Stand-in for core.database.db backed by in-memory sqlite3. Never touches a
	real database. `fail_on` makes the write for a given match id raise, which is
	how the per-match isolation guard is exercised."""

	def __init__(self):
		self.conn = sqlite3.connect(":memory:")
		self.conn.row_factory = sqlite3.Row
		self.conn.executescript(_SCHEMA)
		self.fail_on = set()

	# ── helpers used by the tests to set up fixtures ──────────────────────
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

	# ── the adapter surface backfill/game_stats/game_labels actually call ──
	@staticmethod
	def _sql(sql):
		return sql.replace("%s", "?")

	async def fetchall(self, sql, args=None):
		return [dict(r) for r in self.conn.execute(self._sql(sql), list(args or [])).fetchall()]

	async def fetchone(self, sql, args=None):
		row = self.conn.execute(self._sql(sql), list(args or [])).fetchone()
		return dict(row) if row is not None else None

	async def execute(self, sql, args=None):
		self.conn.execute(self._sql(sql), list(args or []))

	async def insert_many(self, table, rows, on_duplicate=None):
		rows = list(rows)
		if not rows:
			return
		if rows[0].get("replay_match_id") in self.fail_on:
			raise RuntimeError(f"simulated write failure for {rows[0]['replay_match_id']}")
		cols = list(rows[0].keys())
		sql = "INSERT OR REPLACE INTO {} ({}) VALUES ({})".format(
			table, ",".join(f"`{c}`" for c in cols), ",".join(["?"] * len(cols)))
		self.conn.executemany(sql, [[r[c] for c in cols] for r in rows])


class _RecordingLog:
	"""Stand-in for core.console.log that keeps what was written, so the tests can
	assert the batch line carries real counts rather than merely being emitted."""

	def __init__(self):
		self.info_lines = []
		self.error_lines = []

	def info(self, message):
		self.info_lines.append(message)

	def error(self, message):
		self.error_lines.append(message)

	def __getattr__(self, _name):
		return lambda *_a, **_k: None


def _install(monkeypatch, fake):
	"""Point every module that writes at the same fake, and give each test a
	fresh quarantine so state cannot leak between tests."""
	monkeypatch.setattr(backfill, "db", fake)
	monkeypatch.setattr(game_stats, "db", fake)
	monkeypatch.setattr(game_labels, "db", fake)
	monkeypatch.setattr(backfill, "_stats_attempts", {})
	monkeypatch.setattr(backfill, "_labels_attempts", {})


def _match_with_players(fake, mid, players=2):
	fake.add("replay_matches", replay_match_id=mid, played_at="2024-05-01 13:22")
	for i in range(1, players + 1):
		fake.add("replay_players", replay_match_id=mid, profile_id=1000 * mid + i,
		         player_number=i, civ="Franks", team=str(i), winner=i % 2,
		         eapm=40 + i, villagers=100 - i, military=10 * i)


def _match_with_labels(fake, mid, keys, played_at=1700000000, player_number=1):
	fake.add("replay_matches", replay_match_id=mid, played_at="2024-05-01 13:22")
	for key in keys:
		fake.add("cls_results", key=key, aoe2_match_id=mid,
		         player_number=player_number, played_at=played_at)


# ── pending detection ────────────────────────────────────────────────────

def test_pending_game_stats_lists_only_matches_missing_derived_rows(monkeypatch):
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	_match_with_players(fake, 1)          # nothing derived  -> pending
	_match_with_players(fake, 2)          # fully derived    -> not pending
	_match_with_players(fake, 3)          # half derived     -> pending
	for pn in (1, 2):
		fake.add("game_stats", replay_match_id=2, player_number=pn, has_production=1, computed_at=1)
	fake.add("game_stats", replay_match_id=3, player_number=1, has_production=1, computed_at=1)

	assert asyncio.run(backfill.pending_game_stats()) == [3, 1]


def test_pending_game_labels_ignores_keys_outside_the_allowlist(monkeypatch):
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	_match_with_labels(fake, 10, ["archer_rush"])                  # allowlisted -> pending
	_match_with_labels(fake, 11, ["luck_baseline"])                # excluded    -> never pending
	_match_with_labels(fake, 12, ["luck_baseline", "spawn_isolated"])  # one counts -> pending
	fake.add("game_labels", replay_match_id=12, player_number=1, label="spawn_isolated",
	         kind="spawn", evidence="{}", played_at=1700000000)

	assert asyncio.run(backfill.pending_game_labels()) == [10]


def test_match_with_no_source_rows_at_all_is_never_pending(monkeypatch):
	# The 8 ingested matches carrying no cls_results are the live example: they
	# must not appear in the pending set even once, or the loop never terminates.
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	fake.add("replay_matches", replay_match_id=99, played_at="2024-05-01 13:22")

	assert asyncio.run(backfill.pending_game_stats()) == []
	assert asyncio.run(backfill.pending_game_labels()) == []


# ── convergence: the property the whole design exists for ────────────────

def test_drain_converges_to_zero_and_does_not_reprocess(monkeypatch):
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	_match_with_players(fake, 1)
	_match_with_players(fake, 2, players=4)
	_match_with_labels(fake, 1, ["archer_rush", "spawn_isolated"])
	_match_with_labels(fake, 2, ["knight_rush"])

	assert asyncio.run(backfill.drain_game_stats(computed_at=777)) == 2
	assert asyncio.run(backfill.drain_game_labels()) == 2
	assert len(fake.rows("game_stats")) == 6
	assert len(fake.rows("game_labels")) == 3

	# Second pass: nothing pending, nothing processed, nothing rewritten.
	assert asyncio.run(backfill.pending_game_stats()) == []
	assert asyncio.run(backfill.pending_game_labels()) == []
	assert asyncio.run(backfill.drain_game_stats(computed_at=888)) == 0
	assert asyncio.run(backfill.drain_game_labels()) == 0
	assert {r["computed_at"] for r in fake.rows("game_stats")} == {777}


def test_zero_row_matches_never_re_enter_the_pending_set(monkeypatch):
	# THE convergence case. A match whose only classifier hit is luck_baseline
	# derives zero game_labels rows by design, and a match with no players
	# derives zero game_stats rows. Under a "has no derived rows" predicate both
	# would be picked up on every tick forever; under the count comparison they
	# are never pending, before or after a full drain of everything else.
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	_match_with_labels(fake, 5, ["luck_baseline"])
	_match_with_players(fake, 6)
	_match_with_labels(fake, 6, ["archer_rush"])

	for _ in range(3):
		asyncio.run(backfill.drain_game_stats(computed_at=1))
		asyncio.run(backfill.drain_game_labels())

	assert fake.rows("game_labels", replay_match_id=5) == []
	assert asyncio.run(backfill.pending_game_labels()) == []
	assert asyncio.run(backfill.pending_game_stats()) == []


def test_duplicate_player_number_still_converges(monkeypatch):
	# replay_players' PK is (replay_match_id, profile_id), so two rows can share a
	# player_number; game_stats' PK is (replay_match_id, player_number), so they
	# store as one. A plain COUNT(*) would leave 2 > 1 forever. COUNT(DISTINCT
	# player_number) is the number of rows the write can persist, so it converges.
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	fake.add("replay_matches", replay_match_id=7, played_at="2024-05-01 13:22")
	fake.add("replay_players", replay_match_id=7, profile_id=100, player_number=1,
	         eapm=40, villagers=90, military=10)
	fake.add("replay_players", replay_match_id=7, profile_id=200, player_number=1,
	         eapm=50, villagers=80, military=20)

	assert asyncio.run(backfill.drain_game_stats(computed_at=1)) == 1
	assert len(fake.rows("game_stats", replay_match_id=7)) == 1
	assert asyncio.run(backfill.pending_game_stats()) == []


def test_deleted_derived_row_is_repaired_on_the_next_pass(monkeypatch):
	# The loop doubles permanently as repair: this is what heals a live
	# ingest-time write that failed its best-effort guard.
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	_match_with_players(fake, 4, players=3)
	asyncio.run(backfill.drain_game_stats(computed_at=1))
	fake.conn.execute("DELETE FROM game_stats WHERE replay_match_id=4 AND player_number=2")

	assert asyncio.run(backfill.pending_game_stats()) == [4]
	asyncio.run(backfill.drain_game_stats(computed_at=2))
	assert len(fake.rows("game_stats", replay_match_id=4)) == 3


# ── over-population: the half `>` could never heal ───────────────────────
# utils/classifications/dbio.py's wipe_results is a supported operation — the
# offline runner calls it per classification before a full-window rebuild — so a
# retuned trigger legitimately REDUCES or SUBSTITUTES a match's cls_results
# rows. Under `src.n > COALESCE(dst.n, 0)` none of the three cases below is ever
# pending again and the stale label survives forever.

def test_a_source_row_removed_upstream_repairs_the_stored_label(monkeypatch):
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	_match_with_labels(fake, 20, ["archer_rush", "knight_rush"])
	asyncio.run(backfill.drain_game_labels())
	assert len(fake.rows("game_labels", replay_match_id=20)) == 2

	# The runner retunes archer_rush; this match no longer qualifies for it.
	fake.conn.execute("DELETE FROM cls_results WHERE aoe2_match_id=20 AND `key`='archer_rush'")

	assert asyncio.run(backfill.pending_game_labels()) == [20]
	asyncio.run(backfill.drain_game_labels())
	assert [r["label"] for r in fake.rows("game_labels", replay_match_id=20)] == ["knight_rush"]


def test_a_same_count_substitution_upstream_is_repaired(monkeypatch):
	# The case NO count comparison can see: the same number of rows on both
	# sides, but a different label. `>` and `<>` are both blind to it and leave a
	# silently WRONG label on a public match card forever; comparing the (player,
	# label) SETS is what catches it.
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	_match_with_labels(fake, 21, ["archer_rush", "spawn_isolated"])
	asyncio.run(backfill.drain_game_labels())
	assert sorted(r["label"] for r in fake.rows("game_labels", replay_match_id=21)) == \
		["archer_rush", "spawn_isolated"]

	# A full-window rebuild retunes both triggers: archer_rush -> scout_rush for
	# the same player. Two source rows before, two after.
	fake.conn.execute("DELETE FROM cls_results WHERE aoe2_match_id=21 AND `key`='archer_rush'")
	fake.add("cls_results", key="scout_rush", aoe2_match_id=21, player_number=1,
	         played_at=1700000000)

	assert asyncio.run(backfill.pending_game_labels()) == [21]
	asyncio.run(backfill.drain_game_labels())
	assert sorted(r["label"] for r in fake.rows("game_labels", replay_match_id=21)) == \
		["scout_rush", "spawn_isolated"]
	assert asyncio.run(backfill.pending_game_labels()) == []


def test_a_label_moving_to_another_player_is_repaired(monkeypatch):
	# Same label, same count, different PLAYER — the medal-misattribution shape
	# of the same blind spot, on the labels side.
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	_match_with_labels(fake, 25, ["archer_rush"], player_number=1)
	asyncio.run(backfill.drain_game_labels())
	assert [r["player_number"] for r in fake.rows("game_labels", replay_match_id=25)] == [1]

	fake.conn.execute("DELETE FROM cls_results WHERE aoe2_match_id=25")
	fake.add("cls_results", key="archer_rush", aoe2_match_id=25, player_number=2,
	         played_at=1700000000)

	assert asyncio.run(backfill.pending_game_labels()) == [25]
	asyncio.run(backfill.drain_game_labels())
	assert [r["player_number"] for r in fake.rows("game_labels", replay_match_id=25)] == [2]


def test_a_source_dropping_to_zero_still_cleans_the_orphaned_rows(monkeypatch):
	# The case a LEFT JOIN from the source cannot express at all: with no source
	# rows left the match produces no `src` group, so it never appears in the
	# join and its orphaned derived rows are never touched again.
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	_match_with_labels(fake, 22, ["archer_rush"])
	asyncio.run(backfill.drain_game_labels())
	assert len(fake.rows("game_labels", replay_match_id=22)) == 1

	fake.conn.execute("DELETE FROM cls_results WHERE aoe2_match_id=22")

	assert asyncio.run(backfill.pending_game_labels()) == [22]
	asyncio.run(backfill.drain_game_labels())
	assert fake.rows("game_labels", replay_match_id=22) == []
	# ...and having cleaned them it converges: an all-zero match is not pending.
	assert asyncio.run(backfill.pending_game_labels()) == []


def test_orphaned_game_stats_rows_are_cleaned_when_the_raw_rows_go(monkeypatch):
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	_match_with_players(fake, 23, players=2)
	asyncio.run(backfill.drain_game_stats(computed_at=1))
	fake.conn.execute("DELETE FROM replay_players WHERE replay_match_id=23")

	assert asyncio.run(backfill.pending_game_stats()) == [23]
	asyncio.run(backfill.drain_game_stats(computed_at=2))
	assert fake.rows("game_stats", replay_match_id=23) == []
	assert asyncio.run(backfill.pending_game_stats()) == []


def test_an_extra_derived_row_is_healed_rather_than_left_forever(monkeypatch):
	# Over-population from the other direction: a stray row nobody derived.
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	_match_with_players(fake, 24, players=2)
	asyncio.run(backfill.drain_game_stats(computed_at=1))
	fake.add("game_stats", replay_match_id=24, player_number=9, has_production=1, computed_at=1)

	assert asyncio.run(backfill.pending_game_stats()) == [24]
	asyncio.run(backfill.drain_game_stats(computed_at=2))
	assert sorted(r["player_number"] for r in fake.rows("game_stats", replay_match_id=24)) == [1, 2]


# ── batch cap ────────────────────────────────────────────────────────────

def test_drain_processes_at_most_BATCH_matches_per_pass(monkeypatch):
	# Uses the real BATCH, not a patched one, so shifting the constant is caught
	# too — 30 pending matches must leave exactly 30 - BATCH behind.
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	for mid in range(1, 31):
		_match_with_players(fake, mid, players=1)

	assert len(asyncio.run(backfill.pending_game_stats())) == backfill.BATCH
	assert asyncio.run(backfill.drain_game_stats(computed_at=1)) == backfill.BATCH
	assert len(fake.rows("game_stats")) == backfill.BATCH
	assert len(asyncio.run(backfill.pending_game_stats())) == 30 - backfill.BATCH

	asyncio.run(backfill.drain_game_stats(computed_at=1))
	assert asyncio.run(backfill.pending_game_stats()) == []


def test_pending_honours_an_explicit_smaller_limit(monkeypatch):
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	for mid in range(1, 6):
		_match_with_players(fake, mid, players=1)

	assert asyncio.run(backfill.pending_game_stats(limit=3)) == [5, 4, 3]


# ── per-match isolation ──────────────────────────────────────────────────

def test_one_failing_match_does_not_abort_the_rest_of_the_batch(monkeypatch):
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	for mid in (1, 2, 3):
		_match_with_players(fake, mid, players=2)
	fake.fail_on.add(2)

	assert asyncio.run(backfill.drain_game_stats(computed_at=1)) == 2
	assert {r["replay_match_id"] for r in fake.rows("game_stats")} == {1, 3}
	# The failure is remembered so it cannot occupy a slot in every future batch.
	assert backfill._stats_attempts == {2: 1}
	assert asyncio.run(backfill.pending_game_stats()) == [2]


def test_a_match_failing_MAX_ATTEMPTS_times_stops_blocking_the_queue(monkeypatch):
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	_match_with_players(fake, 1, players=2)
	fake.fail_on.add(1)

	for _ in range(backfill.MAX_ATTEMPTS):
		asyncio.run(backfill.drain_game_stats(computed_at=1))

	assert backfill._stats_attempts == {1: backfill.MAX_ATTEMPTS}
	assert asyncio.run(backfill.pending_game_stats()) == []


def test_labels_drain_isolates_a_failing_match_too(monkeypatch):
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	for mid in (1, 2, 3):
		_match_with_labels(fake, mid, ["archer_rush"])
	fake.fail_on.add(3)

	assert asyncio.run(backfill.drain_game_labels()) == 2
	assert {r["replay_match_id"] for r in fake.rows("game_labels")} == {1, 2}


# ── row content ──────────────────────────────────────────────────────────

def test_peak_eapm_is_null_when_the_match_has_no_apm_buckets(monkeypatch):
	# replay_apm is empty for every pre-backfill match; NULL peak_eapm is the
	# correct historical value, and avg_eapm must still come through.
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	_match_with_players(fake, 1, players=2)
	asyncio.run(backfill.drain_game_stats(computed_at=1))

	rows = fake.rows("game_stats", replay_match_id=1)
	assert [r["peak_eapm"] for r in rows] == [None, None]
	assert sorted(r["avg_eapm"] for r in rows) == [41, 42]


def test_peak_eapm_is_the_bucket_max_when_buckets_do_exist(monkeypatch):
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	_match_with_players(fake, 1, players=1)
	for minute, actions in enumerate((12, 55, 31)):
		fake.add("replay_apm", replay_match_id=1, player_number=1,
		         minute=minute, actions=actions)
	asyncio.run(backfill.drain_game_stats(computed_at=1))

	assert fake.rows("game_stats", replay_match_id=1)[0]["peak_eapm"] == 55


def test_stats_rows_carry_the_match_id_and_serialised_top_units(monkeypatch):
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	_match_with_players(fake, 1, players=1)
	fake.add("replay_units", replay_match_id=1, player_number=1, unit="Knight",
	         category="cavalry", is_military=1, total=30)
	fake.add("replay_units", replay_match_id=1, player_number=1, unit="Villager",
	         category="economy", is_military=0, total=99)
	asyncio.run(backfill.drain_game_stats(computed_at=4242))

	row = fake.rows("game_stats", replay_match_id=1)[0]
	assert row["computed_at"] == 4242
	assert json.loads(row["top_units"]) == [dict(unit="Knight", category="cavalry", total=30)]


def test_label_rows_take_played_at_from_their_own_match_not_a_shared_value(monkeypatch):
	# replay_matches.played_at holds a DATE STRING; game_labels.played_at is an epoch
	# BIGINT. The epoch must come from the match's own cls_results rows, so the
	# two matches below — drained in one batch — must keep different epochs.
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	_match_with_labels(fake, 1, ["archer_rush"], played_at=1600000000)
	_match_with_labels(fake, 2, ["knight_rush"], played_at=1700000000)
	asyncio.run(backfill.drain_game_labels())

	stored = {r["replay_match_id"]: r["played_at"] for r in fake.rows("game_labels")}
	assert stored == {1: 1600000000, 2: 1700000000}


def test_label_evidence_is_scoped_to_its_own_key_and_player(monkeypatch):
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	_match_with_labels(fake, 1, ["archer_rush"], player_number=1)
	fake.add("cls_results", key="knight_rush", aoe2_match_id=1, player_number=2,
	         played_at=1700000000)
	fake.add("cls_result_metrics", key="archer_rush", aoe2_match_id=1, player_number=1,
	         metric="archers", value=12.0)
	fake.add("cls_result_metrics", key="knight_rush", aoe2_match_id=1, player_number=2,
	         metric="knights", value=7.0)
	asyncio.run(backfill.drain_game_labels())

	got = {(r["player_number"], r["label"]): json.loads(r["evidence"])
	       for r in fake.rows("game_labels")}
	assert got == {(1, "archer_rush"): {"archers": 12.0},
	               (2, "knight_rush"): {"knights": 7.0}}


def test_played_at_of_returns_none_when_the_match_has_no_epoch():
	assert backfill.played_at_of([]) is None
	assert backfill.played_at_of([dict(played_at=None), dict(played_at=99)]) == 99


# ── logging ──────────────────────────────────────────────────────────────

def test_batch_logs_one_line_with_the_real_counts(monkeypatch):
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	logs = _RecordingLog()
	monkeypatch.setattr(backfill, "log", logs)
	for mid in range(1, 31):
		_match_with_players(fake, mid, players=2)

	asyncio.run(backfill.drain_game_stats(computed_at=1))

	assert len(logs.info_lines) == 1
	line = logs.info_lines[0]
	assert f"{backfill.BATCH} matches processed" in line
	assert f"{backfill.BATCH * 2} rows written" in line
	assert f"{30 - backfill.BATCH} still pending" in line


def test_nothing_pending_logs_nothing(monkeypatch):
	# The converged steady state is the ONE silent case: nothing processed,
	# nothing failed, nothing pending. It still computes the total (see
	# test_a_fully_quarantined_backlog_is_never_silent) -- it just reports it at
	# debug, because an identical line every POLL_INTERVAL forever carries no
	# information a reader does not already have.
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	logs = _RecordingLog()
	monkeypatch.setattr(backfill, "log", logs)

	assert asyncio.run(backfill.drain_game_stats(computed_at=1)) == 0
	assert asyncio.run(backfill.drain_game_labels()) == 0
	assert logs.info_lines == []


def test_a_fully_quarantined_backlog_is_never_silent(monkeypatch):
	# The hole this must never hide: every remaining pending match has failed
	# MAX_ATTEMPTS times, so the batch query returns nothing and the drain
	# processes nothing -- but the backlog is real and permanent. Returning early
	# on an empty batch made that indistinguishable from convergence, with no log
	# line anywhere. The line must still be emitted, must carry the real total,
	# and must say the total is quarantined rather than merely "pending".
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	logs = _RecordingLog()
	monkeypatch.setattr(backfill, "log", logs)
	for mid in (1, 2):
		_match_with_players(fake, mid, players=2)
	fake.fail_on.update({1, 2})

	for _ in range(backfill.MAX_ATTEMPTS):
		asyncio.run(backfill.drain_game_stats(computed_at=1))
	assert asyncio.run(backfill.pending_game_stats()) == []   # nothing attemptable left
	logs.info_lines.clear()

	# A pass that picks up nothing at all, with the backlog fully quarantined.
	assert asyncio.run(backfill.drain_game_stats(computed_at=1)) == 0

	assert len(logs.info_lines) == 1
	line = logs.info_lines[0]
	assert "0 matches processed" in line
	assert "2 still pending" in line
	assert "2 of those quarantined" in line
	assert "0 attemptable" in line


def test_the_pending_total_separates_quarantined_from_attemptable(monkeypatch):
	# Two failing matches and one healthy one: the log must not blur them into a
	# single "3 pending" that looks like ordinary backlog.
	fake = _SqliteDB()
	_install(monkeypatch, fake)
	logs = _RecordingLog()
	monkeypatch.setattr(backfill, "log", logs)
	for mid in (1, 2, 3):
		_match_with_labels(fake, mid, ["archer_rush"])
	fake.fail_on.update({1, 2})

	for _ in range(backfill.MAX_ATTEMPTS):
		asyncio.run(backfill.drain_game_labels())

	line = logs.info_lines[-1]
	assert "2 still pending" in line
	assert "2 of those quarantined" in line
	assert "0 attemptable" in line
	# The healthy match was written and left the backlog entirely.
	assert [r["replay_match_id"] for r in fake.rows("game_labels")] == [3]


# ── tick integration ─────────────────────────────────────────────────────

class _ExplodingDB:
	async def fetchall(self, *_a, **_k):
		raise RuntimeError("database is gone")

	fetchone = fetchall
	execute = fetchall
	insert_many = fetchall


def _run_think(job, frame_time):
	"""think() only schedules, so the spawned task has to be awaited before the
	loop closes — otherwise the assertions race the work."""
	async def go():
		await job.think(frame_time)
		await asyncio.gather(*list(backfill._tasks), return_exceptions=True)
	asyncio.run(go())
	backfill._tasks.clear()


def test_think_never_raises_when_every_query_fails(monkeypatch):
	monkeypatch.setattr(backfill, "db", _ExplodingDB())
	logs = _RecordingLog()
	monkeypatch.setattr(backfill, "log", logs)

	_run_think(backfill.DerivedBackfill(), 1000.0)   # must not raise

	# Both drains reported their own failure; neither cost the other its pass.
	assert len(logs.error_lines) == 2


def test_think_swallows_a_failure_in_its_own_body(monkeypatch):
	# Distinct from the test above, which only exercises failures inside the
	# spawned _run task. think()'s own try/except covers the scheduling code --
	# create_task raising under memory pressure, a patched asyncio, a bad
	# frame_time -- and on_think has no guard of its own, so anything escaping
	# here takes down the whole 1s tick for every other job in it.
	logs = _RecordingLog()
	monkeypatch.setattr(backfill, "log", logs)

	class _BrokenAsyncio:
		@staticmethod
		def create_task(coro):
			coro.close()   # never scheduled; keeps "coroutine was never awaited" quiet
			raise RuntimeError("cannot schedule")

	monkeypatch.setattr(backfill, "asyncio", _BrokenAsyncio)
	job = backfill.DerivedBackfill()

	asyncio.run(job.think(1000.0))   # must not raise

	assert job._running is False     # and must not wedge the job shut forever
	assert len(logs.error_lines) == 1
	assert "think()" in logs.error_lines[0]

	# Proof it is not wedged: with asyncio restored the next due pass runs.
	monkeypatch.undo()
	logs2 = _RecordingLog()
	monkeypatch.setattr(backfill, "log", logs2)
	calls = []

	async def _noop():
		calls.append(1)

	monkeypatch.setattr(backfill, "drain_game_stats", _noop)
	monkeypatch.setattr(backfill, "drain_game_labels", _noop)
	_run_think(job, 1000.0 + backfill.POLL_INTERVAL)
	assert calls == [1, 1]


def test_think_is_cadence_gated_and_non_overlapping(monkeypatch):
	calls = []

	async def _stats():
		calls.append("stats")

	async def _labels():
		calls.append("labels")

	monkeypatch.setattr(backfill, "drain_game_stats", _stats)
	monkeypatch.setattr(backfill, "drain_game_labels", _labels)
	job = backfill.DerivedBackfill()

	_run_think(job, 1000.0)
	_run_think(job, 1000.0 + backfill.POLL_INTERVAL - 1)   # too soon, skipped
	_run_think(job, 1000.0 + backfill.POLL_INTERVAL)

	assert calls == ["stats", "labels", "stats", "labels"]
	assert job._running is False
