"""Unit tests for the pure analysis helpers in bot.post_game.

Covers civ ranking, the result-vs-meta observation logic, and selection. The
DB read and embed rendering are kept out of these helpers so they run without a
database or nextcord (see conftest's fakes).
"""
from __future__ import annotations

import bot.post_game as pg


def _civ_data(n=20, top=0.60, step=0.01, games=100):
	"""n civs C1..Cn with descending win-rates (C1 best). civ_stats shape."""
	return {
		f"C{i}": {"civ": f"C{i}", "games": games, "winrate": round(top - (i - 1) * step, 4)}
		for i in range(1, n + 1)
	}


# ── _civ_index ───────────────────────────────────────────────────────────
def test_civ_index_ranks_by_winrate_desc():
	idx = pg._civ_index(_civ_data(n=5))
	assert idx["c1"]["rank"] == 1 and idx["c1"]["total"] == 5
	assert idx["c5"]["rank"] == 5
	assert pg._is_top(idx["c1"]) and not pg._is_top(idx["c5"])
	assert pg._is_bottom(idx["c5"]) and not pg._is_bottom(idx["c1"])


def test_topness_bottomness_bounds():
	idx = pg._civ_index(_civ_data(n=10))
	assert pg._topness(idx["c1"]) == 1.0
	assert pg._bottomness(idx["c10"]) == 1.0


# ── _collect_observations: per-player tiers ──────────────────────────────
def test_winner_with_top_civ():
	idx = pg._civ_index(_civ_data())
	obs = pg._collect_observations([{"nick": "A", "civ": "C1", "team": 0}], winner=0, civ_index=idx)
	assert any(o["type"] == "winner_top" and o["nick"] == "A" for o in obs)


def test_loser_with_top_civ_is_a_choke():
	idx = pg._civ_index(_civ_data())
	# Bob on team 1 has the #2 civ but team 0 won -> loser_top.
	obs = pg._collect_observations([{"nick": "Bob", "civ": "C2", "team": 1}], winner=0, civ_index=idx)
	assert any(o["type"] == "loser_top" for o in obs)


def test_winner_with_bottom_civ_is_an_upset():
	idx = pg._civ_index(_civ_data())
	obs = pg._collect_observations([{"nick": "C", "civ": "C20", "team": 0}], winner=0, civ_index=idx)
	upset = next(o for o in obs if o["type"] == "winner_bottom")
	# Upsets should outscore a vanilla winner_top.
	top = pg._collect_observations([{"nick": "D", "civ": "C1", "team": 0}], winner=0, civ_index=idx)
	top = next(o for o in top if o["type"] == "winner_top")
	assert upset["score"] > top["score"]


def test_no_tier_calls_when_too_few_civs():
	idx = pg._civ_index(_civ_data(n=5))  # below MIN_CIVS_FOR_TIERS
	obs = pg._collect_observations([{"nick": "A", "civ": "C1", "team": 0}], winner=0, civ_index=idx)
	assert not any(o["type"].endswith(("_top", "_bottom")) for o in obs)


def test_unknown_civ_is_skipped():
	idx = pg._civ_index(_civ_data())
	obs = pg._collect_observations([{"nick": "A", "civ": "Nonexistent", "team": 0}], winner=0, civ_index=idx)
	assert obs == []


# ── _collect_observations: team civ-pool comparison ──────────────────────
def test_team_favored_when_winner_pool_stronger():
	idx = pg._civ_index(_civ_data())
	players = [{"nick": "A", "civ": "C1", "team": 0}, {"nick": "B", "civ": "C20", "team": 1}]
	obs = pg._collect_observations(players, winner=0, civ_index=idx)
	assert any(o["type"] == "team_favored" for o in obs)


def test_team_upset_when_winner_pool_weaker():
	idx = pg._civ_index(_civ_data())
	players = [{"nick": "A", "civ": "C1", "team": 0}, {"nick": "B", "civ": "C20", "team": 1}]
	obs = pg._collect_observations(players, winner=1, civ_index=idx)  # weaker pool won
	upset = next(o for o in obs if o["type"] == "team_upset")
	favored = pg._collect_observations(players, winner=0, civ_index=idx)
	favored = next(o for o in favored if o["type"] == "team_favored")
	assert upset["score"] > favored["score"]


# ── _select ──────────────────────────────────────────────────────────────
def test_select_one_line_per_player_and_one_team_line():
	obs = [
		{"type": "winner_top", "nick": "A", "score": 5},
		{"type": "loser_top", "nick": "A", "score": 4},      # same player -> dropped
		{"type": "winner_bottom", "nick": "B", "score": 3},
		{"type": "team_favored", "score": 2},
		{"type": "team_upset", "score": 1},                  # second team line -> dropped
	]
	chosen = pg._select(obs, limit=4)
	nicks = [c.get("nick") for c in chosen if "nick" in c]
	assert nicks == ["A", "B"]
	assert sum(1 for c in chosen if c["type"].startswith("team_")) == 1


def test_select_respects_limit():
	obs = [{"type": "winner_top", "nick": f"P{i}", "score": i} for i in range(6)]
	assert len(pg._select(obs, limit=3)) == 3


# ── Replay analysis commentary ───────────────────────────────────────────
def test_impact_payload_tags_boom_carry():
	rows = [
		{
			"nick": "Boomer", "civ": "Bengalis", "team": "2", "bot_team": 0, "winner": 1,
			"villagers": 160, "vil_pre_castle": 40, "military": 75, "mil_pre_castle": 1,
			"feudal_s": 600, "castle_s": 1000, "imperial_s": 2100,
		},
		{
			"nick": "Raider", "civ": "Huns", "team": "1", "bot_team": 1, "winner": 0,
			"villagers": 70, "vil_pre_castle": 18, "military": 70, "mil_pre_castle": 15,
			"feudal_s": 700, "castle_s": 1300, "imperial_s": 3200,
		},
	]
	impact = pg._impact_payload(rows[0], rows)
	assert impact["result"] == "W"
	assert impact["team"] == 0
	assert "Boom carry" in impact["impact_tags"]


def test_impact_payload_uses_bot_team_not_replay_team():
	row = {
		"nick": "Mapped", "civ": "Franks", "team": "2", "bot_team": 1, "result": "L",
		"villagers": 80, "vil_pre_castle": 20, "military": 40, "mil_pre_castle": 5,
		"feudal_s": 800, "castle_s": 1400, "imperial_s": 3000,
	}
	impact = pg._impact_payload(row, [row])
	assert impact["team"] == 1
	assert impact["result"] == "L"


def test_match_analysis_lines_include_win_loss_and_carry():
	player_rows = [
		{"nick": "Boomer", "civ": "Bengalis", "team": 0, "result": "W", "impact_score": 70, "impact_tags": ["Boom carry"]},
		{"nick": "Wall", "civ": "Teutons", "team": 0, "result": "W", "impact_score": 55, "impact_tags": ["Recovery"]},
		{"nick": "Raider", "civ": "Huns", "team": 1, "result": "L", "impact_score": 68, "impact_tags": ["Army pressure"]},
		{"nick": "Pocket", "civ": "Franks", "team": 1, "result": "L", "impact_score": 48, "impact_tags": []},
	]
	lines = pg._match_analysis_lines(player_rows, {0: "Alpha", 1: "Beta"})
	body = "\n".join(lines)
	assert "**Alpha** (W)" in body
	assert "**Beta** (L)" in body
	assert "**Boomer**" in body
	assert "Carry check" in body


def test_team_card_fields_render_two_teams_with_carry_and_tags():
	player_rows = [
		{"nick": "Boomer", "civ": "Bengalis", "team": 0, "result": "W", "impact_score": 70, "impact_tags": ["Boom carry", "Recovery"]},
		{"nick": "Wall", "civ": "Teutons", "team": 0, "result": "W", "impact_score": 55, "impact_tags": ["Recovery"]},
		{"nick": "Raider", "civ": "Huns", "team": 1, "result": "L", "impact_score": 68, "impact_tags": ["Army pressure"]},
		{"nick": "Pocket", "civ": "Franks", "team": 1, "result": "L", "impact_score": 48, "impact_tags": []},
	]
	fields = pg._team_card_fields(player_rows, {0: "Alpha", 1: "Beta"})
	assert [f["name"] for f in fields] == ["🟩 Alpha · W", "🟥 Beta · L"]
	assert "**Boomer** 👑 **CARRY**" in fields[0]["value"]
	assert "`Boom carry`" in fields[0]["value"]
	assert "`No tags`" in fields[1]["value"]
