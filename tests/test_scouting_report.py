"""The stage-5a scouting report: nammaoe2bot/features/scouting/report.py's renderer, the
player_rollups readers behind it, and the `/rank` wiring that chooses between
a report, the pending-linking notice and no field at all.

Most of the assertions below run the REAL aggregation (nammaoe2bot/derived/rollups.
compute_rollup) over game_stats/game_labels-shaped rows and render its output,
rather than hand-writing a tidy blob. Every copy rule this task exists to
enforce is a rule about the relationship between a number and the rows behind
it -- a rate and its denominator, a split and its floor, a median and its
sample -- and a hand-written blob asserts that relationship into existence
instead of testing it. Two of the four mutants these tests are written to kill
(medal rates over games played; the per-split floor removed) live in
compute_rollup, and only an end-to-end test reaches them.

No pytest-asyncio in this repo -- an `async def test_...` is collected and
SKIPPED, reporting green while asserting nothing -- so every coroutine here is
driven with asyncio.run().
"""
import ast
import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import nammaoe2bot.community as community
import nammaoe2bot.features.identity.resolver as identity
import nammaoe2bot.derived.rollups as rollups
from nammaoe2bot.runtime.database import db
import nammaoe2bot.features.scouting.report as scouting_report
from nammaoe2bot.derived.rollups import SPLIT_MIN_GAMES, compute_rollup

_REPO_ROOT = Path(__file__).resolve().parent.parent

# A fixed "now" so every windowing assertion is arithmetic rather than a race
# against the clock. A game defaults to being played at this instant, i.e. inside
# any window, so a test that is not about the window need not think about one.
NOW = 1_800_000_000
DAY = 86400


# ── fixtures ─────────────────────────────────────────────────────────────
# game_stats / game_labels row shapes, same as tests/test_rollups.py's.

def _stat(mid, winner=True, avg_eapm=50, peak_eapm=None, military_medal=None,
          villager_medal=None, units=("Knight",), has_production=True, played_at=NOW):
	return dict(
		replay_match_id=mid, player_number=1, profile_id=11,
		civ="Franks", team="1", winner=winner,
		avg_eapm=avg_eapm, peak_eapm=peak_eapm,
		military_medal=military_medal, villager_medal=villager_medal,
		has_production=has_production,
		top_units=[dict(unit=u, category="cavalry", total=10) for u in units],
		computed_at=1000, played_at=played_at, compute_version=1,
	)


def _label(mid, key, kind):
	return dict(replay_match_id=mid, player_number=1, label=key, kind=kind,
	            evidence={}, played_at=500)


def _rich():
	"""A player with something to say on every line.

	12 games; 10 of them measured (`has_production`), of which 5 carry a
	military medal and 2 a villager medal -- so the medal denominator (10) is
	NOT the game count (12) and NOT the medal count. eAPM is present on 11 of
	the 12, peak on none of them (production's shape today). Two games have an
	unresolved outcome, so every split rests on 10 games while the player has
	played 12.

	Each block also carries a second, thinner key that must not reach the
	reader: the report prints the top one only."""
	stats, labels = [], []
	for i in range(1, 13):
		# Games 11 and 12 are unresolved; 9 and 10 were never measured.
		winner = True if i <= 5 else (False if i <= 10 else None)
		stats.append(_stat(
			i,
			winner=winner,
			avg_eapm=None if i == 12 else 40 + i,
			military_medal=1 if i <= 5 else None,
			villager_medal=2 if i <= 2 else None,
			has_production=i <= 10,
			units=("Knight",) if i <= 8 else ("Archer",),
		))
		# archer_rush on 6 resolved games (4 won), safe_castle on 3 -- below
		# the floor and therefore absent from the blob entirely.
		labels.append(_label(i, "archer_rush" if i <= 6 else "safe_castle", "strategy"))
		labels.append(_label(i, "spawn_near_enemy" if i <= 7 else "spawn_isolated", "spawn"))
	return compute_rollup(stats, labels)


def _blob(**overrides):
	"""A contract-shaped rollup for the renderer-only cases, where the point is
	a blob shape rather than the arithmetic that produced it."""
	blob = dict(medal_rates=dict(military=None, villager=None, games_ranked=0),
	            apm=dict(median_avg=None, median_peak=None, games_avg=0, games_peak=0),
	            strategies=[], spawns=[], units=[],
	            window_days=None, baseline=dict(games=0, wins=0))
	blob.update(overrides)
	return blob


def _lines(text):
	return (text or "").split("\n")


def _line_with(text, needle):
	found = [ln for ln in _lines(text) if needle in ln]
	assert len(found) == 1, f"expected exactly one {needle!r} line in:\n{text}"
	return found[0]


# ── the pending-linking case ─────────────────────────────────────────────
# The design's own stage-5 acceptance criterion (identity v2 §5): an unlinked
# player has NO player_rollups row, and the absence is the signal.

def test_a_player_with_no_rollup_row_renders_exactly_the_pending_string():
	assert scouting_report.render(None) == "Statistics pending linking"


def test_the_pending_field_carries_nothing_else_at_all():
	""" Not a zeroed report beside it, not a hedge under it, not a digit
	anywhere: a row of zeros would be a measurement nobody took. """
	rendered = scouting_report.render(None)
	assert _lines(rendered) == ["Statistics pending linking"]
	assert not any(ch.isdigit() for ch in rendered)
	assert "%" not in rendered and "W-" not in rendered


def test_the_pending_string_goes_through_the_callers_translator():
	seen = []

	def gt(text):
		seen.append(text)
		return text.upper()

	assert scouting_report.render(None, gt) == "STATISTICS PENDING LINKING"
	assert seen == ["Statistics pending linking"]


def test_the_two_floors_are_the_same_number():
	""" MIN_GAMES is spelled here rather than imported from rollups (importing it
	would drag nammaoe2bot.runtime.database into a module whose whole test suite rests on
	importing nothing that reaches a database), so nothing but this test stops
	the two drifting. One report must not quote a strategy over 5 games and a
	medal rate over 2. """
	assert scouting_report.MIN_GAMES == SPLIT_MIN_GAMES


def test_the_medal_glyphs_are_the_ones_the_match_card_already_stamps():
	""" Copied from nammaoe2bot/features/postgame/card.MEDAL_GLYPHS for the same import reason, and
	pinned here: a player who sees a crossed sword on the card has to see the
	same mark on the report, or they are two different awards. Parsed as text
	rather than imported -- nammaoe2bot/features/postgame/card.py reaches Discord. """
	source = (_REPO_ROOT / "nammaoe2bot" / "features" / "postgame" / "card.py").read_text(encoding="utf-8")
	declared = ast.literal_eval(
		source.split("MEDAL_GLYPHS = ", 1)[1].split("\n", 1)[0])
	assert dict(declared) == {
		"military_medal": scouting_report._MILITARY_GLYPH,
		"villager_medal": scouting_report._VILLAGER_GLYPH,
	}


def test_medals_render_as_a_count_per_game_not_as_a_percentage():
	""" The framing this replaced. Rate and per-game count are the same number
	here -- a player holds at most one military medal in a game -- but "50%
	military" invites the reader to ask 50% of what, and the honest answer is
	not something a percent sign conveys. """
	rendered = scouting_report.render(_rich())
	medals = _line_with(rendered, "military")

	assert "**0.50** military" in medals
	assert "**0.20** villager" in medals
	assert "%" not in medals


def test_medal_counts_rest_on_the_ranked_denominator_not_games_played():
	""" games_ranked (10) is neither the game count (12) nor the medal count.
	Dividing by games played would print 0.42 and 0.17 here. """
	medals = _line_with(scouting_report.render(_rich()), "military")

	assert "over 10 ranked games" in medals
	assert "0.42" not in medals and "0.17" not in medals


def test_the_medal_denominator_is_always_printed_beside_the_counts():
	""" The binding copy rule. Two figures summing past a half are only legible
	against the sample they were taken over. """
	rendered = scouting_report.render(_rich())
	medals = _line_with(rendered, "military")

	assert "ranked games" in medals
	assert str(_rich()["medal_rates"]["games_ranked"]) in medals


def test_the_medal_line_is_omitted_when_no_game_was_ever_ranked():
	""" compute_rollup returns None rather than 0.0 for a player nothing was
	scored for: "never medals" and "never measured" are different claims. """
	rollup = compute_rollup([_stat(1, has_production=False)], [])
	assert rollup["medal_rates"] == dict(military=None, villager=None, games_ranked=0)
	assert "military" not in (scouting_report.render(rollup) or "")


def test_the_medal_line_is_omitted_below_the_floor():
	""" The state the window made common: somebody who played twice this month
	and medalled in both would otherwise read "1.00 military medals per game".
	The denominator is printed, but a reader who has to discount a number is
	exactly what this report exists not to produce. """
	stats = [_stat(i, military_medal=1, villager_medal=1)
	         for i in range(1, SPLIT_MIN_GAMES)]
	rollup = compute_rollup(stats, [])
	assert rollup["medal_rates"]["games_ranked"] == SPLIT_MIN_GAMES - 1
	assert "military" not in (scouting_report.render(rollup) or "")


def test_the_medal_line_appears_at_exactly_the_floor():
	""" A minimum, not a strict one -- a test that only asked about 4 and 6
	games would pass against either. """
	stats = [_stat(i, military_medal=1) for i in range(1, SPLIT_MIN_GAMES + 1)]
	assert "military" in scouting_report.render(compute_rollup(stats, []))


# ── eAPM ─────────────────────────────────────────────────────────────────

def test_the_apm_line_omits_the_peak_entirely_while_none_was_captured():
	""" peak_eapm is NULL on every row ingested before the bucket-capturing
	parser shipped, so median_peak is null and games_peak is 0. Nothing may
	stand in for it -- not a blank, not an em-dash, not a zero. """
	rollup = _rich()
	assert (rollup["apm"]["median_peak"], rollup["apm"]["games_peak"]) == (None, 0)

	apm = _line_with(scouting_report.render(rollup), "eAPM")
	assert "peak" not in apm.lower()
	assert "—" not in apm and "--" not in apm
	assert "0" not in apm.replace("**", "")


def test_the_apm_line_carries_its_own_sample_count_not_the_game_count():
	""" 11 games have an eAPM, 12 were played. The line rests on 11. """
	apm = _line_with(scouting_report.render(_rich()), "eAPM")
	assert "over 11 games" in apm


def test_the_peak_appears_with_its_own_count_once_buckets_arrive():
	""" Two independent samples, never one shared figure: the day peaks start
	being captured they will be captured for fewer games than the averages. """
	stats = [_stat(i, peak_eapm=100 + i if i <= 5 else None, avg_eapm=50 + i)
	         for i in range(1, 9)]
	rollup = compute_rollup(stats, [])
	assert (rollup["apm"]["median_peak"], rollup["apm"]["games_peak"]) == (103, 5)

	apm = _line_with(scouting_report.render(rollup), "eAPM")
	assert "median peak **103** over 5" in apm
	assert "over 8 games" in apm


def test_the_peak_is_held_back_until_its_own_sample_clears_the_floor():
	""" The peak has a separate count, so it gets the floor separately. A median
	over 40 games beside a peak over 2 is one line quoting two very different
	confidences without saying so. """
	stats = [_stat(i, peak_eapm=100 + i if i <= 2 else None, avg_eapm=50 + i)
	         for i in range(1, 9)]
	rollup = compute_rollup(stats, [])
	assert rollup["apm"]["games_peak"] == 2

	apm = _line_with(scouting_report.render(rollup), "eAPM")
	assert "peak" not in apm.lower()
	assert "over 8 games" in apm


def test_a_half_median_is_not_rounded_away():
	""" compute_rollup deliberately does not round an even-length sample to an
	integer: 62 and 62.5 are different samples. """
	stats = [_stat(i, avg_eapm=60) for i in range(1, 4)] + \
	        [_stat(i, avg_eapm=65) for i in range(4, 7)]
	assert "median **62.5**" in _line_with(scouting_report.render(compute_rollup(stats, [])), "eAPM")


def test_a_whole_median_renders_without_a_pointless_decimal():
	stats = [_stat(i, avg_eapm=60) for i in range(1, 4)] + \
	        [_stat(i, avg_eapm=64) for i in range(4, 7)]
	assert "median **62**" in _line_with(scouting_report.render(compute_rollup(stats, [])), "eAPM")


def test_the_apm_line_is_omitted_when_no_game_carried_an_eapm():
	rollup = compute_rollup([_stat(i, avg_eapm=None) for i in range(1, 9)], [])
	assert "eAPM" not in (scouting_report.render(rollup) or "")


# ── the two sentences ────────────────────────────────────────────────────

def _split_fixture(strategy_games, spawn_games, unit_games):
	"""Rows giving each block exactly one key, at exactly the game count asked
	for. Every game is resolved, and the player wins the first of each."""
	total = max(strategy_games, spawn_games, unit_games)
	stats, labels = [], []
	for i in range(1, total + 1):
		stats.append(_stat(i, winner=(i == 1), units=("Knight",) if i <= unit_games else ()))
		if i <= strategy_games:
			labels.append(_label(i, "archer_rush", "strategy"))
		if i <= spawn_games:
			labels.append(_label(i, "spawn_near_enemy", "spawn"))
	return compute_rollup(stats, labels)


def test_the_wins_most_sentence_names_all_three_dimensions_with_their_records():
	rendered = scouting_report.render(_rich())
	wins = _line_with(rendered, "Wins most")

	assert "opening **Archer Rush** (5W-1L)" in wins
	assert "**spawning next to the enemy** (5W-2L)" in wins
	assert "massing **Knight** (5W-3L)" in wins
	assert wins.endswith(".")


def test_a_spawn_reads_as_a_phrase_rather_than_as_a_label():
	""" Spawn is not something a player chooses, so the clause has to say what
	happened to them. "Top spawn: Near Enemy" was a heading over a dice roll;
	"wins most when spawning next to the enemy" is a sentence about how they
	play when it happens. """
	rendered = scouting_report.render(_rich())

	assert "spawning next to the enemy" in rendered
	assert "Near Enemy" not in rendered and "spawn_near_enemy" not in rendered


def test_only_the_three_position_spawns_can_ever_reach_the_sentence():
	""" The other eight stored spawn labels describe the MAP -- gold_poor,
	near_stone, tight_villagers -- and "wins most when spawning stone-poor" is a
	claim about the map generator. compute_rollup drops them from the blob, so
	they cannot reach the reader even as a fallback. """
	stats = [_stat(i, winner=True) for i in range(1, 13)]
	labels = [_label(i, "spawn_gold_poor", "spawn") for i in range(1, 13)]
	labels += [_label(i, "tight_villagers", "spawn") for i in range(1, 13)]
	rollup = compute_rollup(stats, labels)

	assert rollup["spawns"] == []
	assert "gold" not in (scouting_report.render(rollup) or "").lower()


def test_a_support_unit_built_beside_the_mass_never_reaches_the_sentence():
	""" 30 Knights and 5 Monks is a Knight game, not a Monk game. Support
	units are built in small numbers mostly in games already being won, so
	counting the whole stored top three would hand Monk an inflated record
	and print "massing Monk" for a knight player. """
	stats = []
	for i in range(1, 9):
		row = _stat(i, winner=(i % 2 == 1), units=("Knight", "Monk"))
		row["top_units"][0]["total"], row["top_units"][1]["total"] = 30, 5
		stats.append(row)
	rendered = scouting_report.render(compute_rollup(stats, []))

	assert "massing **Knight**" in rendered
	assert "Monk" not in rendered


def test_the_loses_most_sentence_never_repeats_the_wins_most_pick():
	""" "This is both what they are best and what they are worst at" is not a
	fact about a player. """
	rollup = _three_way()
	wins = _line_with(scouting_report.render(rollup), "Wins most")
	loses = _line_with(scouting_report.render(rollup), "Loses most")

	assert "Trebuchet" in wins and "Trebuchet" not in loses
	assert "Safe Castle" in loses and "Safe Castle" not in wins


def _three_way():
	"""One block holding three units that each rank first under a DIFFERENT
	rule, so the three candidate selections are told apart rather than merely
	exercised:

	  Safe Castle  28W-22L  (50 games, 56%) -- the most WINS, and the most games
	  Trebuchet    27W-10L  (37 games, 73%) -- the most wins per game at scale
	  Arambai       5W-1L   ( 6 games, 83%) -- the highest RAW rate, on nothing

	Ranking on wins picks Safe Castle; ranking on raw rate picks Arambai; the
	shrunk rate picks Trebuchet. Only the last is a true sentence.
	"""
	stats, mid = [], 1
	for unit, games, wins in (("Safe Castle", 50, 28), ("Trebuchet", 37, 27), ("Arambai", 6, 5)):
		for i in range(games):
			stats.append(_stat(mid, winner=(i < wins), units=(unit,)))
			mid += 1
	# 27 more games carrying no unit at all, which drag the player's own record
	# to exactly 50%. Without them the baseline is the mean of these three units
	# (65%) and the shrinkage pulls the 6-game fluke UP toward it instead of back
	# -- the fixture would then agree with the raw rate and prove nothing.
	for _ in range(27):
		stats.append(_stat(mid, winner=False, units=()))
		mid += 1
	return compute_rollup(stats, [])


def test_the_wins_most_pick_is_the_frequent_winner_not_the_small_sample_fluke():
	""" MUTANT GUARD: ranking on the raw win rate. At a 5-game floor the extreme
	rates are always the smallest samples, so a raw rate reliably picks the
	6-game fluke over the 37-game strength. """
	wins = _line_with(scouting_report.render(_three_way()), "Wins most")

	assert "massing **Trebuchet** (27W-10L)" in wins
	assert "Arambai" not in wins


def test_the_wins_most_pick_is_not_simply_the_most_played():
	""" MUTANT GUARD: ranking on absolute wins, which is what "wins the most
	games" literally means and which is really a volume measure. Safe Castle has
	both the most games and the most wins here, at a 56% rate -- and on real
	production data the same rule picked a 40%-win strategy as a player's
	strength, printing "wins most opening Safe Castle (12W-18L)". """
	wins = _line_with(scouting_report.render(_three_way()), "Wins most")
	assert "Safe Castle" not in wins


def test_the_loses_most_pick_is_the_weakest_relative_to_the_players_own_form():
	loses = _line_with(scouting_report.render(_three_way()), "Loses most")
	assert "massing **Safe Castle** (28W-22L)" in loses


def test_the_baseline_is_the_players_own_record_not_an_even_coin():
	""" Shrinking a 40%-win player's clauses toward 50% would rank their
	least-bad option as a strength. The prior is their own form over the same
	window, so "wins most" means "better than they usually are". """
	rollup = _three_way()
	assert rollup["baseline"] == dict(games=120, wins=60), "an even record, by construction"


def test_a_block_with_one_key_gives_a_wins_clause_and_no_loses_clause():
	""" The worst is chosen from what is left after the best is taken, so a
	single split cannot be both. """
	rollup = _split_fixture(strategy_games=SPLIT_MIN_GAMES, spawn_games=0, unit_games=0)
	rendered = scouting_report.render(rollup)

	assert "Wins most opening **Archer Rush**" in rendered
	assert "Loses most" not in rendered


def test_a_one_clause_sentence_reads_as_a_sentence():
	rendered = scouting_report.render(
		_split_fixture(strategy_games=SPLIT_MIN_GAMES, spawn_games=0, unit_games=0))
	wins = _line_with(rendered, "Wins most")

	assert wins == "Wins most opening **Archer Rush** (1W-4L)."
	assert " and " not in wins and "," not in wins


def test_a_two_clause_sentence_joins_with_and_and_no_comma():
	rendered = scouting_report.render(
		_split_fixture(strategy_games=SPLIT_MIN_GAMES, spawn_games=SPLIT_MIN_GAMES, unit_games=0))
	wins = _line_with(rendered, "Wins most")

	assert " and " in wins and "," not in wins


def test_a_three_clause_sentence_joins_with_commas_and_a_final_and():
	wins = _line_with(scouting_report.render(_rich()), "Wins most")

	assert wins.count(",") == 1
	assert wins.count(" and ") == 1
	assert wins.index(",") < wins.index(" and ")


def test_a_thin_strategy_drops_its_clause_while_the_other_two_stay():
	rendered = scouting_report.render(_split_fixture(
		strategy_games=SPLIT_MIN_GAMES - 1, spawn_games=SPLIT_MIN_GAMES, unit_games=SPLIT_MIN_GAMES))

	assert "opening" not in rendered
	assert "spawning" in rendered and "massing" in rendered


def test_a_thin_spawn_drops_its_clause_while_the_other_two_stay():
	rendered = scouting_report.render(_split_fixture(
		strategy_games=SPLIT_MIN_GAMES, spawn_games=SPLIT_MIN_GAMES - 1, unit_games=SPLIT_MIN_GAMES))

	assert "spawning" not in rendered
	assert "opening" in rendered and "massing" in rendered


def test_a_thin_unit_drops_its_clause_while_the_other_two_stay():
	rendered = scouting_report.render(_split_fixture(
		strategy_games=SPLIT_MIN_GAMES, spawn_games=SPLIT_MIN_GAMES, unit_games=SPLIT_MIN_GAMES - 1))

	assert "massing" not in rendered
	assert "opening" in rendered and "spawning" in rendered


def test_a_split_at_exactly_the_floor_still_renders():
	""" The floor is a minimum, not a strict one. """
	rendered = scouting_report.render(_split_fixture(
		strategy_games=SPLIT_MIN_GAMES, spawn_games=0, unit_games=0))
	assert "Archer Rush" in rendered


def test_a_dropped_split_is_dropped_silently_with_no_low_sample_warning():
	""" A number the reader has to discount is worse than no number, and every
	hedge this product ever shipped was eventually read as a fact anyway. """
	rendered = scouting_report.render(_split_fixture(
		strategy_games=2, spawn_games=SPLIT_MIN_GAMES, unit_games=SPLIT_MIN_GAMES))

	lowered = rendered.lower()
	for hedge in ("few", "small sample", "so far", "just", "low"):
		assert hedge not in lowered


def test_a_split_smaller_than_the_players_game_count_renders_without_complaint():
	""" Unresolved outcomes are excluded from every split but still count as
	games played, so a split's `games` is legitimately smaller than the row's.
	Nothing here may try to reconcile them. """
	stats = [_stat(i, winner=(True if i <= 5 else (False if i <= 8 else None))) for i in range(1, 13)]
	labels = [_label(i, "archer_rush", "strategy") for i in range(1, 13)]
	rollup = compute_rollup(stats, labels)

	assert rollup["strategies"][0]["games"] == 8, "4 unresolved games are out of the split"
	wins = _line_with(scouting_report.render(rollup), "Wins most")
	assert "opening **Archer Rush** (5W-3L)" in wins
	assert "12" not in wins


def test_a_stored_label_key_renders_as_a_readable_name():
	rendered = scouting_report.render(
		_split_fixture(strategy_games=SPLIT_MIN_GAMES, spawn_games=0, unit_games=0))
	assert "archer_rush" not in rendered and "**Archer Rush**" in rendered


def test_a_unit_name_is_never_title_cased_or_pluralised():
	""" Unit names arrive as display names already. Title-casing mangles
	"Hei Guang Cavalry"-style names subtly, and a naive plural turns Arambai and
	Mangudai -- which are already plural -- into nonsense. """
	stats = [_stat(i, winner=True, units=("Bombard Cannon",)) for i in range(1, 9)]
	rendered = scouting_report.render(compute_rollup(stats, []))

	assert "massing **Bombard Cannon**" in rendered
	assert "Cannons" not in rendered


# ── the window ───────────────────────────────────────────────────────────

def test_a_game_older_than_the_window_is_not_in_the_report():
	stats = [_stat(i, played_at=NOW - 10 * DAY) for i in range(1, 9)]
	stats += [_stat(i, played_at=NOW - 200 * DAY) for i in range(9, 40)]
	rollup = compute_rollup(stats, [], now=NOW, window_days=60)

	assert rollup["baseline"]["games"] == 8
	assert rollup["window_days"] == 60


def test_an_undated_game_is_outside_every_window_rather_than_inside_this_one():
	""" 19 production matches carry no date. Filing an unknown year into "the
	last 60 days" is the one claim the window exists to make. """
	stats = [_stat(i, played_at=None) for i in range(1, 9)]
	rollup = compute_rollup(stats, [], now=NOW, window_days=60)

	assert rollup["baseline"]["games"] == 0


def test_the_window_cutoff_and_the_printed_window_cannot_disagree():
	""" One parameter pair, not a precomputed cutoff plus a label to print
	beside it: a blob saying "last 60 days" over a 30-day cutoff is wrong in the
	one way none of its own numbers would reveal. """
	stats = [_stat(i, played_at=NOW - 45 * DAY) for i in range(1, 9)]

	assert compute_rollup(stats, [], now=NOW, window_days=30)["baseline"]["games"] == 0
	assert compute_rollup(stats, [], now=NOW, window_days=60)["baseline"]["games"] == 8
	assert compute_rollup(stats, [], now=NOW, window_days=30)["window_days"] == 30


def test_the_window_reaches_the_splits_through_the_stat_rows_alone():
	""" The filter is applied once, to the stat rows. A label whose game fell
	out of the window has no slot to join to and drops with it -- so the splits
	cannot end up describing a different span of time from the medals above
	them. """
	stats = [_stat(i, winner=True, played_at=NOW - 200 * DAY) for i in range(1, 13)]
	labels = [_label(i, "archer_rush", "strategy") for i in range(1, 13)]
	rollup = compute_rollup(stats, labels, now=NOW, window_days=60)

	assert rollup["strategies"] == []
	assert rollup["medal_rates"]["games_ranked"] == 0


def test_a_linked_player_with_no_recent_game_is_told_that_not_told_to_link():
	""" The state the window created, and the one that must not collapse into
	either neighbour. They ARE linked and their history is right there; PENDING
	would tell them to do something they have already done, and silence would
	read as "we have nothing on you". """
	stats = [_stat(i, played_at=NOW - 200 * DAY) for i in range(1, 40)]
	rendered = scouting_report.render(compute_rollup(stats, [], now=NOW, window_days=60))

	assert rendered == "No games in the last 60 days"
	assert scouting_report.PENDING not in rendered


def test_a_player_with_a_few_recent_games_is_told_how_few_rather_than_nothing():
	""" Some games, none of the lines above their floor. The count says which it
	is -- a quiet player, not a broken report -- without printing one figure
	that rests on it. """
	stats = [_stat(i, avg_eapm=None, has_production=False) for i in range(1, 3)]
	rendered = scouting_report.render(compute_rollup(stats, [], now=NOW, window_days=60))

	assert rendered == "Only 2 games in the last 60 days"


def test_the_one_game_case_is_a_sentence_rather_than_a_spliced_plural():
	""" The common shape of this state, and "Only 1 games" is the first thing a
	reader notices. Two whole sentences rather than one with an "s" spliced in:
	a translator handed "Only {games} game{s}" cannot render a language whose
	plural is not a suffix. """
	rollup = compute_rollup([_stat(1, avg_eapm=None, has_production=False)],
	                        [], now=NOW, window_days=60)
	assert scouting_report.render(rollup) == "Only 1 game in the last 60 days"


def test_an_unwindowed_rollup_with_nothing_to_say_still_renders_no_field():
	""" window_days is None for a lifetime rollup, and neither window sentence
	can be reached from one -- there is no window to name. """
	assert scouting_report.render(_blob()) is None


# ── translation ──────────────────────────────────────────────────────────

def test_every_rendered_string_goes_through_the_callers_translator():
	""" Same convention as every other user-facing string in these files: the
	translator sees whole, formattable sentences, never assembled fragments. """
	seen = []

	def gt(text):
		seen.append(text)
		return text

	scouting_report.render(_rich(), gt)

	assert "Wins most" in seen
	assert "opening {name}" in seen and "massing {name}" in seen
	assert "spawning next to the enemy" in seen
	assert any("ranked games" in s for s in seen)
	assert any("median" in s for s in seen)
	assert all(s == s.strip() for s in seen), "no fragment is handed over with loose whitespace"


def test_the_window_sentences_go_through_the_translator_too():
	seen = []

	def gt(text):
		seen.append(text)
		return text

	stats = [_stat(i, played_at=NOW - 200 * DAY) for i in range(1, 40)]
	scouting_report.render(compute_rollup(stats, [], now=NOW, window_days=60), gt)
	assert "No games in the last {days} days" in seen


# ── the player_rollups readers ───────────────────────────────────────────

class _FakeDb:
	def __init__(self, rows=()):
		self.rows = [dict(r) for r in rows]

	async def select_one(self, columns, table, where=None):
		assert table == "player_rollups"
		for row in self.rows:
			if all(row.get(k) == v for k, v in (where or {}).items()):
				return {c: row.get(c) for c in columns}
		return None

	async def select(self, columns, table, where=None, **_kwargs):
		assert table == "player_rollups"
		return [{c: row.get(c) for c in columns} for row in self.rows
		        if all(row.get(k) == v for k, v in (where or {}).items())]


def _stored(community_id, user_id, blob):
	return dict(community_id=community_id, user_id=user_id, games=12,
	            rollup=json.dumps(blob, sort_keys=True), computed_at=1000)


def test_fetch_returns_none_for_a_player_with_no_row(monkeypatch):
	""" None, never an empty blob: the absence IS the signal, and an empty
	blob would render as a report with every line missing instead of as
	"Statistics pending linking". """
	monkeypatch.setattr(rollups, "db", _FakeDb([_stored(1, 42, _blob())]))

	assert asyncio.run(rollups.fetch(1, 999)) is None
	assert asyncio.run(rollups.fetch(2, 42)) is None, "and a rollup in another community is not this one's"


def test_fetch_decodes_the_stored_json_blob(monkeypatch):
	monkeypatch.setattr(rollups, "db", _FakeDb([_stored(1, 42, _rich())]))

	assert asyncio.run(rollups.fetch(1, 42)) == _rich()


# ── /identity status' gated-features list ────────────────────────────────

# ── the /rank wiring (nammaoe2bot/discord/commands/stats.py) ─────────────────────────────
# stats.py does `from nextcord import Member, Embed, Colour, File` and
# `import bot`; under CI none of that resolves, and importing it normally
# would first run bot/commands/__init__.py and star-import every other command
# module. Same trick as tests/test_identity.py: register minimal nextcord
# fakes and load the file by path.

def _load_stats_module(monkeypatch):
	fake_nextcord = types.ModuleType("nextcord")
	fake_nextcord.Member = object
	fake_nextcord.Colour = lambda value=0: value
	fake_nextcord.File = object

	# One Embed fake for the whole suite — see tests/conftest.py's FakeEmbed for
	# why a per-file copy was order-dependent rather than merely duplicated.
	from tests.conftest import FakeEmbed

	fake_nextcord.Embed = FakeEmbed
	monkeypatch.setitem(sys.modules, "nextcord", fake_nextcord)

	fake_nextcord_utils = types.ModuleType("nextcord.utils")
	fake_nextcord_utils.get = lambda *a, **k: None
	fake_nextcord_utils.find = lambda *a, **k: None
	fake_nextcord_utils.escape_markdown = lambda s: s
	monkeypatch.setitem(sys.modules, "nextcord.utils", fake_nextcord_utils)

	path = _REPO_ROOT / "nammaoe2bot" / "discord" / "commands" / "stats.py"
	spec = importlib.util.spec_from_file_location("stats_standalone_test", path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


class _FakeCtx:
	def __init__(self, channel_id=4242):
		self.qc = types.SimpleNamespace(gt=lambda s: s, id=channel_id)
		self.channel = types.SimpleNamespace(id=channel_id)


def _wire(monkeypatch, community_id, rollup):
	asked = []

	async def _community_for_channel(channel_id):
		asked.append(channel_id)
		return community_id

	async def _fetch(cid, user_id):
		asked.append((cid, user_id))
		return rollup

	monkeypatch.setattr(community, "community_for_channel", _community_for_channel)
	monkeypatch.setattr(rollups, "fetch", _fetch)
	return asked


def test_rank_renders_the_pending_string_for_a_player_with_no_rollup(monkeypatch):
	stats = _load_stats_module(monkeypatch)
	asked = _wire(monkeypatch, community_id=7, rollup=None)

	value = asyncio.run(stats._scouting_report(_FakeCtx(channel_id=4242), 42))

	assert value == "Statistics pending linking"
	assert asked == [4242, (7, 42)], "the rollup is read for the channel's community"


def test_rank_renders_the_measured_report_for_a_scouted_player(monkeypatch):
	stats = _load_stats_module(monkeypatch)
	_wire(monkeypatch, community_id=7, rollup=_rich())

	value = asyncio.run(stats._scouting_report(_FakeCtx(), 42))

	assert value == scouting_report.render(_rich())
	assert "Statistics pending linking" not in value


def test_rank_says_nothing_at_all_on_a_channel_with_no_community(monkeypatch):
	""" An unenrolled channel is the ordinary state for most channels, not a
	linking gap: nothing was ever measured there and nothing is pending. """
	stats = _load_stats_module(monkeypatch)
	asked = _wire(monkeypatch, community_id=None, rollup=_rich())

	assert asyncio.run(stats._scouting_report(_FakeCtx(), 42)) is None
	assert asked == [4242], "and no rollup is read"


# ── the embed field itself ───────────────────────────────────────────────
# _scouting_report returning None and the FIELD not being added are two
# different facts, and only the second one is what a player sees. Nothing drove
# _rank_profile: replacing its `if scouting:` guard with `value=scouting or "—"`
# shipped green, putting the em-dash this stage explicitly forbids in front of
# exactly the population the third state exists for. These drive the real
# function and assert on the real embed.

class _FakeQc:
	def __init__(self, channel_id=4242):
		self.id = channel_id
		self.rating = types.SimpleNamespace(channel_id=channel_id)

	def gt(self, text):
		return text

	async def get_lb(self):
		return [dict(user_id=42, rating=1500, deviation=50, wins=6, losses=4, draws=0,
		             is_hidden=0, streak=1)]

	def rating_rank(self, _rating):
		return dict(rank="〈Gold〉")


class _FakeTarget:
	id = 42
	display_avatar = None
	nick = None
	name = "player"
	display_name = "player"


class _FakeRankCtx:
	""" The narrowest ctx _rank_profile actually touches on the non-detailed
	path: no interaction (so it never defers), a leaderboard the target is on,
	and a reply that keeps the embed instead of sending it. """

	def __init__(self, channel_id=4242):
		self.qc = _FakeQc(channel_id)
		self.channel = types.SimpleNamespace(id=channel_id)
		self.author = _FakeTarget()
		self.replied = []

	async def reply(self, embed=None, file=None):
		self.replied.append(embed)


def _prepared_stats_module(monkeypatch):
	""" stats.py, loaded standalone, with the two things _rank_profile needs
	that the CI stubs cannot supply.

	`find` comes from nextcord.utils, which conftest fakes as a function
	returning None -- so the leaderboard lookup would miss its own row and fall
	through to a database read. The real one-liner is restored rather than
	worked around, because the row it finds is what every later line reads.

	player_profile is faked because gathering a profile needs the database and
	has nothing to do with the scouting field; everything between that profile
	and the field is the shipped code.
	"""
	import nammaoe2bot.features.scouting.profile as player_profile

	stats = _load_stats_module(monkeypatch)
	monkeypatch.setattr(stats, "find", lambda predicate, seq: next(
		(item for item in seq if predicate(item)), None))
	monkeypatch.setattr(player_profile, "web_profile_url", lambda *_a, **_k: "")

	async def _gather(*_a, **_k):
		return {}

	monkeypatch.setattr(player_profile, "gather_profile", _gather)
	return stats


def _drive_rank_profile(monkeypatch, rollup):
	""" Run the REAL _rank_profile and hand back the embed it built. """
	stats = _prepared_stats_module(monkeypatch)
	_wire(monkeypatch, community_id=7, rollup=rollup)

	ctx = _FakeRankCtx()
	asyncio.run(stats._rank_profile(ctx))
	assert len(ctx.replied) == 1
	return ctx.replied[0]


def _scouting_fields(embed):
	return [f for f in embed.fields if "Scouting report" in (f["name"] or "")]


def test_rank_omits_the_scouting_field_entirely_for_a_linked_player_with_nothing_to_report(monkeypatch):
	""" The third state, end to end. A linked player whose every line is below
	its floor gets NO field — not an em-dash, not a placeholder, not "pending
	linking" (which would be false about a linked player). """
	embed = _drive_rank_profile(monkeypatch, rollup=_blob())

	assert _scouting_fields(embed) == []
	values = " ".join(str(f["value"]) for f in embed.fields)
	for placeholder in ("—", "--", "N/A", "n/a", "None"):
		assert placeholder not in values, f"a {placeholder!r} stood in for a measurement nobody took"


def test_rank_adds_the_pending_field_for_a_player_with_no_rollup_row(monkeypatch):
	embed = _drive_rank_profile(monkeypatch, rollup=None)

	fields = _scouting_fields(embed)
	assert len(fields) == 1
	assert fields[0]["value"] == scouting_report.PENDING
	assert fields[0]["inline"] is False


def test_rank_adds_the_measured_field_for_a_scouted_player(monkeypatch):
	embed = _drive_rank_profile(monkeypatch, rollup=_rich())

	fields = _scouting_fields(embed)
	assert len(fields) == 1
	assert fields[0]["value"] == scouting_report.render(_rich())
	assert scouting_report.PENDING not in fields[0]["value"]


def test_rank_survives_a_scouting_read_that_blows_up_and_shows_no_field(monkeypatch):
	""" Best-effort like every other piece of the profile: a rollup read that
	fails costs the field, not the command — and costs it silently rather than
	rendering a placeholder for a number nobody has. """
	stats = _prepared_stats_module(monkeypatch)

	async def _boom(_channel_id):
		raise RuntimeError("simulated community lookup failure")

	monkeypatch.setattr(community, "community_for_channel", _boom)

	ctx = _FakeRankCtx()
	asyncio.run(stats._rank_profile(ctx))

	assert _scouting_fields(ctx.replied[0]) == []


# ── what stage 5a removed ────────────────────────────────────────────────
# Source-level, because nammaoe2bot/web/server.py cannot be imported under CI (aiohttp.web +
# nammaoe2bot.runtime.client's nextcord) -- the same approach tests/test_web_identity.py
# takes. These pin the deletion half of the cutover: the generated persona and
# scout read are gone from /rank, and nothing recomputes a persona on ingest.

def _source(*parts):
	return (_REPO_ROOT.joinpath(*parts)).read_text()


def test_rank_no_longer_renders_a_generated_persona_or_scout_read():
	""" Parsed rather than grepped: the persona stack has to be gone from the
	CODE, while the comments explaining what replaced it are exactly what a
	future reader needs. """
	tree = ast.parse(_source("nammaoe2bot", "discord", "commands", "stats.py"))
	strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
	names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
	names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}

	for gone in ("persona", "scout_report", "tagline", "epithet", "parsed_matches"):
		assert gone not in strings, f"{gone} is still read out of a snapshot"
	assert "player_overview_snapshot" not in names
	assert not [s for s in strings if "Recurring tags" in s]
	assert "re" not in names, "the tag-stripping regex went with the prose it scrubbed"


def test_the_dashboard_overview_snapshot_is_gone_from_the_web_layer():
	assert "player_overview_snapshot" not in _source("nammaoe2bot", "web", "server.py")


def test_the_ingest_path_no_longer_refreshes_personas():
	""" rs_player_personas stops being written here. The module itself stays
	until stage 6 drops the table, so this asserts the CALL is gone rather
	than the file. """
	src = _source("nammaoe2bot", "ingest", "store.py")
	tree = ast.parse(src)
	calls = [n for n in ast.walk(tree)
	         if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
	         and n.func.attr == "refresh_match_users"]
	assert calls == []
	assert "import persona_store" not in src


def test_rank_still_builds_its_scouting_field_from_the_rollup_helper():
	tree = ast.parse(_source("nammaoe2bot", "discord", "commands", "stats.py"))
	profile = next(n for n in ast.walk(tree)
	               if isinstance(n, ast.AsyncFunctionDef) and n.name == "_rank_profile")
	called = {n.func.id for n in ast.walk(profile)
	          if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
	assert "_scouting_report" in called


def test_the_peak_is_named_as_a_median_rather_than_as_a_high_score():
	""" A bare "peak 103" reads as a MAXIMUM -- this player's busiest minute
	ever. It is the median of their per-game busiest minutes, which on a
	heavy-tailed measure is a much smaller and much more useful number, and the
	max is the one figure rollups deliberately never computes: over a season it
	is one parse artefact away from fiction.

	Both halves of the line say "median" for the same reason. """
	stats = [_stat(i, peak_eapm=100 + i if i <= 5 else None, avg_eapm=50 + i)
	         for i in range(1, 9)]
	apm = _line_with(scouting_report.render(compute_rollup(stats, [])), "eAPM")

	assert apm == "eAPM: median **54.5** over 8 games · median peak **103** over 5"
	# No spelling of the line may leave a bare "peak N" for a reader to misread.
	assert "· peak" not in apm and not apm.endswith("peak")


# ── the eAPM board ───────────────────────────────────────────────────────

def _apm_blob(median_avg=None, games_avg=0, median_peak=None, games_peak=0, window_days=60):
	return _blob(apm=dict(median_avg=median_avg, games_avg=games_avg,
	                      median_peak=median_peak, games_peak=games_peak),
	             window_days=window_days)


# ── /eapm: the command around the board ──────────────────────────────────

class _ReplyCtx(_FakeCtx):
	"""_FakeCtx plus the reply capture the board command needs."""

	def __init__(self, channel_id=4242):
		super().__init__(channel_id)
		self.replies = []

	async def reply(self, embed=None, **_kw):
		self.replies.append(embed)


class _FakeExc:
	"""The two error types the board command raises. bot.Exc is assembled by
	bot/__init__.py, which this suite deliberately never imports (it builds a
	Discord client); the command only needs the names to exist and to raise."""

	class NotFoundError(Exception):
		pass

	class SyntaxError(Exception):  # noqa: A001 — mirrors bot.Exc's own name
		pass


def _wire_board(monkeypatch, community_id, rollups_by_user, hidden=(), names=None):
	import bot as bot_pkg
	monkeypatch.setattr(bot_pkg, "Exc", _FakeExc, raising=False)

	async def _community_for_channel(_channel_id):
		return community_id

	async def _fetch_community(_cid):
		return rollups_by_user

	async def _fetchall(_sql, *_a):
		return [{"user_id": u} for u in hidden]

	async def _names():
		return names or {}

	monkeypatch.setattr(community, "community_for_channel", _community_for_channel)
	monkeypatch.setattr(rollups, "fetch_community", _fetch_community)
	monkeypatch.setattr(identity, "profiles_and_names_by_user", _names)
	monkeypatch.setattr(db, "fetchall", _fetchall)


def _eapm_embed(monkeypatch, rollups_by_user, metric=None, hidden=(), names=None, page=1):
	stats = _load_stats_module(monkeypatch)
	_wire_board(monkeypatch, 7, rollups_by_user, hidden, names)
	ctx = _ReplyCtx()
	asyncio.run(stats.eapm(ctx, metric=metric, page=page))
	return ctx.replies[0]


# ── /eapm_explained ──────────────────────────────────────────────────────

def _explainer(monkeypatch):
	stats = _load_stats_module(monkeypatch)
	ctx = _ReplyCtx()
	asyncio.run(stats.eapm_explained(ctx))
	return ctx.replies[0]

