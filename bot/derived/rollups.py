# -*- coding: utf-8 -*-
"""Derived-community player_rollups: everything the stage-5 scouting report
prints about one player in one community, aggregated once by the refresh job
instead of re-derived on every /rank.

compute_rollup is pure -- no DB, no I/O, and no idea where its rows came from
-- which is the whole point: the stage-4.5 refresh job resolves a user's
profile set through `identities` and hands over the union of their game_stats
and game_labels rows, and NOTHING here re-splits on profile_id. A user may own
several AoE2 profiles (`/identity link ... additional: True`); if the
aggregation re-keyed on profile, a multi-account player's history would halve
and both halves would then fall below SPLIT_MIN_GAMES and vanish from the
report entirely, with no error anywhere.

The two floors live here rather than in the refresh job because they are
properties of what is honest to SHOW, not of how often it is recomputed. Both
were calibrated against the live derived layer (task 4.1), not guessed: at 5,
662 splits survive across 39 of 42 users; at 3 the count only rises to 740 and
starts admitting 3-game "tendencies"; at 8 it falls to 568 and starts costing
real users their strategy line. BOARD_MIN_GAMES=3 clears every user (the
quietest has 6 games) and gates task 4.3's metric_boards, which imports it
from here so the two derived-community modules cannot drift apart.
"""
import json
import statistics

from core.database import db

SPLIT_MIN_GAMES = 5
BOARD_MIN_GAMES = 3

# The five blocks a rollup carries. Enforced by write() rather than assumed,
# for the same reason game_stats/game_labels validate their row key sets: a
# blob missing a block does not fail anywhere, it just renders as a silently
# shorter scouting report.
_ROLLUP_KEYS = ("medal_rates", "apm", "strategies", "spawns", "units")

# Rates are stored rounded to 4 decimal places -- 100x finer than any renderer
# needs, and the exact counts stay recoverable because games_ranked ships
# beside them. Full float quotients would be equally deterministic but turn
# every hand-inspected blob (task 4.7 spot-checks these) into noise.
_RATE_PLACES = 4


def top_units_of(stat_row):
	"""The row's top_units as a list, whether it arrived as the MEDIUMTEXT
	JSON string a SELECT returns or as the list compute_game_stats builds.

	Both shapes are real: the refresh job reads rows out of game_stats, while
	a caller testing against freshly computed rows has never round-tripped
	them through MySQL. A malformed string raises rather than degrading to
	[] -- silently reporting "no units" for a corrupt row is indistinguishable
	from a player who built none, and the refresh job's per-user guard turns
	the raise into a logged, visible failure."""
	raw = stat_row.get("top_units")
	if isinstance(raw, str):
		raw = json.loads(raw)
	return raw or []


def was_medal_eligible(stat_row):
	"""Whether this game ranked the player at all, i.e. belongs in the
	medal-rate denominator.

	KNOWN APPROXIMATION, and the one place this module cannot be exact.
	card_scoring.assign_medals ranks only players with production
	(`villagers + military > 0`) and awards the top three per axis, so the
	true denominator is "games in which this player had production" --
	including games they had production and placed fourth. game_stats does
	not store that flag (compute_game_stats computes it for the medal payload
	and discards it), so eligibility is inferred from the evidence the stored
	row does carry: a medal on either axis, or any military unit in
	top_units. Both imply the parser measured this player's production; a
	game where NOBODY was ranked leaves every player with neither.

	The residual miss is a player who built villagers, no military at all,
	and placed outside the top three on villagers -- a player who died in the
	first minutes. They drop out of the denominator, which nudges their own
	rates up slightly.

	What this must NOT be is "games where the player won a medal". That
	denominator makes every rate a conditional probability, inflates it by
	roughly the reciprocal of the medal rate, and is arithmetically
	impossible against the binding contract's own example: military 0.34 plus
	villager 0.18 is 0.52, and rates over a denominator where every game
	carries at least one medal must sum to at least 1.

	The exact fix, when it is worth a migration: store has_production on
	game_stats and read it here."""
	return (stat_row.get("military_medal") is not None
	        or stat_row.get("villager_medal") is not None
	        or bool(top_units_of(stat_row)))


def _median(values):
	"""Median over the non-null values, or None when there are none.

	Never a mean, on either axis: eAPM is heavy-tailed -- one 300-eAPM game
	(a frantic late-game teamfight, or a parse artefact) moves a season's
	mean by tens of points and moves its median by nothing. An even-length
	sample yields the midpoint of the two central values and is deliberately
	NOT rounded to an integer: rounding would print two genuinely different
	samples as the same number, and formatting is the renderer's job."""
	return statistics.median(values) if values else None


def _ranked_list(counts, key_name, split_min_games):
	"""counts -> the contract's [{key_name, games, wins}] list.

	Two rules, both load-bearing:

	A split below the floor is omitted ENTIRELY -- not emitted with a
	low-sample flag for the renderer to hedge over. A number the reader has
	to discount is worse than no number, and every hedge that has ever been
	added to this product's copy was eventually read as a fact anyway.

	Ordering is (games descending, key ascending), never dict-insertion
	order. The refresh job rewrites rollups constantly, so an unstable order
	would make identical data serialise to a different blob every pass:
	every diff looks like a change, and no future "did this user's numbers
	actually move?" check can ever work."""
	return [
		{key_name: key, "games": tally["games"], "wins": tally["wins"]}
		for key, tally in sorted(counts.items(), key=lambda kv: (-kv[1]["games"], kv[0]))
		if tally["games"] >= split_min_games
	]


def _bump(counts, key, won):
	tally = counts.setdefault(key, dict(games=0, wins=0))
	tally["games"] += 1
	tally["wins"] += 1 if won else 0


def compute_rollup(stat_rows, label_rows, split_min_games=SPLIT_MIN_GAMES):
	"""One user's whole scouting report, in one community. Pure: no DB, no I/O.

	`stat_rows` are game_stats rows for ONE user, already resolved across
	every profile that user owns; `label_rows` are the game_labels rows for
	the same games. Neither is fetched here -- see the module docstring for
	why attribution is deliberately somebody else's job.

	The STAT rows define the user's game set. A label row is joined back to
	them on (replay_match_id, player_number) -- the pair, never the match
	alone, since a match holds up to eight slots and two of them can even
	belong to the same user -- and a label row that joins to nothing is
	dropped. It is either another player's slot or a game whose stats have
	not been derived yet; counting it would let a split's `games` exceed the
	user's own game count and its outcome could never be known.

	`split_min_games` defaults to this module's SPLIT_MIN_GAMES so the
	default and the constant cannot drift, and stays an argument so tests can
	pin the floor's behaviour rather than the value.

	Returns exactly the five contract blocks. The row's top-level `games`
	column is len(stat_rows) and is deliberately NOT in the blob: it is a
	column so task 4.7 can spot-check it with a COUNT(*) without parsing
	JSON, and the caller stamps it in write()."""
	ranked = [r for r in stat_rows if was_medal_eligible(r)]
	games_ranked = len(ranked)

	def rate(field):
		if not games_ranked:
			# Not 0.0. "Never medals" and "no game was ever scored" are
			# different claims, and 5a renders them differently.
			return None
		hits = sum(1 for r in ranked if r.get(field) is not None)
		return round(hits / games_ranked, _RATE_PLACES)

	avgs = [r["avg_eapm"] for r in stat_rows if r.get("avg_eapm") is not None]
	peaks = [r["peak_eapm"] for r in stat_rows if r.get("peak_eapm") is not None]

	winner_by_slot = {
		(r.get("replay_match_id"), r.get("player_number")): bool(r.get("winner"))
		for r in stat_rows
	}

	splits = dict(strategy={}, spawn={})
	for lr in label_rows:
		slot = (lr.get("replay_match_id"), lr.get("player_number"))
		if slot not in winner_by_slot:
			continue
		# `kind` is read off the stored row rather than re-derived from the
		# label: game_labels.kind_for calls it a stored contract, and a row
		# whose kind is neither 'strategy' nor 'spawn' is dropped here by the
		# same rule that kept it out of the table in the first place.
		bucket = splits.get(lr.get("kind"))
		if bucket is None:
			continue
		_bump(bucket, lr.get("label"), winner_by_slot[slot])

	units = {}
	for r in stat_rows:
		won = bool(r.get("winner"))
		# Deduped per game: top_units should never repeat a unit, but if it
		# ever did, one game would count twice and a unit could show more
		# games than the player has played.
		for name in dict.fromkeys(u.get("unit") for u in top_units_of(r)):
			if name is not None:
				_bump(units, name, won)

	return dict(
		medal_rates=dict(
			military=rate("military_medal"),
			villager=rate("villager_medal"),
			games_ranked=games_ranked,
		),
		# Two sample counts, not one shared figure. In production today
		# peak_eapm is NULL on all 8546 attributable rows -- the
		# bucket-capturing parser shipped after the last match was played --
		# while avg_eapm is present on every one. One `games` key beside a
		# null median would imply a peak sample that does not exist.
		apm=dict(
			median_avg=_median(avgs),
			median_peak=_median(peaks),
			games_avg=len(avgs),
			games_peak=len(peaks),
		),
		strategies=_ranked_list(splits["strategy"], "key", split_min_games),
		spawns=_ranked_list(splits["spawn"], "key", split_min_games),
		units=_ranked_list(units, "unit", split_min_games),
	)


_COLUMNS = ("community_id", "user_id", "games", "rollup", "computed_at")


async def write(community_id, user_id, games, rollup, computed_at):
	"""Store one user's rollup for one community. Idempotent.

	DELIBERATELY NOT the delete-then-insert its sibling modules use, and the
	difference is not an oversight. game_stats and game_labels write a SET of
	rows per match whose size can shrink between recomputes, so a delete is
	the only way to stop a stale row surviving. This table's grain is exactly
	one row per (community_id, user_id) -- the primary key is the whole
	argument list -- so there is nothing a REPLACE can orphan, and a
	delete-then-insert would only add a failure mode: the adapter is
	autocommit with no transaction surface (see core/DBAdapters/mysql.py), so
	an insert that fails after the delete succeeded leaves the user with NO
	rollup, and /rank renders "Statistics pending linking" at them until the
	next refresh -- up to a nightly pass away, since task 4.5's backstop is
	daily rather than the 10-second reconciliation loop that covers stage 3.
	A single REPLACE has no such window at all.

	The rollup is validated against the contract's five blocks and rejected
	loudly, not coerced: a blob missing a block raises nothing at read time,
	it just renders as a quietly shorter report. Serialised with sorted keys
	so an unchanged rollup produces a byte-identical blob every pass; that
	only sorts dict KEYS, leaving the lists in the order compute_rollup
	ranked them."""
	if set(rollup or {}) != set(_ROLLUP_KEYS):
		raise ValueError(
			f"player_rollups blob for community {community_id} user {user_id} has keys "
			f"{sorted(rollup or {})}, expected exactly {sorted(_ROLLUP_KEYS)}")
	row = dict(community_id=community_id, user_id=user_id, games=games,
	           rollup=json.dumps(rollup, sort_keys=True), computed_at=computed_at)
	await db.insert("player_rollups", {c: row[c] for c in _COLUMNS}, on_duplicate="replace")
