# -*- coding: utf-8 -*-
"""Derived-community metric_boards: one leaderboard per (community, metric),
aggregated once by a refresh pass instead of scanned live every time stage-5
renders a leaderboard command. compute_board is pure -- no DB, no I/O, and no
idea where its rows came from or how they were resolved to a community and a
user -- same discipline as bot/derived/rollups.py's compute_rollup, and for
the same reason: attribution is a job with its own failure modes (a user may
own several AoE2 profiles, and a community's boundary is its enrolled
channels), and neither belongs inside a pure aggregation that has to be
trivially testable without a database.

THE CATALOG IS A HARD BOUNDARY, not a convenience list. Every metric_id below
resolves to a column on `replay_players` or `game_stats` -- both retained
FOREVER (core/data_registry.py) -- and nothing else. The old quiz catalog
(utils/replay_quiz/build_db.py's build_metrics(), now retired) additionally
scored tech-click-timing and building-count metrics sourced from
`replay_techs`/`replay_buildings`, both `retention="sweepable"`: task 4.6's
sweeper deletes rows from those two tables once a lean community's linked
replays age out. A board built on a dropped field would not error -- it would
silently empty out, one row at a time, as the sweeper ran, and nothing here
would ever notice. Do NOT add a tech or building metric back to this catalog;
if one is ever wanted again, it needs its own retention decision first, not a
quiet re-add here.

`direction` ("high" or "low") records which end of the range wins, per metric,
so the stage-5 renderer never has to hardcode "eapm is descending but
first_tc_s is ascending" -- that knowledge lives exactly once, here.
"""
import json

from core.database import db
from bot.derived.rollups import BOARD_MIN_GAMES  # noqa: F401  (re-exported: see docstring below)

# Kept top single-game performances per board. 3, not an arbitrary round
# number: the retired old quiz catalog's `metric_top_games` table kept
# exactly the top 3 per metric (utils/replay_quiz/build_db.py), and nothing
# about a leaderboard's "hall of fame" slice needs to differ from that
# precedent now that the metric itself has moved tables.
TOP_GAMES_LIMIT = 3

# metric_id -> {label, unit, direction, source, field}. `source`/`field` name
# the exact column a future caller SELECTs (never read by compute_board
# itself, which only ever sees already-resolved {user_id, nick, value,
# replay_match_id} rows) -- they exist so nobody has to reverse-engineer the
# catalog from a SELECT statement written somewhere else, and so
# test_the_catalog_only_contains_permitted_fields has something concrete to
# check against the retention boundary above.
#
# Directions for the fields inherited from the old quiz catalog are UNCHANGED
# from it (villagers/military-family counts: high; the four `_s` age/rush
# timings: low) -- confirmed against utils/replay_quiz/build_db.py's
# build_metrics(), not re-guessed. eapm/peak_eapm postdate that catalog
# entirely (the eAPM pipeline shipped after it was written) and follow the
# binding contract's own example instead: "first_tc_s low is good, eapm high
# is good".
METRICS = {
	"villagers": dict(
		label="Most villagers", unit="count", direction="high",
		source="replay_players", field="villagers"),
	"vil_pre_feudal": dict(
		label="Most villagers before Feudal", unit="count", direction="high",
		source="replay_players", field="vil_pre_feudal"),
	"vil_pre_castle": dict(
		label="Most villagers before Castle", unit="count", direction="high",
		source="replay_players", field="vil_pre_castle"),
	"vil_pre_imperial": dict(
		label="Most villagers before Imperial", unit="count", direction="high",
		source="replay_players", field="vil_pre_imperial"),
	"military": dict(
		label="Biggest army", unit="count", direction="high",
		source="replay_players", field="military"),
	"mil_pre_feudal": dict(
		label="Most military before Feudal", unit="count", direction="high",
		source="replay_players", field="mil_pre_feudal"),
	"mil_pre_castle": dict(
		label="Most military before Castle", unit="count", direction="high",
		source="replay_players", field="mil_pre_castle"),
	"mil_pre_imperial": dict(
		label="Most military before Imperial", unit="count", direction="high",
		source="replay_players", field="mil_pre_imperial"),
	"feudal_s": dict(
		label="Fastest to Feudal Age", unit="seconds", direction="low",
		source="replay_players", field="feudal_s"),
	"castle_s": dict(
		label="Fastest to Castle Age", unit="seconds", direction="low",
		source="replay_players", field="castle_s"),
	"imperial_s": dict(
		label="Fastest to Imperial Age", unit="seconds", direction="low",
		source="replay_players", field="imperial_s"),
	"first_tc_s": dict(
		label="Fastest first Town Center", unit="seconds", direction="low",
		source="replay_players", field="first_tc_s"),
	"eapm": dict(
		label="Highest average eAPM", unit="eapm", direction="high",
		source="replay_players", field="eapm"),
	"peak_eapm": dict(
		label="Highest peak eAPM", unit="eapm", direction="high",
		source="game_stats", field="peak_eapm"),
	# The two medal columns, reused as-is: game_stats.military_medal /
	# villager_medal are already the ordinal place (1=gold, 2=silver,
	# 3=bronze) a player earned IN a game that ranked them -- see
	# rollups.was_medal_eligible for what "ranked" means. Treating the stored
	# rank itself as the metric's per-game "value" needs no invented number:
	# direction "low" falls out of the existing 1-is-best convention the same
	# way `first_tc_s` does, so "best average medal placement" is directly
	# comparable in shape to every timing metric above it. A row only exists
	# for a game where the axis actually produced a medal -- an unranked or
	# fourth-place game contributes no row, the same exclusion
	# was_medal_eligible encodes for player_rollups' denominator.
	"military_medal": dict(
		label="Best military-medal average", unit="place", direction="low",
		source="game_stats", field="military_medal"),
	"villager_medal": dict(
		label="Best villager-medal average", unit="place", direction="low",
		source="game_stats", field="villager_medal"),
	# top_units is DELIBERATELY not in this catalog. It is a list of
	# {unit, category, total} per game, not a scalar -- there is no single
	# number that compares one player's "43 Skirmishers" game to another's
	# "20 Elite Cataphracts" game, and the board contract's leaders/top_games
	# entries have no room for a unit name alongside `value` (both are
	# fixed-key dicts, validated exactly by write()). Fabricating a scalar
	# for it (e.g. "total of your #1 unit, whatever it was") would compare
	# incomparable things under one misleading number. If a unit-specific
	# board is ever wanted ("most Knights in a game"), that is a catalog
	# entry PER UNIT, a real design decision for whoever builds it, not a
	# default this module should invent.
}


def _direction_key(value, direction):
	"""Sort key so `sorted(..., key=_direction_key)` always puts the WINNING
	value first, whether winning means highest or lowest. Isolated in one
	place so a direction bug can only ever be here, not duplicated (and
	drifting) between the leaders sort and the top_games sort."""
	return -value if direction == "high" else value


def compute_board(metric_id, value_rows, min_games=BOARD_MIN_GAMES):
	"""One community's whole board for ONE metric. Pure: no DB, no I/O.

	`value_rows` are already resolved to a community and a user by the
	caller -- {user_id, nick, value, replay_match_id}, one row per game the
	metric has a value for. A game the metric has no value for (a null
	`first_tc_s` on a truncated game, a game nobody was ranked in for a medal
	metric) is simply absent from `value_rows`; it is never represented here
	as a None. Where these rows come from -- which table, which identity
	resolution across a user's linked profiles -- is deliberately not this
	function's job, for the same reason compute_rollup's stat_rows/label_rows
	are handed over pre-resolved: a community's channel set and a user's
	profile set are both non-trivial to resolve, and a pure aggregation has
	no business knowing how.

	`min_games` defaults to BOARD_MIN_GAMES (imported from rollups, never
	redefined here) and stays an argument so tests can pin the floor's
	behaviour rather than the value.

	Returns the board contract: {label, unit, direction, leaders, top_games}.
	`leaders` holds every user whose game count for this metric clears
	`min_games` -- uncapped, exactly like compute_rollup's strategy/spawn/unit
	lists, because BOARD_MIN_GAMES already suppresses the low-sample noise a
	cap would otherwise exist to hide. `top_games` is capped at
	TOP_GAMES_LIMIT and NOT gated by `min_games`: a single standout game from
	a low-sample player is the entire point of a "top games" list, as
	distinct from "leaders".
	"""
	if metric_id not in METRICS:
		raise ValueError(f"metric_boards: unknown metric_id {metric_id!r}, expected one of {sorted(METRICS)}")
	meta = METRICS[metric_id]
	direction = meta["direction"]

	per_user = {}
	all_games = []
	for row in value_rows:
		uid = row["user_id"]
		entry = per_user.setdefault(uid, dict(values=[], name_events=[]))
		entry["values"].append(row["value"])
		# (replay_match_id, nick) recorded per row rather than tracked
		# incrementally: picking the CURRENT nick with a single max() over
		# the whole list, below, makes the choice depend only on the data
		# (highest replay_match_id wins; a nick tie-breaks lexicographically
		# on the vanishingly rare chance two rows share a match id) -- never
		# on which row the loop happened to visit first (see the ordering
		# test).
		entry["name_events"].append((row.get("replay_match_id"), row.get("nick") or ""))
		all_games.append(row)

	leaders = [
		dict(user_id=uid, nick=max(entry["name_events"])[1],
		     avg=round(sum(entry["values"]) / len(entry["values"]), 2), n=len(entry["values"]))
		for uid, entry in per_user.items()
		if len(entry["values"]) >= min_games
	]
	leaders.sort(key=lambda e: (_direction_key(e["avg"], direction), e["user_id"]))

	top_games = [
		dict(user_id=row["user_id"], nick=row.get("nick"), value=row["value"],
		     replay_match_id=row.get("replay_match_id"))
		for row in all_games
	]
	top_games.sort(key=lambda g: (_direction_key(g["value"], direction), g["user_id"], g["replay_match_id"]))
	top_games = top_games[:TOP_GAMES_LIMIT]

	return dict(label=meta["label"], unit=meta["unit"], direction=direction,
	            leaders=leaders, top_games=top_games)


_BOARD_KEYS = ("label", "unit", "direction", "leaders", "top_games")
_COLUMNS = ("community_id", "metric_id", "board", "computed_at")


async def write(community_id, metric_id, board, computed_at):
	"""Store one community's board for one metric. Idempotent.

	A single REPLACE, not the delete-then-insert game_stats/game_labels use:
	this table's grain is exactly one row per (community_id, metric_id) --
	the primary key IS the argument list, mirroring player_rollups exactly
	(see that module's write() docstring for why a REPLACE has no window a
	delete-then-insert would need to close, on an adapter that runs
	autocommit with no transaction surface).

	`board` is validated against the contract's five keys and `metric_id`
	against the catalog, both loudly rather than coerced: a blob missing a
	key, or written under an id no reader's catalog recognises, would not
	fail here -- it would render as a leaderboard silently missing from the
	stage-5 command with no error anywhere.
	"""
	if metric_id not in METRICS:
		raise ValueError(f"metric_boards: refusing to write unknown metric_id {metric_id!r}")
	if set(board or {}) != set(_BOARD_KEYS):
		raise ValueError(
			f"metric_boards blob for community {community_id} metric {metric_id} has keys "
			f"{sorted(board or {})}, expected exactly {sorted(_BOARD_KEYS)}")
	row = dict(community_id=community_id, metric_id=metric_id,
	           board=json.dumps(board, sort_keys=True), computed_at=computed_at)
	await db.insert("metric_boards", {c: row[c] for c in _COLUMNS}, on_duplicate="replace")
