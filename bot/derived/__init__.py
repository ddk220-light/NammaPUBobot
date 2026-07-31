# -*- coding: utf-8 -*-
"""Derived layers. Two generations of table live in this package and the
difference between them is the point of stage 4, so read the split first:

DERIVED-GLOBAL (stage 3) -- per-game facts computed once at ingest instead of
recomputed at render time. Keyed on the match and the player within it, never
on user_id or community_id.

DERIVED-COMMUNITY (stage 4) -- per-player aggregates OF those facts, keyed on
(community_id, user_id). A separate layer rather than more columns on the
global one, because the same game means different things to different
communities: a
community's rollup is scoped to its own channels, and two communities sharing a
player must be able to disagree about that player's numbers without either one
rewriting a fact about the game itself.

Derived-global:

game_stats  -- PK (replay_match_id, player_number). Medal places, avg/peak
             eAPM, top units. Table declared and written by this package
             (task 3.2, bot/derived/game_stats.py).
game_labels -- PK (replay_match_id, player_number, label): one row per label a
             player earned, so the grain is finer than game_stats' by exactly
             one column. Strategy/spawn labels, replacing cls_results (one
             namespace, a `kind` column instead of separate tables). Table
             declared here so its schema lands in the same deploy as
             game_stats, and written by bot/derived/game_labels.py (task 3.3).

Neither table carries a `user_id` column (identity v2 §5). game_stats reaches
a Discord user through its nullable `profile_id`, resolved via `identities` at
read time, so a late `/link` backfills a player's whole history with no
backfill job of its own. game_labels carries no `profile_id` either: a label is
a fact about a slot in a match, and the profile behind that slot is already
recorded once per match on game_stats (and on the raw replay_players row it is
computed from) -- duplicating it here would be a second copy to keep correct
for no read it enables. Both are named `replay_match_id`, which is also what
the raw replay_* tables this data is computed from now call the same column:
these two were written that way from the start and 007_raw_renames brought the
raw side into line, so neither derived table ever needed a rename of its own.
The legacy cls_* tables still spell it `aoe2_match_id` -- they are retired
outright in stage 6 rather than renamed, and bot/derived/backfill.py is the one
module that reads across both spellings.

Derived-community:

player_rollups -- PK (community_id, user_id). One blob per player per
             community: medal rates, eAPM medians, and the strategy/spawn/unit
             splits /rank reads in stage 5a. Written by
             bot/derived/rollups.py (task 4.2) and driven by task 4.5's
             refresh job, which is where identity resolution happens: a user's
             profile set is resolved through `identities` and their
             profile-keyed game_stats/game_labels rows aggregated across it.
             An UNLINKED player therefore gets no row at all rather than a row
             of zeros, which is exactly what makes 5a's "Statistics pending
             linking" implementable -- the absence IS the signal (identity v2
             §5). This is the first table in this package to carry user_id and
             community_id, because it is the first one whose grain is a person
             in a community rather than a slot in a match.

Imported by bot/__init__.py for the db.ensure_table side effect below, the
same as bot/replay_stats/__init__.py: ensure_table's sync wrapper drives the
event loop with `loop.run_until_complete(...)`, which only works from the
synchronous import phase before the bot's persistent event loop starts
running (see PUBobot2.py's boot order). Calling it from a lazy import inside
an already-running coroutine -- e.g. only ever importing this package from
inside store.write_match -- would raise "event loop is already running" on
every single ingest."""
from core.database import db

db.ensure_table(dict(
	tname="game_stats",
	columns=[
		dict(cname="replay_match_id", ctype=db.types.int),
		dict(cname="player_number", ctype=db.types.int),
		dict(cname="profile_id", ctype=db.types.int, notnull=False),
		dict(cname="civ", ctype=db.types.str, notnull=False),
		dict(cname="team", ctype=db.types.str, notnull=False),
		dict(cname="winner", ctype=db.types.bool, notnull=False),
		# replay_players.eapm passed through, never a mean of replay_apm's
		# buckets -- see bot/derived/game_stats.py's docstring for why the two
		# must not be conflated.
		dict(cname="avg_eapm", ctype=db.types.int, notnull=False),
		dict(cname="peak_eapm", ctype=db.types.int, notnull=False),
		dict(cname="military_medal", ctype=db.types.int, notnull=False),  # 1|2|3
		dict(cname="villager_medal", ctype=db.types.int, notnull=False),  # 1|2|3
		# [{unit, category, total}], top 3 military-only units by total, JSON-encoded.
		dict(cname="top_units", ctype=db.types.dict, notnull=False),
		dict(cname="computed_at", ctype=db.types.int, notnull=True),
	],
	primary_keys=["replay_match_id", "player_number"],
))

db.ensure_table(dict(
	tname="game_labels",
	# Declared now so both derived tables land in the same schema-only deploy;
	# task 3.3 owns everything that writes here (the strategy/spawn allowlist,
	# label_rows(), and the write() call from the classification path).
	columns=[
		dict(cname="replay_match_id", ctype=db.types.int),
		dict(cname="player_number", ctype=db.types.int),
		dict(cname="label", ctype=db.types.str),
		dict(cname="kind", ctype=db.types.str, notnull=True),  # 'strategy' | 'spawn'
		dict(cname="evidence", ctype=db.types.dict, notnull=False),
		dict(cname="played_at", ctype=db.types.int, notnull=False),
	],
	primary_keys=["replay_match_id", "player_number", "label"],
))

db.ensure_table(dict(
	tname="player_rollups",
	# Every non-key column is notnull: a rollup row exists only because the
	# refresh job computed one, and each of these three is part of that
	# computation's output. _ensure_table only ever ADDs missing columns and
	# never alters nullability, so a column shipped nullable stays nullable
	# until a migration tightens it -- which is why the intent is stated here,
	# now, rather than left to the default.
	columns=[
		dict(cname="community_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		# Total game_stats rows behind the blob, i.e. every game the user
		# played on any profile they own. A column rather than a key inside
		# the blob so task 4.7 can reconcile it against a COUNT(*) without
		# parsing JSON, and so a later "who has enough games?" query does not
		# have to.
		dict(cname="games", ctype=db.types.int, notnull=True),
		# The five-block contract in bot/derived/rollups.py, JSON-encoded.
		dict(cname="rollup", ctype=db.types.dict, notnull=True),
		dict(cname="computed_at", ctype=db.types.int, notnull=True),
	],
	primary_keys=["community_id", "user_id"],
))

# Imported last, after every ensure_table declaration above, exactly like
# bot/replay_stats/__init__.py's trailing `from .jobs import jobs`: backfill
# reads and writes the two derived-global tables, so their schemas must be
# settled before the job singleton it exposes can ever run. bot/events.py's
# on_think drives it as `bot.derived.jobs.think(frame_time)`.
from .backfill import jobs  # noqa: E402,F401  (DerivedBackfill singleton)
