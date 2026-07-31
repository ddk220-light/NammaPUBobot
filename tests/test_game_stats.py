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
			     top_units=[dict(unit="Knight", category="cavalry", total=30)],
			     computed_at=1000),
			dict(player_number=2, profile_id=22, civ="Mongols", team="2", winner=False,
			     avg_eapm=40, peak_eapm=None, military_medal=None, villager_medal=1,
			     top_units=[], computed_at=1000),
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
# core/DBAdapters/mysql.py's insert_many takes its column list from the FIRST
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
	            top_units=[], computed_at=1000)
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


def test_a_row_with_an_unexpected_key_set_is_rejected_loudly():
	# Dropping or defaulting the difference is how a column silently stops being
	# written; write()'s callers are all best-effort-guarded, so this logs.
	import pytest

	with pytest.raises(ValueError, match="expected exactly"):
		_written_payload([_stats_row(surprise="unmapped")])

	with pytest.raises(ValueError, match="expected exactly"):
		_written_payload([{k: v for k, v in _stats_row().items() if k != "civ"}])
