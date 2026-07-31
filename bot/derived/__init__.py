# -*- coding: utf-8 -*-
"""Derived-global layer (stage 3): per-game facts computed once at ingest
instead of recomputed at render time. Two tables share one grain here --
keyed on (replay_match_id, player_number), never on user_id or community_id.

game_stats  -- medal places, avg/peak eAPM, top units. Table declared and
             written by this package (task 3.2, bot/derived/game_stats.py).
game_labels -- strategy/spawn labels, replacing cls_results (one namespace,
             a `kind` column instead of separate tables). Table declared
             here so its schema lands in the same deploy as game_stats, and
             written by bot/derived/game_labels.py (task 3.3).

Neither table carries a `user_id` column (identity v2 §5): derived-global
keys on `profile_id` only, so a consumer resolves profile -> user through
`identities` at read time, and a late `/link` backfills a player's whole
history with no backfill job of its own. Both are named `replay_match_id`
while the raw rs_* tables this data is computed from still call the same
column `aoe2_match_id` -- task 3.7 renames the raw side to match, so the
derived side is written correctly from the start and never needs a rename.

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
		# rs_player_games.eapm passed through, never a mean of rs_player_apm's
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
