# -*- coding: utf-8 -*-
"""Reconciliation of the derived-global tables against the raw layer they are
computed from — a permanent loop, not a one-shot migration.

It cannot be a migration at all: core/migrations.py runs before `import bot`
and may not import bot.*, while the medal maths game_stats stores lives in
bot/replay_stats/card_scoring.py. Recomputing medals in SQL to dodge that would
fork the logic, the exact divergence identity v2 spent a stage repairing.

CONVERGENCE IS THE WHOLE DESIGN. The obvious predicate — "process matches that
have no derived rows" — never terminates, because matches that legitimately
derive ZERO rows exist: ingested matches carrying no cls_results at all, and
matches whose only classifier hits are luck_baseline, which both allowlists in
game_labels.py deliberately exclude. Under that predicate those matches are
re-processed on every tick, forever.

So the predicate is a SET COMPARISON between the rows the source implies and
the rows actually stored. It is stateless — no marker table, no completion flag
— so it resumes correctly after a restart mid-run, converges to exactly zero
work, and doubles permanently as repair: if a live ingest-time write ever fails
its best-effort guard, or a derived row is deleted by hand, or the SOURCE
changes underneath an already-derived match, the next tick heals it.

    game_stats  pending iff {DISTINCT player_number of replay_players}
                              <> {player_number of game_stats}
    game_labels pending iff {(player_number, allowlisted key) of cls_results}
                              <> {(player_number, label) of game_labels}

A COUNT comparison is NOT enough, in three separate ways, and the third is why
this compares sets rather than cardinalities.

  1. `src > dst` heals under-population only. The source side is not
     append-only: utils/classifications/dbio.py's wipe_results is a documented,
     supported operation the offline runner calls once per classification
     before a full-window rebuild, so retuning a trigger legitimately REDUCES a
     match's cls_results rows. A match that went from 5 allowlisted rows to 4
     is never pending again under `>` (4 > 5 is false) and keeps the retired
     label forever.

  2. A source count that falls to ZERO produces no group on the source side at
     all, so a `FROM (src) LEFT JOIN (dst)` shape never sees that match and its
     orphaned derived rows are never cleaned by any predicate written that way.
     What is wanted is a FULL OUTER JOIN, which MySQL does not have.

  3. `src <> dst` still cannot see a SUBSTITUTION — archer_rush becoming
     scout_rush for one player leaves 5 rows facing 5 rows, so no comparison of
     cardinalities can fire, and the stored label is silently WRONG rather than
     merely stale. A full-window rebuild retunes several classifications in one
     pass, so one key losing a row while another gains one in the same match is
     an ordinary outcome, not a contrived one.

Comparing the SETS fixes all three at once and subsumes the count comparison
entirely (equal sets imply equal counts, so any count difference is also a set
difference). _body below implements it as a single symmetric query: tag every
row on both sides into the same (mid, pn, tag) shape, UNION ALL them, group,
and keep the groups seen exactly once — a group of size 1 is a row present on
one side only, in whichever direction. Nothing about the shape prefers a side,
so the zero-drop case falls out for free.

TERMINATION, which is the property the whole design exists for, needs each side
to contribute AT MOST ONE row per (mid, pn, tag) — otherwise a group could hold
3 and stay unequal forever. Both are provable from the primary keys involved:

  * game_labels — cls_results' PK is (key, aoe2_match_id, player_number), so
    within one match a (key, player_number) pair cannot repeat; label_rows
    emits exactly one row per allowlisted result row; game_labels' PK is
    (replay_match_id, player_number, label). One source row in, one stored row
    out, nothing can collapse or duplicate.

  * game_stats — replay_players' PK is (replay_match_id, profile_id); it does
    NOT constrain player_number, so two source rows CAN share one. game_stats'
    PK is (replay_match_id, player_number), so those two would store as one and
    a raw comparison would sit permanently unequal — precisely the
    non-termination this design exists to rule out. Hence the DISTINCT on the
    source side: it is the set of rows the write can actually persist, exact
    under every shape the raw table can hold, and a no-op in the normal case
    where replay slots are unique.

Note the two spellings of the match id in the SQL below, which are not a typo:
007_raw_renames renamed the raw side's column to `replay_match_id` (matching
what the derived tables always called it) and deliberately left the legacy cls_*
tables on `aoe2_match_id`, since stage 6 retires those outright. This module is
the one place that reads across both.

The grouping therefore only ever produces 1 (one side) or 2 (both sides), and
the predicate is written `= 1` rather than `<> 2` deliberately: if some future
schema change ever did admit a duplicate, `= 1` degrades to a stale row, while
`<> 2` would degrade to a match that is pending forever. Staleness is
recoverable; non-termination is the one failure mode this loop must not have.

After a write both sets are equal BY CONSTRUCTION, not merely closer — write()
is a delete-then-insert of exactly the rows the compute returned — so a
processed match leaves the pending set immediately and permanently.

PLAYED_AT comes from the match's own cls_results rows, never from
replay_matches.played_at. replay_matches.played_at is a VARCHAR date STRING out of the
replay extract — bot/replay_stats/query.py compares it against an ISO date
string — while game_labels.played_at is a BIGINT epoch, and every row the live
path writes carries the bot match's reported_at epoch (see
bot/replay_stats/classification_sync.py's played_at_epoch).
cls_results.played_at is that same epoch, written from matches.reported_at by
both the offline runner (utils/classifications/dbio.window_matches) and the
live sync. Reading it there makes a backfilled row identical to the row the
live path would have written; reading replay_matches instead would push a date
string into an integer column and give history a different unit from live.

Never raises into the think tick: think() only schedules, each drain is
wrapped, and every per-match failure is caught, logged and stepped over so one
bad match cannot cost the other twenty-four in its batch.
"""
import asyncio
import time

from core.console import log
from core.database import db

from . import game_labels, game_stats

BATCH = 25              # matches per drain per pass
POLL_INTERVAL = 10      # seconds between passes; the tick is 1s and each pass is ~250 queries
MAX_ATTEMPTS = 3        # per-process retries before a match is quarantined (see _quarantined)

# Strategy + spawn, exactly the set label_rows keeps. Anything else — luck_baseline
# above all — is excluded here so those matches never enter the pending set at all,
# rather than entering it and deriving zero rows on every tick forever.
LABEL_KEYS = tuple(game_labels.STRATEGY_KEYS) + tuple(game_labels.SPAWN_KEYS)

# Both sides of both comparisons project into one common (mid, pn, tag) shape so
# _body can be written once. game_stats has no per-row tag — its grain is the
# player alone — so it carries a constant, which makes the group key degenerate
# to (mid, pn) exactly as intended. A non-empty literal on purpose: '' would give
# the unioned column MySQL type VARCHAR(0), which is legal but needlessly exotic
# to group by.
_STATS_SRC = ("SELECT DISTINCT replay_match_id AS mid, player_number AS pn, '-' AS tag "
              "FROM replay_players")
_STATS_DST = ("SELECT replay_match_id AS mid, player_number AS pn, '-' AS tag FROM game_stats")
_LABELS_DST = ("SELECT replay_match_id AS mid, player_number AS pn, label AS tag FROM game_labels")

# Match ids that failed MAX_ATTEMPTS times in THIS process. Process-local on
# purpose: a restart clears it, so a transient DB blip is retried on the next
# deploy, while a genuinely undigestible row stops occupying a slot in every
# future batch. Without this a handful of permanently-failing matches sorted
# into the top BATCH would stall the whole reconciliation — the two sides stay
# different, so the same ids come back every pass and nothing behind them is ever
# reached. Quarantined ids are excluded from the batch query but NOT from the
# "still pending" figure in the log line, which stays the honest total.
_stats_attempts = {}
_labels_attempts = {}

_tasks = set()   # strong refs to in-flight drain tasks (asyncio only keeps weak ones)


def _labels_src():
	holes = ",".join(["%s"] * len(LABEL_KEYS))
	return (f"SELECT aoe2_match_id AS mid, player_number AS pn, `key` AS tag FROM cls_results "
	        f"WHERE `key` IN ({holes})"), list(LABEL_KEYS)


def _quarantined(attempts):
	return sorted(mid for mid, n in attempts.items() if n >= MAX_ATTEMPTS)


def _body(src, src_args, dst, skip):
	"""The shared FROM/WHERE of both the batch query and the count query, so the
	two can never drift into disagreeing about what 'pending' means. Returns
	(sql_fragment, args).

	One symmetric pass, because MySQL has no FULL OUTER JOIN and the predicate
	needs one: a match is pending when the source implies a row the derived side
	lacks AND when the derived side holds a row the source no longer implies —
	including the case where the source drops to zero and the match stops being
	a row on that side at all. Stacking both sides into one (mid, pn, tag)
	relation and keeping the groups of size one expresses exactly the symmetric
	difference; a group of two is a row both sides agree on. See the module
	docstring for why size can only be 1 or 2, and why `= 1` rather than `<> 2`.

	`pending` therefore yields one row per DIFFERING (pn, tag) — a match with
	three wrong labels appears three times — so both callers reduce it to
	distinct mids themselves.
	"""
	# No parentheses around the two operands: MySQL accepts them, sqlite (which
	# tests/test_derived_backfill.py runs this very SQL against) does not. Both
	# bind a trailing WHERE/DISTINCT to their own SELECT, which is what the two
	# sources rely on.
	sql = (f"FROM (SELECT mid FROM ({src} UNION ALL {dst}) sides "
	       f"GROUP BY mid, pn, tag HAVING COUNT(*) = 1) pending")
	args = list(src_args)
	if skip:
		sql += " WHERE pending.mid NOT IN (" + ",".join(["%s"] * len(skip)) + ")"
		args += list(skip)
	return sql, args


async def _pending(src, src_args, dst, skip, limit):
	body, args = _body(src, src_args, dst, skip)
	sql = f"SELECT DISTINCT pending.mid AS mid {body} ORDER BY mid DESC LIMIT %s"
	rows = await db.fetchall(sql, [*args, limit])
	return [r["mid"] for r in rows or []]


async def _count_pending(src, src_args, dst, skip=()):
	"""(total pending, how many of those this process has quarantined).

	The total ignores the quarantine — the log must report the real backlog, not
	the part of it this process is still willing to attempt. The second figure is
	what separates "converged" from "permanently stuck": it is the total minus
	what a batch query would still pick up, i.e. the quarantined ids that are
	genuinely still pending (an id can sit in the attempts dict and no longer be
	pending, because the live ingest path wrote its rows in the meantime).
	"""
	body, args = _body(src, src_args, dst, [])
	row = await db.fetchone(f"SELECT COUNT(DISTINCT pending.mid) AS n {body}", args)
	total = (row or {}).get("n") or 0
	if not skip:
		return total, 0
	body, args = _body(src, src_args, dst, skip)
	row = await db.fetchone(f"SELECT COUNT(DISTINCT pending.mid) AS n {body}", args)
	return total, total - ((row or {}).get("n") or 0)


async def pending_game_stats(limit=BATCH):
	"""Match ids whose game_stats rows are missing or incomplete, newest first."""
	return await _pending(_STATS_SRC, [], _STATS_DST, _quarantined(_stats_attempts), limit)


async def pending_game_labels(limit=BATCH):
	"""Match ids whose game_labels rows are missing or incomplete, newest first."""
	src, args = _labels_src()
	return await _pending(src, args, _LABELS_DST, _quarantined(_labels_attempts), limit)


def played_at_of(result_rows):
	"""The match's played_at epoch, read out of its OWN cls_results rows. Every
	row of one match carries the same value (one epoch per match is passed in by
	both writers), so the first non-null is the match's epoch — scoped per match,
	never shared across the batch."""
	for r in result_rows:
		if r.get("played_at") is not None:
			return r["played_at"]
	return None


# ACCEPTED TRADEOFF — do not "fix" this by adding locking or a transaction.
# Both process_* functions below read the raw/cls_ rows of a match that
# store.write_match may be re-ingesting at that exact moment: write_match
# DELETEs a match's rows and re-INSERTs them non-transactionally (the adapter is
# autocommit), so a read landing inside that ~1s window can see a partially
# rewritten match and derive rows from it. It is self-healing by construction —
# the re-ingest finishes, the two sides disagree again, and the next pass
# (POLL_INTERVAL seconds later) rewrites the match from the settled rows. The
# alternatives (a lock, or making the adapter transactional) cost far more than
# a derived row being stale for ten seconds.
async def process_game_stats(match_id, computed_at):
	"""Recompute and rewrite one match's game_stats rows. Returns rows written.

	replay_apm is empty for every pre-backfill match — the bucket-capturing
	parser shipped after the last of them was played, and a parser bump does not
	re-parse completed matches — so historical rows land with peak_eapm = NULL.
	That is the correct value, not a failure: there are no buckets to take a
	maximum over. avg_eapm still comes through, because it is replay_players.eapm
	passed straight along and has nothing to do with the buckets.
	"""
	players = await db.fetchall(
		"SELECT * FROM replay_players WHERE replay_match_id=%s ORDER BY player_number", [match_id])
	units = await db.fetchall("SELECT * FROM replay_units WHERE replay_match_id=%s", [match_id])
	apm = await db.fetchall("SELECT * FROM replay_apm WHERE replay_match_id=%s", [match_id])
	rows = game_stats.compute_game_stats(list(players or []), list(units or []),
	                                     list(apm or []), computed_at)
	await game_stats.write(match_id, rows)
	return len(rows)


async def process_game_labels(match_id):
	"""Recompute and rewrite one match's game_labels rows. Returns rows written.

	Passes the match's full cls_results set to label_rows and lets it apply the
	allowlist, so the stored set is decided by exactly one implementation of the
	allowlist — the same one the live path uses.
	"""
	results = await db.fetchall("SELECT * FROM cls_results WHERE aoe2_match_id=%s", [match_id])
	metrics = await db.fetchall("SELECT * FROM cls_result_metrics WHERE aoe2_match_id=%s", [match_id])
	results = list(results or [])
	rows = game_labels.label_rows(results, list(metrics or []), played_at_of(results))
	await game_labels.write(match_id, rows)
	return len(rows)


async def _drain(name, pending, count_pending, process, attempts):
	"""One batch: at most BATCH matches, each isolated from the others, then a
	single log line carrying the outcome.

	The pending total is computed on EVERY pass, including a pass that picked up
	nothing. An empty batch has two completely different causes — nothing is
	pending, or everything still pending is quarantined in this process — and
	returning early before the count made the second one indistinguishable from
	convergence: a permanent hole with no log line anywhere. The line now names
	both figures, so "0 still pending" and "7 still pending, all 7 quarantined"
	can never be confused, which is exactly what the 3a deploy is verified on.

	The only silent case left is the genuinely converged one (nothing processed,
	nothing failed, nothing pending), which is reported at debug rather than
	info: it is the designed steady state, it costs one line every POLL_INTERVAL
	forever, and it carries no information a reader does not already have.
	"""
	ids = await pending(BATCH)
	done = failed = written = 0
	for match_id in ids:
		try:
			written += await process(match_id)
			attempts.pop(match_id, None)
			done += 1
		except Exception as e:
			failed += 1
			n = attempts.get(match_id, 0) + 1
			attempts[match_id] = n
			log.error(f"Derived backfill {name} failed for match {match_id} "
			          f"(attempt {n}/{MAX_ATTEMPTS}): {e}")
	remaining, blocked = await count_pending(_quarantined(attempts))
	line = (f"Derived backfill {name}: {done} matches processed, {written} rows written, "
	        f"{failed} failed, {remaining} still pending "
	        f"({blocked} of those quarantined in this process, {remaining - blocked} attemptable).")
	if done or failed or remaining:
		log.info(line)
	else:
		log.debug(line)
	return done


async def drain_game_stats(computed_at=None):
	computed_at = int(time.time()) if computed_at is None else computed_at
	return await _drain(
		"game_stats", pending_game_stats,
		lambda skip: _count_pending(_STATS_SRC, [], _STATS_DST, skip),
		lambda mid: process_game_stats(mid, computed_at), _stats_attempts)


async def drain_game_labels():
	src, args = _labels_src()
	return await _drain(
		"game_labels", pending_game_labels,
		lambda skip: _count_pending(src, args, _LABELS_DST, skip),
		process_game_labels, _labels_attempts)


class DerivedBackfill:
	"""Self-isolating tick job, same discipline as bot/replay_stats/jobs.py: think()
	only schedules and can never raise into on_think, the work runs off the tick so
	a ~250-query pass cannot stall in-flight matches, and _running keeps two passes
	from overlapping."""

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
					log.error(f"Derived backfill crashed: {t.exception()}")

			_tasks.add(task)
			task.add_done_callback(_done)
		except Exception as e:
			self._running = False
			log.error(f"Derived backfill think() error (ignored): {e}")

	async def _run(self):
		# Each drain is wrapped separately: game_labels must still reconcile on a
		# pass where game_stats hit a dead database, and vice versa.
		for drain in (drain_game_stats, drain_game_labels):
			try:
				await drain()
			except Exception as e:
				log.error(f"Derived backfill drain error (ignored): {e}")


jobs = DerivedBackfill()
