"""Unit tests for the retention sweeper (bot/derived/sweeper.py).

The bar here is deliberately higher than anywhere else in this repo, because
this is the only module whose failure mode is data that cannot be got back. Four
things shape the file.

1. THE PREDICATE IS THE FEATURE, and it is SQL. A fake returning canned
   candidate ids would test the chunking glue and leave the actual eligibility
   query -- "no link is full" vs "some link is lean", `<` vs `>` on the window,
   whether a missing summary reads as fresh -- completely unguarded. The fake
   adapter below runs the real SQL against an in-memory sqlite3 database
   (stdlib, never a real server), translating only MySQL's %s placeholders.

2. EVERY GUARD TEST NEEDS A POSITIVE CONTROL BESIDE IT. A test asserting "these
   rows survived" passes just as well when the sweeper is broken and sweeps
   NOTHING, which is exactly the state a nervous edit tends to produce. So each
   negative case is written as a pair: the same fixture, one field changed,
   asserting the rows go. test_the_baseline_fixture_is_swept is the anchor for
   all of them.

3. THE FIVE-TABLE BLAST RADIUS IS ASSERTED AGAINST THE DURABLE SPINE, not
   against a list repeated from the module. A live sweep runs and every
   never-swept table is counted before and after.

4. There is no pytest-asyncio in this repo, so an `async def test_...` is
   silently SKIPPED and reports as passing. Every test here is sync and drives
   the coroutines with asyncio.run().
"""
import asyncio
import sqlite3

import bot.derived.sweeper as sweeper
from core.data_registry import REGISTRY

_SCHEMA = """
CREATE TABLE communities (
	community_id INTEGER PRIMARY KEY, guild_id INTEGER, name TEXT, retention TEXT, created_at INTEGER);
CREATE TABLE match_replays (
	community_id INTEGER, match_id INTEGER, replay_match_id INTEGER, linked_at INTEGER,
	PRIMARY KEY (community_id, match_id));
CREATE TABLE player_rollups (
	community_id INTEGER, user_id INTEGER, games INTEGER NOT NULL, rollup TEXT NOT NULL,
	computed_at INTEGER NOT NULL, PRIMARY KEY (community_id, user_id));
CREATE TABLE metric_boards (
	community_id INTEGER, metric_id TEXT, board TEXT NOT NULL, computed_at INTEGER NOT NULL,
	PRIMARY KEY (community_id, metric_id));
CREATE TABLE civ_stats (
	community_id INTEGER, civ TEXT, games INTEGER NOT NULL, wins INTEGER NOT NULL,
	losses INTEGER NOT NULL, computed_at INTEGER NOT NULL, PRIMARY KEY (community_id, civ));
CREATE TABLE game_stats (
	replay_match_id INTEGER, player_number INTEGER, profile_id INTEGER, has_production INTEGER NOT NULL,
	computed_at INTEGER, PRIMARY KEY (replay_match_id, player_number));
CREATE TABLE game_labels (
	replay_match_id INTEGER, player_number INTEGER, label TEXT, kind TEXT, played_at INTEGER,
	PRIMARY KEY (replay_match_id, player_number, label));
CREATE TABLE replay_matches (
	replay_match_id INTEGER PRIMARY KEY, bot_match_id INTEGER, played_at TEXT);
CREATE TABLE replay_players (
	replay_match_id INTEGER, profile_id INTEGER, player_number INTEGER, eapm INTEGER,
	PRIMARY KEY (replay_match_id, profile_id));
CREATE TABLE replay_events (
	replay_match_id INTEGER, player_number INTEGER, seq INTEGER, t_s INTEGER,
	PRIMARY KEY (replay_match_id, player_number, seq));
CREATE TABLE replay_techs (
	replay_match_id INTEGER, player_number INTEGER, tech TEXT, click_s INTEGER,
	PRIMARY KEY (replay_match_id, player_number, tech));
CREATE TABLE replay_buildings (
	replay_match_id INTEGER, player_number INTEGER, building TEXT, count INTEGER,
	PRIMARY KEY (replay_match_id, player_number, building));
CREATE TABLE replay_units (
	replay_match_id INTEGER, player_number INTEGER, unit TEXT, total INTEGER,
	PRIMARY KEY (replay_match_id, player_number, unit));
CREATE TABLE replay_apm (
	replay_match_id INTEGER, player_number INTEGER, minute INTEGER, actions INTEGER,
	PRIMARY KEY (replay_match_id, player_number, minute));
"""

NOW = 1_700_000_000            # an ordinary epoch, so the window arithmetic is realistic
DAY = 86400
OLD_LINK = NOW - 60 * DAY      # comfortably outside RETENTION_DAYS
FRESH_LINK = NOW - 5 * DAY     # comfortably inside it
COMPUTED = OLD_LINK + 1000     # a summary computed after the old link

# Every table the sweeper must never touch, named here independently of the
# module under test so a test cannot agree with a mutated NEVER_SWEPT.
SPINE = ("replay_matches", "replay_players", "game_stats", "game_labels",
         "match_replays", "communities", "player_rollups", "metric_boards", "civ_stats")


class _SqliteDB:
	"""Stand-in for core.database.db backed by in-memory sqlite3. Never touches a
	real database. Only the four methods the sweeper uses are implemented, so a
	sweeper that started writing through some other adapter method would fail
	loudly here rather than being silently unexercised."""

	def __init__(self):
		self.conn = sqlite3.connect(":memory:")
		self.conn.row_factory = sqlite3.Row
		self.conn.executescript(_SCHEMA)
		self.executed = []

	# ── fixture helpers ───────────────────────────────────────────────────
	def add(self, table, **row):
		cols = ",".join(f"`{c}`" for c in row)
		holes = ",".join(["?"] * len(row))
		self.conn.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({holes})", list(row.values()))

	def count(self, table, **where):
		sql = f"SELECT COUNT(*) FROM {table}"
		if where:
			sql += " WHERE " + " AND ".join(f"`{c}`=?" for c in where)
		return self.conn.execute(sql, list(where.values())).fetchone()[0]

	def counts(self, tables):
		return {t: self.count(t) for t in tables}

	# ── the adapter surface ───────────────────────────────────────────────
	@staticmethod
	def _sql(sql):
		return sql.replace("%s", "?")

	async def fetchall(self, sql, args=None):
		return [dict(r) for r in self.conn.execute(self._sql(sql), list(args or [])).fetchall()]

	async def fetchone(self, sql, args=None):
		row = self.conn.execute(self._sql(sql), list(args or [])).fetchone()
		return dict(row) if row is not None else None

	async def execute(self, sql, args=None):
		self.executed.append((sql, list(args or [])))
		self.conn.execute(self._sql(sql), list(args or []))


class _ExplodingDB:
	"""Every call raises. Used to prove think() cannot carry a failure into
	on_think, which is the property the whole tick depends on."""

	def __getattr__(self, _name):
		async def _boom(*_a, **_k):
			raise RuntimeError("database is down")
		return _boom


class _RecordingLog:
	def __init__(self):
		self.info_lines = []
		self.error_lines = []
		self.debug_lines = []

	def info(self, message):
		self.info_lines.append(message)

	def error(self, message):
		self.error_lines.append(message)

	def debug(self, message):
		self.debug_lines.append(message)


def _fresh(dry_run=True):
	db, log = _SqliteDB(), _RecordingLog()
	sweeper.db = db
	sweeper.log = log
	sweeper.DRY_RUN = dry_run
	return db, log


def _community(db, community_id=1, retention="lean", *, boards_at=COMPUTED, rollups_at=COMPUTED,
               civ_at=COMPUTED, boards=True):
	db.add("communities", community_id=community_id, guild_id=community_id * 7,
	       name=f"C{community_id}", retention=retention, created_at=NOW - 400 * DAY)
	if boards:
		db.add("metric_boards", community_id=community_id, metric_id="fastest_castle",
		       board="{}", computed_at=boards_at)
	if rollups_at is not None:
		db.add("player_rollups", community_id=community_id, user_id=1, games=3, rollup="{}",
		       computed_at=rollups_at)
	if civ_at is not None:
		db.add("civ_stats", community_id=community_id, civ="Franks", games=3, wins=2, losses=1,
		       computed_at=civ_at)


def _link(db, replay_match_id, community_id=1, linked_at=OLD_LINK, match_id=None):
	db.add("match_replays", community_id=community_id, match_id=match_id or replay_match_id,
	       replay_match_id=replay_match_id, linked_at=linked_at)


def _detail(db, replay_match_id, *, game_stats=True):
	"""One replay's rows: the spine, the derived-global summary, and one row in
	each of the five bulky child tables."""
	db.add("replay_matches", replay_match_id=replay_match_id, bot_match_id=replay_match_id,
	       played_at="2025-01-01")
	db.add("replay_players", replay_match_id=replay_match_id, profile_id=11, player_number=1, eapm=100)
	if game_stats:
		db.add("game_stats", replay_match_id=replay_match_id, player_number=1, profile_id=11,
		       has_production=1, computed_at=COMPUTED)
		db.add("game_labels", replay_match_id=replay_match_id, player_number=1, label="archer_rush",
		       kind="strategy", played_at=NOW)
	db.add("replay_events", replay_match_id=replay_match_id, player_number=1, seq=0, t_s=10)
	db.add("replay_techs", replay_match_id=replay_match_id, player_number=1, tech="loom", click_s=5)
	db.add("replay_buildings", replay_match_id=replay_match_id, player_number=1, building="House", count=3)
	db.add("replay_units", replay_match_id=replay_match_id, player_number=1, unit="Archer", total=12)
	db.add("replay_apm", replay_match_id=replay_match_id, player_number=1, minute=0, actions=40)


def _baseline(db):
	"""The one fixture every eligibility test perturbs: a single lean community,
	one replay linked to it 60 days ago, whose summary was computed after that
	link. Eligible in full."""
	_community(db)
	_link(db, 1)
	_detail(db, 1)


def _sweep(now=NOW):
	return asyncio.run(sweeper.sweep(now))


def _detail_counts(db):
	return db.counts(sweeper.TARGET_TABLES)


# ── the anchor ────────────────────────────────────────────────────────────────

def test_the_baseline_fixture_is_swept():
	"""The positive control every guard test below is measured against. Without
	it, "the rows survived" passes trivially for a sweeper that never sweeps --
	which is the shape a broken predicate most often takes."""
	db, _log = _fresh(dry_run=False)
	_baseline(db)

	considered, counts, deleted = _sweep()

	assert considered == 1
	assert deleted is True
	assert counts == dict.fromkeys(sweeper.TARGET_TABLES, 1)
	assert _detail_counts(db) == dict.fromkeys(sweeper.TARGET_TABLES, 0)


# ── (1) the lean-only guard ───────────────────────────────────────────────────

def test_a_full_retention_community_is_never_swept():
	"""Today's production shape: the only community (AOE2-GUYSz) is retention
	'full', so the correct candidate count is zero."""
	db, _log = _fresh(dry_run=False)
	_community(db, retention="full")
	_link(db, 1)
	_detail(db, 1)

	considered, _counts, deleted = _sweep()

	assert considered == 0
	assert deleted is False
	assert _detail_counts(db) == dict.fromkeys(sweeper.TARGET_TABLES, 1)


def test_a_replay_linked_to_both_a_lean_and_a_full_community_is_kept():
	"""MUTANT GUARD (a): `WHERE retention = 'lean'` on any single link, rather
	than "no link is full".

	This is the case that costs the flagship its history rather than a partner's:
	a match both communities reported is linked twice, and a predicate that fires
	on the lean link alone deletes rows the full community is paying to keep. The
	lean link is deliberately the FIRST one by community_id, so an implementation
	that stops at the first matching row is caught too."""
	db, _log = _fresh(dry_run=False)
	_community(db, 1, retention="lean")
	_community(db, 2, retention="full")
	_link(db, 1, community_id=1)
	_link(db, 1, community_id=2)
	_detail(db, 1)

	considered, _counts, deleted = _sweep()

	assert considered == 0, "a replay any full community keeps must never be a candidate"
	assert deleted is False
	assert _detail_counts(db) == dict.fromkeys(sweeper.TARGET_TABLES, 1)


def test_the_second_of_two_lean_communities_still_sweeps():
	"""The positive half of the pair above: two links are not themselves
	disqualifying, only a non-lean one is."""
	db, _log = _fresh(dry_run=False)
	_community(db, 1, retention="lean")
	_community(db, 2, retention="lean")
	_link(db, 1, community_id=1)
	_link(db, 1, community_id=2)
	_detail(db, 1)

	considered, _counts, _deleted = _sweep()

	assert considered == 1
	assert _detail_counts(db) == dict.fromkeys(sweeper.TARGET_TABLES, 0)


def test_a_link_to_an_unknown_community_is_kept():
	"""A link whose communities row is missing is a broken invariant, and the
	safe reading of a broken invariant is "not known to be lean". An INNER JOIN
	to communities would drop that link and let the remaining lean one decide."""
	db, _log = _fresh(dry_run=False)
	_community(db, 1, retention="lean")
	_link(db, 1, community_id=1)
	_link(db, 1, community_id=99)      # no communities row
	_detail(db, 1)

	considered, _counts, _deleted = _sweep()

	assert considered == 0
	assert _detail_counts(db) == dict.fromkeys(sweeper.TARGET_TABLES, 1)


# ── (2) the retention window ──────────────────────────────────────────────────

def test_a_recently_linked_replay_is_kept():
	"""MUTANT GUARD (b): the window comparison inverted."""
	db, _log = _fresh(dry_run=False)
	_community(db)
	_link(db, 1, linked_at=FRESH_LINK)
	_detail(db, 1)

	considered, _counts, _deleted = _sweep()

	assert considered == 0
	assert _detail_counts(db) == dict.fromkeys(sweeper.TARGET_TABLES, 1)


def test_the_window_boundary_is_exclusive_on_both_sides():
	"""Exactly RETENTION_DAYS old is NOT yet eligible; one second older is. Pins
	the `<` so a drift to `<=` or a day-count change shows up here."""
	edge = NOW - sweeper.RETENTION_DAYS * DAY
	for linked_at, expected in ((edge, 0), (edge - 1, 1)):
		db, _log = _fresh(dry_run=False)
		# Summary stamped NOW so only the window is in play: the default fixture's
		# summary predates a link this recent and would veto both halves.
		_community(db, boards_at=NOW, rollups_at=NOW, civ_at=NOW)
		_link(db, 1, linked_at=linked_at)
		_detail(db, 1)
		considered, _counts, _deleted = _sweep()
		assert considered == expected, f"linked_at={linked_at} should give {expected} candidates"


def test_the_window_is_measured_from_the_newest_link():
	"""A replay re-linked yesterday is fresh however old its first link is. MIN
	rather than MAX here would sweep a match a community only just adopted.

	Both summaries are stamped NOW so the WINDOW is the only thing under test:
	with the default fixture's summary the fresh link is also stale against
	condition (3), and this test would pass with the window removed entirely."""
	db, _log = _fresh(dry_run=False)
	_community(db, 1, boards_at=NOW, rollups_at=NOW, civ_at=NOW)
	_community(db, 2, boards_at=NOW, rollups_at=NOW, civ_at=NOW)
	_link(db, 1, community_id=1, linked_at=OLD_LINK)
	_link(db, 1, community_id=2, linked_at=FRESH_LINK)
	_detail(db, 1)

	considered, _counts, _deleted = _sweep()

	assert considered == 0
	assert _detail_counts(db) == dict.fromkeys(sweeper.TARGET_TABLES, 1)


def test_two_links_within_one_community_use_the_newer_one():
	"""match_replays' PK is (community_id, match_id), so one community can link
	the same replay twice -- a re-report or a corrected pairing. The newer link
	governs, or a re-report would be swept on the strength of a link it
	superseded."""
	db, _log = _fresh(dry_run=False)
	_community(db)
	_link(db, 1, community_id=1, match_id=500, linked_at=OLD_LINK)
	_link(db, 1, community_id=1, match_id=501, linked_at=FRESH_LINK)
	_detail(db, 1)

	considered, _counts, _deleted = _sweep()

	assert considered == 0
	assert _detail_counts(db) == dict.fromkeys(sweeper.TARGET_TABLES, 1)


def test_a_null_linked_at_is_kept():
	"""Unknown is not old. Treating NULL as epoch 0 would make every unstamped
	link instantly eligible.

	Both the all-NULL and the mixed shape are covered. The mixed one is the
	interesting half: SQL's MAX() SKIPS nulls, so a replay with one NULL link and
	one ancient link has a perfectly ordinary-looking MAX(linked_at) and passes
	the window on the strength of a link that says nothing about the NULL one."""
	for links in ((None,), (None, OLD_LINK)):
		db, _log = _fresh(dry_run=False)
		_community(db, 1, boards_at=NOW, rollups_at=NOW, civ_at=NOW)
		_community(db, 2, boards_at=NOW, rollups_at=NOW, civ_at=NOW)
		for community_id, linked_at in enumerate(links, start=1):
			_link(db, 1, community_id=community_id, linked_at=linked_at)
		_detail(db, 1)

		considered, _counts, _deleted = _sweep()

		assert considered == 0, f"links={links} must not be swept"
		assert _detail_counts(db) == dict.fromkeys(sweeper.TARGET_TABLES, 1)


# ── (3) the summary must demonstrably exist ───────────────────────────────────

def test_a_community_with_no_summary_at_all_is_kept():
	"""MUTANT GUARD (c): dropping the computed_at check. A community that has
	never had a refresh pass has nothing standing in for the detail."""
	db, _log = _fresh(dry_run=False)
	_community(db, boards=False, rollups_at=None, civ_at=None)
	_link(db, 1)
	_detail(db, 1)

	considered, _counts, _deleted = _sweep()

	assert considered == 0
	assert _detail_counts(db) == dict.fromkeys(sweeper.TARGET_TABLES, 1)


def test_a_community_with_rollups_but_no_boards_is_kept():
	"""metric_boards is the existence proof (bot/derived/refresh.py uses the same
	marker for the same reason): player_rollups and civ_stats can each
	legitimately be empty, so neither can distinguish "computed, nothing to say"
	from "never computed"."""
	db, _log = _fresh(dry_run=False)
	_community(db, boards=False)
	_link(db, 1)
	_detail(db, 1)

	considered, _counts, _deleted = _sweep()

	assert considered == 0
	assert _detail_counts(db) == dict.fromkeys(sweeper.TARGET_TABLES, 1)


def test_a_summary_computed_before_the_link_is_kept():
	"""MUTANT GUARD (c): the summary exists, but it was computed before this
	match was linked -- so it cannot possibly include this match."""
	db, _log = _fresh(dry_run=False)
	_community(db, boards_at=OLD_LINK - 1, rollups_at=OLD_LINK - 1, civ_at=OLD_LINK - 1)
	_link(db, 1)
	_detail(db, 1)

	considered, _counts, _deleted = _sweep()

	assert considered == 0
	assert _detail_counts(db) == dict.fromkeys(sweeper.TARGET_TABLES, 1)


def test_a_summary_computed_exactly_at_the_link_is_kept():
	"""`>` not `>=`: a summary stamped in the same second as the link may have
	read the match's rows a moment before they were linked."""
	db, _log = _fresh(dry_run=False)
	_community(db, boards_at=OLD_LINK, rollups_at=OLD_LINK, civ_at=OLD_LINK)
	_link(db, 1)
	_detail(db, 1)

	considered, _counts, _deleted = _sweep()

	assert considered == 0
	assert _detail_counts(db) == dict.fromkeys(sweeper.TARGET_TABLES, 1)


def test_the_stalest_derived_table_decides():
	"""MIN over all three derived-community tables, not MAX and not any one of
	them: a freshly-rebuilt civ_stats row must not vouch for a player_rollups row
	that predates the link."""
	db, _log = _fresh(dry_run=False)
	_community(db, boards_at=COMPUTED, civ_at=COMPUTED, rollups_at=OLD_LINK - 1)
	_link(db, 1)
	_detail(db, 1)

	considered, _counts, _deleted = _sweep()

	assert considered == 0
	assert _detail_counts(db) == dict.fromkeys(sweeper.TARGET_TABLES, 1)


def test_every_linked_community_must_have_a_fresh_summary():
	"""Not just one of them. Two lean communities, one caught up and one not: the
	replay is the second community's only copy of that detail too."""
	db, _log = _fresh(dry_run=False)
	_community(db, 1)
	_community(db, 2, boards_at=OLD_LINK - 1, rollups_at=OLD_LINK - 1, civ_at=OLD_LINK - 1)
	_link(db, 1, community_id=1)
	_link(db, 1, community_id=2)
	_detail(db, 1)

	considered, _counts, _deleted = _sweep()

	assert considered == 0
	assert _detail_counts(db) == dict.fromkeys(sweeper.TARGET_TABLES, 1)


def test_a_summary_is_compared_against_its_own_communitys_link():
	"""Each community's summary is compared against THAT community's link, not
	against the newest link overall. Community 2 linked recently and has a
	summary newer than its own link; community 1 is stale against its own. A
	comparison against a single global MAX(linked_at) would pass both."""
	db, _log = _fresh(dry_run=False)
	_community(db, 1, boards_at=OLD_LINK - 1, rollups_at=OLD_LINK - 1, civ_at=OLD_LINK - 1)
	_community(db, 2, boards_at=NOW, rollups_at=NOW, civ_at=NOW)
	_link(db, 1, community_id=1, linked_at=OLD_LINK)
	_link(db, 1, community_id=2, linked_at=OLD_LINK - 10 * DAY)
	_detail(db, 1)

	considered, _counts, _deleted = _sweep()

	assert considered == 0
	assert _detail_counts(db) == dict.fromkeys(sweeper.TARGET_TABLES, 1)


# ── (4) the derived-global summary must exist ─────────────────────────────────

def test_a_match_with_no_game_stats_is_kept():
	"""game_stats is what replaces replay_units (top_units) and replay_apm
	(peak_eapm). A match with no game_stats row has had no summary computed at
	all, so its detail is the only copy of anything about it."""
	db, _log = _fresh(dry_run=False)
	_community(db)
	_link(db, 1)
	_detail(db, 1, game_stats=False)

	considered, _counts, _deleted = _sweep()

	assert considered == 0
	assert _detail_counts(db) == dict.fromkeys(sweeper.TARGET_TABLES, 1)


# ── (5) convergence ───────────────────────────────────────────────────────────

def test_a_swept_replay_stops_being_a_candidate():
	"""Without the "still has rows" clause an already-swept replay stays eligible
	forever and the daily candidate count -- the one number a human reads before
	flipping DRY_RUN -- never falls."""
	db, _log = _fresh(dry_run=False)
	_baseline(db)

	assert _sweep()[0] == 1
	assert _sweep()[0] == 0, "a replay with nothing left to delete must not be a candidate"


def test_a_replay_with_rows_in_only_one_target_table_is_still_a_candidate():
	"""The convergence clause is an OR across all five tables, not an AND: a
	match that only ever produced replay_apm buckets must still be sweepable."""
	db, _log = _fresh(dry_run=False)
	_community(db)
	_link(db, 1)
	_detail(db, 1)
	for table in ("replay_events", "replay_techs", "replay_buildings", "replay_units"):
		db.conn.execute(f"DELETE FROM {table}")

	considered, counts, _deleted = _sweep()

	assert considered == 1
	assert counts["replay_apm"] == 1
	assert db.count("replay_apm") == 0


# ── DRY_RUN ───────────────────────────────────────────────────────────────────

def test_the_module_ships_with_dry_run_true():
	"""MUTANT GUARD (d), and the deploy contract: flipping this constant must be
	a change that a test notices, in a commit of its own. Read from the source
	file rather than the imported module because the fixtures above mutate the
	attribute at runtime."""
	source = open(sweeper.__file__, encoding="utf-8").read()  # noqa: SIM115
	assert "\nDRY_RUN = True\n" in source, "the committed default must be DRY_RUN = True"


def test_dry_run_deletes_nothing_but_still_reports_real_counts():
	"""MUTANT GUARD (d): DRY_RUN ignored. The counts must be real -- they are the
	whole point of the first deploy -- while every row stays put."""
	db, log = _fresh(dry_run=True)
	_community(db)
	for mid in (1, 2):
		_link(db, mid)
		_detail(db, mid)
	db.add("replay_events", replay_match_id=1, player_number=1, seq=1, t_s=20)

	considered, counts, deleted = _sweep()

	assert considered == 2
	assert deleted is False
	assert counts["replay_events"] == 3, "counts must be measured, not assumed"
	assert counts["replay_apm"] == 2
	assert _detail_counts(db) == {"replay_events": 3, "replay_techs": 2, "replay_buildings": 2,
	                              "replay_units": 2, "replay_apm": 2}
	assert db.executed == [], "DRY_RUN must not issue a single write statement"
	assert len(log.info_lines) == 1
	line = log.info_lines[0]
	assert "DRY RUN" in line and "nothing was deleted" in line
	assert "2 replays eligible" in line and "11 rows WOULD be deleted" in line
	assert "replay_events 3" in line and "replay_apm 2" in line
	assert "lean=1" in line


def test_dry_run_reports_a_line_even_when_nothing_is_eligible():
	"""On today's production data the candidate count is 0 because the only
	community is retention 'full' -- and that line IS the deploy's deliverable,
	so it must be emitted at info rather than swallowed as a quiet steady
	state."""
	db, log = _fresh(dry_run=True)
	_community(db, retention="full")
	_link(db, 1)
	_detail(db, 1)

	considered, counts, deleted = _sweep()

	assert (considered, deleted) == (0, False)
	assert counts == dict.fromkeys(sweeper.TARGET_TABLES, 0)
	assert len(log.info_lines) == 1
	assert "0 replays eligible" in log.info_lines[0]
	assert "full=1" in log.info_lines[0]


# ── the blast radius ──────────────────────────────────────────────────────────

def test_a_live_sweep_leaves_every_never_swept_table_untouched():
	"""MUTANT GUARD (e): an ineligible table in the target list. Asserted against
	an independently written spine list, so a mutated NEVER_SWEPT cannot make
	this test agree with the module."""
	db, _log = _fresh(dry_run=False)
	_baseline(db)
	before = db.counts(SPINE)
	assert all(before.values()), "the fixture must actually populate every spine table"

	_sweep()

	assert db.counts(SPINE) == before
	assert _detail_counts(db) == dict.fromkeys(sweeper.TARGET_TABLES, 0)


def test_only_the_five_child_tables_are_ever_written_to():
	"""Every statement the sweeper issues is inspected: five DELETEs, one per
	target table, and nothing else."""
	db, _log = _fresh(dry_run=False)
	_baseline(db)

	_sweep()

	assert len(db.executed) == 5
	for (sql, _args), table in zip(db.executed, sweeper.TARGET_TABLES):
		assert sql.startswith(f"DELETE FROM {table} WHERE replay_match_id IN ("), sql
	assert not any(t in sql for sql, _ in db.executed for t in SPINE)


def test_target_tables_are_exactly_the_five_bulky_child_tables():
	assert sweeper.TARGET_TABLES == (
		"replay_events", "replay_techs", "replay_buildings", "replay_units", "replay_apm")
	assert not set(sweeper.TARGET_TABLES) & set(SPINE)


# ── the registry lock ─────────────────────────────────────────────────────────

def test_the_registry_and_the_target_list_agree_in_both_directions():
	"""MUTANT GUARD (e). The lock is two-way on purpose: widening the blast
	radius needs an edit in this module AND one in core/data_registry.py, and the
	state in between deletes nothing at all."""
	assert sweeper.targets() == sweeper.TARGET_TABLES
	registry_sweepable = {n for n, m in REGISTRY.items() if m.get("retention") == "sweepable"}
	assert registry_sweepable == set(sweeper.TARGET_TABLES)
	for name in sweeper.NEVER_SWEPT:
		assert REGISTRY.get(name, {}).get("retention") == "forever", name


def test_targets_refuses_a_never_swept_table(monkeypatch):
	monkeypatch.setattr(sweeper, "TARGET_TABLES", sweeper.TARGET_TABLES + ("replay_players",))
	try:
		sweeper.targets()
	except RuntimeError as e:
		assert "replay_players" in str(e)
	else:
		raise AssertionError("targets() must refuse a never-swept table")


def test_targets_refuses_a_table_the_registry_does_not_call_sweepable(monkeypatch):
	monkeypatch.setattr(sweeper, "TARGET_TABLES", sweeper.TARGET_TABLES + ("civ_picks",))
	try:
		sweeper.targets()
	except RuntimeError as e:
		assert "civ_picks" in str(e)
	else:
		raise AssertionError("targets() must refuse a table the registry does not call sweepable")


def test_targets_refuses_when_the_registry_marks_a_table_it_does_not_target(monkeypatch):
	monkeypatch.setattr(sweeper, "TARGET_TABLES", ("replay_events", "replay_techs"))
	try:
		sweeper.targets()
	except RuntimeError as e:
		assert "replay_apm" in str(e)
	else:
		raise AssertionError("targets() must refuse a registry that describes a policy nothing implements")


def test_a_disagreeing_target_list_disables_the_sweep_entirely(monkeypatch):
	"""The guard fails CLOSED: when nobody knows which tables are safe, nothing
	is deleted and the reason is logged."""
	db, log = _fresh(dry_run=False)
	_baseline(db)
	monkeypatch.setattr(sweeper, "TARGET_TABLES", sweeper.TARGET_TABLES + ("game_stats",))

	considered, counts, deleted = _sweep()

	assert (considered, counts, deleted) == (0, {}, False)
	assert db.executed == []
	assert db.counts(SPINE + sweeper.TARGET_TABLES[:5])["game_stats"] == 1
	assert any("DISABLED" in line for line in log.error_lines)


# ── the no-lean interlock ─────────────────────────────────────────────────────

def test_candidates_without_a_lean_community_are_treated_as_a_bug_and_not_deleted(monkeypatch):
	"""The brief's rule as an interlock rather than a note: on a deploy where no
	community is lean, a non-zero candidate count means condition (1) is broken.
	The one state that must not be survivable is "the guard is wrong" AND "the
	deletes are live", so this refuses to delete even with DRY_RUN off."""
	db, log = _fresh(dry_run=False)
	_community(db, retention="full")
	_link(db, 1)
	_detail(db, 1)

	async def _pretend(*_a, **_k):
		return [1]
	monkeypatch.setattr(sweeper, "candidates", _pretend)

	considered, counts, deleted = _sweep()

	assert (considered, counts, deleted) == (1, {}, False)
	assert db.executed == []
	assert _detail_counts(db) == dict.fromkeys(sweeper.TARGET_TABLES, 1)
	assert any("BUG in this job" in line for line in log.error_lines)


# ── chunking and batching ─────────────────────────────────────────────────────

def test_a_sweep_larger_than_one_chunk_deletes_every_replay(monkeypatch):
	"""Chunking must not lose replays off the end of a chunk boundary, and it
	must issue more than one statement per table -- the whole reason it exists is
	that one unbounded DELETE can stall the loop."""
	monkeypatch.setattr(sweeper, "CHUNK", 2)
	db, _log = _fresh(dry_run=False)
	_community(db)
	for mid in range(1, 8):
		_link(db, mid)
		_detail(db, mid)

	considered, counts, _deleted = _sweep()

	assert considered == 7
	assert counts == dict.fromkeys(sweeper.TARGET_TABLES, 7)
	assert _detail_counts(db) == dict.fromkeys(sweeper.TARGET_TABLES, 0)
	assert len(db.executed) == 4 * len(sweeper.TARGET_TABLES)   # ceil(7/2) chunks per table


def test_a_pass_never_considers_more_than_batch_replays(monkeypatch):
	monkeypatch.setattr(sweeper, "BATCH", 3)
	db, _log = _fresh(dry_run=False)
	_community(db)
	for mid in range(1, 8):
		_link(db, mid)
		_detail(db, mid)

	assert _sweep()[0] == 3
	assert db.count("replay_events") == 4


# ── tick isolation ────────────────────────────────────────────────────────────

def test_think_never_raises_into_the_tick():
	sweeper.db = _ExplodingDB()
	log = _RecordingLog()
	sweeper.log = log
	job = sweeper.RetentionSweeper()

	async def _drive():
		await job.think(NOW)
		for _ in range(5):          # let the scheduled task run and fail
			await asyncio.sleep(0)

	asyncio.run(_drive())

	assert job._running is False
	assert any("ignored" in line for line in log.error_lines)


def test_think_schedules_at_most_once_a_day():
	db, _log = _fresh(dry_run=True)
	_community(db, retention="full")
	job = sweeper.RetentionSweeper()

	async def _drive():
		runs = []
		job._run = lambda *_a, **_k: _record(runs)
		await job.think(NOW)
		for _ in range(3):
			await asyncio.sleep(0)
		await job.think(NOW + 60)
		for _ in range(3):
			await asyncio.sleep(0)
		await job.think(NOW + sweeper.POLL_INTERVAL + 1)
		for _ in range(3):
			await asyncio.sleep(0)
		return runs

	async def _record(runs):
		runs.append(1)

	assert len(asyncio.run(_drive())) == 2
	assert sweeper.POLL_INTERVAL == 86400
	assert sweeper.RETENTION_DAYS == 30


# ── wiring ────────────────────────────────────────────────────────────────────

def test_the_sweeper_is_wired_into_the_think_tick():
	import bot.derived as derived
	assert isinstance(derived.sweeper_jobs, sweeper.RetentionSweeper)
	events = open(_repo_path("bot/events.py"), encoding="utf-8").read()  # noqa: SIM115
	assert "bot.derived.sweeper_jobs.think(frame_time)" in events


def _repo_path(relative):
	import os
	return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), relative)
