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


def test_select_caps_personal_lines_so_meta_keeps_room():
	obs = [{"type": "personal_streak", "nick": f"P{i}", "score": 10 - i} for i in range(4)]
	obs.append({"type": "winner_top", "nick": "Meta", "score": 1})
	chosen = pg._select(obs, limit=4, max_personal=2)
	assert sum(1 for c in chosen if c["type"].startswith("personal_")) == 2
	assert any(c["type"] == "winner_top" for c in chosen)


def test_select_personal_slot_not_spent_on_a_dropped_duplicate():
	# Same player twice: the second line is dropped, so it must not eat a slot.
	obs = [
		{"type": "personal_streak", "nick": "A", "score": 9},
		{"type": "personal_record", "nick": "A", "score": 8},
		{"type": "personal_streak", "nick": "B", "score": 7},
		{"type": "personal_record", "nick": "C", "score": 6},
	]
	chosen = pg._select(obs, limit=4, max_personal=2)
	assert [c["nick"] for c in chosen] == ["A", "B"]


# ── Personal civ records ─────────────────────────────────────────────────
def test_personal_record_counts_current_match_and_streak():
	rec = pg._personal_civ_record(["W", "W", "L"], "W")
	assert (rec["wins"], rec["losses"], rec["games"]) == (3, 1, 4)
	assert rec["streak"] == 3          # this win plus the two before it
	assert rec["prior_streak"] == 2
	assert rec["result"] == "W"


def test_personal_record_signs_losing_streak_negative():
	rec = pg._personal_civ_record(["L", "L"], "L")
	assert rec["streak"] == -3
	assert rec["winrate"] == 0.0


def test_personal_record_no_history_is_a_single_game():
	rec = pg._personal_civ_record([], "L")
	assert (rec["games"], rec["streak"], rec["prior_streak"]) == (1, -1, 0)


def test_personal_record_needs_a_decided_result():
	assert pg._personal_civ_record(["W"], None) is None


def _players(**kw):
	base = {"nick": "A", "civ": "Franks", "team": 0, "user_id": 1}
	base.update(kw)
	return [base]


def test_personal_streak_observation_when_run_continues():
	obs = pg._collect_personal_observations(
		_players(), winner=0, histories={(1, "franks"): ["W", "W", "W"]})
	assert [o["type"] for o in obs] == ["personal_streak"]
	assert obs[0]["record"]["streak"] == 4


def test_personal_streak_broken_outscores_a_continuing_streak():
	broken = pg._collect_personal_observations(
		_players(), winner=1, histories={(1, "franks"): ["W", "W", "W"]})[0]
	assert broken["type"] == "personal_streak_broken"
	cont = pg._collect_personal_observations(
		_players(), winner=0, histories={(1, "franks"): ["W", "W", "W"]})[0]
	assert broken["score"] > cont["score"]


def test_personal_record_line_for_lopsided_history_without_a_streak():
	# 4-1 overall, but the run ends at this single win -> record line, not streak.
	obs = pg._collect_personal_observations(
		_players(), winner=0, histories={(1, "franks"): ["L", "W", "W", "W"]})
	assert [o["type"] for o in obs] == ["personal_record"]
	assert obs[0]["record"]["winrate"] == 0.8


def test_no_personal_line_on_a_thin_even_history():
	obs = pg._collect_personal_observations(
		_players(), winner=0, histories={(1, "franks"): ["L"]})
	assert obs == []


def test_personal_observations_skip_players_without_user_id():
	obs = pg._collect_personal_observations(
		_players(user_id=None), winner=0, histories={(None, "franks"): ["W", "W", "W"]})
	assert obs == []


def test_personal_phrases_render_for_every_type():
	for winner, hist in ((0, ["W", "W", "W"]), (1, ["W", "W", "W"]), (0, ["L", "W", "W", "W"])):
		for o in pg._collect_personal_observations(_players(), winner=winner, histories={(1, "franks"): hist}):
			assert "Franks" in pg._phrase(o)


# ── Match Cards ────────────────────────────────────────────────────────
def _cp(nick, team, **kw):
	"""A card payload as _card_payload would produce it."""
	base = dict(nick=nick, civ="Franks", team=team, result="W" if team == 0 else "L",
	            strategies=[], spawn=None, villagers=100, military=50,
	            farms=10, tcs=2, eapm=45, peak_eapm=None, has_production=True,
	            production_coverage=None, impact_score=50, army_score=50,
	            eco_score=50, early_eco_score=50, reboom_score=50)
	base.update(kw)
	return base


def test_team_card_fields_render_two_teams_with_the_crown_on_the_top_player():
	player_rows = [
		_cp("Boomer", 0, impact_score=70),
		_cp("Wall", 0, impact_score=55),
		_cp("Raider", 1, impact_score=68),
		_cp("Pocket", 1, impact_score=48),
	]
	fields = pg._team_card_fields(player_rows, {0: "Alpha", 1: "Beta"})
	assert [f["name"] for f in fields] == ["🟩 Alpha · W", "🟥 Beta · L"]
	assert "👑 **Boomer**" in fields[0]["value"]
	assert "• **Wall**" in fields[0]["value"]
	assert "👑 **Raider**" in fields[1]["value"]


def test_the_old_score_glyphs_and_carry_badge_are_gone():
	fields = pg._team_card_fields([_cp("a", 0), _cp("b", 1)], {0: "A", 1: "B"})
	body = fields[0]["value"] + fields[1]["value"]
	for gone in ("▲", "▼", "⏱", "CARRY", "No tags", "`50`"):
		assert gone not in body


def test_no_player_ever_shows_a_participation_fallback_tag():
	fields = pg._team_card_fields([_cp("a", 0), _cp("b", 0)], {0: "A", 1: "B"})
	for banned in ("All-rounder", "Army-leaning", "Eco-leaning",
	               "Tempo-leaning", "Uphill battle", "High impact"):
		assert banned not in fields[0]["value"]


def test_medals_appear_on_the_top_three_of_the_match():
	rows = [_cp("first", 0, military=90), _cp("second", 0, military=80),
	        _cp("third", 1, military=70), _cp("fourth", 1, military=60)]
	fields = pg._team_card_fields(rows, {0: "A", 1: "B"})
	assert "⚔⚔⚔" in fields[0]["value"]
	assert "⚔⚔" in fields[0]["value"]
	assert "⚔" in fields[1]["value"]
	assert "**fourth**" in fields[1]["value"]


def test_card_line_shows_every_strategy_label_for_the_player():
	line = pg._player_card_line(_cp("a", 0, strategies=["Knight Rush", "Safe Castle"]))
	assert "Knight Rush, Safe Castle" in line


def test_card_line_omits_the_strategy_segment_when_none_fired():
	line = pg._player_card_line(_cp("a", 0, strategies=[]))
	assert line.split("\n")[0].endswith("**Franks**")


def test_stats_line_shows_counts_and_eapm_with_peak():
	line = pg._player_card_line(_cp("a", 0, villagers=84, military=46, farms=14,
	                                tcs=3, eapm=62, peak_eapm=89))
	assert "84 vils" in line
	assert "46 military" in line
	assert "14 farms" in line
	assert "3 TC" in line
	assert "62 eAPM (pk 89)" in line


def test_peak_is_omitted_when_the_apm_table_has_no_rows():
	line = pg._player_card_line(_cp("a", 0, eapm=55, peak_eapm=None))
	assert "55 eAPM" in line
	assert "pk" not in line


def test_eapm_is_omitted_entirely_when_it_was_never_stored():
	line = pg._player_card_line(_cp("a", 0, eapm=None))
	assert "eAPM" not in line


def test_spawn_phrase_is_appended_when_present():
	line = pg._player_card_line(_cp("a", 0, spawn="spawned alone"))
	assert "spawned alone" in line


def test_no_production_renders_the_warning_instead_of_stats():
	line = pg._player_card_line(_cp("a", 0, has_production=False,
	                                villagers=0, military=0))
	assert "partial replay data" in line
	assert "vils" not in line


def test_team_field_stays_within_the_discord_character_cap():
	rows = [_cp("x" * 40, 0, civ="Y" * 30, villagers=999, military=999,
	            farms=999, tcs=99, eapm=99, peak_eapm=999,
	            spawn="spawned next to enemy",
	            strategies=["Late Cav Archers", "Safe Castle", "Ram Push"])
	        for _ in range(4)]
	fields = pg._team_card_fields(rows, {0: "A", 1: "B"})
	assert len(fields[0]["value"]) <= 1024
	# All four players must survive; the stats line is dropped first.
	assert fields[0]["value"].count("—") == 4


def test_analysis_rows_selects_every_column_the_card_needs():
	"""The card joins on (aoe2_match_id, player_number) and reads age_reliable
	and eapm, none of which the original SELECT carried."""
	import inspect

	src = inspect.getsource(pg._analysis_rows)
	for column in ("aoe2_match_id", "player_number", "age_reliable", "eapm",
	               "duration_s"):
		assert column in src, f"{column} missing from the _analysis_rows SELECT"


def test_required_card_columns_are_all_selected():
	import inspect

	from bot.replay_stats import card_scoring

	src = inspect.getsource(pg._analysis_rows)
	for column in card_scoring.REQUIRED_COLUMNS:
		assert column in src, f"{column} is in REQUIRED_COLUMNS but not SELECTed"


def test_merge_analysis_rows_keeps_replay_players_missing_from_bot_civ_rows():
	pm_rows = [
		{"user_id": 1, "nick": "A", "bot_team": 0, "result": "W"},
		{"user_id": 2, "nick": "B", "bot_team": 0, "result": "W"},
		{"user_id": 3, "nick": "C", "bot_team": 0, "result": "W"},
		{"user_id": 4, "nick": "D", "bot_team": 0, "result": "W"},
		{"user_id": 5, "nick": "E", "bot_team": 1, "result": "L"},
		{"user_id": 6, "nick": "F", "bot_team": 1, "result": "L"},
		{"user_id": 7, "nick": "G", "bot_team": 1, "result": "L"},
		{"user_id": 8, "nick": "H", "bot_team": 1, "result": "L"},
	]
	mc_rows = [
		{"user_id": 1, "nick": "A", "bot_team": 0, "civ": "Huns", "result": "W"},
		{"user_id": 2, "nick": "B", "bot_team": 0, "civ": "Franks", "result": "W"},
		{"user_id": 5, "nick": "E", "bot_team": 1, "civ": "Saracens", "result": "L"},
		{"user_id": 6, "nick": "F", "bot_team": 1, "civ": "Mongols", "result": "L"},
	]
	replay_rows = [
		{"user_id": 1, "identity": "A", "civ": "Huns", "replay_team": "1", "winner": 1, "villagers": 80},
		{"user_id": 2, "identity": "B", "civ": "Franks", "replay_team": "1", "winner": 1, "villagers": 82},
		{"user_id": 3, "identity": "C", "civ": "Teutons", "replay_team": "1", "winner": 1, "villagers": 78},
		{"user_id": 4, "identity": "D", "civ": "Vietnamese", "replay_team": "1", "winner": 1, "villagers": 90},
		{"user_id": 5, "identity": "E", "civ": "Saracens", "replay_team": "2", "winner": 0, "villagers": 70},
		{"user_id": 6, "identity": "F", "civ": "Mongols", "replay_team": "2", "winner": 0, "villagers": 71},
		{"user_id": 7, "identity": "G", "civ": "Lithuanians", "replay_team": "2", "winner": 0, "villagers": 68},
		{"user_id": 8, "identity": "H", "civ": "Burmese", "replay_team": "2", "winner": 0, "villagers": 67},
	]
	merged = pg._merge_analysis_rows(mc_rows, replay_rows, pm_rows)
	assert len(merged) == 8
	assert {r["nick"] for r in merged if r["bot_team"] == 0} == {"A", "B", "C", "D"}
	assert {r["nick"] for r in merged if r["bot_team"] == 1} == {"E", "F", "G", "H"}
	assert all(r["result"] == "W" for r in merged if r["bot_team"] == 0)
	assert all(r["result"] == "L" for r in merged if r["bot_team"] == 1)


def test_merge_analysis_rows_keeps_bot_players_missing_replay_mapping():
	pm_rows = [
		{"user_id": 1, "nick": "Mapped", "bot_team": 0, "result": "W"},
		{"user_id": 9, "nick": "BotOnly", "bot_team": 0, "result": "W"},
	]
	mc_rows = [
		{"user_id": 1, "nick": "Mapped", "bot_team": 0, "civ": "Huns", "result": "W"},
		{"user_id": 9, "nick": "BotOnly", "bot_team": 0, "civ": "Mayans", "result": "W"},
	]
	replay_rows = [
		{"user_id": 1, "identity": "Mapped", "civ": "Huns", "replay_team": "1", "winner": 1, "villagers": 80},
	]
	merged = pg._merge_analysis_rows(mc_rows, replay_rows, pm_rows)
	assert [r["nick"] for r in merged] == ["Mapped", "BotOnly"]
	assert merged[1]["civ"] == "Mayans"
	assert merged[1]["result"] == "W"


def test_merge_analysis_rows_pairs_single_unmapped_replay_player_to_bot_roster():
	pm_rows = [
		{"user_id": 1, "nick": "Known", "bot_team": 0, "result": "W"},
		{"user_id": 2, "nick": "DiscordOnly", "bot_team": 0, "result": "W"},
		{"user_id": 3, "nick": "EnemyA", "bot_team": 1, "result": "L"},
		{"user_id": 4, "nick": "EnemyB", "bot_team": 1, "result": "L"},
	]
	mc_rows = [
		{"user_id": 1, "nick": "Known", "bot_team": 0, "civ": "Huns", "result": "W"},
		{"user_id": 3, "nick": "EnemyA", "bot_team": 1, "civ": "Franks", "result": "L"},
		{"user_id": 4, "nick": "EnemyB", "bot_team": 1, "civ": "Britons", "result": "L"},
	]
	replay_rows = [
		{"user_id": 1, "identity": "Known", "civ": "Huns", "replay_team": "1", "winner": 1, "villagers": 80},
		{"user_id": None, "identity": "PrivateProfile", "civ": "Mayans", "replay_team": "1", "winner": 1, "villagers": 90},
		{"user_id": 3, "identity": "EnemyA", "civ": "Franks", "replay_team": "2", "winner": 0, "villagers": 50},
		{"user_id": 4, "identity": "EnemyB", "civ": "Britons", "replay_team": "2", "winner": 0, "villagers": 55},
	]
	merged = pg._merge_analysis_rows(mc_rows, replay_rows, pm_rows)
	discord_only = next(r for r in merged if r["nick"] == "DiscordOnly")
	assert len(merged) == 4
	assert discord_only["identity"] == "PrivateProfile"
	assert discord_only["civ"] == "Mayans"
	assert discord_only["villagers"] == 90


# ── post_match_analysis: the chart must never cost the post ──────────────
# The APM chart is a nice-to-have attachment on an otherwise text-only post. A send
# that fails *because of the file* (realistically: no ATTACH_FILES permission in the
# results channel) must degrade to a fileless send, not drop the whole analysis.


class _FakeEmbed:
	def __init__(self):
		self.image_url = None

	def set_image(self, url=None):
		self.image_url = url


class _FakeChannel:
	"""Rejects any send that carries a file; records every send it is given."""

	def __init__(self, fail_with_file=True):
		self.fail_with_file = fail_with_file
		self.sends = []

	async def send(self, **kwargs):
		self.sends.append(kwargs)
		if self.fail_with_file and "file" in kwargs:
			raise RuntimeError("403 Forbidden (error code: 50013): Missing Permissions")
		return object()


def _wire_post_match_analysis(monkeypatch, channel, chart_file):
	"""Stub out everything post_match_analysis reaches for, leaving only its own
	send/fallback logic under test."""
	import sys
	import types

	cards = _FakeEmbed()
	fake_client = types.ModuleType("core.client")
	fake_client.dc = types.SimpleNamespace(get_channel=lambda _cid: channel)
	monkeypatch.setitem(sys.modules, "core.client", fake_client)

	async def _channel_id(_bot_match_id):
		return 123

	# post_match_analysis fetches the roster once and passes it to the cards
	# builder, so both the fetch and the builder need stubbing.
	async def _rows(_bot_match_id):
		return [{"user_id": 1, "nick": "a", "bot_team": 0, "result": "W"}]

	async def _names(_channel_id, _bot_match_id):
		return {0: "Alpha", 1: "Beta"}

	async def _cards(_channel_id, _bot_match_id, _rows=None, _names=None):
		return cards

	async def _chart(_bot_match_id):
		return chart_file

	monkeypatch.setattr(pg, "_match_channel_id", _channel_id)
	monkeypatch.setattr(pg, "_analysis_rows", _rows)
	monkeypatch.setattr(pg, "_team_names", _names)
	monkeypatch.setattr(pg, "build_match_cards_embed", _cards)
	monkeypatch.setattr(pg, "_apm_chart_file", _chart)
	return cards


def test_post_match_analysis_retries_without_the_file_when_the_attach_fails(monkeypatch):
	import asyncio

	channel = _FakeChannel(fail_with_file=True)
	cards = _wire_post_match_analysis(monkeypatch, channel, chart_file=object())

	assert asyncio.run(pg.post_match_analysis(7)) is True
	# First attempt carried the file and was rejected; the retry dropped it and went out.
	assert len(channel.sends) == 2
	assert "file" in channel.sends[0]
	assert "file" not in channel.sends[1]
	assert channel.sends[1]["embeds"] == [cards]


def test_post_match_analysis_fetches_the_roster_once(monkeypatch):
	"""The roster is fetched once and passed into the cards builder, not
	re-fetched inside it — otherwise the card's extra signal queries double too."""
	import asyncio

	channel = _FakeChannel(fail_with_file=False)
	_wire_post_match_analysis(monkeypatch, channel, chart_file=None)

	calls = []
	original = pg._analysis_rows

	async def _counting(bot_match_id):
		calls.append(bot_match_id)
		return await original(bot_match_id)

	monkeypatch.setattr(pg, "_analysis_rows", _counting)

	assert asyncio.run(pg.post_match_analysis(7)) is True
	assert calls == [7], f"expected one roster fetch, got {len(calls)}"


def test_post_match_analysis_sends_the_file_when_it_is_accepted(monkeypatch):
	import asyncio

	channel = _FakeChannel(fail_with_file=False)
	chart = object()
	cards = _wire_post_match_analysis(monkeypatch, channel, chart_file=chart)

	assert asyncio.run(pg.post_match_analysis(7)) is True
	assert len(channel.sends) == 1
	assert channel.sends[0]["file"] is chart
	assert cards.image_url == "attachment://apm.png"
