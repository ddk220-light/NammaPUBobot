"""Unit tests for the derived-community metric_boards pure aggregation + writer
(bot/derived/boards.py).

Every number a stage-5 leaderboard prints comes out of compute_board, so each
test below is written to fail against a specific plausible-but-wrong
implementation, not merely to exercise the happy path:

  * `direction` inverted, so a low-is-better metric (e.g. first_tc_s) ranks
    its WORST performer first
  * the BOARD_MIN_GAMES gate removed, so a 1-game "leader" reaches the board
  * leaderboard ordering left to dict/list insertion order on a tie, so
    identical data rewrites a different blob every refresh
  * the catalog reaching into replay_techs/replay_buildings fields the
    retention sweeper deletes for lean communities

No pytest-asyncio in this repo -- an `async def test_...` is collected and
SKIPPED, reporting green while asserting nothing -- so write() is driven from
sync tests with asyncio.run().
"""

import asyncio
import json

import pytest

import bot.derived.boards as boards
import bot.derived.rollups as rollups
from bot.derived.boards import METRICS, compute_board


# ── fixtures ─────────────────────────────────────────────────────────────
# Shaped like the rows a future caller hands over: already resolved to a
# community and a user (see the module docstring for why that resolution is
# deliberately not this module's job), one row per game the metric has a
# value for.

def _row(uid, value, mid, nick="p"):
	return dict(user_id=uid, nick=nick, value=value, replay_match_id=mid)


# ── the catalog ──────────────────────────────────────────────────────────

def test_the_catalog_only_contains_permitted_fields():
	# Tech-timing and building-count metrics live in replay_techs and
	# replay_buildings, which the sweeper deletes for lean communities -- a
	# board built on one of those would silently empty out. This is the hard
	# boundary the brief names explicitly; guard it so nobody "restores" a
	# dropped field later without noticing.
	banned_substrings = ("tech", "building", "barracks", "archery", "stable",
	                     "blacksmith", "university", "castle_built")
	for metric_id, meta in METRICS.items():
		haystack = f"{metric_id} {meta.get('field', '')}".lower()
		for bad in banned_substrings:
			assert bad not in haystack, (metric_id, bad)


def test_every_catalog_entry_has_the_required_shape():
	for metric_id, meta in METRICS.items():
		assert set(meta) >= {"label", "unit", "direction", "source", "field"}, metric_id
		assert meta["direction"] in ("high", "low"), metric_id
		assert isinstance(meta["label"], str) and meta["label"], metric_id
		assert meta["source"] in ("replay_players", "game_stats"), metric_id


def test_first_tc_s_is_low_and_eapm_is_high():
	# The two examples the brief pins explicitly.
	assert METRICS["first_tc_s"]["direction"] == "low"
	assert METRICS["eapm"]["direction"] == "high"


def test_board_min_games_is_imported_not_redefined():
	# The brief requires importing BOARD_MIN_GAMES from rollups rather than
	# redefining it, so the two derived-community modules cannot drift apart.
	assert boards.BOARD_MIN_GAMES is rollups.BOARD_MIN_GAMES
	assert boards.BOARD_MIN_GAMES == 3


def test_an_unknown_metric_id_is_rejected_loudly():
	with pytest.raises(ValueError):
		compute_board("first_tc_time_but_misspelled", [], min_games=3)


# ── the contract shape ───────────────────────────────────────────────────

def test_board_has_exactly_the_contract_keys():
	board = compute_board("villagers", [_row(1, 100, 1)], min_games=1)
	assert set(board) == {"label", "unit", "direction", "leaders", "top_games"}
	assert board["label"] == METRICS["villagers"]["label"]
	assert board["unit"] == METRICS["villagers"]["unit"]
	assert board["direction"] == METRICS["villagers"]["direction"]


def test_no_rows_produces_the_shape_with_empty_lists():
	board = compute_board("villagers", [], min_games=3)
	assert board["leaders"] == []
	assert board["top_games"] == []


# ── the BOARD_MIN_GAMES gate ─────────────────────────────────────────────

def test_a_user_below_min_games_does_not_reach_leaders():
	rows = [_row(1, 100, 1), _row(1, 120, 2)]        # 2 games, floor is 3
	board = compute_board("villagers", rows, min_games=3)
	assert board["leaders"] == []


def test_a_user_at_exactly_min_games_reaches_leaders():
	rows = [_row(1, 100, 1), _row(1, 120, 2), _row(1, 140, 3)]
	board = compute_board("villagers", rows, min_games=3)
	assert [entry["user_id"] for entry in board["leaders"]] == [1]
	assert board["leaders"][0]["n"] == 3


def test_the_gate_is_the_argument_not_a_hardcoded_constant():
	rows = [_row(1, 100, 1), _row(1, 120, 2)]
	assert compute_board("villagers", rows, min_games=2)["leaders"] != []
	assert compute_board("villagers", rows, min_games=3)["leaders"] == []


def test_the_gate_does_not_apply_to_top_games():
	# A single stellar game should surface even from a low-sample player --
	# that is the entire point of top_games as distinct from leaders.
	rows = [_row(1, 999, 1)]
	board = compute_board("villagers", rows, min_games=3)
	assert board["leaders"] == []
	assert [g["value"] for g in board["top_games"]] == [999]


# ── leaders: averaging and direction ─────────────────────────────────────

def test_leaders_avg_is_the_mean_of_the_users_values():
	rows = [_row(1, 100, 1), _row(1, 120, 2), _row(1, 140, 3)]
	board = compute_board("villagers", rows, min_games=3)
	assert board["leaders"][0]["avg"] == 120.0


def test_a_high_direction_metric_ranks_the_biggest_average_first():
	rows = ([_row(1, v, i) for i, v in enumerate([100, 100, 100], start=1)]
	        + [_row(2, v, i) for i, v in enumerate([200, 200, 200], start=4)])
	board = compute_board("villagers", rows, min_games=3)      # villagers: high
	assert [entry["user_id"] for entry in board["leaders"]] == [2, 1]


def test_a_low_direction_metric_ranks_the_smallest_average_first():
	# first_tc_s: low is good. User 1 averages faster (smaller) than user 2,
	# so user 1 must lead -- a direction mutant (descending regardless of the
	# catalog) would report user 2 first here.
	rows = ([_row(1, v, i) for i, v in enumerate([20, 20, 20], start=1)]
	        + [_row(2, v, i) for i, v in enumerate([40, 40, 40], start=4)])
	board = compute_board("first_tc_s", rows, min_games=3)
	assert [entry["user_id"] for entry in board["leaders"]] == [1, 2]


def test_leaders_include_nick_and_n():
	rows = [_row(1, 100, 1, nick="ddk"), _row(1, 120, 2, nick="ddk"), _row(1, 140, 3, nick="ddk")]
	board = compute_board("villagers", rows, min_games=3)
	assert board["leaders"][0] == dict(user_id=1, nick="ddk", avg=120.0, n=3)


# ── leaders: deterministic tie-break ─────────────────────────────────────

def test_leaders_tied_on_avg_break_the_tie_on_user_id_ascending():
	rows = ([_row(9, 100, 1), _row(9, 100, 2), _row(9, 100, 3)]
	        + [_row(2, 100, 4), _row(2, 100, 5), _row(2, 100, 6)])
	board = compute_board("villagers", rows, min_games=3)
	assert [entry["user_id"] for entry in board["leaders"]] == [2, 9]


def test_leaders_ordering_is_independent_of_input_row_order():
	rows = ([_row(1, v, i) for i, v in enumerate([100, 110, 120], start=1)]
	        + [_row(2, v, i) for i, v in enumerate([200, 210, 220], start=4)])
	forward = compute_board("villagers", rows, min_games=3)
	backward = compute_board("villagers", list(reversed(rows)), min_games=3)
	assert json.dumps(forward, sort_keys=True) == json.dumps(backward, sort_keys=True)


# ── top_games: ranking, cap, and tie-break ───────────────────────────────

def test_top_games_are_sorted_by_direction_high():
	rows = [_row(1, 50, 1), _row(1, 90, 2), _row(1, 70, 3)]
	board = compute_board("villagers", rows, min_games=1)
	assert [g["value"] for g in board["top_games"]] == [90, 70, 50]


def test_top_games_are_sorted_by_direction_low():
	rows = [_row(1, 50, 1), _row(1, 20, 2), _row(1, 70, 3)]
	board = compute_board("first_tc_s", rows, min_games=1)
	assert [g["value"] for g in board["top_games"]] == [20, 50, 70]


def test_top_games_are_capped():
	rows = [_row(1, v, v) for v in range(1, 11)]           # 10 games
	board = compute_board("villagers", rows, min_games=1)
	assert len(board["top_games"]) == boards.TOP_GAMES_LIMIT
	assert boards.TOP_GAMES_LIMIT < 10


def test_top_games_entries_have_exactly_the_contract_keys():
	rows = [_row(3, 999, 42, nick="ddk")]
	board = compute_board("villagers", rows, min_games=1)
	assert board["top_games"][0] == dict(user_id=3, nick="ddk", value=999, replay_match_id=42)


def test_top_games_tie_broken_by_user_id_then_match_id_ascending():
	rows = [_row(5, 100, 9), _row(2, 100, 3), _row(2, 100, 1)]
	board = compute_board("villagers", rows, min_games=1)
	assert [(g["user_id"], g["replay_match_id"]) for g in board["top_games"]] == [
		(2, 1), (2, 3), (5, 9)]


def test_top_games_ordering_is_independent_of_input_row_order():
	rows = [_row(1, 50, 1), _row(1, 90, 2), _row(1, 70, 3), _row(2, 60, 4)]
	forward = compute_board("villagers", rows, min_games=1)
	backward = compute_board("villagers", list(reversed(rows)), min_games=1)
	assert json.dumps(forward, sort_keys=True) == json.dumps(backward, sort_keys=True)


# ── nick denormalisation ──────────────────────────────────────────────────

def test_nick_is_the_most_recent_by_replay_match_id_not_input_order():
	# A player's nick can change between games; the board should show their
	# CURRENT nick, and that choice must not depend on which row the compute
	# happened to see last.
	rows = [_row(1, 100, 5, nick="OldName"), _row(1, 120, 9, nick="NewName"),
	        _row(1, 110, 7, nick="MidName")]
	board = compute_board("villagers", rows, min_games=1)
	assert board["leaders"][0]["nick"] == "NewName"

	reversed_board = compute_board("villagers", list(reversed(rows)), min_games=1)
	assert reversed_board["leaders"][0]["nick"] == "NewName"


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


def _blob(**overrides):
	blob = dict(label="x", unit="count", direction="high", leaders=[], top_games=[])
	blob.update(overrides)
	return blob


def _written(community_id=1, metric_id="villagers", board=None, computed_at=1700):
	recorder = _RecordingDB()
	original_db = boards.db
	boards.db = recorder
	try:
		asyncio.run(boards.write(community_id, metric_id,
		                          _blob() if board is None else board, computed_at))
	finally:
		boards.db = original_db
	return recorder


def test_write_upserts_one_row():
	board = _blob(leaders=[dict(user_id=1, nick="ddk", avg=120.0, n=3)])
	recorder = _written(community_id=1, metric_id="villagers", board=board, computed_at=1700)
	assert [c[0] for c in recorder.calls] == ["insert"]
	_, table, row, on_duplicate = recorder.calls[0]
	assert table == "metric_boards"
	assert on_duplicate == "replace"
	assert row["community_id"] == 1
	assert row["metric_id"] == "villagers"
	assert row["computed_at"] == 1700
	assert json.loads(row["board"]) == board


def test_write_emits_exactly_the_declared_columns_in_one_order():
	row = _written().calls[0][2]
	assert list(row.keys()) == list(boards._COLUMNS)


def test_write_serialises_the_board_with_sorted_keys_so_the_blob_is_stable():
	forward = _blob()
	backward = dict(reversed(list(_blob().items())))
	assert list(forward) != list(backward)
	assert _written(board=forward).calls[0][2]["board"] == _written(board=backward).calls[0][2]["board"]


def test_write_refuses_a_board_that_is_not_the_contract_shape():
	missing = _blob()
	missing.pop("top_games")
	with pytest.raises(ValueError, match="expected exactly"):
		_written(board=missing)
	with pytest.raises(ValueError, match="expected exactly"):
		_written(board=_blob(surprise=1))


def test_write_refuses_an_unknown_metric_id():
	with pytest.raises(ValueError):
		_written(metric_id="not_a_real_metric")


def test_write_accepts_what_compute_board_returns():
	board = compute_board("villagers", [_row(1, 100, 1), _row(1, 120, 2), _row(1, 140, 3)], min_games=3)
	row = _written(board=board).calls[0][2]
	assert json.loads(row["board"]) == board
