"""Unit tests for the pure analysis helpers in bot.team_insights.

These cover the record-counting, candidate-scoring and selection logic that
drives the post-team-formation "storyline" embed. The DB read and embed
rendering are deliberately kept out of these helpers so they can be exercised
without a database or nextcord (see conftest's fakes).
"""
from __future__ import annotations

import bot.team_insights as ti


def _rows(*matches):
	"""Build raw qc_player_matches-style rows.

	Each arg is ``(match_id, winner, {user_id: team})``. ``winner`` is the
	winning team index (0/1) or None for a draw. A team value of None models a
	never-picked player.
	"""
	rows = []
	for mid, winner, teams in matches:
		for uid, team in teams.items():
			rows.append({"match_id": mid, "user_id": uid, "team": team, "winner": winner})
	return rows


# ── _index_history ───────────────────────────────────────────────────────
def test_index_history_groups_and_skips_null_team():
	rows = _rows(
		(1, 0, {10: 0, 20: 1}),
		(2, 1, {10: 0, 30: None}),  # uid 30 was never picked -> dropped
	)
	by_match = ti._index_history(rows)
	assert by_match[1] == {"winner": 0, "players": {10: 0, 20: 1}}
	assert by_match[2] == {"winner": 1, "players": {10: 0}}  # 30 skipped


# ── _synergy_record ──────────────────────────────────────────────────────
def test_synergy_record_only_counts_same_team_all_present():
	by_match = ti._index_history(_rows(
		(1, 0, {1: 0, 2: 0}),     # together, team 0 won  -> win
		(2, 1, {1: 0, 2: 0}),     # together, team 1 won  -> loss
		(3, None, {1: 0, 2: 0}),  # together, draw        -> draw
		(4, 0, {1: 0, 2: 1}),     # opposite teams        -> ignored
		(5, 0, {1: 0}),           # partner absent        -> ignored
	))
	assert ti._synergy_record(by_match, [1, 2]) == (1, 1, 1)


def test_synergy_record_trio_requires_all_three():
	by_match = ti._index_history(_rows(
		(1, 0, {1: 0, 2: 0, 3: 0}),  # all three, won
		(2, 0, {1: 0, 2: 0}),        # only two present -> ignored for trio
	))
	assert ti._synergy_record(by_match, [1, 2, 3]) == (1, 0, 0)


# ── _rivalry_record ──────────────────────────────────────────────────────
def test_rivalry_record_counts_opposite_team_results():
	by_match = ti._index_history(_rows(
		(1, 0, {1: 0, 2: 1}),     # a(team0) wins
		(2, 1, {1: 0, 2: 1}),     # b(team1) wins
		(3, 1, {1: 1, 2: 0}),     # a(team1) wins
		(4, None, {1: 0, 2: 1}),  # draw
		(5, 0, {1: 0, 2: 0}),     # same team -> ignored
	))
	assert ti._rivalry_record(by_match, 1, 2) == (2, 1, 1)


# ── _synergy_candidates ──────────────────────────────────────────────────
def test_synergy_quad_outranks_smaller_groups():
	# Same four win three games together; bigger stack should score highest.
	by_match = ti._index_history(_rows(
		(1, 0, {1: 0, 2: 0, 3: 0, 4: 0}),
		(2, 0, {1: 0, 2: 0, 3: 0, 4: 0}),
		(3, 0, {1: 0, 2: 0, 3: 0, 4: 0}),
	))
	cands = ti._synergy_candidates(by_match, [1, 2, 3, 4], team_idx=0)
	top = max(cands, key=lambda c: c["score"])
	assert top["size"] == 4
	assert top["good"] is True
	assert top["team_idx"] == 0
	# Pairs have only 3 games together but need 4 -> none emitted.
	assert all(c["size"] != 2 for c in cands)


def test_synergy_marks_cursed_combo_and_respects_min_games():
	by_match = ti._index_history(_rows(
		(1, 1, {1: 0, 2: 0}),  # loss
		(2, 1, {1: 0, 2: 0}),  # loss
		(3, 1, {1: 0, 2: 0}),  # loss
		(4, 0, {1: 0, 2: 0}),  # win
	))
	cands = ti._synergy_candidates(by_match, [1, 2], team_idx=1)
	pair = next(c for c in cands if c["size"] == 2)
	assert pair["good"] is False        # 1-3 record => cursed
	assert (pair["wins"], pair["losses"]) == (1, 3)

	# One game short of the pair minimum -> nothing emitted.
	thin = ti._index_history(_rows(
		(1, 0, {1: 0, 2: 0}),
		(2, 0, {1: 0, 2: 0}),
		(3, 0, {1: 0, 2: 0}),
	))
	assert ti._synergy_candidates(thin, [1, 2], team_idx=0) == []


# ── _rivalry_candidates ──────────────────────────────────────────────────
def test_rivalry_dominant_vs_even_classification():
	# 1 beats 2 four times, loses once -> dominant nemesis, leader is 1.
	dom = ti._index_history(_rows(
		(1, 0, {1: 0, 2: 1}),
		(2, 0, {1: 0, 2: 1}),
		(3, 0, {1: 0, 2: 1}),
		(4, 0, {1: 0, 2: 1}),
		(5, 1, {1: 0, 2: 1}),
	))
	cand = ti._rivalry_candidates(dom, [1], [2])[0]
	assert cand["kind"] == "dominant"
	assert cand["leader"] == 1 and cand["trail"] == 2
	assert (cand["leader_wins"], cand["trail_wins"]) == (4, 1)

	# 3-3 over six games -> an even, classic rivalry.
	even = ti._index_history(_rows(
		(1, 0, {1: 0, 2: 1}), (2, 0, {1: 0, 2: 1}), (3, 0, {1: 0, 2: 1}),
		(4, 1, {1: 0, 2: 1}), (5, 1, {1: 0, 2: 1}), (6, 1, {1: 0, 2: 1}),
	))
	cand = ti._rivalry_candidates(even, [1], [2])[0]
	assert cand["kind"] == "even"
	assert (cand["leader_wins"], cand["trail_wins"]) == (3, 3)


def test_rivalry_below_min_games_is_ignored():
	by_match = ti._index_history(_rows(
		(1, 0, {1: 0, 2: 1}),
		(2, 0, {1: 0, 2: 1}),
		(3, 0, {1: 0, 2: 1}),  # only 3 decisive, need 4
	))
	assert ti._rivalry_candidates(by_match, [1], [2]) == []


# ── _select ──────────────────────────────────────────────────────────────
def _syn(players, score):
	return {"type": "synergy", "players": frozenset(players), "score": score}


def _riv(score):
	return {"type": "rivalry", "score": score}


def test_select_suppresses_nested_synergy():
	quad = _syn({1, 2, 3, 4}, 5.0)
	pair = _syn({1, 2}, 4.0)   # subset of quad -> redundant
	other = _syn({5, 6}, 3.0)
	chosen = ti._select([quad, pair, other], [], limit=4)
	assert quad in chosen and other in chosen
	assert pair not in chosen


def test_select_guarantees_a_rivalry_when_synergy_fills_up():
	syns = [_syn({i, i + 100}, 5.0 - i) for i in range(4)]  # 4 disjoint, scores 5..2
	riv = _riv(0.5)
	chosen = ti._select(syns, [riv], limit=4)
	assert riv in chosen
	assert len(chosen) == 4
	# The weakest synergy (score 2.0) was bumped to make room.
	assert syns[3] not in chosen
