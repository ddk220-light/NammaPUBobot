"""Unit tests for the pure analysis helpers in nammaoe2bot.features.storylines.insights.

The recency-weighted candidate builders, drama scoring, and the anti-saturation
selection all run on hand-built history dicts — no DB, no nextcord (see conftest).
"""
from __future__ import annotations

import random

import pytest

import nammaoe2bot.features.storylines.insights as ti


def _rows(*matches):
	"""Build raw match_players-style rows. Each arg is
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
	"""Every type _candidates can emit must render. Samples are keyed by type and
	checked against ti.CANDIDATE_TYPES so a future type added to that tuple
	without a matching sample here fails loudly instead of quietly passing.

	Each sample also carries the ``players``/``teams`` a real candidate would
	have, since _frame (wired into _phrase) reads them directly off ``c``."""
	nick = {1: "Alice", 2: "Bob"}
	meta = [{"name": "Alpha", "emoji": ""}, {"name": "Beta", "emoji": ""}]
	rosters = {0: [1, 2]}
	rng = random.Random(0)
	samples = {
		"perfect": ({"ids": [1, 2], "n": 5, "won": False, "team_idx": 0}, (1, 2), (0,)),
		"mate_wr": ({"p": 1, "q": 2, "wr": 0.0, "base": 0.5, "games": 8, "kind": "worst"}, (1, 2), (0,)),
		"h2h": ({"winner": 1, "loser": 2, "k": 4, "series": 6, "sweep": False}, (1, 2), (0, 1)),
		"mate": ({"ids": [1, 2], "k": 4, "series": 12, "won": True}, (1, 2), (0,)),
		"deadlock": ({"ids": [1, 2], "each": 3, "n": 6}, (1, 2), (0, 1)),
		"form": ({"p": 1, "k": 5, "won": False}, (1,), (0,)),
		"trio": ({"ids": [1, 2], "wins": 4, "games": 5, "won": True, "team_idx": 0}, (1, 2), (0,)),
		"lineup": ({"ids": [1, 2], "wins": 2, "games": 2, "one_way": True, "won": True, "team_idx": 0}, (1, 2), (0,)),
	}
	assert set(samples) == set(ti.CANDIDATE_TYPES)
	for t in ti.CANDIDATE_TYPES:
		data, players, teams = samples[t]
		c = {"type": t, "data": data, "players": frozenset(players), "teams": frozenset(teams)}
		line = ti._phrase(c, nick, meta, rosters, rng=rng)
		assert isinstance(line, str) and "Alice" in line


# ── history window ───────────────────────────────────────────────────────
def test_window_start_is_ninety_days_back():
	assert ti.WINDOW_DAYS == 90
	assert ti.window_start(1_000_000_000) == 1_000_000_000 - 90 * 86400
	assert ti.window_start(1_000_000_000, days=7) == 1_000_000_000 - 7 * 86400


def test_fetch_history_binds_the_cutoff_into_the_query():
	"""The window is applied in SQL so the read stays cheap as history grows."""
	import asyncio

	seen = {}

	class _DB:
		async def fetchall(self, sql, params=None):
			seen["sql"] = sql
			seen["params"] = params
			return []

	real_db = ti.db
	ti.db = _DB()
	try:
		asyncio.run(ti._fetch_history(7, [1, 2, 3], 1234))
	finally:
		ti.db = real_db
	assert "m.reported_at >= %s" in seen["sql"]
	assert seen["params"] == [7, 1234, 1, 2, 3]


def test_fetch_history_needs_two_players():
	import asyncio
	assert asyncio.run(ti._fetch_history(7, [1], 1234)) == []


# ── group records (trio / lineup) ────────────────────────────────────────
def test_group_series_only_counts_matches_where_all_were_on_one_side():
	h = _hist(
		(1, 0, {1: 0, 2: 0, 3: 0, 4: 1}),   # all three together, won
		(2, 1, {1: 0, 2: 0, 3: 1, 4: 1}),   # 3 switched sides -> not counted
		(3, 1, {1: 0, 2: 0, 3: 0, 4: 1}),   # all three together, lost
		(4, 0, {1: 0, 2: 0, 4: 1}),         # 3 absent -> not counted
	)
	assert ti._group_series([1, 2, 3, 4], h.matches, frozenset((1, 2, 3))) == [True, False]


def test_group_series_drops_draws():
	h = _hist(
		(1, 0, {1: 0, 2: 0, 3: 1}),
		(2, None, {1: 0, 2: 0, 3: 1}),   # draw
	)
	assert ti._group_series([1, 2], h.matches, frozenset((1, 2))) == [True]


def test_group_series_respects_the_prior_cutoff():
	h = _hist(
		(1, 0, {1: 0, 2: 0, 3: 1}),
		(2, 1, {1: 0, 2: 0, 3: 1}),
	)
	assert ti._group_series([1], h.matches, frozenset((1, 2))) == [True]


# ── trio ─────────────────────────────────────────────────────────────────
def _trio_hist(results):
	"""History where players 1,2,3 share side 0 against 4, one match per result."""
	return _hist(*[(i + 1, 0 if won else 1, {1: 0, 2: 0, 3: 0, 4: 1})
	               for i, won in enumerate(results)])


def test_trio_fires_at_five_games_and_seventy_five_percent():
	h = _trio_hist([True, True, True, True, False])   # 4-1 = 80%
	cands = ti._trio_candidates(h.order, h.matches, [1, 2, 3], [4])
	assert len(cands) == 1
	assert cands[0]["type"] == "trio"
	assert cands[0]["data"]["wins"] == 4
	assert cands[0]["data"]["games"] == 5
	assert cands[0]["data"]["won"] is True
	assert cands[0]["players"] == frozenset((1, 2, 3))


def test_trio_fires_at_exactly_the_share_threshold():
	"""TRIO_MIN_GAMES is a floor on the sample, not a fixture size -- at 8 games
	6/8 == 0.75 exactly in binary, so the >= TRIO_MIN_SHARE boundary is directly
	testable instead of needing a wider window to land on an exact fraction."""
	h = _trio_hist([True, True, True, True, True, True, False, False])   # 6-2 = 75%
	cands = ti._trio_candidates(h.order, h.matches, [1, 2, 3], [4])
	assert len(cands) == 1
	assert cands[0]["data"]["wins"] == 6
	assert cands[0]["data"]["games"] == 8
	assert cands[0]["data"]["won"] is True


def test_trio_is_silent_below_the_share_bar():
	h = _trio_hist([True, True, True, False, False])   # 3-2 = 60%
	assert ti._trio_candidates(h.order, h.matches, [1, 2, 3], [4]) == []


def test_trio_is_silent_below_five_games():
	h = _trio_hist([True, True, True, True])           # 4-0 but only 4 games
	assert ti._trio_candidates(h.order, h.matches, [1, 2, 3], [4]) == []


def test_a_losing_trio_fires_and_is_marked_lost():
	h = _trio_hist([False, False, False, False, True])  # 1-4
	c = ti._trio_candidates(h.order, h.matches, [1, 2, 3], [4])[0]
	assert c["data"]["won"] is False
	assert c["data"]["wins"] == 1


def test_trio_enumeration_is_sorted_and_complete():
	assert list(ti._trios([30, 10, 20, 40])) == [
		(10, 20, 30), (10, 20, 40), (10, 30, 40), (20, 30, 40)]


# ── exact lineup ─────────────────────────────────────────────────────────
def test_lineup_fires_when_this_exact_side_played_together_before():
	h = _hist(
		(1, 0, {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 1}),
		(2, 1, {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 1}),
	)
	cands = ti._lineup_candidates(h.order, h.matches, [1, 2, 3], [4, 5, 6])
	assert len(cands) == 2                       # one per side
	side0 = next(c for c in cands if c["data"]["team_idx"] == 0)
	assert side0["data"] == {"ids": [1, 2, 3], "wins": 1, "games": 2,
	                         "one_way": False, "won": False, "team_idx": 0}


def test_lineup_needs_two_prior_games():
	h = _hist((1, 0, {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 1}))
	assert ti._lineup_candidates(h.order, h.matches, [1, 2, 3], [4, 5, 6]) == []


def test_a_prior_side_containing_tonights_roster_counts_as_shared_history():
	"""_lineup_candidates matches on "every member of tonight's side was united
	on one side of the prior match", not on exact roster equality — so a prior
	4-man side that contains tonight's 3-man side still counts."""
	h = _hist(
		(1, 0, {1: 0, 2: 0, 3: 0, 9: 0, 4: 1, 5: 1, 6: 1, 8: 1}),
		(2, 0, {1: 0, 2: 0, 3: 0, 9: 0, 4: 1, 5: 1, 6: 1, 8: 1}),
	)
	# players 1,2,3 were united both times, so the *trio* has history, but the
	# lineup line is about tonight's exact roster and must still fire on it.
	cands = ti._lineup_candidates(h.order, h.matches, [1, 2, 3], [4, 5, 6])
	side0 = next(c for c in cands if c["data"]["team_idx"] == 0)
	assert side0["data"]["games"] == 2


def test_a_one_way_lineup_scores_above_a_mixed_one():
	mixed = _hist(
		(1, 0, {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 1}),
		(2, 1, {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 1}),
	)
	clean = _hist(
		(1, 0, {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 1}),
		(2, 0, {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 1}),
	)
	m = next(c for c in ti._lineup_candidates(mixed.order, mixed.matches, [1, 2, 3], [4, 5, 6])
	         if c["data"]["team_idx"] == 0)
	c = next(c for c in ti._lineup_candidates(clean.order, clean.matches, [1, 2, 3], [4, 5, 6])
	         if c["data"]["team_idx"] == 0)
	assert c["data"]["one_way"] is True
	assert c["score"] > m["score"]


def test_lineup_is_skipped_for_tiny_sides():
	"""On a 2v2 the lineup IS the pair, so it would just duplicate that line."""
	h = _hist(
		(1, 0, {1: 0, 2: 0, 4: 1, 5: 1}),
		(2, 0, {1: 0, 2: 0, 4: 1, 5: 1}),
	)
	assert ti._lineup_candidates(h.order, h.matches, [1, 2], [4, 5]) == []


# ── per-type caps ────────────────────────────────────────────────────────
def test_only_one_trio_line_survives_selection():
	cands = [
		_cand("trio", 50, (1, 2, 3), teams=(0,)),
		_cand("trio", 49, (1, 2, 4), teams=(0,)),   # overlaps but is not a subset
		_cand("form", 5, (7,), teams=(1,)),
	]
	chosen = ti._select(cands, rng=random.Random(0))
	assert sum(1 for c in chosen if c["type"] == "trio") == 1


def test_only_one_lineup_line_survives_selection():
	cands = [
		_cand("lineup", 100, (1, 2, 3), teams=(0,)),
		_cand("lineup", 99, (4, 5, 6), teams=(1,)),
		_cand("form", 5, (9,), teams=(1,)),
	]
	chosen = ti._select(cands, rng=random.Random(0))
	assert sum(1 for c in chosen if c["type"] == "lineup") == 1


def test_the_cap_holds_through_the_fill_pass():
	"""PASS 3 relaxes the generic type cap; the hard caps must survive it."""
	cands = [
		_cand("trio", 50, (1, 2, 3), teams=(0,)),
		_cand("trio", 49, (4, 5, 6), teams=(1,)),
		_cand("trio", 48, (7, 8, 9), teams=(0,)),
	]
	chosen = ti._select(cands, rng=random.Random(0))
	assert sum(1 for c in chosen if c["type"] == "trio") == 1


def test_the_fill_pass_relaxes_the_generic_cap_all_the_way_to_the_limit():
	"""PASS 3's whole job is filling a thin match. A generic type must not hit a
	second, hidden ceiling on the way — four disjoint form lines should all land."""
	cands = [
		_cand("form", 40, (1,), teams=(0,)),
		_cand("form", 39, (2,), teams=(0,)),
		_cand("form", 38, (3,), teams=(1,)),
		_cand("form", 37, (4,), teams=(1,)),
	]
	chosen = ti._select(cands, rng=random.Random(0))
	assert len(chosen) == 4


# ── team framing ─────────────────────────────────────────────────────────
_NICK = {1: "Ann", 2: "Bo", 3: "Cy", 4: "Dee", 5: "Eve", 6: "Fay", 7: "Gil", 8: "Hal"}
_META = [{"name": "Alpha", "emoji": "🟦"}, {"name": "Beta", "emoji": "🟥"}]
_ROSTERS = {0: [1, 2, 3, 4], 1: [5, 6, 7, 8]}


def _frame_for(typ, players, data, teams=(0,), rosters=None, rng=None):
	c = {"type": typ, "score": 1, "players": frozenset(players),
	     "teams": frozenset(teams), "data": data}
	return ti._frame(c, _NICK, _META, rosters if rosters is not None else _ROSTERS,
	                 rng=rng if rng is not None else random.Random(0))


def test_a_pair_line_names_the_other_two():
	out = _frame_for("mate", (1, 2), {"ids": [1, 2], "won": False, "team_idx": 0})
	assert "Cy" in out and "Dee" in out


def test_a_trio_line_names_the_last_teammate():
	out = _frame_for("trio", (1, 2, 3), {"ids": [1, 2, 3], "won": True, "team_idx": 0})
	assert "Dee" in out
	assert "Cy" not in out


def test_a_lineup_line_has_no_complement_so_it_addresses_the_side():
	out = _frame_for("lineup", (1, 2, 3, 4),
	                 {"ids": [1, 2, 3, 4], "won": True, "team_idx": 0})
	assert "Alpha" in out


def test_a_complement_past_two_collapses_to_the_team_name():
	out = _frame_for("form", (1,), {"p": 1, "k": 5, "won": True})
	assert "the rest of Alpha" in out


def test_join_names():
	assert ti._join_names([]) == ""
	assert ti._join_names(["a"]) == "a"
	assert ti._join_names(["a", "b"]) == "a & b"
	assert ti._join_names(["a", "b", "c"]) == "a, b & c"


def test_positive_reads_the_right_field_per_type():
	assert ti._positive({"type": "mate_wr", "data": {"kind": "best"}}) is True
	assert ti._positive({"type": "mate_wr", "data": {"kind": "worst"}}) is False
	assert ti._positive({"type": "trio", "data": {"won": False}}) is False
	assert ti._positive({"type": "h2h", "data": {}}) is True


def test_subject_of_picks_the_side_whose_win_settles_the_tease():
	assert ti.subject_of({"type": "mate", "data": {"team_idx": 1}}) == ("team", 1)
	assert ti.subject_of({"type": "trio", "data": {"team_idx": 0}}) == ("team", 0)
	assert ti.subject_of({"type": "lineup", "data": {"team_idx": 1}}) == ("team", 1)
	assert ti.subject_of({"type": "perfect", "data": {"team_idx": 0}}) == ("team", 0)
	assert ti.subject_of({"type": "h2h", "data": {"winner": 42}}) == ("player", 42)
	assert ti.subject_of({"type": "deadlock", "data": {"ids": [7, 9]}}) == ("player", 7)
	assert ti.subject_of({"type": "form", "data": {"p": 5}}) == ("player", 5)
	assert ti.subject_of({"type": "mate_wr", "data": {"p": 3, "q": 4}}) == ("player", 3)


def test_every_candidate_type_phrases_with_a_frame():
	"""No type may crash or silently drop the framing clause."""
	cases = [
		("lineup", (1, 2, 3, 4), {"ids": [1, 2, 3, 4], "wins": 2, "games": 2,
		                          "one_way": True, "won": True, "team_idx": 0}, (0,)),
		("trio", (1, 2, 3), {"ids": [1, 2, 3], "wins": 4, "games": 5,
		                     "won": True, "team_idx": 0}, (0,)),
		("perfect", (1, 2), {"ids": [1, 2], "n": 5, "won": False, "team_idx": 0}, (0,)),
		("mate_wr", (1, 2), {"p": 1, "q": 2, "wr": 0.8, "base": 0.5,
		                     "games": 8, "kind": "best"}, (0,)),
		("h2h", (1, 5), {"winner": 1, "loser": 5, "k": 4, "series": 6,
		                 "sweep": False}, (0, 1)),
		("mate", (1, 2), {"ids": [1, 2], "k": 4, "series": 7, "won": True,
		                  "team_idx": 0}, (0,)),
		("deadlock", (1, 5), {"ids": [1, 5], "each": 3, "n": 6}, (0, 1)),
		("form", (1,), {"p": 1, "k": 5, "won": True}, (0,)),
	]
	for typ, players, data, teams in cases:
		c = {"type": typ, "score": 1, "players": frozenset(players),
		     "teams": frozenset(teams), "data": data}
		line = ti._phrase(c, _NICK, _META, _ROSTERS, rng=random.Random(1))
		assert isinstance(line, str) and line.strip(), typ


def test_phrasing_is_deterministic_under_a_seeded_rng():
	c = {"type": "form", "score": 1, "players": frozenset((1,)),
	     "teams": frozenset((0,)), "data": {"p": 1, "k": 5, "won": True}}
	a = ti._phrase(c, _NICK, _META, _ROSTERS, rng=random.Random(7))
	b = ti._phrase(c, _NICK, _META, _ROSTERS, rng=random.Random(7))
	assert a == b


def test_phrase_raises_for_an_unknown_candidate_type():
	"""An unrenderable candidate must fail loudly at the call site rather than
	silently costing the whole embed (the bug that shipped trio/lineup once)."""
	c = {"type": "bogus", "score": 1, "players": frozenset((1,)),
	     "teams": frozenset((0,)), "data": {}}
	with pytest.raises(ValueError):
		ti._phrase(c, _NICK, _META, _ROSTERS, rng=random.Random(0))


# ── sentence case (defect 1: lowercase sentence starts) ─────────────────
class _FirstChoice:
	"""Deterministic stand-in for ``random`` that always takes the first pooled
	variant, so a test can pin down which template renders without brute-forcing
	a real seed."""

	def choice(self, seq):
		return seq[0]


class _CountingChoice:
	"""Wraps ``_FirstChoice`` while counting ``.choice`` calls, so a test can
	assert a frame function burns exactly one RNG draw per invocation."""

	def __init__(self):
		self.calls = 0

	def choice(self, seq):
		self.calls += 1
		return seq[0]


class _LastChoice:
	"""Deterministic stand-in for ``random`` that always takes the final pooled
	variant, so a test can pin down the directional "does X have an answer for
	Y" template without brute-forcing a real seed."""

	def choice(self, seq):
		return seq[-1]


def test_sentence_case_leaves_a_bold_name_fragment_unchanged():
	assert ti._sentence_case("**Ann** & **Bo**") == "**Ann** & **Bo**"


def test_sentence_case_capitalises_a_lowercase_phrase():
	assert ti._sentence_case("the rest of Alpha") == "The rest of Alpha"


def test_sentence_case_handles_empty_string():
	assert ti._sentence_case("") == ""


def test_a_large_complement_frame_does_not_lowercase_after_the_period():
	"""complement_of falls back to 'the rest of {name}' once a side's uninvolved
	players exceed two. Several _frame templates put `who` at the start of a
	sentence -- the rendered line must never read '. the rest of'."""
	out = _frame_for("form", (1,), {"p": 1, "k": 5, "won": False}, rng=_FirstChoice())
	assert ". the rest of" not in out
	assert out.startswith("The rest of Alpha")


# ── rival backers (defect 2: generic rivalry filler) ─────────────────────
_SMALL_ROSTERS = {0: [1, 2, 3], 1: [5, 6, 7]}


def test_rival_backers_returns_each_players_own_side():
	"""Per the ordering contract, an h2h pair comes back leader (data['winner'])
	first, trailing rival (data['loser']) second -- winner=1 here also happens
	to have the lower user id, so this alone doesn't pin the direction; see
	test_rival_backers_orders_the_leader_first_regardless_of_user_id for that."""
	c = {"type": "h2h", "players": frozenset((1, 5)), "teams": frozenset((0, 1)),
	     "data": {"winner": 1, "loser": 5, "k": 4, "series": 6, "sweep": False}}
	out = ti.rival_backers(c, _NICK, _META, _SMALL_ROSTERS)
	assert out is not None and len(out) == 2
	(n1, w1, t1), (n2, w2, t2) = out
	assert n1 == "**Ann**" and w1 == "**Bo** & **Cy**" and t1 == "Alpha"
	assert n2 == "**Eve**" and w2 == "**Fay** & **Gil**" and t2 == "Beta"


def test_rival_backers_orders_the_leader_first_regardless_of_user_id():
	"""The ordering contract is winner-then-loser, not sorted-by-id. Here the
	winner (Eve) has the *larger* user id than the loser (Ann) -- sorting by id
	would put the loser first and silently reverse the direction, which is
	exactly the bug this fix targets."""
	c = {"type": "h2h", "players": frozenset((1, 5)), "teams": frozenset((0, 1)),
	     "data": {"winner": 5, "loser": 1, "k": 5, "series": 16, "sweep": False}}
	out = ti.rival_backers(c, _NICK, _META, _SMALL_ROSTERS)
	(n1, _w1, t1), (n2, _w2, t2) = out
	assert n1 == "**Eve**" and t1 == "Beta"     # leader (winner) first
	assert n2 == "**Ann**" and t2 == "Alpha"    # trailing rival (loser) second


def test_rival_backers_keeps_sorted_order_for_a_leaderless_deadlock():
	"""deadlock has no winner/loser -- a tie has no leader to assert, so
	rival_backers keeps the plain sorted-by-user-id order for it."""
	c = {"type": "deadlock", "players": frozenset((5, 1)), "teams": frozenset((0, 1)),
	     "data": {"ids": [1, 5], "each": 3, "n": 6}}
	out = ti.rival_backers(c, _NICK, _META, _SMALL_ROSTERS)
	(n1, _w1, t1), (n2, _w2, t2) = out
	assert n1 == "**Ann**" and t1 == "Alpha"
	assert n2 == "**Eve**" and t2 == "Beta"


def test_rival_backers_collapses_to_the_team_name_past_two():
	c = {"type": "h2h", "players": frozenset((1, 5)), "teams": frozenset((0, 1)),
	     "data": {"winner": 1, "loser": 5, "k": 4, "series": 6, "sweep": False}}
	(n1, w1, t1), (n2, w2, t2) = ti.rival_backers(c, _NICK, _META, _ROSTERS)
	assert w1 == "the rest of Alpha" and w2 == "the rest of Beta"
	assert t1 == "Alpha" and t2 == "Beta"


def test_short_backers_prefers_the_team_name_once_collapsed():
	assert ti._short_backers("the rest of Alpha", "Alpha") == "Alpha"
	assert ti._short_backers("**Bo** & **Cy**", "Alpha") == "**Bo** & **Cy**"


def test_rival_backers_is_none_when_a_player_is_missing_from_every_roster():
	c = {"type": "h2h", "players": frozenset((1, 99)), "teams": frozenset((0, 1)),
	     "data": {"winner": 1, "loser": 99, "k": 4, "series": 6, "sweep": False}}
	assert ti.rival_backers(c, _NICK, _META, _ROSTERS) is None


def test_an_h2h_frame_names_backers_instead_of_generic_filler():
	data = {"winner": 1, "loser": 5, "k": 4, "series": 6, "sweep": False}
	out = _frame_for("h2h", (1, 5), data, teams=(0, 1), rosters=_SMALL_ROSTERS,
	                 rng=_FirstChoice())
	assert out not in ("Do their teammates get a say?", "Six other players would like a word.")
	assert "Bo" in out and "Cy" in out and "Fay" in out and "Gil" in out


def test_an_h2h_frame_uses_the_team_name_once_backers_collapse():
	"""The bug this module fixes: in a 4v4 the complement of an opposing pair is
	always three, so rival_backers always collapses to "the rest of {team}" --
	naming no one. The frame must say just the team name instead."""
	data = {"winner": 1, "loser": 5, "k": 4, "series": 6, "sweep": False}
	out = _frame_for("h2h", (1, 5), data, teams=(0, 1), rosters=_ROSTERS, rng=_FirstChoice())
	assert "the rest of" not in out
	assert "Alpha" in out and "Beta" in out


def test_h2h_frame_asks_the_trailing_side_for_an_answer_about_the_leader():
	"""Real-bug regression, mirroring the live-harness example: **I'm Dead
	Guyzzz...** (winner, smaller user id, on Alpha) is 5-0 over **ddk**
	(loser). The old frame sorted its two rival_backers entries by user id, so
	the winner landed first and "Does {sa} have an answer for {nb}?" asked the
	winner's *own* side (Alpha) whether they had an answer for the player they
	were already beating -- backwards. The frame must ask the trailing side
	(backing the loser) whether they have an answer for the leader, never the
	reverse."""
	data = {"winner": 1, "loser": 5, "k": 5, "series": 16, "sweep": False}
	out = _frame_for("h2h", (1, 5), data, teams=(0, 1), rosters=_ROSTERS, rng=_LastChoice())
	assert out == "Does Beta have an answer for **Ann**?"


def test_h2h_frame_falls_back_to_generic_when_rival_backers_is_none():
	data = {"winner": 1, "loser": 99, "k": 4, "series": 6, "sweep": False}
	out = _frame_for("h2h", (1, 99), data, teams=(0, 1), rosters=_ROSTERS,
	                 rng=_FirstChoice())
	assert out in ("Do their teammates get a say?", "Six other players would like a word.")


def test_frame_consumes_exactly_one_rng_choice_call_per_branch():
	"""One branch, one draw: build_insights_embed renders every chosen candidate
	off a single shared rng, so a branch that quietly grew a second rng.choice
	call would shift the draw offset for every candidate rendered after it in
	the same embed -- a branch change cannot silently reword the other module's
	output just by changing how many draws its own branch takes."""
	cases = [
		("h2h", (1, 5), {"winner": 1, "loser": 5, "k": 4, "series": 6, "sweep": False},
		 (0, 1), _SMALL_ROSTERS),
		("h2h", (1, 99), {"winner": 1, "loser": 99, "k": 4, "series": 6, "sweep": False},
		 (0, 1), _ROSTERS),
		("lineup", (1, 2, 3, 4), {"ids": [1, 2, 3, 4], "won": True, "team_idx": 0},
		 (0,), _ROSTERS),
		("form", (1,), {"p": 1, "k": 5, "won": True}, (0,), _ROSTERS),
		("form", (1,), {"p": 1, "k": 5, "won": False}, (0,), _ROSTERS),
	]
	for typ, players, data, teams, rosters in cases:
		c = {"type": typ, "score": 1, "players": frozenset(players),
		     "teams": frozenset(teams), "data": data}
		counter = _CountingChoice()
		ti._frame(c, _NICK, _META, rosters, rng=counter)
		assert counter.calls == 1, (typ, data)


# ── build_insights_embed / storyline_ctx (F1) ───────────────────────────────
class _IP:
	def __init__(self, uid):
		self.id = uid


class _IT(list):
	def __init__(self, ids, name, emoji):
		super().__init__(_IP(i) for i in ids)
		self.name = name
		self.emoji = emoji


class _IMatch:
	def __init__(self, match_id=500):
		self.id = match_id
		self.teams = [_IT([1, 2, 3, 4], "Alpha", "🟦"), _IT([5, 6, 7, 8], "Beta", "🟥")]
		self.qc = type("QC", (), {"id": 77})()


def _mate_streak_rows():
	"""Players 1 & 2 lost their last four as teammates, of seven together --
	enough to clear MATE_MIN_TOGETHER/MATE_MIN_STREAK and produce a candidate."""
	rows = []
	for mid in range(1, 4):
		for uid, team in ((1, 0), (2, 0), (5, 1), (6, 1)):
			rows.append({"match_id": mid, "user_id": uid, "nick": f"u{uid}", "team": team, "winner": 0})
	for mid in range(4, 8):
		for uid, team in ((1, 0), (2, 0), (5, 1), (6, 1)):
			rows.append({"match_id": mid, "user_id": uid, "nick": f"u{uid}", "team": team, "winner": 1})
	return rows


def _run_insights_build(monkeypatch, match, rows):
	import asyncio
	import sys
	import types

	class _DB:
		async def fetchall(self, sql, params=None):
			return rows

	monkeypatch.setattr(ti, "db", _DB())

	fake_utils = types.ModuleType("nammaoe2bot.runtime.utils")
	fake_utils.get_nick = lambda p: f"u{p.id}"
	monkeypatch.setitem(sys.modules, "nammaoe2bot.runtime.utils", fake_utils)

	class _FakeEmbed:
		def __init__(self, *, title=None, colour=None, description=None):
			self.title = title
			self.colour = colour
			self.description = description
			self.footer_text = None

		def set_footer(self, *, text=None):
			self.footer_text = text

	fake_nextcord = types.ModuleType("nextcord")
	fake_nextcord.Embed = _FakeEmbed
	fake_nextcord.Colour = lambda value: value
	monkeypatch.setitem(sys.modules, "nextcord", fake_nextcord)
	return asyncio.run(ti.build_insights_embed(match))


def test_build_insights_embed_stashes_the_tease_context_when_it_teases(monkeypatch):
	"""F1: nammaoe2bot/features/storylines/payoff.py recomputes these storylines at report time and
	needs the exact window/seed/rosters the tease used, because none of the three
	is stable across a match's lifetime on its own. build_insights_embed must
	stash them on the match the moment it actually posts a tease."""
	match = _IMatch(match_id=500)
	embed = _run_insights_build(monkeypatch, match, _mate_streak_rows())
	assert embed is not None
	ctx = match.storyline_ctx
	assert ctx["seed"] == 500
	assert ctx["rosters"] == {0: [1, 2, 3, 4], 1: [5, 6, 7, 8]}
	assert isinstance(ctx["since"], int)


def test_build_insights_embed_does_not_stash_when_there_is_no_tease(monkeypatch):
	"""No candidates fired (empty history) -> no embed, and nothing to pin either;
	build_payoff_embed's ``getattr(match, "storyline_ctx", None)`` check depends
	on the attribute genuinely being absent here, not set to some empty value."""
	match = _IMatch(match_id=500)
	embed = _run_insights_build(monkeypatch, match, [])
	assert embed is None
	assert not hasattr(match, "storyline_ctx")
