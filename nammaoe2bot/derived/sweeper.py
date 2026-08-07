# -*- coding: utf-8 -*-
"""The retention sweeper -- the ONLY code in this project that permanently
destroys data.

Everything else that deletes is a rewrite: store.write_match deletes a match's
raw rows and immediately re-inserts them, the derived writers delete-then-insert
what they just computed, rollups.delete removes a row a later pass would rebuild
from the same inputs. Every one of those is recoverable by re-running the thing
that wrote it. This module is not. The rows it removes are the parsed remains of
a replay file that no longer exists anywhere: aoe.ms expires replays, and 1353
rows in `replay_ingest` already sit at `gave_up` for exactly that reason. There
is no re-parse, no backup path, and no second copy. A wrong predicate here is
not a bug that gets fixed next deploy; it is data that is gone.

Everything below is shaped by that asymmetry. Where a normal job would pick the
predicate that is correct, this one picks the predicate that is correct AND
whose every failure mode keeps rows rather than deletes them.

-- WHY IT EXISTS ------------------------------------------------------------

The flagship community keeps everything: replay_events alone is 591099 rows,
replay_techs 205466, replay_buildings 117892, replay_units 31046. That is a
deliberate, paid-for choice and nothing here changes it. The architecture is
being made portable to PARTNER communities, which must not inherit unbounded
storage growth as the price of installing the bot. `communities.retention` is
the switch: 'full' (the flagship, and anything in cfg.FLAGSHIP_GUILD_IDS) keeps
the detail forever; 'lean' (every new community's default) keeps the SUMMARY and
lets the detail age out. This job is the thing that makes 'lean' mean something.

-- WHAT IS ELIGIBLE, AND WHY THE LIST CANNOT BE WIDENED BY ACCIDENT ---------

Only the five bulky per-match CHILD tables in TARGET_TABLES. `replay_matches`
and `replay_players` are the durable spine -- one row per match and per player,
tiny, and the join target of everything downstream. game_stats/game_labels are
the derived-global summary and player_rollups/metric_boards/civ_stats are the
derived-community one; deleting any of those would delete the very thing that
justifies deleting the detail.

That list is not merely written down here, it is CHECKED, in both directions,
against nammaoe2bot/runtime/data_registry.py on every pass (see targets()). A table this module
targets must carry retention='sweepable' in the registry, and a table the
registry calls 'sweepable' must appear here. Widening the blast radius therefore
takes two edits in two files that already disagree with each other in between --
and if they ever do disagree, targets() raises, sweep() catches it, logs, and
deletes NOTHING. The failure mode of the guard is the guard being off.

-- THE PREDICATE ------------------------------------------------------------

A replay's child rows are eligible when ALL of the following hold. Each is
written so that missing or unexpected data fails CLOSED (keeps the rows):

  1. EVERY community the replay is linked to is retention='lean'.

     Written as "no link is non-lean", never as "some link is lean". Those are
     different queries and the difference is the flagship's entire history: a
     match that a partner community also reported is linked to BOTH, and
     `WHERE retention = 'lean'` matches it. The HAVING below counts the links
     that are NOT lean and requires zero of them, so a single 'full' link vetoes
     the whole replay no matter how many lean ones sit beside it.

     The join to `communities` is a LEFT JOIN and a NULL retention counts as
     non-lean. A link to a community row that does not exist is a broken
     invariant, and the safe reading of a broken invariant is "I do not know
     that this is lean".

  2. The NEWEST link is older than RETENTION_DAYS. MAX(linked_at), not MIN: a
     replay re-linked yesterday is fresh even if its first link was a year ago.
     A NULL linked_at disqualifies the replay outright rather than being treated
     as epoch 0, because "unknown" is not "old" -- and it does so whether the
     NULL sits beside another community's link or beside a second link inside
     the SAME community, which is why the check is applied twice: once in
     _LINKS_SQL's per-community collapse, where MAX() would otherwise skip the
     NULL and hide it, and once in the HAVING over the collapsed set.

  3. Every linked community's derived-community rows were computed AFTER that
     community's newest link -- the summary that replaces this detail
     demonstrably exists, and demonstrably saw this match.

     The freshness figure is MIN(computed_at) over all three derived-community
     tables for that community, and the EXISTENCE proof is metric_boards
     specifically. Same reasoning nammaoe2bot/derived/refresh.py gives for using
     metric_boards as its marker: a refresh pass always writes one board per
     catalogued metric, while player_rollups can be empty (a community whose
     players are all unlinked) and civ_stats can be empty (no resolved picks).
     A marker that can legitimately be empty cannot distinguish "computed, and
     the answer was nothing" from "never computed" -- and on this side of the
     project that distinction is the difference between a summary existing and
     the data being gone with nothing to show for it.

  4. The match has at least one game_stats row. Not in the brief; added because
     game_stats IS the derived-global summary of two of the five tables
     (top_units summarises replay_units, peak_eapm summarises replay_apm), and a
     match with no game_stats row has had no summary computed at all. Its cost
     is that a match which legitimately derives zero game_stats rows is never
     swept. That is a permanent retention of a handful of rows, which is the
     side of this trade worth being wrong on.

  5. At least one target table still HAS a row for the match. Pure convergence:
     without it, every already-swept replay stays "eligible" forever, the daily
     log line reports a candidate count that never falls, and the one number a
     human reads to decide whether to flip DRY_RUN becomes meaningless.

-- CADENCE AND ISOLATION ----------------------------------------------------

Daily, off-tick, and self-isolating in exactly the shape nammaoe2bot/derived/backfill.py
and nammaoe2bot/derived/refresh.py established: think() only schedules and can never
raise into on_think, _running keeps two passes from overlapping, and the pass
itself catches everything. Deletes are chunked (CHUNK ids per statement, BATCH
replays per pass) with a yield between chunks, so a first live sweep of a large
backlog cannot hold the event loop or the connection for an unbounded time.

Counts in the log are always REAL -- rows counted in the database, never rows
intended. In DRY_RUN they are what WOULD have gone; live they are what was
there immediately before the DELETE ran.
"""
import asyncio
import time

from nammaoe2bot.runtime.console import log
from nammaoe2bot.runtime.data_registry import REGISTRY
from nammaoe2bot.runtime.database import db

# ─────────────────────────────────────────────────────────────────────────────
#  D R Y _ R U N  --  the last thing standing between this module and
#  irreversible data loss.
#
#  While True: every pass computes the full candidate set, counts exactly how
#  many rows it would remove from each table, writes that to the log, and
#  DELETES NOTHING.
#
#  Setting this to False makes every subsequent pass issue real DELETE
#  statements against the five tables in TARGET_TABLES. Those rows cannot be
#  restored -- the replay files they were parsed from expire from aoe.ms, and
#  1353 replay_ingest rows are already `gave_up` because theirs are gone.
#
#  Flip it in a commit OF ITS OWN, containing nothing else, and only after a
#  human has read a live log line from this job and agrees with the counts it
#  reported. It is not a config var on purpose: turning permanent deletion on
#  should require a code review and a deploy, not an env var edit.
# ─────────────────────────────────────────────────────────────────────────────
DRY_RUN = True

RETENTION_DAYS = 30     # a lean community's replay detail lives this long past its link
POLL_INTERVAL = 86400   # daily; there is nothing time-critical about aging rows out
BATCH = 200             # replays considered per pass
CHUNK = 25              # replay ids per COUNT/DELETE statement (see the module docstring)

# The ONLY tables this job may ever delete from. Cross-checked against
# nammaoe2bot/runtime/data_registry.py by targets() on every pass -- read that function before
# touching this tuple, and read the module docstring before touching that.
TARGET_TABLES = ("replay_events", "replay_techs", "replay_buildings", "replay_units", "replay_apm")

# The durable spine and the summary. Named explicitly rather than left implicit
# in "not in TARGET_TABLES" so that a future edit which adds one of these to the
# target list fails loudly against a list that says why it must not, instead of
# quietly passing a registry lookup somebody also edited.
NEVER_SWEPT = frozenset({
	"replay_matches", "replay_players",          # raw spine: one row per match / per player
	"game_stats", "game_labels",                 # derived-global summary
	"player_rollups", "metric_boards", "civ_stats",  # derived-community summary
	"match_replays", "communities",              # the link and tenancy tables the predicate reads
})

_tasks = set()   # strong refs to in-flight sweep tasks (asyncio only keeps weak ones)


def targets():
	"""The tables this pass is permitted to delete from, or a raised exception.

	Two-way agreement with nammaoe2bot/runtime/data_registry.py, checked every pass rather than
	once at import: the registry is the project's single source of truth for what
	a table's retention CONTRACT is, and this module is the only thing that acts
	on it. If the two ever disagree, the honest reading is that nobody currently
	knows which tables are safe -- so this raises, sweep() catches it, and the
	pass deletes nothing. A guard whose failure mode is "sweeper off" is the only
	kind worth having here.

	The reverse direction (registry says sweepable, this module does not target
	it) is checked too. On its own it deletes nothing and is therefore harmless,
	but it means the registry is describing a retention policy that no code
	implements -- and the next person to read it would reasonably believe those
	rows are being aged out when they are not.
	"""
	forbidden = sorted(set(TARGET_TABLES) & NEVER_SWEPT)
	if forbidden:
		raise RuntimeError(f"sweeper TARGET_TABLES names never-swept tables: {forbidden}")
	unregistered = sorted(t for t in TARGET_TABLES if REGISTRY.get(t, {}).get("retention") != "sweepable")
	if unregistered:
		raise RuntimeError(f"sweeper targets tables the data registry does not call sweepable: {unregistered}")
	untargeted = sorted(
		name for name, meta in REGISTRY.items()
		if meta.get("retention") == "sweepable" and name not in TARGET_TABLES
	)
	if untargeted:
		raise RuntimeError(f"data registry calls tables sweepable that the sweeper does not target: {untargeted}")
	return TARGET_TABLES


# Per community: how fresh its derived-community layer is, and whether it has
# been computed at all. MIN over all three tables so the freshness figure is the
# STALEST thing the community stores -- a fresh civ_stats row must not vouch for
# a rollup that predates the link. `boards` is the existence proof; see the
# module docstring for why metric_boards specifically.
#
# No parentheses around the UNION ALL operands: MySQL accepts them, sqlite (which
# tests/test_sweeper.py runs this very SQL against) does not.
_SUMMARY_SQL = (
	"SELECT d.community_id AS community_id, MIN(d.computed_at) AS at, SUM(d.is_board) AS boards FROM ("
	"SELECT community_id, computed_at, 0 AS is_board FROM player_rollups "
	"UNION ALL SELECT community_id, computed_at, 1 AS is_board FROM metric_boards "
	"UNION ALL SELECT community_id, computed_at, 0 AS is_board FROM civ_stats"
	") d GROUP BY d.community_id"
)

# One row per (replay, community), carrying that community's NEWEST link to the
# replay. match_replays' PK is (community_id, match_id), which does not constrain
# replay_match_id -- two bot matches in one community can point at the same
# replay (a re-report, a corrected pairing) -- so without this collapse a replay
# would be judged once per bot match and the oldest of those links would decide.
#
# THE NULL CHECK LIVES HERE, INSIDE THE COLLAPSE, and not only in the HAVING that
# reads this. SQL's MAX() SKIPS nulls, so a group holding one NULL link and one
# 60-day-old link would emit a perfectly ordinary-looking `linked_at = OLD` and
# arrive at the outer query having forgotten the NULL entirely -- the exact
# re-report shape the paragraph above exists for, judged on the strength of a
# link that says nothing about the unstamped one. The CASE makes an unknown
# ANYWHERE in the group poison the whole group's answer, which is what "unknown
# is not old" has to mean per community as well as across communities.
#
# nammaoe2bot/community.py declares linked_at notnull=True, so this can only bite a
# database whose column predates that declaration -- _ensure_table never alters
# an existing column's nullability, so production's stays nullable permanently
# and this guard stays load-bearing rather than belt-and-braces.
_LINKS_SQL = (
	"SELECT replay_match_id, community_id, "
	"CASE WHEN COUNT(*) > COUNT(linked_at) THEN NULL ELSE MAX(linked_at) END AS linked_at "
	"FROM match_replays GROUP BY replay_match_id, community_id"
)


def _candidates_sql(tables):
	"""The eligibility query, generated from the verified target list.

	Generated rather than written out so condition (5) covers exactly the tables
	this pass would delete from -- a target list and a convergence check that can
	drift apart would leave a swept replay reported as eligible forever.

	Every condition lives in the HAVING over the replay's WHOLE set of links, not
	in a WHERE over one link at a time. That placement is the point: a WHERE
	filters rows out of the group, so a replay linked to one lean and one full
	community would arrive at the HAVING having already forgotten about the full
	one.
	"""
	still_has_rows = " OR ".join(
		f"EXISTS (SELECT 1 FROM {t} WHERE {t}.replay_match_id = link.replay_match_id)" for t in tables)
	return (
		"SELECT link.replay_match_id AS mid "
		f"FROM ({_LINKS_SQL}) link "
		"LEFT JOIN communities c ON c.community_id = link.community_id "
		f"LEFT JOIN ({_SUMMARY_SQL}) summary ON summary.community_id = link.community_id "
		# (4) the derived-global summary exists, and (5) there is still something to delete.
		"WHERE EXISTS (SELECT 1 FROM game_stats gs WHERE gs.replay_match_id = link.replay_match_id) "
		f"AND ({still_has_rows}) "
		"GROUP BY link.replay_match_id "
		# (1) NOT ONE of this replay's links is to a non-lean community. A NULL
		# retention (no communities row) counts as non-lean and vetoes too.
		"HAVING SUM(CASE WHEN c.retention = 'lean' THEN 0 ELSE 1 END) = 0 "
		# (2) the window, measured from the newest link. An unknown linked_at vetoes
		# outright, ACROSS communities: one community with no stamped link beside
		# another with an ancient one says nothing about the first. The mirror of
		# this test WITHIN one community lives in _LINKS_SQL, because by the time a
		# group reaches here it has already been collapsed and a plain MAX() would
		# have skipped the NULL out of existence -- read that comment for the shape.
		#
		# This clause is REDUNDANT TODAY and kept deliberately. Condition (3) below
		# already vetoes the same rows for free -- `summary.at > NULL` is NULL, so a
		# NULL-linked row lands in the ELSE and fails the sum -- which means no test
		# can distinguish this line being present from it being absent, and the
		# mutation round for task 4.6 records it as an equivalent mutant rather than
		# a hole. It stays because the two clauses assert different things: "unknown
		# is not old" is a claim about the WINDOW and must not depend for its truth on
		# the NULL semantics of a comparison in another condition, which a later
		# COALESCE(summary.at, 0) would quietly take away. (_LINKS_SQL's half is NOT
		# redundant in that way -- a real test kills it: see tests/test_sweeper.py's
		# test_a_null_linked_at_beside_an_ancient_one_in_the_SAME_community_is_kept.)
		"AND SUM(CASE WHEN link.linked_at IS NULL THEN 1 ELSE 0 END) = 0 "
		"AND MAX(link.linked_at) < %s "
		# (3) every linked community's summary exists and post-dates that link.
		"AND SUM(CASE WHEN summary.at IS NOT NULL AND summary.boards > 0 "
		"AND summary.at > link.linked_at THEN 0 ELSE 1 END) = 0 "
		"ORDER BY mid LIMIT %s"
	)


async def candidates(now, limit=BATCH, tables=None):
	"""Replay ids whose child rows are eligible for deletion, oldest id first."""
	tables = targets() if tables is None else tables
	cutoff = now - RETENTION_DAYS * 86400
	rows = await db.fetchall(_candidates_sql(tables), [cutoff, limit])
	return [r["mid"] for r in rows or []]


async def retention_census():
	"""{retention: communities} -- the denominator the log line is read against."""
	rows = await db.fetchall("SELECT retention, COUNT(*) AS n FROM communities GROUP BY retention") or []
	return {r["retention"]: r["n"] for r in rows}


async def _count_rows(table, ids):
	holes = ",".join(["%s"] * len(ids))
	row = await db.fetchone(f"SELECT COUNT(*) AS n FROM {table} WHERE replay_match_id IN ({holes})", list(ids))
	return (row or {}).get("n") or 0


def _chunks(ids):
	for i in range(0, len(ids), CHUNK):
		yield ids[i:i + CHUNK]


async def sweep(now=None):
	"""One pass. Returns (replays considered, {table: rows}, deleted?).

	Rows are COUNTED before they are deleted, in the same chunk, because the
	adapter's execute() returns lastrowid rather than a rowcount and a log line
	on this job must never report an intention. The count and the DELETE are two
	statements, so a re-ingest landing between them would make the reported
	figure an undercount of what was actually removed -- accepted, and strictly
	preferable to the alternative of reporting a number nothing verified.
	"""
	now = int(time.time()) if now is None else now
	try:
		tables = targets()
	except Exception as e:
		log.error(f"Retention sweep DISABLED and nothing deleted -- the target list and the data "
		          f"registry disagree about what is sweepable: {e}")
		return 0, {}, False

	ids = await candidates(now, BATCH, tables)
	census = await retention_census()
	lean = census.get("lean", 0)
	census_line = ", ".join(f"{k}={v}" for k, v in sorted(census.items())) or "none"

	# The interlock the brief calls for, as code rather than as a note in a
	# report: on today's production data the only community is retention 'full',
	# so the correct candidate count is ZERO and a non-zero one means condition
	# (1) is wrong. Refuse to delete in that state even when DRY_RUN is off --
	# the one arrangement under which "the guard is broken" and "the deletes are
	# live" can coexist is the one that must not be survivable.
	if ids and not lean:
		log.error(f"Retention sweep found {len(ids)} eligible replays while NO community has "
		          f"retention 'lean' (communities: {census_line}). That is impossible if the "
		          f"lean-only guard is correct, so it is a BUG in this job, not a surprise. "
		          f"Nothing was deleted.")
		return len(ids), {}, False

	counts = dict.fromkeys(tables, 0)
	deleted = False
	for chunk in _chunks(ids):
		holes = ",".join(["%s"] * len(chunk))
		for table in tables:
			counts[table] += await _count_rows(table, chunk)
			if not DRY_RUN:
				await db.execute(f"DELETE FROM {table} WHERE replay_match_id IN ({holes})", list(chunk))
				deleted = True
		# Yield between chunks so a first live sweep of a large backlog cannot
		# hold the loop, even though the pass already runs off the tick.
		await asyncio.sleep(0)

	total = sum(counts.values())
	per_table = ", ".join(f"{t} {counts[t]}" for t in tables)
	if DRY_RUN:
		log.info(f"Retention sweep (DRY RUN -- nothing was deleted): {len(ids)} replays eligible, "
		         f"{total} rows WOULD be deleted ({per_table}); communities: {census_line}. "
		         f"Flipping DRY_RUN is a separate commit.")
	elif ids:
		log.info(f"Retention sweep: {len(ids)} replays swept, {total} rows deleted "
		         f"({per_table}); communities: {census_line}.")
	else:
		log.debug(f"Retention sweep: nothing eligible; communities: {census_line}.")
	return len(ids), counts, deleted


class RetentionSweeper:
	"""Self-isolating tick job, same discipline as nammaoe2bot/derived/backfill.py and
	nammaoe2bot/derived/refresh.py: think() only schedules and can never raise into
	on_think, the work runs off the tick, and _running keeps two passes from
	overlapping.

	next_run starts at 0 so the first pass happens shortly after boot rather than
	24h later -- on the DRY_RUN deploy that first line IS the deliverable, and a
	container that restarts daily would otherwise never emit one.
	"""

	def __init__(self):
		self.next_run = 0
		self._running = False

	async def think(self, frame_time):
		try:
			if self._running or frame_time < self.next_run:
				return
			self.next_run = frame_time + POLL_INTERVAL
			self._running = True
			task = asyncio.create_task(self._run())

			def _done(t):
				self._running = False
				_tasks.discard(t)
				if not t.cancelled() and t.exception() is not None:
					log.error(f"Retention sweep crashed: {t.exception()}")

			_tasks.add(task)
			task.add_done_callback(_done)
		except Exception as e:
			self._running = False
			log.error(f"Retention sweep think() error (ignored): {e}")

	async def _run(self, now=None):
		try:
			await sweep(now)
		except Exception as e:
			log.error(f"Retention sweep error (ignored): {e}")


jobs = RetentionSweeper()
