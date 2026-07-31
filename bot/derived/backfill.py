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

So the predicate is a COUNT COMPARISON between the source rows and the derived
rows. It is stateless — no marker table, no completion flag — so it resumes
correctly after a restart mid-run, converges to exactly zero work, and doubles
permanently as repair: if a live ingest-time write ever fails its best-effort
guard, or a row is deleted by hand, the next tick heals it.

    game_stats  pending iff COUNT(DISTINCT rs_player_games.player_number)
                              > COUNT(game_stats rows)
    game_labels pending iff COUNT(allowlisted cls_results rows)
                              > COUNT(game_labels rows)

Both become EXACT equalities the moment a match is processed, and that
exactness — not mere shrinkage — is what makes the loop terminate. The
asymmetry between the two (DISTINCT on one side, a plain count on the other) is
deliberate, and each half is provable from the primary keys involved:

  * game_labels — cls_results' PK is (key, aoe2_match_id, player_number), so
    within one match a (key, player_number) pair cannot repeat; label_rows
    emits exactly one row per allowlisted result row; game_labels' PK is
    (replay_match_id, player_number, label). One source row in, one stored row
    out, nothing can collapse. A plain COUNT(*) is exact.

  * game_stats — rs_player_games' PK is (aoe2_match_id, profile_id); it does
    NOT constrain player_number. compute_game_stats emits one row per input
    row, but game_stats' PK is (replay_match_id, player_number), so two source
    rows sharing a player_number would store as one, and a plain COUNT(*)
    would sit permanently above the stored count — precisely the
    non-termination this design exists to rule out. COUNT(DISTINCT
    player_number) is the number of rows the write can actually persist, so it
    is exact under every shape the raw table can hold, and identical to
    COUNT(*) in the normal case where replay slots are unique.

PLAYED_AT comes from the match's own cls_results rows, never from
rs_matches.played_at. rs_matches.played_at is a VARCHAR date STRING out of the
replay extract — bot/replay_stats/query.py compares it against an ISO date
string — while game_labels.played_at is a BIGINT epoch, and every row the live
path writes carries the bot match's reported_at epoch (see
bot/replay_stats/classification_sync.py's played_at_epoch).
cls_results.played_at is that same epoch, written from matches.reported_at by
both the offline runner (utils/classifications/dbio.window_matches) and the
live sync. Reading it there makes a backfilled row identical to the row the
live path would have written; reading rs_matches instead would push a date
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

_STATS_SRC = ("SELECT aoe2_match_id AS mid, COUNT(DISTINCT player_number) AS n "
              "FROM rs_player_games GROUP BY aoe2_match_id")
_STATS_DST = ("SELECT replay_match_id AS mid, COUNT(*) AS n "
              "FROM game_stats GROUP BY replay_match_id")
_LABELS_DST = ("SELECT replay_match_id AS mid, COUNT(*) AS n "
               "FROM game_labels GROUP BY replay_match_id")

# Match ids that failed MAX_ATTEMPTS times in THIS process. Process-local on
# purpose: a restart clears it, so a transient DB blip is retried on the next
# deploy, while a genuinely undigestible row stops occupying a slot in every
# future batch. Without this a handful of permanently-failing matches sorted
# into the top BATCH would stall the whole reconciliation — the counts stay
# unequal, so the same ids come back every pass and nothing behind them is ever
# reached. Quarantined ids are excluded from the batch query but NOT from the
# "still pending" figure in the log line, which stays the honest total.
_stats_attempts = {}
_labels_attempts = {}

_tasks = set()   # strong refs to in-flight drain tasks (asyncio only keeps weak ones)


def _labels_src():
	holes = ",".join(["%s"] * len(LABEL_KEYS))
	return (f"SELECT aoe2_match_id AS mid, COUNT(*) AS n FROM cls_results "
	        f"WHERE `key` IN ({holes}) GROUP BY aoe2_match_id"), list(LABEL_KEYS)


def _quarantined(attempts):
	return sorted(mid for mid, n in attempts.items() if n >= MAX_ATTEMPTS)


def _body(src, dst, skip):
	"""The shared FROM/WHERE of both the batch query and the count query, so the
	two can never drift into disagreeing about what 'pending' means."""
	sql = (f"FROM ({src}) src LEFT JOIN ({dst}) dst ON dst.mid = src.mid "
	       f"WHERE src.n > COALESCE(dst.n, 0)")
	if skip:
		sql += " AND src.mid NOT IN (" + ",".join(["%s"] * len(skip)) + ")"
	return sql


async def _pending(src, src_args, dst, skip, limit):
	sql = f"SELECT src.mid AS mid {_body(src, dst, skip)} ORDER BY src.mid DESC LIMIT %s"
	rows = await db.fetchall(sql, [*src_args, *skip, limit])
	return [r["mid"] for r in rows or []]


async def _count_pending(src, src_args, dst):
	"""Total pending, ignoring the quarantine — the log must report the real
	backlog, not the part of it this process is still willing to attempt."""
	row = await db.fetchone(f"SELECT COUNT(*) AS n {_body(src, dst, [])}", list(src_args))
	return (row or {}).get("n") or 0


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


async def process_game_stats(match_id, computed_at):
	"""Recompute and rewrite one match's game_stats rows. Returns rows written.

	rs_player_apm is empty for every pre-backfill match — the bucket-capturing
	parser shipped after the last of them was played, and a parser bump does not
	re-parse completed matches — so historical rows land with peak_eapm = NULL.
	That is the correct value, not a failure: there are no buckets to take a
	maximum over. avg_eapm still comes through, because it is rs_player_games.eapm
	passed straight along and has nothing to do with the buckets.
	"""
	players = await db.fetchall(
		"SELECT * FROM rs_player_games WHERE aoe2_match_id=%s ORDER BY player_number", [match_id])
	units = await db.fetchall("SELECT * FROM rs_player_units WHERE aoe2_match_id=%s", [match_id])
	apm = await db.fetchall("SELECT * FROM rs_player_apm WHERE aoe2_match_id=%s", [match_id])
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
	single log line carrying the outcome. Silent when there is nothing to do, so
	the steady state after convergence costs one query and no noise."""
	ids = await pending(BATCH)
	if not ids:
		return 0
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
	remaining = await count_pending()
	log.info(f"Derived backfill {name}: {done} matches processed, {written} rows written, "
	         f"{failed} failed, {remaining} still pending, "
	         f"{len(_quarantined(attempts))} quarantined.")
	return done


async def drain_game_stats(computed_at=None):
	computed_at = int(time.time()) if computed_at is None else computed_at
	return await _drain(
		"game_stats", pending_game_stats,
		lambda: _count_pending(_STATS_SRC, [], _STATS_DST),
		lambda mid: process_game_stats(mid, computed_at), _stats_attempts)


async def drain_game_labels():
	src, args = _labels_src()
	return await _drain(
		"game_labels", pending_game_labels,
		lambda: _count_pending(src, args, _LABELS_DST),
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
