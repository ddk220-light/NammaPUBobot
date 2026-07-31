# -*- coding: utf-8 -*-
"""Derived-global game_stats: per-player facts computed once at ingest instead
of re-derived every time a card renders. compute_game_stats is pure -- no DB,
no I/O -- so the exact same function drives both the live write below (called
from bot/replay_stats/store.py right after a match parses) and the stage-3.4
reconciliation loop that backfills the 1126 already-ingested historical
matches. Neither caller teaches the function anything about where its input
rows came from, which is what lets one implementation serve both."""
import json
import re

from core.database import db

# Bumped whenever THIS function's output changes for unchanged input. Stored on
# every row, and bot/derived/backfill.py's pending predicate compares the stored
# version against this constant -- so changing the compute below and bumping this
# is all it takes for the reconciliation loop to rewrite every stale row on its
# own.
#
# WHY THIS EXISTS. The loop's predicate is a set comparison of the ROWS each side
# implies, which is structurally blind to a change in what a row CONTAINS: adding
# has_production (008) and switching top_units to style units (010) both changed
# every row's value while changing neither side's row set, so both compared equal
# forever and needed a hand-written migration to bypass the loop. 008's docstring
# calls that out and argues a migration beats "a second, column-level predicate".
# It is right about the column-level predicate and this is not one: the version
# travels in the EXISTING (mid, pn, tag) tuple the comparison already groups on
# (see backfill._STATS_SRC), so a stale row is a plain set difference like any
# other, with no new query machinery and the same termination proof. A migration
# still has to seed the column, but it no longer has to reimplement this compute
# in SQL -- which for top_units would mean a second, silently divergent copy of
# the style-unit rules below.
COMPUTE_VERSION = 2

# Unit lines that cost no gold -- AoE2's "trash" units. Excluded from top_units
# because they are what a player is FORCED into (a counter, or an empty gold
# pile), not what they chose, and the whole point of the stored list is to say
# something about how somebody plays. In production these are three of the six
# most-built units in the game, so leaving them in means the scouting report
# tells most players they mass Spearman.
#
# Categories rather than unit names, because the category is exactly the "line":
# utils/replay/extract.classify_unit already folds Scout Cavalry, Light Cavalry,
# Hussar and the Camel/Eagle scouts into `scout`, and Spearman/Pikeman/
# Halberdier into `spearman_line`. A name list would have to be extended for
# every civ-specific variant and would silently miss the next one.
TRASH_CATEGORIES = ("scout", "skirmisher", "spearman_line")

# Gold units excluded for the OTHER reason: they are ubiquitous. Both are
# siege, both are built by nearly everybody in small numbers as a means to an
# end rather than as a plan, and a unit nearly everybody builds separates
# nobody -- it just crowds out the unit that would have. Measured on
# production: Trebuchet is the 2nd most common military unit in the database
# (2324 player-games, ~5 built at a time) and Battering Ram the 7th (1391),
# and between them they took 9 of the 42 wins-most/loses-most unit clauses.
#
# MATCHED ON WORD BOUNDARIES, NOT AS A BARE SUBSTRING, and that is not
# fastidiousness: "Arambai" contains the letters r-a-m. A substring test for
# "ram" silently deletes a real unique unit -- one that currently holds a
# wins-most clause -- while looking entirely correct. \b also still catches
# every multi-word spelling that matters: Traction/Mounted Trebuchet, and
# Capped/Siege Ram.
UBIQUITOUS_UNITS = ("trebuchet", "ram")

_UBIQUITOUS_RE = re.compile(
	r"\b(?:" + "|".join(re.escape(token) for token in UBIQUITOUS_UNITS) + r")\b", re.I)


def is_style_unit(unit_row):
	"""Whether a replay_units row says anything about how this player CHOOSES to
	play, i.e. belongs in top_units. Pure.

	Military, gold-costing, and not ubiquitous -- see TRASH_CATEGORIES and
	UBIQUITOUS_UNITS for why each exclusion is here. Applied BEFORE the top-3
	cut below, which is the load-bearing part: filtering the stored list at read
	time instead would leave a player whose three most-built units are Spearman,
	Scout Cavalry and Skirmisher with no unit line at all, and in production the
	first style unit sits outside the unfiltered top 3 for 1429 of 6061
	player-games. The cut has to happen after the filter or the filter mostly
	deletes rather than selects."""
	if not unit_row.get("is_military"):
		return False
	if unit_row.get("category") in TRASH_CATEGORIES:
		return False
	return not _UBIQUITOUS_RE.search(str(unit_row.get("unit") or ""))


def compute_game_stats(players, units, apm, computed_at, played_at=None):
	"""Per-player derived facts for one match. Pure: no DB, no I/O.

	`players` are replay_players-shaped dicts, `units` replay_units-shaped,
	`apm` replay_apm-shaped. Returns one row per player, keyed by
	player_number, with NO replay_match_id -- the caller stamps that, so the
	same function serves the live ingest and the backfill without either one
	teaching it where the id comes from.

	`played_at` is the match's epoch, stamped onto every row so a stat row can
	be placed in time WITHOUT a join. player_rollups windows the scouting report
	to the last WINDOW_DAYS (bot/derived/rollups.py), and the two obvious
	alternatives both decide that window with the wrong thing: joining
	game_labels means a game is dated only if some classifier happened to fire on
	it, and joining replay_matches means parsing a VARCHAR date string on every
	read. Passed in rather than derived, for the same reason replay_match_id is:
	the live path already holds the bot match's epoch and the backfill reads the
	raw table's, and neither should teach this function about the other.
	None is legitimate -- 19 production matches have no recorded date -- and
	rollups treats an undated game as outside every window rather than inside the
	current one.

	Precondition, not enforced here: `bot.replay_stats` must already be
	imported somewhere in the process before the first call, because the lazy
	`from bot.replay_stats import card_scoring` below runs that package's
	`db.ensure_table` side effect on its first import -- fine from the bot's
	synchronous boot phase, but an offline `utils/` script (e.g. task 3.4's
	backfill) that imports this module standalone must import bot.replay_stats
	itself first, or hit "event loop is already running".
	"""
	from bot.replay_stats import card_scoring

	# `has_production` is defined HERE and nowhere else. It decides two things
	# at once -- whether assign_medals ranks this player, and whether the game
	# belongs in player_rollups' medal-rate denominator -- and those two must be
	# the same predicate by construction, not by two implementations agreeing.
	# So the row below reads it back off this payload rather than recomputing
	# it; a second copy of the expression is how the stored flag would drift
	# from the medals it explains.
	payloads = [dict(
		player_number=p.get("player_number"),
		military=p.get("military"),
		villagers=p.get("villagers"),
		has_production=bool((p.get("villagers") or 0) + (p.get("military") or 0)),
	) for p in players]
	medals = card_scoring.assign_medals(payloads)

	# Peak eAPM is a plain MAX over the per-minute buckets -- genuinely "eAPM"
	# because apm_buckets already applies mgz's effective-APM filter before
	# storing a bucket. Absent entirely for historical rows: the bucket
	# feature shipped after the last pre-backfill match was played, so the
	# backfill's peak_eapm is legitimately None, not a bug.
	peak = {}
	for a in apm:
		pn, n = a.get("player_number"), a.get("actions") or 0
		if pn is not None and n > peak.get(pn, -1):
			peak[pn] = n

	# top_units is STYLE units only -- see is_style_unit. Unfiltered, "top 3 by
	# total" is Villager plus two others for every player alive; military-only
	# but trash-inclusive, it is Spearman and Scout Cavalry for most of them.
	# Neither says anything about how somebody plays.
	tops = {}
	for u in units:
		if not is_style_unit(u):
			continue
		tops.setdefault(u.get("player_number"), []).append(u)
	for lst in tops.values():
		lst.sort(key=lambda u: (-(u.get("total") or 0), str(u.get("unit") or "")))

	rows = []
	for i, p in enumerate(players):
		pn = p.get("player_number")
		rows.append(dict(
			player_number=pn,
			profile_id=p.get("profile_id"),
			civ=p.get("civ"),
			team=p.get("team"),
			winner=p.get("winner"),
			# replay_players.eapm passed through unchanged -- never a mean of
			# the buckets above. Bucket rows are absent for zero-action
			# minutes, so averaging only the buckets that exist overstates;
			# bot/replay_stats/apm_query.py computes a deliberately different
			# `mean_active` for charts and documents that the two must not be
			# conflated.
			avg_eapm=p.get("eapm"),
			peak_eapm=peak.get(pn),
			military_medal=medals[i]["military_medal"],
			villager_medal=medals[i]["villager_medal"],
			# Indexed the same way medals[i] is, off the payload list built in
			# the same order as `players` -- never recomputed. See above.
			has_production=payloads[i]["has_production"],
			top_units=[dict(unit=u.get("unit"), category=u.get("category"),
			                total=u.get("total")) for u in tops.get(pn, [])[:3]],
			computed_at=computed_at,
			played_at=played_at,
			compute_version=COMPUTE_VERSION,
		))
	return rows


# The column order every payload row is emitted in -- see the identical note in
# bot/derived/game_labels.py: insert_many builds its column list from the FIRST
# row's keys and zips every other row's .values() against it, so rows whose keys
# are in a different order write values into the wrong columns with no error.
_COLUMNS = ("replay_match_id", "player_number", "profile_id", "civ", "team", "winner",
            "avg_eapm", "peak_eapm", "military_medal", "villager_medal", "has_production",
            "top_units", "computed_at", "played_at", "compute_version")


async def write(replay_match_id, rows):
	"""Idempotent per-match write: DELETE this match's rows, then insert what
	compute_game_stats returned, stamping replay_match_id onto each row (the
	pure function above deliberately never sees it -- see its docstring).

	Mirrors bot/replay_stats/store.py's write_match delete-then-insert for the
	same reason: a match can be re-ingested (parser bump, manual retry, or the
	stage-3.4 backfill correcting a stale row) and the stored set must exactly
	match the latest compute, never accumulate leftovers from a run with a
	different player count.

	ACCEPTED TRADEOFF, deliberate: the DELETE and the INSERT are not one
	transaction, because the adapter runs in autocommit (see
	core/DBAdapters/mysql.py's connect) and has no transaction surface to reach
	for. If the insert fails after the delete succeeded, this match briefly has
	no stored medals and its card renders bare -- and bot/derived/backfill.py's
	reconciliation loop notices the set difference and rewrites it within
	POLL_INTERVAL. Making this atomic means giving the adapter transactions,
	which is a change to every writer in the bot, for a window that already
	self-heals. Do not "fix" it here.
	"""
	await db.execute("DELETE FROM game_stats WHERE replay_match_id=%s", [replay_match_id])
	if not rows:
		return
	payload = []
	for r in rows:
		row = dict(r)
		row["replay_match_id"] = replay_match_id
		row["top_units"] = json.dumps(row.get("top_units") or [], sort_keys=True)
		if set(row) != set(_COLUMNS):
			# Loud, not coerced -- see game_labels.write for why.
			raise ValueError(
				f"game_stats row for match {replay_match_id} has keys {sorted(row)}, "
				f"expected exactly {sorted(_COLUMNS)}")
		payload.append({c: row[c] for c in _COLUMNS})
	await db.insert_many("game_stats", payload, on_duplicate="replace")
