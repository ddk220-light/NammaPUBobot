# -*- coding: utf-8 -*-
"""The derived-COMMUNITY refresh job: the one place that decides WHO a game
belongs to and WHEN their aggregates are rebuilt.

The three writers this job drives -- rollups.py (per user), boards.py (per
community metric) and civ_stats.py (per community civ) -- are all pure compute
plus a write(). None of them resolves an identity and none of them decides what
is out of date, deliberately and for the same reason their docstrings each give:
attribution and scheduling are jobs with their own failure modes and they do not
belong inside a function that has to be trivially testable without a database.
This module is that dedicated owner.

ATTRIBUTION (identity v2 SS5). A user's rollup aggregates the game_stats and
game_labels rows of EVERY AoE2 profile they own, not one. `/identity link ...
additional: True` exists because the flagship community has genuine
multi-account players, so resolving a single profile per user would silently
halve their history -- and both halves would then fall under
rollups.SPLIT_MIN_GAMES and vanish from the report entirely, with no error
anywhere. Profiles are therefore resolved through identity.profiles_for_users,
which returns the whole set, and every query below binds the whole set.

An UNLINKED profile aggregates into no rollup at all: `_IMPLIED_SQL` joins
`identities` and requires `user_id IS NOT NULL`, so an unlinked player is not in
the implied set and gets NO row. Not a row of zeros -- the ABSENCE is the signal
stage 5a renders "Statistics pending linking" from. That is also why
rollups.delete exists and is called here: a player who HAD a rollup and then had
their profile unlinked must lose the row, and nothing about their game_stats
rows changes to say so.

-- WHY THE WORK LIST IS NOT A LIST ------------------------------------------

A plain in-memory set of dirty users loses everything on every deploy, and this
bot deploys often: a player who runs `/link` seconds before a deploy would
silently never get a rollup, and no log line anywhere would say so. A PERSISTED
dirty-set table fixes that and was the other candidate. This module does neither.

There is no work list. Pendingness is DERIVED, on every pass, from a comparison
between what the data implies and what is stored -- the same discipline
bot/derived/backfill.py uses for the derived-global layer, and chosen here for
the same three properties: nothing to leak (a dirty row for a user that can
never be computed occupies a slot forever), nothing to lose (a restart mid-pass
resumes exactly where it was, because there was never any state to resume), and
permanent repair rather than one-shot catch-up (a row deleted by hand, a
restored backup, a rolled-back deploy, or a hook that was never written all heal
on the next pass instead of never). It also means this project has ONE mental
model for "a derived table catches up", not two.

A (community, user) rollup is pending when any of:

  1. the data implies it and no row is stored          -- a new player, a new link
  2. a row is stored and the data no longer implies it -- unlinked, or every
                                                          profile relinked away
  3. stored.computed_at <= the newest INPUT feeding it -- ordinary staleness
  4. stored.computed_at < now - MAX_AGE                -- the backstop, below

`<=` in (3) rather than `<`, deliberately, and it is the whole race guard. The
job reads a user's game_stats rows at second T and stamps the rollup with second
T; an ingest writing a new game_stats row for that same user during that same
second lands computed_at = T too. Under `<` that game is invisible for good.
Under `<=` the rollup is pending once more, is recomputed on the next pass at
T+POLL_INTERVAL, and converges there -- the only cost is one redundant recompute
in the rare second where an input and a write collide. Convergence is still
guaranteed because the write's timestamp is wall-clock and the inputs' are in
the past: after any pass, computed_at is strictly greater than every input
already stored. (A clock genuinely running backwards on another container would
keep a user pending until real time catches up. Bounded and self-healing, and
not a state worth trading the race guard for.)

THE HARD INPUT IS IDENTITY, and it is the reason this design needed a schema
change rather than a clever query. Game data announces itself through
game_stats.computed_at. An identity change announces itself through NOTHING: a
`/link` makes a player's entire history attributable without touching one
game_stats row, so a comparison built on game data alone would never fire and
the headline promise of identity v2 -- link late, get everything -- would
silently not happen. 009_identities_bound_at adds `identities.bound_at`, stamped
by exactly the three paths in bot/identity.py that MOVE a binding
(_insert_binding, _overwrite_binding when the owner actually changes, and
unlink) and by none of the paths that merely re-observe one. `last_seen_at` was
rejected for this: it is an observation timestamp that every replay ingest and
even every REFUSED claim bumps, so depending on it would recompute every player
of every ingested match for no reason AND couple this comparison's correctness
to a column whose meaning belongs to another module.

The LOSING side of a binding move is the subtle half. When profile P moves from
user A to user B, B is covered (they now own P, so P.bound_at is in their
watermark) and A is not -- A no longer owns P, A's remaining profiles did not
move, and A's own game_stats rows never change. A would keep P's games in their
rollup forever. `_IDENTITY_WATERMARK_SQL` closes that by taking the watermark
over every profile a user has EVER been associated with: the profiles they own
now, UNION the profiles they appear against in `identity_conflicts`. That second
half costs nothing to maintain because bot/identity.py already records every
displacement and every removal there (`superseded` / `unlinked`) -- the audit
trail identity v2 built for humans turns out to be exactly the record this
comparison needs. The union is over-approximate in one direction only (a
REFUSED claimant is also associated with the profile, and simply gets a harmless
extra recompute), which is the safe direction.

ONE INPUT IS DELIBERATELY NOT COMPARED: `game_labels` carries no computed_at
column at all (it stores the match's played_at epoch, not a compute time), so a
label that changes without its match's game_stats changing -- an offline
classification retune, which is the only way that happens -- is invisible to (3).
That is what (4) is for. Rather than a cron or a "did tonight's pass run?"
marker, staleness is simply capped: no derived-community row may be older than
MAX_AGE, so every rollup is recomputed at least daily whatever else did or did
not fire. Same statelessness, and it doubles as the backstop for any hook this
module has not thought of, including ones added later and wired up wrong.

-- CADENCE: PER-USER vs PER-COMMUNITY ---------------------------------------

player_rollups is per (community, user) and drains BATCH per pass. metric_boards
and civ_stats are per COMMUNITY -- one metric board is a scan of every game
every member of the community has played -- so rebuilding them every time one
user's rollup moves would redo the whole community's work once per user, and
never rebuilding them would leave them stale forever.

They run on a DEBOUNCE derived from the pass itself, not on a timer: the
community pass only runs on a pass where the rollup drain processed NOTHING.
Boards and civ_stats are aggregates over the same inputs the rollups are built
from, so recomputing them while the per-user layer is still catching up is
work that is guaranteed to be thrown away. On a cold start that means all ~42
rollups converge first (5 passes) and the boards are then built exactly once,
instead of once per pass. "Processed nothing" rather than "nothing is pending"
on purpose: a user quarantined after MAX_ATTEMPTS failures leaves the pending
count permanently non-zero, and gating on that would starve the community layer
forever behind one bad row.

A community is then pending when it is missing a board for any catalogued
metric (a first run, or a metric added by a deploy), or when the OLDEST of its
catalogued boards is `<=` its own watermark -- max(newest player_rollups
computed_at in the community, newest civ_picks.at across its channels). The
first term is what makes boards trail the per-user layer automatically; the
second is what catches a match report, whose civ_picks rows land with no
game_stats and therefore move no rollup at all. metric_boards is the marker for
both tables rather than each having its own, because a pass always writes one
row per catalogued metric while civ_stats can legitimately write ZERO rows (a
community with no resolved picks) -- and a marker that can be empty is the exact
shape of non-termination bot/derived/backfill.py's docstring dissects.

-- HOOKS --------------------------------------------------------------------

The brief for this task called for dirty-marking hooks in replay ingest, match
report and every identity path. Under a derived work list two of those three are
already there and must NOT be added:

  replay ingest -- bot/replay_stats/store.write_match already writes game_stats
                   with a fresh computed_at. That IS the hook.
  match report  -- bot/stats/stats.py's register_match_* schedules civ recording,
                   whose civ_picks rows carry `at`. That IS the hook.
  identity      -- the one that had nothing to observe, and now does:
                   bot/identity.py stamps `bound_at`.

Adding mark_dirty() calls beside those writes would create a second, weaker
source of truth that can disagree with the data, which is the failure this
design exists to rule out.

Never raises into the think tick: think() only schedules, each drain is wrapped,
and every per-user and per-metric failure is caught, logged and stepped over so
one bad row cannot cost the rest of its batch.
"""
import asyncio
import time

from core.console import log
from core.database import db

from bot import identity

from . import boards, civ_stats, rollups

BATCH = 10              # rollups recomputed per pass
POLL_INTERVAL = 30      # seconds between passes
MAX_ATTEMPTS = 3        # per-process retries before a row is quarantined (see _quarantined)
MAX_AGE = 86400         # nothing derived-community may be older than this (see the docstring)

# A community's replays, deduplicated. match_replays' primary key is
# (community_id, match_id), which does NOT constrain replay_match_id: two bot
# matches in one community can legitimately point at the same replay (a
# re-report, a corrected pairing), and without the DISTINCT every game_stats row
# of that replay would be counted twice into the same user's rollup. Same
# property, same fix, and the same reasoning as bot/derived/backfill.py's
# DISTINCT over replay_players.
_REPLAYS = "(SELECT DISTINCT community_id, replay_match_id FROM match_replays) mr"

# What the data implies: one row per (community, user) that has at least one
# attributable game, carrying the newest computed_at among those games.
# `i.user_id IS NOT NULL` is what makes an unlinked profile aggregate into
# nothing at all rather than into a rollup of zeros.
_IMPLIED_SQL = (
	"SELECT mr.community_id AS community_id, i.user_id AS user_id, "
	"MAX(gs.computed_at) AS games_at "
	"FROM game_stats gs "
	"JOIN identities i ON i.profile_id = gs.profile_id "
	f"JOIN {_REPLAYS} ON mr.replay_match_id = gs.replay_match_id "
	"WHERE i.user_id IS NOT NULL "
	"GROUP BY mr.community_id, i.user_id"
)

# When each user's identity picture last MOVED, over every profile they have
# ever been associated with -- owned now, or recorded against them in
# identity_conflicts. See the module docstring for why the second half is
# load-bearing rather than defensive: it is the only record that user A ever
# held profile P once P has been moved to user B.
_IDENTITY_WATERMARK_SQL = (
	"SELECT assoc.user_id AS user_id, MAX(i.bound_at) AS bound_at FROM ("
	"SELECT user_id, profile_id FROM identities WHERE user_id IS NOT NULL "
	"UNION "
	"SELECT claimed_user_id AS user_id, profile_id FROM identity_conflicts"
	") assoc JOIN identities i ON i.profile_id = assoc.profile_id "
	"GROUP BY assoc.user_id"
)

_STORED_SQL = "SELECT community_id, user_id, computed_at FROM player_rollups"

_USER_STATS_SQL = (
	"SELECT gs.* FROM game_stats gs "
	f"JOIN {_REPLAYS} ON mr.replay_match_id = gs.replay_match_id "
	"WHERE mr.community_id = %s AND gs.profile_id IN ({holes})"
)

# Labels are scoped through game_stats rather than read on the label table
# alone, because game_labels carries no profile_id (a label is a fact about a
# slot; the profile behind that slot is recorded once, on game_stats -- see
# bot/derived/__init__.py). The join is on the PAIR (replay_match_id,
# player_number), never the match alone: a match holds up to eight slots and two
# of them can belong to the same user.
_USER_LABELS_SQL = (
	"SELECT gl.* FROM game_labels gl "
	"JOIN game_stats gs ON gs.replay_match_id = gl.replay_match_id "
	"AND gs.player_number = gl.player_number "
	f"JOIN {_REPLAYS} ON mr.replay_match_id = gl.replay_match_id "
	"WHERE mr.community_id = %s AND gs.profile_id IN ({holes})"
)

_COMMUNITIES_SQL = "SELECT community_id FROM communities"
_ROLLUP_WATERMARK_SQL = "SELECT community_id, MAX(computed_at) AS at FROM player_rollups GROUP BY community_id"
_PICK_WATERMARK_SQL = (
	"SELECT cc.community_id AS community_id, MAX(cp.at) AS at FROM civ_picks cp "
	"JOIN community_channels cc ON cc.channel_id = cp.channel_id "
	"GROUP BY cc.community_id"
)
_BOARD_MARKER_SQL = "SELECT community_id, metric_id, computed_at FROM metric_boards"

_COMMUNITY_PICKS_SQL = (
	"SELECT cp.channel_id AS channel_id, cp.civ AS civ, cp.result AS result FROM civ_picks cp "
	"JOIN community_channels cc ON cc.channel_id = cp.channel_id "
	"WHERE cc.community_id = %s"
)
_COMMUNITY_CHANNELS_SQL = "SELECT channel_id, community_id FROM community_channels WHERE community_id = %s"

# Rows that failed MAX_ATTEMPTS times in THIS process, keyed the same way the
# work they stand for is. Process-local on purpose, exactly as in
# bot/derived/backfill.py: a restart clears it, so a transient DB blip is retried
# on the next deploy, while a genuinely uncomputable row stops occupying a slot
# in every future batch. Without it a handful of permanently-failing users
# sorted to the front of the batch would stall everything behind them forever --
# they stay pending, so the same keys come back every pass.
_rollup_attempts = {}
_community_attempts = {}

_tasks = set()   # strong refs to in-flight drain tasks (asyncio only keeps weak ones)


def _quarantined(attempts):
	return {key for key, n in attempts.items() if n >= MAX_ATTEMPTS}


def _note_failure(attempts, key):
	n = attempts.get(key, 0) + 1
	attempts[key] = n
	return n


def _holes(values):
	return ",".join(["%s"] * len(values))


def _catalog_fields(source):
	"""Every column boards.METRICS reads out of `source`, deduped and ordered.

	Built from the catalog rather than listed here so adding a metric needs one
	edit in one module: boards.py owns which column a metric reads, this module
	only needs to fetch it."""
	return sorted({meta["field"] for meta in boards.METRICS.values() if meta["source"] == source})


def _players_board_sql():
	cols = "".join(f", rp.`{field}`" for field in _catalog_fields("replay_players"))
	return (
		f"SELECT i.user_id AS user_id, rp.identity AS nick, rp.replay_match_id AS replay_match_id{cols} "
		"FROM replay_players rp "
		"JOIN identities i ON i.profile_id = rp.profile_id "
		f"JOIN {_REPLAYS} ON mr.replay_match_id = rp.replay_match_id "
		"WHERE i.user_id IS NOT NULL AND mr.community_id = %s"
	)


def _stats_board_sql():
	cols = "".join(f", gs.`{field}`" for field in _catalog_fields("game_stats"))
	return (
		f"SELECT i.user_id AS user_id, rp.identity AS nick, gs.replay_match_id AS replay_match_id{cols} "
		"FROM game_stats gs "
		"JOIN identities i ON i.profile_id = gs.profile_id "
		f"JOIN {_REPLAYS} ON mr.replay_match_id = gs.replay_match_id "
		"LEFT JOIN replay_players rp ON rp.replay_match_id = gs.replay_match_id "
		"AND rp.profile_id = gs.profile_id "
		"WHERE i.user_id IS NOT NULL AND mr.community_id = %s"
	)


async def _implied_rollups():
	"""{(community_id, user_id): newest game_stats.computed_at}."""
	rows = await db.fetchall(_IMPLIED_SQL) or []
	return {(r["community_id"], r["user_id"]): r["games_at"] or 0 for r in rows}


async def _identity_watermarks():
	"""{user_id: newest identities.bound_at over every profile ever associated}."""
	rows = await db.fetchall(_IDENTITY_WATERMARK_SQL) or []
	return {r["user_id"]: r["bound_at"] or 0 for r in rows}


async def _stored_rollups():
	"""{(community_id, user_id): computed_at}."""
	rows = await db.fetchall(_STORED_SQL) or []
	return {(r["community_id"], r["user_id"]): r["computed_at"] or 0 for r in rows}


async def pending_rollups(now, limit=None):
	"""Every (community_id, user_id) whose rollup is out of date, stalest first.

	Derived from three aggregate reads and compared in Python rather than
	expressed as one query: the comparison has four distinct branches (see the
	module docstring), MySQL has no FULL OUTER JOIN to express the two
	set-difference ones symmetrically, and the aggregates being compared are one
	row per user rather than one row per game -- so the scan still happens in the
	database and only the ~42-row result crosses the wire. A single query
	spelling this would need the implied relation twice and would be unreadable
	in exchange for two saved round trips.

	Stalest first (missing counts as epoch 0) so a batch that cannot keep up
	still makes progress on the oldest data rather than revisiting the same
	front of a fixed ordering; ties break on the key so the order is total and
	tests can pin it."""
	implied = await _implied_rollups()
	identity_at = await _identity_watermarks()
	stored = await _stored_rollups()

	pending = []
	for key in set(implied) | set(stored):
		computed_at = stored.get(key)
		if computed_at is None:
			pending.append((0, key))          # (1) implied, never computed
			continue
		if key not in implied:
			pending.append((computed_at, key))  # (2) stored, no longer implied
			continue
		watermark = max(implied[key], identity_at.get(key[1], 0))
		if computed_at <= watermark or computed_at < now - MAX_AGE:
			pending.append((computed_at, key))  # (3) stale input / (4) too old

	pending.sort(key=lambda item: (item[0], item[1]))
	keys = [key for _at, key in pending]
	return keys if limit is None else keys[:limit]


async def refresh_user(community_id, user_id, now):
	"""Recompute one user's rollup for one community, from scratch. Returns True
	if a rollup was written, False if the row was removed.

	FULL RECOMPUTE, never an incremental delta. The busiest real player in
	production has 588 games, so the whole input is two small queries and a pure
	function -- and rebuild-per-user is the only unit where a late `/link`, a
	relink, a corrected medal and a retuned label all produce the right answer
	through one code path instead of four.

	Both queries bind the user's WHOLE profile set. A single-profile assumption
	here is invisible: it produces a smaller, entirely plausible rollup for the
	multi-account players this community actually has."""
	profiles = (await identity.profiles_for_users([user_id])).get(user_id) or []
	if not profiles:
		# Unlinked since the last pass (or never linked and holding a row from
		# before they were unlinked). No row at all is the correct state --
		# see rollups.delete for why the absence is what 5a renders.
		await rollups.delete(community_id, user_id)
		return False

	args = [community_id, *profiles]
	holes = _holes(profiles)
	stat_rows = await db.fetchall(_USER_STATS_SQL.format(holes=holes), args) or []
	if not stat_rows:
		# Linked, but no attributable game in THIS community. Same rule: the
		# player has no rollup here rather than an empty one.
		await rollups.delete(community_id, user_id)
		return False

	label_rows = await db.fetchall(_USER_LABELS_SQL.format(holes=holes), args) or []
	rollup = rollups.compute_rollup(list(stat_rows), list(label_rows))
	await rollups.write(community_id, user_id, len(stat_rows), rollup, now)
	return True


async def drain_rollups(now):
	"""One batch: at most BATCH rollups, each isolated from the others, then a
	single log line carrying the outcome. Returns how many were processed.

	The pending total is recomputed AFTER the batch and always reported, even on
	a pass that picked up nothing, for the reason bot/derived/backfill.py's
	_drain spells out: an empty batch has two completely different causes --
	converged, or everything still pending is quarantined in this process -- and
	a line that cannot distinguish them turns a permanent hole into silence.
	This job's log line is how the deploy gets verified, so it carries real
	counts of what happened, never of what was attempted."""
	blocked = _quarantined(_rollup_attempts)
	candidates = await pending_rollups(now)
	batch = [key for key in candidates if key not in blocked][:BATCH]

	written = removed = failed = 0
	for community_id, user_id in batch:
		try:
			if await refresh_user(community_id, user_id, now):
				written += 1
			else:
				removed += 1
			_rollup_attempts.pop((community_id, user_id), None)
		except Exception as e:
			failed += 1
			n = _note_failure(_rollup_attempts, (community_id, user_id))
			log.error(f"Derived refresh rollup failed for community {community_id} user {user_id} "
			          f"(attempt {n}/{MAX_ATTEMPTS}): {e}")

	remaining = await pending_rollups(now)
	still_blocked = _quarantined(_rollup_attempts) & set(remaining)
	line = (f"Derived refresh rollups: {written} written, {removed} removed, {failed} failed, "
	        f"{len(remaining)} still stale ({len(still_blocked)} of those quarantined in this "
	        f"process, {len(remaining) - len(still_blocked)} attemptable).")
	if written or removed or failed or remaining:
		log.info(line)
	else:
		log.debug(line)
	return written + removed + failed


async def _community_watermarks():
	"""{community_id: newest input that feeds its boards and civ stats}.

	Two terms, and the second is not redundant. The player_rollups term is what
	makes the community layer trail the per-user layer for free. The civ_picks
	term covers a match REPORT, which writes civ_picks rows and no game_stats at
	all -- the replay is ingested minutes to hours later, if ever -- so a
	community whose civ stats moved but whose rollups did not would otherwise
	never notice."""
	out = {}
	for sql in (_ROLLUP_WATERMARK_SQL, _PICK_WATERMARK_SQL):
		for row in await db.fetchall(sql) or []:
			cid = row["community_id"]
			out[cid] = max(out.get(cid, 0), row["at"] or 0)
	return out


async def pending_communities(now):
	"""Every community whose boards/civ stats are out of date, stalest first.

	The marker is metric_boards and not civ_stats, because a pass always writes
	exactly one board per catalogued metric while civ_stats can legitimately
	write ZERO rows -- a community with no resolved picks. A marker that can be
	empty is indistinguishable from a marker that was never written, which is
	the shape that never terminates (bot/derived/backfill.py's docstring
	dissects the same trap on the derived-global side).

	Uncatalogued metric_ids are excluded from the marker deliberately: a board
	for a metric this deploy no longer knows about must not hold a community's
	freshness hostage, and delete_uncatalogued removes it during the pass."""
	watermarks = await _community_watermarks()

	stored = {}
	for row in await db.fetchall(_BOARD_MARKER_SQL) or []:
		if row["metric_id"] in boards.METRICS:
			stored.setdefault(row["community_id"], {})[row["metric_id"]] = row["computed_at"] or 0

	pending = []
	for row in await db.fetchall(_COMMUNITIES_SQL) or []:
		cid = row["community_id"]
		have = stored.get(cid, {})
		if set(boards.METRICS) - set(have):
			pending.append((0, cid))       # never computed, or a metric this deploy added
			continue
		marker = min(have.values())
		if marker <= watermarks.get(cid, 0) or marker < now - MAX_AGE:
			pending.append((marker, cid))

	pending.sort()
	return [cid for _at, cid in pending]


async def refresh_community(community_id, now):
	"""Rebuild one community's metric_boards and civ_stats. Returns
	(boards written, uncatalogued boards dropped, civs written, failures).

	Two queries feed every board rather than one per metric: the catalog names
	the source table and column for each metric (boards.METRICS), so the whole
	catalog is fetched per SOURCE and split in Python. Seventeen metrics, two
	queries -- and the catalog stays the single place that knows where a metric
	lives.

	`nick` comes from replay_players.identity, the in-game name observed in the
	replay itself, which is why the game_stats-sourced query joins back to
	replay_players for it. Deliberately not a Discord nickname: a display name
	belongs to a guild member and changes with it, while the name on a board
	entry has to describe the account that played the game (see
	bot/identity.py's profiles_and_names_by_user docstring for the same rule).

	Every metric is isolated from every other and civ_stats from all of them: a
	catalog entry naming a column that a schema change removed must not cost the
	community its other sixteen boards, and it must not cost it civ stats at all.

	THE SOURCE FETCH IS GUARDED SEPARATELY, and its failure does NOT degrade to
	an empty result set. The two queries are generated FROM the catalog, so a
	single bad entry -- a field renamed out from under it -- makes the whole
	query for that source unparseable, not just that metric's slice of it.
	Treating that as "no rows" would rewrite every board on that source as empty
	and it would render as a community where nobody has ever played; every one
	of them is failed instead, so the previous boards stand and the community
	stays pending until somebody fixes the catalog."""
	failed = 0
	value_rows = {}
	for source, sql in (("replay_players", _players_board_sql()), ("game_stats", _stats_board_sql())):
		try:
			value_rows[source] = list(await db.fetchall(sql, [community_id]) or [])
		except Exception as e:
			log.error(f"Derived refresh could not read {source} boards for community "
			          f"{community_id}: {e}")

	written = 0
	for metric_id, meta in boards.METRICS.items():
		try:
			if meta["source"] not in value_rows:
				raise RuntimeError(f"{meta['source']} could not be read this pass")
			rows = [
				dict(user_id=r["user_id"], nick=r.get("nick"), value=r[meta["field"]],
				     replay_match_id=r["replay_match_id"])
				for r in value_rows[meta["source"]] if r.get(meta["field"]) is not None
			]
			await boards.write(community_id, metric_id, boards.compute_board(metric_id, rows), now)
			written += 1
		except Exception as e:
			failed += 1
			log.error(f"Derived refresh board {metric_id} failed for community {community_id}: {e}")

	dropped = 0
	try:
		dropped = await boards.delete_uncatalogued(community_id)
	except Exception as e:
		failed += 1
		log.error(f"Derived refresh could not drop uncatalogued boards for community {community_id}: {e}")

	civs = 0
	try:
		picks = await db.fetchall(_COMMUNITY_PICKS_SQL, [community_id]) or []
		channels = await db.fetchall(_COMMUNITY_CHANNELS_SQL, [community_id]) or []
		counts = civ_stats.compute_civ_stats(list(picks), list(channels)).get(community_id, {})
		await civ_stats.write(community_id, counts, now)
		civs = len(counts)
	except Exception as e:
		failed += 1
		log.error(f"Derived refresh civ_stats failed for community {community_id}: {e}")

	return written, dropped, civs, failed


async def drain_communities(now):
	"""At most ONE community per pass, and only when its inputs have moved.

	One rather than all of them because a community pass scans every game every
	member has played: with several communities enrolled, spreading them over
	consecutive passes keeps any single pass bounded, and the stalest-first
	ordering guarantees each one is reached."""
	blocked = _quarantined(_community_attempts)
	candidates = [cid for cid in await pending_communities(now) if cid not in blocked]
	if not candidates:
		log.debug("Derived refresh communities: nothing pending.")
		return 0

	community_id = candidates[0]
	try:
		written, dropped, civs, failed = await refresh_community(community_id, now)
	except Exception as e:
		n = _note_failure(_community_attempts, community_id)
		log.error(f"Derived refresh community {community_id} failed "
		          f"(attempt {n}/{MAX_ATTEMPTS}): {e}")
		return 0

	if failed:
		n = _note_failure(_community_attempts, community_id)
		log.info(f"Derived refresh community {community_id}: {written} boards written, "
		         f"{dropped} uncatalogued dropped, {civs} civs, {failed} parts failed "
		         f"(attempt {n}/{MAX_ATTEMPTS}), {len(candidates) - 1} communities still pending.")
	else:
		_community_attempts.pop(community_id, None)
		log.info(f"Derived refresh community {community_id}: {written} boards written, "
		         f"{dropped} uncatalogued dropped, {civs} civs, "
		         f"{len(candidates) - 1} communities still pending.")
	return 1


class DerivedRefresh:
	"""Self-isolating tick job, same discipline as bot/derived/backfill.py:
	think() only schedules and can never raise into on_think, the work runs off
	the tick so a pass cannot stall in-flight matches, and _running keeps two
	passes from overlapping."""

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
					log.error(f"Derived refresh crashed: {t.exception()}")

			_tasks.add(task)
			task.add_done_callback(_done)
		except Exception as e:
			self._running = False
			log.error(f"Derived refresh think() error (ignored): {e}")

	async def _run(self, now=None):
		"""One pass: the per-user layer, then the per-community layer only if the
		per-user layer had nothing to do. See the module docstring's cadence
		section for why that debounce is derived from the pass rather than from a
		timer, and why it is gated on "processed nothing" rather than "nothing is
		pending".

		Both drains are wrapped separately so a dead database on one cannot stop
		the other from being attempted at all."""
		now = int(time.time()) if now is None else now
		processed = 0
		try:
			processed = await drain_rollups(now)
		except Exception as e:
			log.error(f"Derived refresh rollup drain error (ignored): {e}")
			return
		if processed:
			return
		try:
			await drain_communities(now)
		except Exception as e:
			log.error(f"Derived refresh community drain error (ignored): {e}")


jobs = DerivedRefresh()
