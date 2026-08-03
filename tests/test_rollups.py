"""Unit tests for the derived-community player_rollups pure aggregation +
writer (bot/derived/rollups.py).

Every number the stage-5 scouting report prints comes out of compute_rollup,
so each test below is written to fail against a specific plausible-but-wrong
implementation, not merely to exercise the happy path:

  * medal rates divided by TOTAL games instead of ranked games
  * eligibility re-inferred from a medal or a non-empty top_units instead of
    read off the stored has_production column (what this module did before
    008_game_stats_has_production, and what it must never drift back to)
  * a mean where the contract says median
  * one shared apm sample count instead of the two the contract carries
  * a game whose `winner` is NULL read through bool(), which scores an
    unresolved outcome as a LOSS in every split it touches
  * every unit in a game's stored top three tallied, so a support unit
    built in small numbers beside the real mass collects an inflated record
  * the split floor dropped, so 2-game "tendencies" reach the reader
  * list ordering left to dict insertion order, so identical data rewrites
    a different blob every refresh

No pytest-asyncio in this repo -- an `async def test_...` is collected and
SKIPPED, reporting green while asserting nothing -- so write() is driven from
sync tests with asyncio.run().
"""

import asyncio
import json

import pytest

import bot.derived.rollups as rollups
from bot.derived.rollups import compute_rollup


# ── fixtures ─────────────────────────────────────────────────────────────
# Shaped like the rows the stage-4.5 refresh job SELECTs out of game_stats /
# game_labels, including the columns compute_rollup never reads (civ, team,
# evidence): a compute that accidentally depends on one of them should be
# caught by a test that removes it, not hidden by a fixture that never had it.

def _stat(mid, pn=1, profile_id=11, winner=True, avg_eapm=50, peak_eapm=None,
          military_medal=None, villager_medal=None, units=("Knight",), has_production=True,
          totals=None):
	return dict(
		replay_match_id=mid, player_number=pn, profile_id=profile_id,
		civ="Franks", team="1", winner=winner,
		avg_eapm=avg_eapm, peak_eapm=peak_eapm,
		military_medal=military_medal, villager_medal=villager_medal,
		# Stored by 008_game_stats_has_production onwards, and the ONLY input to
		# medal eligibility. Defaults True because the overwhelming majority of
		# real rows are measured games; the tests that care pass it explicitly.
		has_production=has_production,
		top_units=[dict(unit=u, category="cavalry", total=t)
		           for u, t in zip(units, totals or [10] * len(units))],
		computed_at=1000,
	)


def _label(mid, key, kind, pn=1):
	return dict(replay_match_id=mid, player_number=pn, label=key, kind=kind,
	            evidence={}, played_at=500)


def _units_for(i):
	# Knight 5 games, Archer 5, Trebuchet 7 -- inserted Knight-first, so a tie
	# broken by insertion order would report Knight before Archer.
	if i <= 5:
		return ("Knight",)
	if i <= 10:
		return ("Archer",)
	return ("Trebuchet",)


def _blob(**overrides):
	"""A contract-shaped rollup, so write()'s shape guard is exercised by the
	tests that are about something else rather than tripped by them."""
	blob = dict(medal_rates=dict(military=None, villager=None, games_ranked=0),
	            apm=dict(median_avg=None, median_peak=None, games_avg=0, games_peak=0),
	            strategies=[], spawns=[], units=[],
	            window_days=None, baseline=dict(games=0, wins=0))
	blob.update(overrides)
	return blob


# ── constants ────────────────────────────────────────────────────────────

def test_floors_are_the_calibrated_values():
	# Measured, not guessed: at 5, 662 splits survive across 39 of 42 live
	# users; at 3 the count only rises to 740 and starts admitting 3-game
	# "tendencies"; at 8 it falls to 568 and costs real users their strategy
	# line. BOARD_MIN_GAMES=3 clears every user (min games = 6).
	assert rollups.SPLIT_MIN_GAMES == 5
	assert rollups.BOARD_MIN_GAMES == 3
	# Also measured: at 60 days 35 of 42 linked players have a game and 30 clear
	# SPLIT_MIN_GAMES; at 30 that is 29 and 26, and the number keeping a full
	# three-clause sentence falls from 19 to 13.
	assert rollups.WINDOW_DAYS == 60


# ── the contract shape ───────────────────────────────────────────────────

def test_rollup_has_exactly_the_contract_keys():
	rollup = compute_rollup([_stat(1)], [], 5)
	assert set(rollup) == {"medal_rates", "apm", "strategies", "spawns", "units",
	                       "window_days", "baseline"}
	assert set(rollup["medal_rates"]) == {"military", "villager", "games_ranked"}
	assert set(rollup["apm"]) == {"median_avg", "median_peak", "games_avg", "games_peak"}
	assert set(rollup["baseline"]) == {"games", "wins"}


def test_no_games_produces_the_shape_with_no_invented_numbers():
	# An empty rollup must not read as "0% medals, 0 APM" -- absence and zero
	# are different claims, and the stage-5 renderer distinguishes them.
	rollup = compute_rollup([], [], 5)
	assert rollup["medal_rates"] == dict(military=None, villager=None, games_ranked=0)
	assert rollup["apm"] == dict(median_avg=None, median_peak=None, games_avg=0, games_peak=0)
	assert rollup["strategies"] == []
	assert rollup["spawns"] == []
	assert rollup["units"] == []


# ── medal_rates: the denominator ─────────────────────────────────────────

def test_medal_rates_divide_by_ranked_games_not_total_games():
	# 6 games; in 2 of them the parser measured no production at all, so
	# assign_medals never ranked the player. Those two are excluded from the
	# denominator, so the rates are out of 4.
	#
	# The numbers are chosen so all three candidate denominators disagree:
	#   ranked (4)              -> military 0.25   <- correct
	#   total games (6)         -> military 0.1667 <- mutant (a)
	#   games with any medal(3) -> military 0.3333 <- medal-presence proxy
	stat_rows = [
		_stat(1, military_medal=1),
		_stat(2, villager_medal=2),
		_stat(3, villager_medal=3),
		_stat(4),                                       # measured, medalled on neither axis
		_stat(5, units=(), has_production=False),       # not measured
		_stat(6, units=(), has_production=False),       # not measured
	]
	rollup = compute_rollup(stat_rows, [], 5)
	assert rollup["medal_rates"] == dict(military=0.25, villager=0.5, games_ranked=4)


def test_a_measured_game_with_no_medal_still_counts_in_the_denominator():
	# Placing 4th is a result, not a missing measurement. Counting only games
	# the player medalled in would make every rate a conditional probability
	# and inflate it -- and is arithmetically impossible against the binding
	# example (military 0.34 + villager 0.18 = 0.52 < 1, which cannot happen
	# when every counted game carries at least one medal).
	stat_rows = [_stat(1, military_medal=1)] + [_stat(i) for i in range(2, 5)]
	rollup = compute_rollup(stat_rows, [], 5)
	assert rollup["medal_rates"]["games_ranked"] == 4
	assert rollup["medal_rates"]["military"] == 0.25


def test_a_measured_game_with_no_military_units_still_counts():
	# A player who only ever made villagers has an empty top_units. They were
	# still measured, so the game is in the denominator.
	stat_rows = [_stat(1, units=(), villager_medal=1),
	             _stat(2, units=(), has_production=False)]
	rollup = compute_rollup(stat_rows, [], 5)
	assert rollup["medal_rates"] == dict(military=0.0, villager=1.0, games_ranked=1)


def test_the_measured_unmedalled_unarmed_game_the_old_inference_missed_now_counts():
	# THE case 008_game_stats_has_production exists for, and the one the
	# pre-008 inference got wrong. This player built villagers, no military at
	# all (so top_units is empty), and placed outside the top three on
	# villagers (so neither medal column is set) -- a player who died in the
	# first minutes. Every signal the old inference had says "not measured";
	# the stored flag says otherwise, and it is right.
	#
	# 4 such games plus 1 medalled game. Correct: 1/5 = 0.2. The old
	# inference saw a denominator of 1 and reported 1.0 -- a five-fold
	# overstatement of this player's medal rate.
	stat_rows = [_stat(1, units=(), military_medal=1)] + [
		_stat(i, units=()) for i in range(2, 6)]
	rollup = compute_rollup(stat_rows, [], 5)
	assert rollup["medal_rates"] == dict(military=0.2, villager=0.0, games_ranked=5)


def test_eligibility_reads_the_stored_flag_and_nothing_else_on_the_row():
	# Contradictory by construction -- a medal and a full top_units cannot
	# coexist with has_production false in real data -- and that is the point:
	# it isolates the flag as the SOLE input. Every signal the deleted
	# inference used to read says "eligible" here; only the stored column says
	# otherwise, and it must win. An `or`-ed fallback reintroduced "just in
	# case" fails this and nothing else.
	stat_rows = [_stat(1, military_medal=1, villager_medal=1, units=("Knight",),
	                   has_production=False)]
	rollup = compute_rollup(stat_rows, [], 5)
	assert rollup["medal_rates"] == dict(military=None, villager=None, games_ranked=0)


def test_a_row_missing_the_stored_flag_raises_rather_than_silently_emptying_the_denominator():
	# game_stats.has_production is NOT NULL and 008 backfilled every historical
	# row, so a row without it means the caller's SELECT dropped the column.
	# Defaulting it to falsy would report "never medals" for that whole user as
	# though it were a measurement; the refresh job's per-user guard turns this
	# raise into a logged, visible failure instead. Same discipline as
	# top_units_of raising on malformed JSON.
	row = _stat(1)
	del row["has_production"]
	with pytest.raises(KeyError):
		compute_rollup([row], [], 5)


def test_the_binding_contracts_own_example_is_reachable_with_this_denominator():
	# The contract's worked example is military 0.34 / villager 0.18 over a
	# 41-game player. Those sum to 0.52, which is only possible when the
	# denominator counts games the player was ELIGIBLE in -- over a denominator
	# of "games carrying at least one medal", every rate pair must sum to at
	# least 1. So this reproduces the published numbers from real rows and
	# thereby pins that the shipped denominator is the one the contract was
	# written against.
	#
	# 41 games, 3 of them unmeasured -> games_ranked 38; 13 military medals and
	# 7 villager medals among the measured 38.
	stat_rows = (
		[_stat(i, military_medal=1) for i in range(1, 14)]            # 13 military
		+ [_stat(i, villager_medal=2) for i in range(14, 21)]         # 7 villager
		+ [_stat(i) for i in range(21, 39)]                           # 18 measured, no medal
		+ [_stat(i, units=(), has_production=False) for i in range(39, 42)]
	)
	assert len(stat_rows) == 41
	rollup = compute_rollup(stat_rows, [], 5)
	assert rollup["medal_rates"] == dict(military=0.3421, villager=0.1842, games_ranked=38)
	rates = rollup["medal_rates"]
	assert round(rates["military"], 2) == 0.34
	assert round(rates["villager"], 2) == 0.18
	assert rates["military"] + rates["villager"] < 1.0


def test_medal_rates_are_none_when_nothing_was_ever_ranked():
	rollup = compute_rollup([_stat(1, units=(), has_production=False),
	                         _stat(2, units=(), has_production=False)], [], 5)
	assert rollup["medal_rates"] == dict(military=None, villager=None, games_ranked=0)


# ── apm: medians, and two sample counts ──────────────────────────────────

def test_median_avg_is_a_median_not_a_mean():
	# One 300-eAPM outlier must not move the season's figure.
	# median = 50, mean = 130.
	stat_rows = [_stat(1, avg_eapm=40), _stat(2, avg_eapm=50), _stat(3, avg_eapm=300)]
	rollup = compute_rollup(stat_rows, [], 5)
	assert rollup["apm"]["median_avg"] == 50
	assert rollup["apm"]["games_avg"] == 3


def test_median_over_an_even_sample_is_the_midpoint_of_the_two_middles():
	# 4 values: median = (50+60)/2 = 55.0, mean = 112.5. An implementation
	# that took the lower middle would say 50, the upper middle 60.
	stat_rows = [_stat(1, avg_eapm=40), _stat(2, avg_eapm=50),
	             _stat(3, avg_eapm=60), _stat(4, avg_eapm=300)]
	rollup = compute_rollup(stat_rows, [], 5)
	assert rollup["apm"]["median_avg"] == 55.0
	assert rollup["apm"]["games_avg"] == 4


def test_median_ignores_input_order():
	shuffled = [_stat(1, avg_eapm=300), _stat(2, avg_eapm=40), _stat(3, avg_eapm=50)]
	assert compute_rollup(shuffled, [], 5)["apm"]["median_avg"] == 50


def test_nulls_are_dropped_from_the_sample_not_treated_as_zero():
	# Counting a missing eAPM as 0 would drag the median down by a third here.
	stat_rows = [_stat(1, avg_eapm=None), _stat(2, avg_eapm=60), _stat(3, avg_eapm=70)]
	rollup = compute_rollup(stat_rows, [], 5)
	assert rollup["apm"]["median_avg"] == 65.0
	assert rollup["apm"]["games_avg"] == 2


def test_peak_carries_its_own_sample_count_beside_a_real_avg_sample():
	# Production today: peak_eapm is NULL on all 8546 attributable rows (the
	# bucket-capturing parser shipped after the last match was played) while
	# avg_eapm is present on every one. A single shared `games` figure beside
	# a null median would imply a peak sample that does not exist.
	stat_rows = [_stat(i, avg_eapm=50, peak_eapm=None) for i in range(1, 4)]
	rollup = compute_rollup(stat_rows, [], 5)
	assert rollup["apm"] == dict(median_avg=50, median_peak=None, games_avg=3, games_peak=0)


def test_the_two_apm_counts_are_never_swapped_or_shared():
	# Both samples non-empty and DIFFERENT sizes, and the two medians differ
	# too, so reporting either figure in the other's slot fails.
	stat_rows = [
		_stat(1, avg_eapm=10, peak_eapm=100),
		_stat(2, avg_eapm=20, peak_eapm=200),
		_stat(3, avg_eapm=30, peak_eapm=None),
		_stat(4, avg_eapm=40, peak_eapm=None),
	]
	rollup = compute_rollup(stat_rows, [], 5)
	assert rollup["apm"] == dict(median_avg=25.0, median_peak=150.0, games_avg=4, games_peak=2)


# ── strategies / spawns ──────────────────────────────────────────────────

def test_splits_are_grouped_by_label_with_wins_from_the_joined_stat_row():
	stat_rows = [_stat(i, winner=(i <= 3)) for i in range(1, 6)]   # 3 wins, 2 losses
	label_rows = [_label(i, "scout_rush", "strategy") for i in range(1, 6)]
	rollup = compute_rollup(stat_rows, label_rows, 5)
	assert rollup["strategies"] == [dict(key="scout_rush", games=5, wins=3)]


def test_spawns_and_strategies_land_in_their_own_lists():
	stat_rows = [_stat(i) for i in range(1, 6)]
	label_rows = ([_label(i, "scout_rush", "strategy") for i in range(1, 6)]
	              + [_label(i, "spawn_near_enemy", "spawn") for i in range(1, 6)])
	rollup = compute_rollup(stat_rows, label_rows, 5)
	assert [s["key"] for s in rollup["strategies"]] == ["scout_rush"]
	assert [s["key"] for s in rollup["spawns"]] == ["spawn_near_enemy"]


def test_a_label_row_with_no_matching_stat_row_is_ignored():
	# The stat rows define the user's game set. A label row that joins to
	# nothing is another player's slot (or a game whose stats have not been
	# derived yet) -- counting it would let a split's `games` exceed the
	# user's own game count, and its win could never be known.
	stat_rows = [_stat(i) for i in range(1, 6)]
	label_rows = ([_label(i, "scout_rush", "strategy") for i in range(1, 6)]
	              + [_label(1, "scout_rush", "strategy", pn=7)]      # another slot
	              + [_label(99, "scout_rush", "strategy")])          # unknown match
	rollup = compute_rollup(stat_rows, label_rows, 5)
	assert rollup["strategies"] == [dict(key="scout_rush", games=5, wins=5)]


def test_a_game_whose_winner_is_unknown_is_dropped_from_every_split():
	"""game_stats.winner is NULLABLE and mgz genuinely fails to resolve a winner
	on some replays. Read through bool(), NULL is False -- so an unresolved game
	scores as a LOSS in every strategy, spawn and unit split it touches, and the
	player renders as 4-8 when they are 4-5 with 3 unknowns.

	9 resolved games (4 won, 5 lost) plus 3 unresolved. `games` must be 9 in
	every split, never 12: `games` and `wins` have to describe the same set or
	their quotient is not a win rate."""
	stat_rows = (
		[_stat(i, winner=True) for i in range(1, 5)]
		+ [_stat(i, winner=False) for i in range(5, 10)]
		+ [_stat(i, winner=None) for i in range(10, 13)]
	)
	label_rows = ([_label(i, "scout_rush", "strategy") for i in range(1, 13)]
	              + [_label(i, "spawn_isolated", "spawn") for i in range(1, 13)])
	rollup = compute_rollup(stat_rows, label_rows, 5)
	assert rollup["strategies"] == [dict(key="scout_rush", games=9, wins=4)]
	assert rollup["spawns"] == [dict(key="spawn_isolated", games=9, wins=4)]
	assert rollup["units"] == [dict(unit="Knight", games=9, wins=4)]


def test_a_split_of_only_unresolved_games_is_omitted_rather_than_reading_as_all_losses():
	# The starkest shape: five games of one strategy, none of them called. The
	# honest answer is that there is nothing to say about it, not "0 wins in 5".
	stat_rows = [_stat(i, winner=None) for i in range(1, 6)]
	label_rows = [_label(i, "scout_rush", "strategy") for i in range(1, 6)]
	rollup = compute_rollup(stat_rows, label_rows, 5)
	assert rollup["strategies"] == []
	assert rollup["units"] == []
	assert rollup["medal_rates"]["games_ranked"] == 5, "the games themselves are not discarded"


def test_an_unresolved_outcome_still_counts_toward_medal_rates_and_apm():
	# The rule is scoped to the win/loss splits and nowhere else. medal_rates'
	# denominator is has_production and apm's is the presence of an eAPM figure;
	# neither claims anything about winning, so dropping the row from those too
	# would throw away measurements that are perfectly good.
	stat_rows = [
		_stat(1, winner=None, avg_eapm=40, military_medal=1),
		_stat(2, winner=None, avg_eapm=60),
		_stat(3, winner=True, avg_eapm=80),
	]
	rollup = compute_rollup(stat_rows, [], 5)
	assert rollup["medal_rates"] == dict(military=0.3333, villager=0.0, games_ranked=3)
	assert rollup["apm"] == dict(median_avg=60, median_peak=None, games_avg=3, games_peak=0)


def test_a_label_row_whose_kind_is_neither_is_dropped():
	stat_rows = [_stat(i) for i in range(1, 6)]
	label_rows = [_label(i, "mystery", "vibes") for i in range(1, 6)]
	rollup = compute_rollup(stat_rows, label_rows, 5)
	assert rollup["strategies"] == []
	assert rollup["spawns"] == []


# ── the floor ────────────────────────────────────────────────────────────

def test_a_split_below_the_floor_is_omitted_entirely():
	# 4 games of archer_rush, 5 of scout_rush, floor 5. A number the reader
	# has to discount is worse than no number, so the 4-game split does not
	# appear at all -- not as an entry, not with a warning flag.
	stat_rows = [_stat(i) for i in range(1, 10)]
	label_rows = ([_label(i, "archer_rush", "strategy") for i in range(1, 5)]
	              + [_label(i, "scout_rush", "strategy") for i in range(5, 10)])
	rollup = compute_rollup(stat_rows, label_rows, 5)
	assert rollup["strategies"] == [dict(key="scout_rush", games=5, wins=5)]


def test_the_floor_is_the_argument_not_a_hardcoded_constant():
	stat_rows = [_stat(i) for i in range(1, 10)]
	label_rows = [_label(i, "archer_rush", "strategy") for i in range(1, 5)]
	assert compute_rollup(stat_rows, label_rows, 4)["strategies"] == [
		dict(key="archer_rush", games=4, wins=4)]
	assert compute_rollup(stat_rows, label_rows, 5)["strategies"] == []


def test_the_floor_applies_to_spawns_and_units_too():
	stat_rows = [_stat(i, units=("Knight",) if i <= 4 else ("Archer",))
	             for i in range(1, 10)]           # Knight 4 games, Archer 5
	label_rows = [_label(i, "spawn_isolated", "spawn") for i in range(1, 5)]
	rollup = compute_rollup(stat_rows, label_rows, 5)
	assert rollup["spawns"] == []
	assert rollup["units"] == [dict(unit="Archer", games=5, wins=5)]
	assert compute_rollup(stat_rows, label_rows, 6)["units"] == []


# ── units ────────────────────────────────────────────────────────────────

def test_a_game_counts_toward_only_the_unit_the_player_built_most():
	# "Wins most massing X" claims X was the plan that game, and 5 Monks
	# beside 30 Knights were not the plan. Counting every entry of the stored
	# top three would call this a Monk game too, and support units -- built
	# in small numbers, mostly in games already being won -- would collect
	# an inflated rate no renderer-side shrinkage corrects.
	stat_rows = [_stat(i, units=("Knight", "Monk"), totals=(30, 5), winner=(i % 2 == 1))
	             for i in range(1, 6)]
	rollup = compute_rollup(stat_rows, [], 5)
	assert rollup["units"] == [dict(unit="Knight", games=5, wins=3)]


def test_the_most_built_unit_is_read_off_the_totals_not_the_list_order():
	# compute_game_stats stores top_units pre-sorted, but the tally must not
	# depend on that: the same units handed over in another order must tally
	# the same game.
	stat_rows = [_stat(i, units=("Monk", "Knight"), totals=(5, 30)) for i in range(1, 6)]
	assert compute_rollup(stat_rows, [], 5)["units"] == [dict(unit="Knight", games=5, wins=5)]


def test_an_equal_production_tie_breaks_on_the_unit_name():
	# The same tie-break compute_game_stats sorts by, so a genuinely equal
	# game tallies one deterministic unit rather than whichever is listed first.
	stat_rows = [_stat(i, units=("Knight", "Archer")) for i in range(1, 6)]
	assert compute_rollup(stat_rows, [], 5)["units"] == [dict(unit="Archer", games=5, wins=5)]


def test_top_units_is_read_from_the_json_string_the_database_returns():
	# game_stats.top_units is a MEDIUMTEXT column, so rows SELECTed by the
	# stage-4.5 refresh job carry a JSON STRING, while rows handed straight
	# from compute_game_stats carry a list. The same compute must serve both.
	rows = []
	for i in range(1, 6):
		row = _stat(i, units=("Knight",))
		row["top_units"] = json.dumps(row["top_units"])
		rows.append(row)
	rollup = compute_rollup(rows, [], 5)
	assert rollup["units"] == [dict(unit="Knight", games=5, wins=5)]
	# Eligibility is unaffected by the serialisation, because it reads the
	# stored has_production column rather than parsing top_units at all.
	assert rollup["medal_rates"]["games_ranked"] == 5


def test_an_empty_top_units_string_is_not_a_unit():
	rows = [dict(_stat(i), top_units="[]") for i in range(1, 6)]
	assert compute_rollup(rows, [], 5)["units"] == []


# ── multi-profile aggregation ────────────────────────────────────────────

def test_games_from_every_profile_the_user_owns_aggregate_into_one_rollup():
	# `/identity link ... additional: True` lets one Discord user own several
	# AoE2 profiles. The refresh job resolves them and hands over the union;
	# nothing here may re-split on profile_id, or a multi-account player's
	# history silently halves -- and both halves would then sit below the
	# floor and vanish from the report entirely.
	stat_rows = (
		[_stat(i, profile_id=11, avg_eapm=40, military_medal=1) for i in range(1, 4)]
		+ [_stat(i, profile_id=22, avg_eapm=80, winner=False) for i in range(4, 6)]
	)
	label_rows = [_label(i, "knight_rush", "strategy") for i in range(1, 6)]
	rollup = compute_rollup(stat_rows, label_rows, 5)

	assert rollup["strategies"] == [dict(key="knight_rush", games=5, wins=3)]
	assert rollup["medal_rates"] == dict(military=0.6, villager=0.0, games_ranked=5)
	assert rollup["apm"]["games_avg"] == 5
	assert rollup["apm"]["median_avg"] == 40          # 40,40,40,80,80
	assert rollup["units"] == [dict(unit="Knight", games=5, wins=3)]


def test_the_same_match_played_by_two_of_the_users_profiles_is_two_games():
	# Rare but legal: two profiles the same user owns sat in one lobby. The
	# join key is (replay_match_id, player_number), so the two slots stay
	# distinct instead of collapsing into one.
	stat_rows = ([_stat(i, pn=1, profile_id=11) for i in range(1, 4)]
	             + [_stat(i, pn=2, profile_id=22) for i in range(1, 4)])
	label_rows = ([_label(i, "maa_rush", "strategy", pn=1) for i in range(1, 4)]
	              + [_label(i, "maa_rush", "strategy", pn=2) for i in range(1, 4)])
	rollup = compute_rollup(stat_rows, label_rows, 5)
	assert rollup["strategies"] == [dict(key="maa_rush", games=6, wins=6)]


# ── deterministic ordering ───────────────────────────────────────────────

def test_lists_sort_by_games_descending_then_key_ascending():
	# Every list is built so that dict-insertion order, alphabetical-only
	# order and games-only order all differ from the required order:
	#   inserted: scout(5)  archer(5)  ram_push(7)
	#   required: ram_push(7), archer_rush(5), scout_rush(5)
	stat_rows = [_stat(i, units=_units_for(i)) for i in range(1, 18)]
	label_rows = ([_label(i, "scout_rush", "strategy") for i in range(1, 6)]
	              + [_label(i, "archer_rush", "strategy") for i in range(6, 11)]
	              + [_label(i, "ram_push", "strategy") for i in range(11, 18)])
	rollup = compute_rollup(stat_rows, label_rows, 5)
	assert [s["key"] for s in rollup["strategies"]] == ["ram_push", "archer_rush", "scout_rush"]
	assert [u["unit"] for u in rollup["units"]] == ["Trebuchet", "Archer", "Knight"]


def test_identical_data_in_a_different_order_serialises_byte_for_byte_the_same():
	# The refresh job rewrites rollups constantly. Unstable ordering makes
	# every diff look like a change and defeats any future "did this user's
	# numbers actually move?" check.
	stat_rows = [_stat(i, units=_units_for(i), avg_eapm=40 + i) for i in range(1, 18)]
	label_rows = ([_label(i, "scout_rush", "strategy") for i in range(1, 6)]
	              + [_label(i, "archer_rush", "strategy") for i in range(6, 11)]
	              + [_label(i, "ram_push", "strategy") for i in range(11, 18)])

	forward = compute_rollup(stat_rows, label_rows, 5)
	backward = compute_rollup(list(reversed(stat_rows)), list(reversed(label_rows)), 5)
	assert json.dumps(forward, sort_keys=True) == json.dumps(backward, sort_keys=True)


# ── write() ──────────────────────────────────────────────────────────────

class _RecordingDB:
	def __init__(self):
		self.calls = []

	async def execute(self, sql, args=None):
		self.calls.append(("execute", sql, list(args) if args else []))

	async def insert(self, table, row, on_duplicate=None):
		self.calls.append(("insert", table, dict(row), on_duplicate))

	async def insert_many(self, table, rows, on_duplicate=None):
		self.calls.append(("insert_many", table, list(rows), on_duplicate))


def _written(community_id=1, user_id=7, games=41, rollup=None, computed_at=1700):
	recorder = _RecordingDB()
	original_db = rollups.db
	rollups.db = recorder
	try:
		asyncio.run(rollups.write(community_id, user_id, games,
		                          _blob() if rollup is None else rollup, computed_at))
	finally:
		rollups.db = original_db
	return recorder


def test_write_upserts_one_row_and_never_leaves_the_user_without_one():
	# A DELETE-then-INSERT here would open a window (and, on a failed insert,
	# a whole refresh cycle) in which /rank renders "Statistics pending
	# linking" for a user who has a rollup. The grain is one row per PK, so
	# there is nothing a REPLACE can orphan.
	blob = _blob(medal_rates=dict(military=0.34, villager=0.18, games_ranked=41))
	recorder = _written(community_id=1, user_id=7, games=41, rollup=blob, computed_at=1700)
	assert [c[0] for c in recorder.calls] == ["insert"]
	_, table, row, on_duplicate = recorder.calls[0]
	assert table == "player_rollups"
	assert on_duplicate == "replace"
	assert row["community_id"] == 1
	assert row["user_id"] == 7
	assert row["games"] == 41
	assert row["computed_at"] == 1700
	assert json.loads(row["rollup"]) == blob


def test_write_emits_exactly_the_declared_columns_in_one_order():
	row = _written().calls[0][2]
	assert list(row.keys()) == list(rollups._COLUMNS)


def test_write_serialises_the_rollup_with_sorted_keys_so_the_blob_is_stable():
	forward = _blob()
	backward = dict(reversed(list(_blob().items())))
	assert list(forward) != list(backward)      # the fixture really is reordered
	assert _written(rollup=forward).calls[0][2]["rollup"] == \
	       _written(rollup=backward).calls[0][2]["rollup"]

	# ...and list ORDER inside the blob is not sorted away: sort_keys touches
	# dict keys only, and the lists arrive already ranked by compute_rollup.
	units = _blob(units=[dict(unit="Knight", games=9, wins=5),
	                     dict(unit="Archer", games=5, wins=1)])
	stored = json.loads(_written(rollup=units).calls[0][2]["rollup"])
	assert [u["unit"] for u in stored["units"]] == ["Knight", "Archer"]


def test_write_refuses_a_rollup_that_is_not_the_contract_shape():
	# Same discipline as game_stats/game_labels' write: loud, not coerced. A
	# blob missing a block would render as a silently shorter report.
	missing = _blob()
	missing.pop("units")
	with pytest.raises(ValueError, match="expected exactly"):
		_written(rollup=missing)
	with pytest.raises(ValueError, match="expected exactly"):
		_written(rollup=_blob(surprise=1))


def test_write_accepts_what_compute_rollup_returns():
	rollup = compute_rollup([_stat(1)], [], 5)
	row = _written(rollup=rollup).calls[0][2]
	assert json.loads(row["rollup"]) == rollup
