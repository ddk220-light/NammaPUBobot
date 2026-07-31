"""Unit tests for the derived-community civ_stats pure aggregation + writer
(bot/derived/civ_stats.py).

Every number the stage-5 civ-stats page prints comes out of
compute_civ_stats, so each test below is written to fail against a specific
plausible-but-wrong implementation, not merely to exercise the happy path:

  * wins and losses swapped
  * community scoping dropped, so two communities' channels aggregate
    together into one bucket
  * an unresolved (NULL) result counted as a game anyway, breaking the
    invariant games == wins + losses
  * a pick from a never-enrolled channel counted into some community anyway

No pytest-asyncio in this repo -- an `async def test_...` is collected and
SKIPPED, reporting green while asserting nothing -- so write() is driven from
sync tests with asyncio.run().
"""

import asyncio
import json

import pytest

import bot.derived.civ_stats as civ_stats
from bot.derived.civ_stats import compute_civ_stats


# ── fixtures ─────────────────────────────────────────────────────────────
# civ_picks-shaped rows (only channel_id/civ/result are read; the rest of the
# real table's columns -- id, replay_match_id, aoe2_name, at, bot_match_id,
# user_id, nick, team -- are deliberately absent from most fixtures, the same
# discipline test_rollups.py uses to prove the compute never reaches for a
# column it wasn't handed).

def _pick(channel_id, civ, result):
	return dict(channel_id=channel_id, civ=civ, result=result)


def _channel(channel_id, community_id):
	return dict(channel_id=channel_id, community_id=community_id, added_at=1000)


# ── the join: channel -> community ────────────────────────────────────────

def test_picks_scope_to_their_channels_community():
	picks = [_pick(100, "Franks", "W")]
	out = compute_civ_stats(picks, [_channel(100, community_id=7)])
	assert set(out) == {7}
	assert out[7]["Franks"] == dict(games=1, wins=1, losses=0)


def test_two_channels_in_different_communities_never_mix():
	# The mutant this guards: scoping dropped so all channels aggregate into
	# one bucket. Same civ, same channel-less numbers would be indistinguishable
	# from correct if community boundaries were ignored -- so the two channels
	# below are given DIFFERENT win/loss shapes that only stay distinguishable
	# if the communities are kept apart.
	picks = ([_pick(100, "Franks", "W")] * 3 + [_pick(100, "Franks", "L")]
	         + [_pick(200, "Franks", "L")] * 5)
	channels = [_channel(100, community_id=1), _channel(200, community_id=2)]
	out = compute_civ_stats(picks, channels)
	assert out[1]["Franks"] == dict(games=4, wins=3, losses=1)
	assert out[2]["Franks"] == dict(games=5, wins=0, losses=5)


def test_two_channels_in_the_same_community_do_aggregate():
	# Scoping is by COMMUNITY, not by channel -- a community can enroll more
	# than one channel, and both must land in the one row for that civ.
	picks = [_pick(100, "Franks", "W"), _pick(200, "Franks", "L")]
	channels = [_channel(100, community_id=1), _channel(200, community_id=1)]
	out = compute_civ_stats(picks, channels)
	assert out[1]["Franks"] == dict(games=2, wins=1, losses=1)


def test_a_pick_from_a_never_enrolled_channel_is_dropped_entirely():
	picks = [_pick(999, "Franks", "W")]
	out = compute_civ_stats(picks, [_channel(100, community_id=1)])
	assert out == {}


def test_join_ignores_extra_columns_on_both_sides():
	# The real civ_picks/community_channels rows carry many more columns than
	# compute_civ_stats reads; a compute that accidentally depended on one of
	# them should be caught by a fixture that never had it, not hidden by one
	# that always includes every column.
	pick = dict(id=1, channel_id=100, replay_match_id=555, aoe2_name="ddk",
	            civ="Franks", at=1000, bot_match_id=9, user_id=42, nick="ddk",
	            team="1", result="W")
	channel = dict(channel_id=100, community_id=1, added_at=1000)
	out = compute_civ_stats([pick], [channel])
	assert out[1]["Franks"] == dict(games=1, wins=1, losses=0)


# ── wins / losses / games ────────────────────────────────────────────────

def test_wins_and_losses_are_counted_from_result_and_never_swapped():
	# Deliberately asymmetric counts (3 wins, 1 loss) so a swap mutant is
	# unmistakable in either direction.
	picks = [_pick(100, "Franks", "W")] * 3 + [_pick(100, "Franks", "L")]
	out = compute_civ_stats(picks, [_channel(100, community_id=1)])
	assert out[1]["Franks"] == dict(games=4, wins=3, losses=1)


def test_games_always_equals_wins_plus_losses():
	picks = ([_pick(100, "Franks", "W")] * 5 + [_pick(100, "Franks", "L")] * 2
	         + [_pick(100, "Franks", None)] * 4)
	out = compute_civ_stats(picks, [_channel(100, community_id=1)])
	tally = out[1]["Franks"]
	assert tally["games"] == tally["wins"] + tally["losses"]
	assert tally == dict(games=7, wins=5, losses=2)


def test_an_unresolved_result_is_dropped_from_every_count():
	picks = [_pick(100, "Franks", "W"), _pick(100, "Franks", None)]
	out = compute_civ_stats(picks, [_channel(100, community_id=1)])
	assert out[1]["Franks"] == dict(games=1, wins=1, losses=0)


def test_a_result_that_is_neither_w_nor_l_is_dropped_too():
	# Defensive: any stray value that is not exactly 'W' or 'L' must not
	# silently become a win, a loss, or an extra game.
	picks = [_pick(100, "Franks", "W"), _pick(100, "Franks", "D")]
	out = compute_civ_stats(picks, [_channel(100, community_id=1)])
	assert out[1]["Franks"] == dict(games=1, wins=1, losses=0)


# ── multiple civs ─────────────────────────────────────────────────────────

def test_different_civs_get_their_own_buckets_within_one_community():
	picks = [_pick(100, "Franks", "W"), _pick(100, "Mongols", "L"), _pick(100, "Mongols", "L")]
	out = compute_civ_stats(picks, [_channel(100, community_id=1)])
	assert out[1] == {
		"Franks": dict(games=1, wins=1, losses=0),
		"Mongols": dict(games=2, wins=0, losses=2),
	}


def test_no_picks_produces_no_communities():
	assert compute_civ_stats([], [_channel(100, community_id=1)]) == {}


# ── write() ──────────────────────────────────────────────────────────────

class _RecordingDB:
	def __init__(self):
		self.calls = []

	async def execute(self, sql, args=None):
		self.calls.append(("execute", sql, list(args) if args else []))

	async def insert_many(self, table, rows, on_duplicate=None):
		self.calls.append(("insert_many", table, list(rows), on_duplicate))


def _written(community_id=1, civ_counts=None, computed_at=1700):
	recorder = _RecordingDB()
	original_db = civ_stats.db
	civ_stats.db = recorder
	try:
		asyncio.run(civ_stats.write(community_id,
		                            {"Franks": dict(games=4, wins=3, losses=1)} if civ_counts is None else civ_counts,
		                            computed_at))
	finally:
		civ_stats.db = original_db
	return recorder


def test_write_deletes_before_insert_and_stamps_community_id():
	recorder = _written(community_id=9, civ_counts={"Franks": dict(games=4, wins=3, losses=1),
	                                                 "Mongols": dict(games=2, wins=0, losses=2)},
	                     computed_at=1700)
	assert [c[0] for c in recorder.calls] == ["execute", "insert_many"]
	_, delete_sql, delete_args = recorder.calls[0]
	assert "DELETE" in delete_sql.upper()
	assert "civ_stats" in delete_sql
	assert delete_args == [9]

	_, table, payload, on_duplicate = recorder.calls[1]
	assert table == "civ_stats"
	assert on_duplicate == "replace"
	assert len(payload) == 2
	assert all(r["community_id"] == 9 for r in payload)
	assert all(r["computed_at"] == 1700 for r in payload)


def test_write_with_no_civs_still_deletes_but_never_inserts():
	recorder = _written(civ_counts={})
	assert [c[0] for c in recorder.calls] == ["execute"]


def test_write_emits_exactly_the_declared_columns_in_one_order():
	payload = _written().calls[1][2]
	assert list(payload[0].keys()) == list(civ_stats._COLUMNS)


def test_write_rows_carry_the_right_civ_and_counts():
	recorder = _written(civ_counts={"Franks": dict(games=4, wins=3, losses=1),
	                                 "Mongols": dict(games=2, wins=0, losses=2)})
	payload = recorder.calls[1][2]
	by_civ = {r["civ"]: r for r in payload}
	assert by_civ["Franks"]["games"] == 4
	assert by_civ["Franks"]["wins"] == 3
	assert by_civ["Franks"]["losses"] == 1
	assert by_civ["Mongols"] == dict(community_id=1, civ="Mongols", games=2, wins=0, losses=2, computed_at=1700)


def test_write_accepts_exactly_what_compute_civ_stats_returns():
	picks = [_pick(100, "Franks", "W")] * 3 + [_pick(100, "Franks", "L")]
	out = compute_civ_stats(picks, [_channel(100, community_id=1)])
	recorder = _written(community_id=1, civ_counts=out[1], computed_at=1700)
	payload = recorder.calls[1][2]
	assert payload == [dict(community_id=1, civ="Franks", games=4, wins=3, losses=1, computed_at=1700)]


def test_write_rejects_a_malformed_civ_counts_row():
	with pytest.raises(ValueError, match="expected exactly"):
		_written(civ_counts={"Franks": dict(games=4, wins=3)})   # losses missing
