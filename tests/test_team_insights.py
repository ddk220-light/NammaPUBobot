"""Unit tests for the pure analysis helpers in bot.team_insights.

The recency-weighted candidate builders, drama scoring, and the anti-saturation
selection all run on hand-built history dicts — no DB, no nextcord (see conftest).
"""
from __future__ import annotations

import random

import bot.team_insights as ti


def _rows(*matches):
	"""Build raw qc_player_matches-style rows. Each arg is
	``(match_id, winner, {user_id: team})``; winner is 0/1 or None (draw); a team
	of None models a never-picked player."""
	rows = []
	for mid, winner, teams in matches:
		for uid, team in teams.items():
			rows.append({"match_id": mid, "user_id": uid, "nick": f"u{uid}", "team": team, "winner": winner})
	return rows


def _hist(*matches):
	return ti._index_history(_rows(*matches))


def _cand(typ, score, players, teams=(0, 1)):
	return {"type": typ, "score": score, "players": frozenset(players), "teams": frozenset(teams), "data": {}}


# ── ordered history ──────────────────────────────────────────────────────
def test_index_history_orders_and_skips_null_team():
	h = ti._index_history(_rows(
		(3, 0, {10: 0, 20: 1}),
		(1, 1, {10: 0, 30: None}),  # out of order; uid 30 never picked -> dropped
		(2, None, {10: 0, 20: 1}),
	))
	assert h.order == [1, 2, 3]
	assert h.matches[1] == {"winner": 1, "teams": {10: 0}}
	assert h.nicks[10] == "u10"


# ── primitives ───────────────────────────────────────────────────────────
def test_trailing_streak():
	assert ti._trailing_streak([10, 10, 20, 10, 10, 10]) == (3, 10)
	assert ti._trailing_streak([]) == (0, None)
	assert ti._trailing_streak([True, True, None]) == (0, None)  # trailing draw breaks
	assert ti._trailing_streak([True, True]) == (2, True)


def test_series_respect_prior_cutoff():
	h = _hist(
		(1, 0, {1: 0, 2: 1}),   # 1 beats 2 (opponents)
		(2, 1, {1: 0, 2: 1}),   # 2 beats 1
	)
	assert ti._h2h_series([1], h.matches, 1, 2) == [1]        # prior excludes mid 2
	assert ti._h2h_series([1, 2], h.matches, 1, 2) == [1, 2]


# ── candidate builders + gates ───────────────────────────────────────────
def test_h2h_candidate_fires_k4_not_k3():
	h = _hist(*[(i, 0, {1: 0, 2: 1}) for i in range(1, 5)])   # 1 beats 2 four times
	cands = ti._h2h_candidates(h.order, h.matches, [1], [2])
	assert len(cands) == 1 and cands[0]["data"]["winner"] == 1 and cands[0]["data"]["k"] == 4
	h3 = _hist(*[(i, 0, {1: 0, 2: 1}) for i in range(1, 4)])
	assert ti._h2h_candidates(h3.order, h3.matches, [1], [2]) == []


def test_h2h_sweep_bonus_outscores_equal_k():
	sweep = _hist(*[(i, 0, {1: 0, 2: 1}) for i in range(1, 5)])         # 4-0, never lost
	non = _hist((1, 1, {1: 0, 2: 1}), *[(i, 0, {1: 0, 2: 1}) for i in range(2, 6)])  # L then 4 wins
	cs = ti._h2h_candidates(sweep.order, sweep.matches, [1], [2])[0]
	cn = ti._h2h_candidates(non.order, non.matches, [1], [2])[0]
	assert cs["data"]["sweep"] is True and cn["data"]["sweep"] is False
	assert cs["data"]["k"] == cn["data"]["k"] == 4
	assert cs["score"] > cn["score"]


def test_mate_candidate_loss_run_and_min_together():
	h = _hist(
		(1, 0, {1: 0, 2: 0}), (2, 0, {1: 0, 2: 0}),                       # 2 wins
		*[(i, 1, {1: 0, 2: 0}) for i in range(3, 7)],                     # 4 losses
	)
	c = ti._mate_candidates(h.order, h.matches, [1, 2], [])[0]
	assert c["data"]["won"] is False and c["data"]["k"] == 4 and c["data"]["series"] == 6
	h5 = _hist(*[(i, 1, {1: 0, 2: 0}) for i in range(1, 6)])             # only 5 together
	assert ti._mate_candidates(h5.order, h5.matches, [1, 2], []) == []


def test_mate_wr_worst_present_flags_zero():
	h = _hist(
		*[(i, 1, {1: 0, 2: 0}) for i in range(1, 7)],     # 1&2 teamed, lost x6
		*[(i, 0, {1: 0, 3: 0}) for i in range(7, 12)],    # 1&3 teamed, won x5
	)
	cands = ti._mate_wr_candidates(h.order, h.matches, [1, 2], [])
	worst = [c for c in cands if c["data"]["kind"] == "worst" and c["data"]["p"] == 1]
	assert worst and worst[0]["data"]["q"] == 2 and worst[0]["data"]["wr"] == 0.0


def test_perfect_pair_clean_and_cursed():
	clean = _hist(*[(i, 0, {1: 0, 2: 0}) for i in range(1, 6)])    # 5-0
	cursed = _hist(*[(i, 1, {1: 0, 2: 0}) for i in range(1, 6)])   # 0-5
	cc = ti._perfect_candidates(clean.order, clean.matches, [1, 2], [])[0]
	xc = ti._perfect_candidates(cursed.order, cursed.matches, [1, 2], [])[0]
	assert cc["data"]["won"] is True and cc["data"]["n"] == 5
	assert xc["data"]["won"] is False and xc["score"] > cc["score"]   # cursed bias
	four = _hist(*[(i, 0, {1: 0, 2: 0}) for i in range(1, 5)])
	assert ti._perfect_candidates(four.order, four.matches, [1, 2], []) == []


def test_deadlock_even_recent_window():
	h = _hist(
		*[(i, 0, {1: 0, 2: 1}) for i in range(1, 4)],     # 1 wins x3
		*[(i, 1, {1: 0, 2: 1}) for i in range(4, 7)],     # 2 wins x3
	)
	c = ti._deadlock_candidates(h.order, h.matches, [1], [2])[0]
	assert c["data"]["each"] == 3 and c["data"]["n"] == 6
	h42 = _hist(*[(i, 0, {1: 0, 2: 1}) for i in range(1, 5)], *[(i, 1, {1: 0, 2: 1}) for i in range(5, 7)])
	assert ti._deadlock_candidates(h42.order, h42.matches, [1], [2]) == []   # 4-2


def test_form_fires_k5_not_k4():
	h = _hist(*[(i, 1, {1: 0, 9: 1}) for i in range(1, 6)])   # player 1 loses last 5
	c = ti._form_candidates(h.order, h.matches, [1], {1: 0})[0]
	assert c["data"]["k"] == 5 and c["data"]["won"] is False
	h4 = _hist(*[(i, 1, {1: 0, 9: 1}) for i in range(1, 5)])
	assert ti._form_candidates(h4.order, h4.matches, [1], {1: 0}) == []


# ── selection (the saturation fix) ───────────────────────────────────────
def test_select_caps_per_player():
	# four high-score lines all featuring player 1 -> at most PER_PLAYER_CAP chosen
	cands = [
		_cand("h2h", 10, (1, 2)), _cand("mate", 9, (1, 3), teams=(0,)),
		_cand("deadlock", 8, (1, 4)), _cand("form", 7, (1,), teams=(0,)),
	]
	chosen = ti._select(cands, rng=random.Random(0))
	assert sum(1 for c in chosen if 1 in c["players"]) <= ti.PER_PLAYER_CAP


def test_select_caps_per_type_when_variety_available():
	cands = [
		_cand("h2h", 10, (1, 2)), _cand("h2h", 9, (3, 4)),
		_cand("h2h", 8, (5, 6)), _cand("h2h", 7.5, (7, 8)),
		_cand("mate", 7, (11, 12), teams=(0,)), _cand("mate", 6, (13, 14), teams=(1,)),
	]
	chosen = ti._select(cands, rng=random.Random(0))
	assert len(chosen) == 4
	assert sum(1 for c in chosen if c["type"] == "h2h") == ti.PER_TYPE_CAP
	assert sum(1 for c in chosen if c["type"] == "mate") == 2


def test_select_deadlock_capped_to_one_even_when_filling():
	cands = [_cand("deadlock", 10, (1, 2)), _cand("deadlock", 9, (3, 4)), _cand("deadlock", 8, (5, 6))]
	chosen = ti._select(cands, rng=random.Random(0))
	assert sum(1 for c in chosen if c["type"] == "deadlock") == 1


def test_select_dedups_same_pair_keeps_higher():
	cands = [
		_cand("perfect", 30, (1, 2), teams=(0,)),
		_cand("mate", 10, (1, 2), teams=(0,)),
		_cand("form", 5, (9,), teams=(1,)),
	]
	chosen = ti._select(cands, rng=random.Random(0))
	pair12 = [c for c in chosen if c["players"] == frozenset((1, 2))]
	assert len(pair12) == 1 and pair12[0]["type"] == "perfect"


def test_select_pulls_in_missing_team():
	cands = [
		_cand("mate", 10, (1, 2), teams=(0,)),
		_cand("form", 9, (3,), teams=(0,)),
		_cand("perfect", 8, (4, 5), teams=(1,)),
	]
	chosen = ti._select(cands, limit=2, rng=random.Random(0))
	assert set().union(*(c["teams"] for c in chosen)) == {0, 1}


def test_select_empty_returns_empty():
	assert ti._select([], rng=random.Random(0)) == []


def test_select_deterministic_with_seeded_rng():
	cands = [_cand("h2h", 5, (1, 2)), _cand("h2h", 5, (3, 4)), _cand("mate", 5, (5, 6), teams=(0,))]
	a = ti._select(cands, rng=random.Random(42))
	b = ti._select(cands, rng=random.Random(42))
	assert [sorted(c["players"]) for c in a] == [sorted(c["players"]) for c in b]


# ── phrasing ─────────────────────────────────────────────────────────────
def test_phrase_all_types_render_without_keyerror():
	nick = {1: "Alice", 2: "Bob"}
	meta = [{"name": "Alpha", "emoji": ""}, {"name": "Beta", "emoji": ""}]
	rng = random.Random(0)
	samples = [
		{"type": "perfect", "data": {"ids": [1, 2], "n": 5, "won": False, "team_idx": 0}},
		{"type": "mate_wr", "data": {"p": 1, "q": 2, "wr": 0.0, "base": 0.5, "games": 8, "kind": "worst"}},
		{"type": "h2h", "data": {"winner": 1, "loser": 2, "k": 4, "series": 6, "sweep": False}},
		{"type": "mate", "data": {"ids": [1, 2], "k": 4, "series": 12, "won": True}},
		{"type": "deadlock", "data": {"ids": [1, 2], "each": 3, "n": 6}},
		{"type": "form", "data": {"p": 1, "k": 5, "won": False}},
	]
	for c in samples:
		line = ti._phrase(c, nick, meta, rng=rng)
		assert isinstance(line, str) and "Alice" in line
