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

from . import game_labels

SPLIT_MIN_GAMES = 5
BOARD_MIN_GAMES = 3

# How far back the scouting report looks. The report answers "how is this player
# playing now", so a lifetime aggregate is the wrong measurement: the busiest
# production player has 571 games going back years, and a strategy they abandoned
# eighteen months ago outweighs the one they have played all month.
#
# 60 days rather than 30, measured rather than guessed. At 60 days 35 of 42
# linked players have a game, 30 clear SPLIT_MIN_GAMES and the median player has
# 25 games; at 30 days that falls to 29 / 26 / 13, and the number of players who
# keep a complete three-clause sentence drops from 19 to 13. 90 days was also
# measured and is nearly identical to 60 on this community's activity (1692 vs
# 1556 games), so 60 is the shorter of two equivalent choices rather than a
# compromise.
#
# ONLY player_rollups is windowed. metric_boards deliberately are not: a
# leaderboard is a record, and a record that silently expires is not one.
WINDOW_DAYS = 60

# The seven blocks a rollup carries. Enforced by write() rather than assumed,
# for the same reason game_stats/game_labels validate their row key sets: a
# blob missing a block does not fail anywhere, it just renders as a silently
# shorter scouting report.
_ROLLUP_KEYS = ("medal_rates", "apm", "strategies", "spawns", "units",
                "window_days", "baseline")

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

	Exactly the stored flag, and nothing else on the row. game_stats.
	has_production is written by the same compute that feeds
	card_scoring.assign_medals -- one expression, one definition of "the parser
	measured this player" -- so "ranked and placed fourth" and "never ranked"
	are now distinguishable from a stored row, which is the whole reason
	008_game_stats_has_production exists.

	This used to INFER eligibility from indirect evidence (a medal on either
	axis, or a non-empty top_units). That approximation missed exactly one
	shape: a player who built villagers, no military at all, and placed outside
	the top three on villagers -- someone who died in the first minutes. They
	dropped out of their own denominator, which nudged their rates up. Do not
	reintroduce any of that as a fallback: the column is NOT NULL and 008
	backfilled every historical row, so there is no row left for a fallback to
	serve, and an `or`-ed inference would only ever paper over a caller that
	stopped SELECTing the column -- turning a loud failure into every player's
	rates being quietly slightly wrong.

	Subscripted rather than .get()ed for that reason. A caller whose SELECT
	omits the column raises here, and the refresh job's per-user guard turns
	that into a logged, visible failure -- same discipline as top_units_of
	raising on malformed JSON. .get() would default it to falsy and silently
	empty the denominator for that user, reporting "never medals" as a fact.

	What this must NOT become is "games where the player won a medal". That
	denominator makes every rate a conditional probability, inflates it by
	roughly the reciprocal of the medal rate, and is arithmetically impossible
	against the binding contract's own example: military 0.34 plus villager
	0.18 is 0.52, and rates over a denominator where every game carries at
	least one medal must sum to at least 1."""
	return bool(stat_row["has_production"])


def _median(values):
	"""Median over the non-null values, or None when there are none.

	Never a mean, on either axis: eAPM is heavy-tailed -- one 300-eAPM game
	(a frantic late-game teamfight, or a parse artefact) moves a season's
	mean by tens of points and moves its median by nothing. An even-length
	sample yields the midpoint of the two central values and is deliberately
	NOT rounded to an integer: rounding would print two genuinely different
	samples as the same number, and formatting is the renderer's job."""
	return statistics.median(values) if values else None


def has_known_outcome(stat_row):
	"""Whether this game's result is resolved, i.e. belongs in a win/loss split.

	game_stats.winner is NULLABLE (bot/derived/__init__.py), passed straight
	through from replay_players.winner, which is itself nullable because mgz
	genuinely cannot determine a winner on some replays. NULL means "nobody
	knows", and this is the one place that distinction has to be honoured:
	`bool(None)` is False, so an unresolved game read as a plain boolean is
	scored as a LOSS -- silently, in every split it touches.

	The rule is therefore the same one four sibling modules already take
	(bot/civ_matcher.py, bot/civ_sync.py, bot/lobby/completed.py and this
	package's civ_stats.py, all spelled `winner is not None`): an unresolved
	outcome is DROPPED from the split rather than scored into it. civ_stats'
	docstring argues the case in full -- counting an unknown into `games` alone
	breaks the ratio `games`/`wins` is read as, and a player who is 4-5 with 3
	unknowns would render as 4 wins from 12 games. Stage 5a prints that as a
	record.

	.get()ed rather than subscripted, unlike was_medal_eligible above, and the
	asymmetry is deliberate: has_production is NOT NULL, so its absence can only
	mean a caller's SELECT dropped the column and must raise. `winner` is
	legitimately absent from a row, so there is nothing here to fail loudly
	about."""
	return stat_row.get("winner") is not None


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


def in_window(stat_row, window_start):
	"""Whether this game falls inside the report's window.

	AN UNDATED GAME IS OUTSIDE EVERY WINDOW, never inside the current one.
	game_stats.played_at is nullable because 19 production matches genuinely have
	no recorded date, and `(None or 0) >= window_start` is False for every
	window_start after 1970 — deliberately, not incidentally. The alternative,
	treating an unknown date as "recent enough", would file a game from an unknown
	year into "the last 60 days", which is the one claim the window exists to make.

	`window_start=None` disables the window entirely and is what makes every
	pre-window caller and test still meaningful."""
	return window_start is None or (stat_row.get("played_at") or 0) >= window_start


def compute_rollup(stat_rows, label_rows, split_min_games=SPLIT_MIN_GAMES,
                   now=None, window_days=WINDOW_DAYS):
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

	THE THREE SPLITS COUNT ONLY GAMES WITH A RESOLVED OUTCOME. `games` and
	`wins` inside strategies/spawns/units always describe the same set, so
	their quotient is a real win rate rather than one deflated by every game
	mgz could not call (see has_known_outcome). A row whose `winner` is NULL is
	therefore absent from the join map entirely, which also drops that slot's
	label rows -- the third reason a label row can join to nothing, alongside
	the two above.

	Deliberately NOT applied to the other two blocks. medal_rates' denominator
	is has_production and apm's is the presence of an eAPM figure; neither
	claims anything about winning, so an unresolved game is a perfectly good
	sample for both. The top-level `games` column stays len(stat_rows) for the
	same reason -- it is the user's game count, not a split's denominator -- so
	a split's `games` can legitimately be smaller than it.

	`split_min_games` defaults to this module's SPLIT_MIN_GAMES so the
	default and the constant cannot drift, and stays an argument so tests can
	pin the floor's behaviour rather than the value.

	`now` is what makes the report a report on the last `window_days` rather than
	on all of history: games played before `now - window_days` are dropped.
	Passing now=None disables the window and keeps every pre-window caller
	meaningful. The two are ONE parameter pair rather than a precomputed cutoff
	plus a label to print beside it, deliberately: those could disagree, and a
	blob that says "last 60 days" over a 30-day cutoff is wrong in the one way
	none of its numbers would reveal.

	The cutoff is applied to the STAT rows ONLY — once, at the top. Everything
	else follows for free,
	because every other figure here is already derived from that set: the label
	join tests membership of a stat slot, so an out-of-window game's labels drop
	out with it, and the unit tallies iterate the stat rows directly. One filter,
	one place, and no way for the three splits to end up describing a different
	span of time from the medals above them.

	Returns exactly the seven contract blocks. The row's top-level `games`
	column is len(stat_rows) — the user's LIFETIME game count in this community,
	not the windowed one — and is deliberately NOT in the blob: it is a column so
	task 4.7 can spot-check it with a COUNT(*) without parsing JSON, and the
	caller stamps it in write(). The windowed count lives in `baseline.games`."""
	window_start = None if now is None else now - window_days * 86400
	stat_rows = [r for r in stat_rows if in_window(r, window_start)]
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

	# Resolved rows only, so membership doubles as "this slot is the user's AND
	# its outcome is known" -- the single test the label join below needs.
	winner_by_slot = {
		(r.get("replay_match_id"), r.get("player_number")): bool(r.get("winner"))
		for r in stat_rows if has_known_outcome(r)
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
		label = lr.get("label")
		# Spawn labels are narrowed to the three POSITION keys here, and this is
		# the one place a rollup drops a stored label on display grounds. The
		# other eight spawn labels describe the map rather than the player
		# (gold_poor, near_stone, tight_villagers), and the scouting report reads
		# its spawn line as "wins most when spawning X" — "wins most when spawning
		# stone-poor" is a fact about the map generator.
		#
		# Consistent with this table rather than a new exception to it:
		# player_rollups already applies SPLIT_MIN_GAMES, which is equally a
		# display rule. game_labels keeps all eleven, per its own "storing is not
		# displaying" rule, so nothing is lost — only this summary narrows.
		if bucket is splits["spawn"] and label not in game_labels.POSITION_KEYS:
			continue
		_bump(bucket, label, winner_by_slot[slot])

	units = {}
	for r in stat_rows:
		if not has_known_outcome(r):
			continue
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
		# Self-describing on purpose. A renderer told the window separately could
		# print "the last 60 days" over a blob computed at 30 and nothing would
		# catch it — every number in it would still be real. None when the
		# rollup covers all of history.
		window_days=(None if now is None else window_days),
		# The player's OWN record over the same window: the prior that
		# bot/scouting_report.py regresses each split's win rate toward when it
		# picks the wins-most and loses-most clauses. Stored as counts rather than
		# a rate so the renderer can choose its own shrinkage without the blob
		# having pre-rounded the evidence away, and so `games` doubles as the
		# windowed game count every "no games / only N games" line needs.
		#
		# RESOLVED GAMES ONLY, matching the splits exactly (see has_known_outcome).
		# A baseline computed over unresolved games too would sit below every
		# split's rate by construction, and every clause would then look
		# above-average.
		baseline=dict(games=len(winner_by_slot),
		              wins=sum(1 for won in winner_by_slot.values() if won)),
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


def _decode(raw):
	"""The stored blob as a dict, whether it arrived as the MEDIUMTEXT JSON
	string a SELECT returns or as the dict a caller handed straight over.

	Same shape of decision as top_units_of above, and the same answer: a
	malformed blob RAISES rather than degrading to {}. An empty dict renders
	as a scouting report with every line missing, which is indistinguishable
	from a player whose data is genuinely thin -- while the caller's guard
	turns a raise into a logged failure and one missing field."""
	return json.loads(raw) if isinstance(raw, str) else (raw or {})


async def fetch(community_id, user_id):
	"""One user's rollup blob for one community, or None when they have no row.

	THE ABSENCE IS THE SIGNAL (see delete() above and this package's __init__):
	None means the player is unlinked, or owns no game this community can
	reach, and bot/scouting_report.py renders "Statistics pending linking"
	from it. Never return an empty blob for a missing row -- that is a report
	of zeros standing in for a measurement nobody took.

	Reads the blob alone and not the `games` column beside it. Nothing in the
	report is denominated in total games played: medal rates rest on
	games_ranked, the eAPM medians on their own two counts, and each split on
	its own resolved-game count -- which can legitimately be smaller than
	`games`. Handing the renderer a number none of its lines rest on is an
	invitation to print it as if one of them did."""
	row = await db.select_one(
		["rollup"], "player_rollups", where=dict(community_id=community_id, user_id=user_id))
	return _decode(row["rollup"]) if row else None


async def fetch_community(community_id):
	"""Every rollup in one community as {user_id: blob}.

	For `/identity status`'s gated-features list, which needs to count how
	many players have no row and how many rows carry no split -- questions
	about the whole community rather than one player. ~42 rows in production,
	so this is a full read by design rather than a paginated one."""
	rows = await db.select(["user_id", "rollup"], "player_rollups", where=dict(community_id=community_id))
	return {row["user_id"]: _decode(row["rollup"]) for row in rows or []}


async def delete(community_id, user_id):
	"""Remove one user's rollup for one community. Idempotent (a DELETE that
	matches nothing is not an error).

	THE ABSENCE IS THE SIGNAL, so removal has to be a real operation and not
	something write() can express. An unlinked player must end up with NO row
	rather than a row of zeros -- that is what makes stage 5a's "Statistics
	pending linking" implementable (identity v2 §5, and this package's
	__init__ docstring). A player who HAD a rollup and then had their profile
	unlinked or relinked away is exactly that case arriving late: nothing
	about their game_stats rows changes, so without a delete they would keep
	rendering somebody else's history forever.

	Lives here rather than in bot/derived/refresh.py, which is the only
	caller, so `player_rollups` keeps exactly one writing module --
	core/data_registry.py's stated target of one dedicated writer per table,
	and the reason the registry does not have to grow a second entry for the
	refresh job."""
	await db.delete("player_rollups", where=dict(community_id=community_id, user_id=user_id))
