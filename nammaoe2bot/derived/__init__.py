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
             (task 3.2, nammaoe2bot/derived/game_stats.py).
game_labels -- PK (replay_match_id, player_number, label): one row per label a
             player earned, so the grain is finer than game_stats' by exactly
             one column. Strategy/spawn labels, replacing cls_results (one
             namespace, a `kind` column instead of separate tables). Table
             declared here so its schema lands in the same deploy as
             game_stats, and written by nammaoe2bot/derived/game_labels.py (task 3.3).

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
outright in stage 6 rather than renamed, and nammaoe2bot/derived/backfill.py is the one
module that reads across both spellings.

Derived-community:

player_rollups -- PK (community_id, user_id). One blob per player per
             community: medal rates, eAPM medians, and the strategy/spawn/unit
             splits /rank reads in stage 5a. Written by
             nammaoe2bot/derived/rollups.py (task 4.2) and driven by task 4.5's
             refresh job, which is where identity resolution happens: a user's
             profile set is resolved through `identities` and their
             profile-keyed game_stats/game_labels rows aggregated across it.
             An UNLINKED player therefore gets no row at all rather than a row
             of zeros, which is exactly what makes 5a's "Statistics pending
             linking" implementable -- the absence IS the signal (identity v2
             §5). This is the first table in this package to carry user_id and
             community_id, because it is the first one whose grain is a person
             in a community rather than a slot in a match.

metric_boards -- PK (community_id, metric_id). One leaderboard blob per
             metric per community: label/unit/direction plus a leaders list
             (BOARD_MIN_GAMES-gated averages) and a top_games list (single
             best performances, uncapped by the floor). Written by
             nammaoe2bot/derived/boards.py (task 4.3), whose module docstring carries
             the metric catalog and the retention boundary that shapes it:
             only replay_players/game_stats fields, never a replay_techs or
             replay_buildings field the task-4.6 sweeper can delete out from
             under a lean community. Like player_rollups, the caller resolves
             identity before handing rows over -- compute_board never touches
             `identities` itself.

civ_stats   -- PK (community_id, civ). Per-civ win/loss tallies for one
             community, aggregated from `civ_picks` by
             nammaoe2bot/derived/civ_stats.py (task 4.4). Unlike the two tables
             above, this one performs its own community join (civ_picks
             carries no community_id column; the join is a single
             community_channels lookup, not an identities graph walk) --
             see that module's docstring for why that asymmetry with
             player_rollups/metric_boards is deliberate. Name deliberately
             collides in spelling only with the pre-existing
             `nammaoe2bot/features/civs/pools.py` (CSV-backed civ pool randomiser, retired
             stage 5c) -- the two are unrelated and neither imports the
             other.

Imported by bot/__init__.py for the db.ensure_table side effect below, the
same as nammaoe2bot/ingest/__init__.py: ensure_table's sync wrapper drives the
event loop with `loop.run_until_complete(...)`, which only works from the
synchronous import phase before the bot's persistent event loop starts
running (see PUBobot2.py's boot order). Calling it from a lazy import inside
an already-running coroutine -- e.g. only ever importing this package from
inside store.write_match -- would raise "event loop is already running" on
every single ingest."""
from nammaoe2bot.runtime.database import db

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
		# buckets -- see nammaoe2bot/derived/game_stats.py's docstring for why the two
		# must not be conflated.
		dict(cname="avg_eapm", ctype=db.types.int, notnull=False),
		dict(cname="peak_eapm", ctype=db.types.int, notnull=False),
		dict(cname="military_medal", ctype=db.types.int, notnull=False),  # 1|2|3
		dict(cname="villager_medal", ctype=db.types.int, notnull=False),  # 1|2|3
		# Whether the parser measured this player's production at all, i.e.
		# whether card_scoring.assign_medals RANKED them -- the two medal
		# columns above are None both for "ranked and placed fourth" and for
		# "never ranked", and those are different claims. Stored rather than
		# inferred because it is the denominator of player_rollups' medal_rates
		# (nammaoe2bot/derived/rollups.py), and inferring it from a medal or a non-empty
		# top_units silently drops the player who built villagers, no military,
		# and placed outside the top three.
		#
		# notnull=True, joining computed_at as the only two here, and NOT for
		# stylistic symmetry with player_rollups below: this value is always
		# knowable. It is a total function of the source row
		# (COALESCE(villagers,0) + COALESCE(military,0) > 0), so there is no
		# state in which a writer legitimately has nothing to say -- whereas
		# civ/team/winner/eapm all genuinely can be absent from a replay, which
		# is what every notnull=False above is recording.
		# NULL would be a third value meaning "not backfilled", which is exactly
		# the ambiguity this column exists to remove. Deliberately carries no
		# DEFAULT either: the sole writer validates its whole key set (see
		# game_stats.write), so a DEFAULT could only ever be reached by a writer
		# that is already a bug, and it would turn that bug into a silently
		# unranked player instead of a loud error. 008_game_stats_has_production
		# adds this column to the live table under exactly the same definition
		# and backfills it; _ensure_table only ever ADDs missing columns and
		# never alters nullability, so this choice is permanent without another
		# migration.
		dict(cname="has_production", ctype=db.types.bool, notnull=True),
		# [{unit, category, total}], top 3 STYLE units by total, JSON-encoded --
		# gold-costing, non-ubiquitous military units, filtered BEFORE the top-3
		# cut (nammaoe2bot/derived/game_stats.is_style_unit).
		dict(cname="top_units", ctype=db.types.dict, notnull=False),
		dict(cname="computed_at", ctype=db.types.int, notnull=True),
		# The match's own epoch, so a stat row can be placed in time with no
		# join. player_rollups windows the scouting report on this column; see
		# compute_game_stats' docstring for why it is stamped here rather than
		# joined from game_labels or parsed out of replay_matches at read time.
		# Nullable because 19 production matches genuinely have no recorded date,
		# and rollups reads a NULL as "outside every window" rather than "now".
		dict(cname="played_at", ctype=db.types.int, notnull=False),
		# Which revision of compute_game_stats wrote this row. NOT NULL for the
		# same reason has_production is: it is always knowable, and a NULL would
		# be a third value meaning "not backfilled" -- exactly the ambiguity the
		# column exists to remove. 010_game_stats_played_at seeds it to 0 on
		# every historical row, which makes all of them differ from
		# game_stats.COMPUTE_VERSION and so pending for the reconciliation loop,
		# which then rewrites them with the current compute. That is the whole
		# mechanism: see game_stats.COMPUTE_VERSION and backfill._STATS_SRC.
		dict(cname="compute_version", ctype=db.types.int, notnull=True),
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
		# The five-block contract in nammaoe2bot/derived/rollups.py, JSON-encoded.
		dict(cname="rollup", ctype=db.types.dict, notnull=True),
		dict(cname="computed_at", ctype=db.types.int, notnull=True),
	],
	primary_keys=["community_id", "user_id"],
))

db.ensure_table(dict(
	tname="metric_boards",
	# Every non-key column is notnull for the same reason as player_rollups
	# above: a board row exists only because a refresh pass computed one.
	columns=[
		dict(cname="community_id", ctype=db.types.int),
		dict(cname="metric_id", ctype=db.types.str),
		# The board contract in nammaoe2bot/derived/boards.py -- {label, unit,
		# direction, leaders, top_games} -- JSON-encoded.
		dict(cname="board", ctype=db.types.dict, notnull=True),
		dict(cname="computed_at", ctype=db.types.int, notnull=True),
	],
	primary_keys=["community_id", "metric_id"],
))

db.ensure_table(dict(
	tname="civ_stats",
	# Every non-key column is notnull: a row exists only because
	# nammaoe2bot/derived/civ_stats.py's write() put it there for a civ that had at
	# least one resolved-result pick, and games/wins/losses are always
	# knowable once that is true (unlike, say, replay_players' avg_eapm,
	# which can genuinely be absent from a replay).
	columns=[
		dict(cname="community_id", ctype=db.types.int),
		dict(cname="civ", ctype=db.types.str),
		dict(cname="games", ctype=db.types.int, notnull=True),
		dict(cname="wins", ctype=db.types.int, notnull=True),
		dict(cname="losses", ctype=db.types.int, notnull=True),
		dict(cname="computed_at", ctype=db.types.int, notnull=True),
	],
	primary_keys=["community_id", "civ"],
))

# Imported last, after every ensure_table declaration above, exactly like
# nammaoe2bot/ingest/__init__.py's trailing `from .jobs import jobs`: backfill
# reads and writes the two derived-global tables, so their schemas must be
# settled before the job singleton it exposes can ever run. bot/events.py's
# on_think drives it as `nammaoe2bot.derived.jobs.think(frame_time)`.
from .backfill import jobs  # noqa: E402,F401  (DerivedBackfill singleton)

# The derived-COMMUNITY counterpart, exported under its own name rather than
# replacing `jobs`: the two loops are independent and bot/events.py drives both.
# `jobs` keeps the bare name it has had since stage 3 so that call site (and the
# tests pinning it) do not have to move for a rename that buys nothing.
# refresh imports nammaoe2bot.features.identity.resolver, which is already imported by this point --
# bot/__init__.py pulls in nammaoe2bot.ingest (whose store.py imports it) before
# this package.
from .refresh import jobs as refresh_jobs  # noqa: E402,F401  (DerivedRefresh singleton)

# The retention sweeper (task 4.6) -- the counterpart to both loops above and
# the only job in this package that DELETES rather than recomputes. It reads the
# derived-community tables declared above as its proof that a lean community's
# summary exists before the raw detail behind it is destroyed, so it is imported
# last for the same reason the other two are. Ships with DRY_RUN = True; read
# nammaoe2bot/derived/sweeper.py before changing that.
from .sweeper import jobs as sweeper_jobs  # noqa: E402,F401  (RetentionSweeper singleton)
