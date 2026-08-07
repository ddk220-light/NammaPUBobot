"""Unit tests for the derived-global game_stats pure compute (bot/derived/game_stats.py)."""

import asyncio
import json

import bot.derived.game_stats as game_stats
from bot.derived.game_stats import compute_game_stats


def _p(pnum, profile_id, **kw):
	row = dict(player_number=pnum, profile_id=profile_id, civ="Franks", team="1",
	           winner=True, eapm=50, villagers=100, military=20)
	row.update(kw)
	return row


def test_medals_rank_match_wide_on_raw_counts():
	players = [_p(1, 11, villagers=120, military=10), _p(2, 22, villagers=90, military=40),
	           _p(3, 33, villagers=80, military=30)]
	rows = compute_game_stats(players, [], [], 1000)
	by_pnum = {r["player_number"]: r for r in rows}
	assert by_pnum[1]["villager_medal"] == 1
	assert by_pnum[2]["military_medal"] == 1
	assert by_pnum[3]["military_medal"] == 2


def test_row_fields_pass_through_without_swapping():
	# profile_id is deliberately far from player_number so a mutant that reads
	# player_number instead of profile_id (e.g. `profile_id=p.get("player_number")`)
	# cannot pass by coincidence. civ and team hold unmistakably different
	# values -- not two similar-looking strings -- so a mutant that swaps the
	# two assignments fails on both fields, not just one that happens to match.
	players = [_p(7, 424242, civ="Mongols", team="2", winner=False)]
	rows = compute_game_stats(players, [], [], 918273)
	row = rows[0]
	assert row["player_number"] == 7
	assert row["profile_id"] == 424242
	assert row["civ"] == "Mongols"
	assert row["team"] == "2"
	assert row["winner"] is False
	assert row["computed_at"] == 918273


def test_player_with_no_production_gets_no_medal():
	players = [_p(1, 11, villagers=0, military=0), _p(2, 22, villagers=50, military=5)]
	rows = compute_game_stats(players, [], [], 1000)
	by_pnum = {r["player_number"]: r for r in rows}
	assert by_pnum[1]["military_medal"] is None
	assert by_pnum[1]["villager_medal"] is None
	assert by_pnum[2]["villager_medal"] == 1


# ── has_production ───────────────────────────────────────────────────────
# The flag was always computed for assign_medals' payload and then discarded,
# which left "no medal because nobody measured me" and "no medal because I
# placed fourth" indistinguishable from a stored row -- and that distinction is
# the denominator of player_rollups' medal_rates. It is now emitted, and these
# pin it to the SAME predicate the medals are ranked by, not merely to a
# plausible one.

def test_has_production_is_emitted_on_every_row():
	players = [_p(1, 11, villagers=100, military=20), _p(2, 22, villagers=0, military=0)]
	rows = compute_game_stats(players, [], [], 1000)
	by_pnum = {r["player_number"]: r for r in rows}
	assert by_pnum[1]["has_production"] is True
	assert by_pnum[2]["has_production"] is False


def test_has_production_is_true_on_either_axis_alone():
	# Villagers only and military only are both "measured". A mutant reading
	# only one of the two counts passes on one of these rows and fails the other.
	players = [_p(1, 11, villagers=7, military=0), _p(2, 22, villagers=0, military=7)]
	rows = compute_game_stats(players, [], [], 1000)
	by_pnum = {r["player_number"]: r for r in rows}
	assert by_pnum[1]["has_production"] is True
	assert by_pnum[2]["has_production"] is True


def test_missing_counts_read_as_no_production_not_as_an_error():
	# NULL villagers/military is what an unparsed slot looks like in
	# replay_players; `(x or 0)` must read them as zero, same as the SQL
	# COALESCE in 008_game_stats_has_production's backfill.
	rows = compute_game_stats([_p(1, 11, villagers=None, military=None)], [], [], 1000)
	assert rows[0]["has_production"] is False


def test_has_production_agrees_with_medal_eligibility_for_every_player():
	# The property that matters, stated directly: a player the medals ranked
	# must carry the flag, and a player they refused to rank must not. Three
	# measured players means all three place on both axes, so "ranked" is
	# observable from the medals alone -- and the fourth, unmeasured, gets
	# neither. A second, drifting definition of "has production" fails here
	# rather than merely producing a wrong number in a rollup two layers away.
	players = [_p(1, 11, villagers=100, military=30), _p(2, 22, villagers=80, military=20),
	           _p(3, 33, villagers=60, military=10), _p(4, 44, villagers=0, military=0)]
	rows = compute_game_stats(players, [], [], 1000)
	for row in rows:
		medalled = row["military_medal"] is not None or row["villager_medal"] is not None
		assert row["has_production"] is medalled, row


def test_exact_tie_breaks_on_player_number_not_input_order():
	# Identical counts, with the higher player_number listed FIRST: a pass only
	# proves the tie is decided by player_number, not by input list order.
	# (compute_game_stats's payload only carries player_number/military/
	# villagers/has_production into assign_medals -- a seeded "identity" never
	# reaches it, so it would be an inert decoy here, not a real guard.)
	players = [_p(2, 22, villagers=50, military=50),
	           _p(1, 11, villagers=50, military=50)]
	rows = compute_game_stats(players, [], [], 1000)
	by_pnum = {r["player_number"]: r for r in rows}
	assert by_pnum[1]["villager_medal"] == 1
	assert by_pnum[2]["villager_medal"] == 2


def test_top_units_is_military_only_top_three_by_total():
	units = [
		dict(player_number=1, unit="Villager", category="economy", is_military=False, total=999),
		dict(player_number=1, unit="Knight", category="cavalry", is_military=True, total=30),
		dict(player_number=1, unit="Spearman", category="infantry", is_military=True, total=20),
		dict(player_number=1, unit="Archer", category="archer", is_military=True, total=10),
		dict(player_number=1, unit="Scout Cavalry", category="cavalry", is_military=True, total=5),
	]
	rows = compute_game_stats([_p(1, 11)], units, [], 1000)
	assert [u["unit"] for u in rows[0]["top_units"]] == ["Knight", "Spearman", "Archer"]


def test_top_units_empty_when_no_military_rows():
	units = [dict(player_number=1, unit="Villager", category="economy", is_military=False, total=99)]
	rows = compute_game_stats([_p(1, 11)], units, [], 1000)
	assert rows[0]["top_units"] == []


def test_top_units_tie_breaks_alphabetically_by_unit_name():
	# Both units tie exactly on total, and are listed with the alphabetically
	# LATER one first, so a pass only proves the tie is broken by unit name --
	# a plain stable sort on -total alone would leave the input order intact
	# and report ["Spearman", "Knight"].
	units = [
		dict(player_number=1, unit="Spearman", category="infantry", is_military=True, total=20),
		dict(player_number=1, unit="Knight", category="cavalry", is_military=True, total=20),
	]
	rows = compute_game_stats([_p(1, 11)], units, [], 1000)
	assert [u["unit"] for u in rows[0]["top_units"]] == ["Knight", "Spearman"]


def test_avg_eapm_passes_through_and_is_not_a_bucket_mean():
	# 3 buckets averaging 20, but eapm says 50. The stored value must be 50 --
	# bucket rows are absent for zero-action minutes, so their mean overstates.
	apm = [dict(player_number=1, minute=0, actions=10),
	       dict(player_number=1, minute=1, actions=20),
	       dict(player_number=1, minute=2, actions=30)]
	rows = compute_game_stats([_p(1, 11, eapm=50)], [], apm, 1000)
	assert rows[0]["avg_eapm"] == 50
	assert rows[0]["peak_eapm"] == 30


def test_peak_eapm_is_none_without_buckets():
	rows = compute_game_stats([_p(1, 11)], [], [], 1000)
	assert rows[0]["peak_eapm"] is None


# ── write() ──────────────────────────────────────────────────────────────
# write() is called from store.write_match inside a try/except that swallows
# any exception and only logs -- so a defect here produces an empty table and
# fully green CI unless something exercises it directly. No pytest-asyncio in
# this repo, so this is a plain sync test driving the coroutine with
# asyncio.run(), never `async def test_...` (which pytest would silently skip).
class _RecordingDB:
	def __init__(self):
		self.calls = []

	async def execute(self, sql, args=None):
		self.calls.append(("execute", sql, list(args) if args else []))

	async def insert_many(self, table, rows, on_duplicate=None):
		self.calls.append(("insert_many", table, list(rows), on_duplicate))


def test_write_deletes_before_insert_stamps_id_and_serialises_top_units():
	recorder = _RecordingDB()
	original_db = game_stats.db
	game_stats.db = recorder
	try:
		rows = [
			dict(player_number=1, profile_id=11, civ="Franks", team="1", winner=True,
			     avg_eapm=50, peak_eapm=60, military_medal=1, villager_medal=None,
			     has_production=True,
			     top_units=[dict(unit="Knight", category="cavalry", total=30)],
			     computed_at=1000, played_at=900, compute_version=game_stats.COMPUTE_VERSION),
			dict(player_number=2, profile_id=22, civ="Mongols", team="2", winner=False,
			     avg_eapm=40, peak_eapm=None, military_medal=None, villager_medal=1,
			     has_production=True, top_units=[], computed_at=1000, played_at=900,
			     compute_version=game_stats.COMPUTE_VERSION),
		]
		asyncio.run(game_stats.write(555, rows))
	finally:
		game_stats.db = original_db

	# (a) DELETE fires before the insert.
	assert [c[0] for c in recorder.calls] == ["execute", "insert_many"]
	_, delete_sql, delete_args = recorder.calls[0]
	assert "DELETE" in delete_sql.upper()
	assert "game_stats" in delete_sql
	assert delete_args == [555]

	_, table, payload, on_duplicate = recorder.calls[1]
	assert table == "game_stats"
	assert on_duplicate == "replace"
	assert len(payload) == 2

	# (b) replay_match_id stamped onto EVERY row.
	assert all(r["replay_match_id"] == 555 for r in payload)

	# (c) top_units serialised the way the adapter expects: a JSON string
	# (MEDIUMTEXT column), not the raw list compute_game_stats returned.
	assert isinstance(payload[0]["top_units"], str)
	assert json.loads(payload[0]["top_units"]) == [dict(unit="Knight", category="cavalry", total=30)]
	assert json.loads(payload[1]["top_units"]) == []


def test_write_with_no_rows_still_deletes_but_never_inserts():
	recorder = _RecordingDB()
	original_db = game_stats.db
	game_stats.db = recorder
	try:
		asyncio.run(game_stats.write(555, []))
	finally:
		game_stats.db = original_db

	assert [c[0] for c in recorder.calls] == ["execute"]


# ── payload column order ─────────────────────────────────────────────────
# nammaoe2bot/runtime/database/mysql.py's insert_many takes its column list from the FIRST
# row's keys and then zips every later row's .values() against it. Two rows
# carrying the same keys in a DIFFERENT order therefore write values into the
# wrong columns -- silently, with no MySQL error whenever the types happen to be
# compatible (civ/team, avg_eapm/peak_eapm, the two medals). Every caller is
# safe by construction today; nothing enforced it until write() normalised.

def _written_payload(rows, replay_match_id=555):
	recorder = _RecordingDB()
	original_db = game_stats.db
	game_stats.db = recorder
	try:
		asyncio.run(game_stats.write(replay_match_id, rows))
	finally:
		game_stats.db = original_db
	return recorder.calls[1][2]


def _stats_row(**kw):
	base = dict(player_number=1, profile_id=11, civ="Franks", team="1", winner=True,
	            avg_eapm=50, peak_eapm=60, military_medal=1, villager_medal=2,
	            has_production=True, top_units=[], computed_at=1000, played_at=900,
	            compute_version=game_stats.COMPUTE_VERSION)
	base.update(kw)
	return base


def test_every_written_row_uses_one_column_order():
	# Second row's dict is built in a deliberately different key order.
	first = _stats_row(player_number=1)
	second = {k: v for k, v in reversed(list(_stats_row(player_number=2, civ="Goths").items()))}

	payload = _written_payload([first, second])

	assert list(payload[0].keys()) == list(game_stats._COLUMNS)
	assert list(payload[1].keys()) == list(game_stats._COLUMNS)
	# ...and the values still belong to their own columns after normalising.
	assert payload[1]["player_number"] == 2
	assert payload[1]["civ"] == "Goths"


def test_write_accepts_exactly_what_compute_game_stats_returns():
	# The two halves are validated against each other by write()'s key-set
	# guard, so a field added to the compute and not to _COLUMNS (or vice
	# versa) raises here instead of the ingest path swallowing it -- store.
	# write_match calls write() inside a try/except that only logs.
	players = [_p(1, 11, villagers=100, military=20), _p(2, 22, villagers=0, military=0)]
	payload = _written_payload(compute_game_stats(players, [], [], 1000))
	assert [r["has_production"] for r in payload] == [True, False]
	assert all(list(r.keys()) == list(game_stats._COLUMNS) for r in payload)


def test_a_row_with_an_unexpected_key_set_is_rejected_loudly():
	# Dropping or defaulting the difference is how a column silently stops being
	# written; write()'s callers are all best-effort-guarded, so this logs.
	import pytest

	with pytest.raises(ValueError, match="expected exactly"):
		_written_payload([_stats_row(surprise="unmapped")])

	with pytest.raises(ValueError, match="expected exactly"):
		_written_payload([{k: v for k, v in _stats_row().items() if k != "civ"}])


# ── style units: what top_units is allowed to contain ────────────────────

def _unit(name, category, total, is_military=1, player_number=1):
	return dict(player_number=player_number, unit=name, category=category,
	            total=total, is_military=is_military)


def test_a_non_military_unit_is_never_a_style_unit():
	assert not game_stats.is_style_unit(_unit("Villager", "villager", 200, is_military=0))


def test_the_three_trash_lines_are_excluded():
	""" Spearman, Skirmisher and the scout line cost no gold. They are what a
	player is FORCED into -- a counter, or an empty gold pile -- not what they
	chose, and in production they are three of the six most-built units in the
	game. Leaving them in tells most players they mass Spearman. """
	for name, category in (("Spearman", "spearman_line"), ("Halberdier", "spearman_line"),
	                       ("Skirmisher", "skirmisher"), ("Elite Skirmisher", "skirmisher"),
	                       ("Scout Cavalry", "scout"), ("Hussar", "scout"),
	                       ("Camel Scout", "scout"), ("Eagle Scout", "scout")):
		assert not game_stats.is_style_unit(_unit(name, category, 60)), name


def test_every_trebuchet_variant_is_excluded_however_it_is_spelled():
	""" Not trash -- it costs gold -- and excluded for the other reason: it is
	the second most common military unit in the database, built in a quarter of
	all player-games and only ~5 at a time, because almost every imperial game
	ends with somebody knocking a building down. A unit nearly everybody builds
	separates nobody, so it crowds out the one that would have.

	Matched as a substring because three spellings already exist in production
	and a civ release can add a fourth. """
	for name in ("Trebuchet", "Traction Trebuchet", "Mounted Trebuchet", "trebuchet"):
		assert not game_stats.is_style_unit(_unit(name, "siege", 8)), name


def test_the_other_siege_units_are_style_units():
	""" The exclusion is Trebuchet and the ram line specifically, NOT siege as a
	category: going Mangonel, Scorpion or Bombard Cannon is a real choice about
	how somebody plays, while a treb or a ram is how a game ends. """
	for name in ("Mangonel", "Scorpion", "Bombard Cannon", "Organ Gun", "Siege Onager"):
		assert game_stats.is_style_unit(_unit(name, "siege", 12)), name


def test_gold_units_survive():
	for name, category in (("Knight", "knight_line"), ("Archer", "archer_line"),
	                       ("Champion", "militia_line"), ("Monk", "monk"),
	                       ("Mangudai", "unique_other"), ("Camel Rider", "camel_line")):
		assert game_stats.is_style_unit(_unit(name, category, 30)), name


def test_the_trash_filter_runs_before_the_top_three_cut():
	""" THE LOAD-BEARING ORDER. Filtering the stored list at read time instead
	would leave a player whose three most-built units are Spearman, Scout
	Cavalry and Skirmisher with no unit line at all -- and in production the
	first style unit sits outside the unfiltered top 3 for 1429 of 6061
	player-games, so this is the common case rather than the corner. """
	players = [dict(player_number=1, profile_id=11, civ="Franks", team="1", winner=True,
	                eapm=50, villagers=100, military=40)]
	units = [
		_unit("Spearman", "spearman_line", 200),      # would be 1st unfiltered
		_unit("Scout Cavalry", "scout", 150),         # would be 2nd
		_unit("Skirmisher", "skirmisher", 120),       # would be 3rd
		_unit("Trebuchet", "siege", 90),              # would be 4th
		_unit("Knight", "knight_line", 60),
		_unit("Monk", "monk", 20),
		_unit("Scorpion", "siege", 10),
	]

	rows = game_stats.compute_game_stats(players, units, [], computed_at=1)

	assert [u["unit"] for u in rows[0]["top_units"]] == ["Knight", "Monk", "Scorpion"]


def test_a_player_who_built_only_trash_gets_an_empty_list_not_a_trash_one():
	players = [dict(player_number=1, profile_id=11, civ="Franks", team="1", winner=True,
	                eapm=50, villagers=100, military=40)]
	units = [_unit("Spearman", "spearman_line", 200), _unit("Trebuchet", "siege", 40)]

	rows = game_stats.compute_game_stats(players, units, [], computed_at=1)

	assert rows[0]["top_units"] == []


# ── played_at and the compute version ────────────────────────────────────

def test_every_row_carries_the_matchs_date_and_the_current_compute_version():
	players = [dict(player_number=n, profile_id=10 + n, civ="Franks", team="1", winner=True,
	                eapm=50, villagers=100, military=40) for n in (1, 2)]

	rows = game_stats.compute_game_stats(players, [], [], computed_at=1, played_at=1700000000)

	assert {r["played_at"] for r in rows} == {1700000000}
	assert {r["compute_version"] for r in rows} == {game_stats.COMPUTE_VERSION}


def test_an_undated_match_stamps_no_date_rather_than_inventing_one():
	""" 19 production matches have no recorded date, and rollups.in_window reads
	a NULL as outside every window. A 0 or a "now" would file a game from an
	unknown year into the last 60 days. """
	players = [dict(player_number=1, profile_id=11, civ="Franks", team="1", winner=True,
	                eapm=50, villagers=100, military=40)]

	rows = game_stats.compute_game_stats(players, [], [], computed_at=1)

	assert rows[0]["played_at"] is None


def test_every_ram_is_excluded_however_it_is_spelled():
	""" Same reason as the trebuchet: built by nearly everybody in small numbers
	as a means to an end. 7th most common military unit in production, and it
	held 9 of the 42 unit clauses on the live report. """
	for name in ("Battering Ram", "Capped Ram", "Siege Ram", "battering ram"):
		assert not game_stats.is_style_unit(_unit(name, "siege", 9)), name


def test_arambai_survives_the_ram_exclusion():
	""" MUTANT GUARD, and the reason UBIQUITOUS_UNITS is matched on word
	boundaries rather than as a bare substring: "Arambai" contains r-a-m. A
	substring test deletes a real unique unit -- one that currently holds a
	wins-most clause on live data -- and looks entirely correct doing it. """
	assert game_stats.is_style_unit(_unit("Arambai", "siege", 40))


def test_no_other_unit_name_is_caught_by_the_word_boundary_rule():
	""" Every military unit name in production that contains the letters of an
	excluded token somewhere inside a word. """
	for name in ("Arambai", "Rocket Cart", "Shrivamsha Rider", "Karambit Warrior",
	             "Ramped Wagon", "Camel Archer"):
		assert game_stats.is_style_unit(_unit(name, "unique_other", 20)), name


def test_the_compute_version_is_ahead_of_what_production_holds():
	""" The version is the ONLY thing that makes bot/derived/backfill.py rewrite
	rows whose values changed but whose row set did not -- so a change to what
	top_units contains that forgets to bump this converges to zero work with
	every stored row still holding the old answer. """
	assert game_stats.COMPUTE_VERSION >= 2, (
		"excluding rams changed top_units for every match; COMPUTE_VERSION must move "
		"or the reconciliation loop will never notice")
