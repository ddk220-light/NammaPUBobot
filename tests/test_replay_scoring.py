"""Unit tests for bot/replay_stats/scoring.py — the shared impact/tag formula.

The regression scenarios here encode the July-2026 recalibration contract:
a player who idles early and rebooms late must NOT out-impact a steady
contributor, and the carry-style tags require a real early economy.
"""
from bot.replay_stats.scoring import (
	carry_sort_key, derive_impact_tags, impact_scores, impact_tag_names, strength_glyphs,
)


def _player(**kw):
	base = {
		"player_number": 1,
		"villagers": 90,
		"vil_pre_castle": 22,
		"military": 80,
		"mil_pre_castle": 5,
		"feudal_s": 720,
		"castle_s": 1200,
		"imperial_s": 2400,
	}
	base.update(kw)
	return base


def _tags(scores):
	return {t["key"] for t in derive_impact_tags(scores)}


def test_reboomer_does_not_outscore_steady_contributor():
	"""Late reboom (tiny early eco, huge final villager count) must not beat a
	player who was strong all game. This was the core carry-tag bug."""
	steady = _player(player_number=1, villagers=110, vil_pre_castle=30, military=110, mil_pre_castle=8)
	reboomer = _player(player_number=2, villagers=150, vil_pre_castle=10, military=60, mil_pre_castle=0)
	filler = _player(player_number=3)
	group = [steady, reboomer, filler]
	assert impact_scores(steady, group)["impact"] > impact_scores(reboomer, group)["impact"]


def test_reboomer_gets_reboom_tag_not_carry_tags():
	# Slightly weak early eco, clearly strongest final villager count: a real
	# reboom. It earns the Reboom tag but must not read as an eco/boom carry.
	reboomer = _player(player_number=2, villagers=150, vil_pre_castle=18, military=60, mil_pre_castle=0)
	group = [
		_player(player_number=1, villagers=90, vil_pre_castle=22),
		reboomer,
		_player(player_number=3, villagers=95, vil_pre_castle=24),
		_player(player_number=4, villagers=85, vil_pre_castle=20),
	]
	tags = _tags(impact_scores(reboomer, group))
	assert "reboom" in tags
	assert "eco_carry" not in tags
	assert "boom_carry" not in tags


def test_strong_early_boom_gets_boom_carry():
	boomer = _player(player_number=1, villagers=130, vil_pre_castle=33, military=75, mil_pre_castle=2)
	group = [
		boomer,
		_player(player_number=2, villagers=90, vil_pre_castle=22, military=80, mil_pre_castle=7),
		_player(player_number=3, villagers=85, vil_pre_castle=20, military=95, mil_pre_castle=10),
	]
	tags = _tags(impact_scores(boomer, group))
	assert "boom_carry" in tags
	assert "reboom" not in tags


def test_reboom_component_is_clamped():
	"""Legacy formula let the reboom component swing the full 0-100 scale
	(difference of two clamped z's). It must stay within one z-clamp now."""
	reboomer = _player(player_number=1, villagers=200, vil_pre_castle=5)
	group = [
		reboomer,
		_player(player_number=2, villagers=90, vil_pre_castle=30),
		_player(player_number=3, villagers=95, vil_pre_castle=28),
	]
	assert impact_scores(reboomer, group)["reboom"] <= 80  # 50 + 2.0 * 15


def test_all_in_pressure_needs_sacrificed_eco():
	rusher = _player(player_number=1, military=140, mil_pre_castle=20, villagers=60, vil_pre_castle=14)
	group = [
		rusher,
		_player(player_number=2, military=70, mil_pre_castle=4, villagers=100, vil_pre_castle=28),
		_player(player_number=3, military=75, mil_pre_castle=5, villagers=105, vil_pre_castle=30),
	]
	tags = _tags(impact_scores(rusher, group))
	assert "all_in_pressure" in tags
	assert "map_pressure" not in tags


def test_high_impact_fires_for_top_performer():
	star = _player(player_number=1, villagers=120, vil_pre_castle=32, military=130,
	               mil_pre_castle=12, feudal_s=650, castle_s=1050, imperial_s=2100)
	group = [
		star,
		_player(player_number=2),
		_player(player_number=3, villagers=85, military=70),
	]
	assert "high_impact" in _tags(impact_scores(star, group))


def test_missing_replay_data_scores_neutral_and_untagged():
	row = {"player_number": 1}
	group = [row, {"player_number": 2}]
	scores = impact_scores(row, group)
	assert scores["impact"] == 50
	assert derive_impact_tags(scores) == []


def test_tag_names_map_per_surface():
	scores = {"army": 70, "eco": 40, "timing": 50, "early_eco": 50, "early_army": 60,
	          "reboom": 50, "impact": 59}
	assert impact_tag_names(scores) == ["Low-eco pressure", "High impact"]
	assert impact_tag_names(scores, style="stored") == ["All-in pressure", "High impact"]


def test_carry_sort_is_deterministic_and_tie_broken_by_army():
	a = {"nick": "a", "impact_score": 55, "army_score": 60, "eco_score": 50}
	b = {"nick": "b", "impact_score": 55, "army_score": 52, "eco_score": 58}
	c = {"nick": "c", "impact_score": 60, "army_score": 40, "eco_score": 40}
	assert sorted([a, b, c], key=carry_sort_key)[0] is c
	assert sorted([b, a], key=carry_sort_key)[0] is a


def test_early_aggressor_outranks_late_spam_turtle():
	"""Regression for bot match 1390398 (aoe2 491780322, 70-min Land Nomad):
	the fast-Imp aggressor who applied force all mid-game must not lose the
	top-impact spot to a home-castle turtle whose only edge was more villagers
	and army created in the last stretch of a marathon."""
	def p(n, fs, cs, ims, v, vpc, vpi, m, mpc, mpi):
		return {"player_number": n, "feudal_s": fs, "castle_s": cs, "imperial_s": ims,
		        "villagers": v, "vil_pre_castle": vpc, "vil_pre_imperial": vpi,
		        "military": m, "mil_pre_castle": mpc, "mil_pre_imperial": mpi}
	aggressor = p(1, 605, 1081, 2384, 192, 39, 119, 463, 0, 119)   # Dark De Bruyne
	turtle = p(2, 664, 983, 3093, 245, 38, 198, 451, 0, 104)       # Shadeslayer
	group = [
		aggressor,
		turtle,
		p(3, 812, 2204, 3212, 254, 67, 199, 278, 0, 9),
		p(4, 781, 992, 2372, 221, 29, 104, 308, 0, 74),
		p(5, 712, 926, 3088, 300, 49, 241, 572, 0, 40),
		p(6, 667, 1033, 2155, 147, 38, 81, 291, 0, 17),
		p(7, 705, 1456, 2392, 168, 55, 77, 97, 0, 37),
		p(8, 708, 1111, 2621, 250, 48, 152, 406, 0, 97),
	]
	assert impact_scores(aggressor, group)["impact"] > impact_scores(turtle, group)["impact"]


def test_impact_queries_select_every_scoring_column():
	"""bot/web.py and bot/post_game.py feed _impact_payload from hand-written
	SELECT column lists. A missing column silently zeroes that component for
	every player (this shipped once: mil_pre_imperial was absent, flattening
	the new army mix on the live site while the backfill — which uses
	SELECT * — wrote correct stored tags)."""
	from pathlib import Path
	from bot.replay_stats.scoring import REQUIRED_COLUMNS

	root = Path(__file__).resolve().parent.parent
	for rel in ("bot/web.py", "bot/post_game.py"):
		src = (root / rel).read_text()
		queries = [chunk for chunk in src.split('await db.fetchall(')
		           if 'rs_player_games' in chunk.split('FROM')[0] + chunk[:600]
		           and 'g.villagers' in chunk[:600]]
		assert queries, f"no rs_player_games impact query found in {rel}"
		for q in queries:
			head = q[:600]
			for col in REQUIRED_COLUMNS:
				assert f"g.{col}" in head, f"{rel}: impact query missing g.{col}"


def test_fallback_tag_partial_replay_when_no_production_data():
	from bot.replay_stats.scoring import fallback_tag
	row = {"player_number": 1, "villagers": 0, "military": None}
	scores = impact_scores(row, [row, {"player_number": 2}])
	assert fallback_tag(scores, row)["key"] == "partial_replay"


def test_fallback_tag_picks_strongest_lean():
	from bot.replay_stats.scoring import fallback_tag
	row = _player(villagers=100, vil_pre_castle=26, military=85)
	group = [row, _player(player_number=2), _player(player_number=3, villagers=85)]
	scores = impact_scores(row, group)
	fb = fallback_tag(scores, row)
	assert fb["key"] in ("lean_army", "lean_eco", "lean_tempo")


def test_fallback_tag_all_rounder_when_flat():
	from bot.replay_stats.scoring import fallback_tag
	row = _player()
	scores = {"army": 50, "eco": 51, "timing": 49, "impact": 50,
	          "early_eco": 50, "early_army": 50, "reboom": 50}
	assert fallback_tag(scores, row)["key"] == "all_rounder"


def test_fallback_tag_uphill_when_below_average_everywhere():
	# Uniformly weak game must not read as the same "All-rounder" a
	# balanced-strong game gets.
	from bot.replay_stats.scoring import fallback_tag
	row = _player()
	scores = {"army": 44, "eco": 46, "timing": 43, "impact": 44,
	          "early_eco": 45, "early_army": 50, "reboom": 45}
	assert fallback_tag(scores, row)["key"] == "uphill_battle"


def test_payload_names_always_return_at_least_one_tag():
	from bot.replay_stats.scoring import impact_tag_names_with_fallback
	row = _player()
	group = [row, _player(player_number=2), _player(player_number=3)]
	scores = impact_scores(row, group)
	names = impact_tag_names_with_fallback(scores, row)
	assert len(names) >= 1


def test_strength_glyphs_have_no_numbers():
	text = strength_glyphs({"army": 70, "eco": 30, "timing": 50})
	assert text == "⚔▲ 🌾▼ ⏱·"
	assert not any(ch.isdigit() for ch in text)


def test_player_tags_loads_standalone_like_the_backfill_script():
	"""utils/backfill_player_game_tags.py loads player_tags.py by file path with
	no parent package — the scoring import must survive that."""
	import importlib.util
	from pathlib import Path

	path = Path(__file__).resolve().parent.parent / "bot" / "replay_stats" / "player_tags.py"
	spec = importlib.util.spec_from_file_location("player_tags_standalone_test", path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	row = _player(villagers=130, vil_pre_castle=33, military=75, mil_pre_castle=2)
	group = [row, _player(player_number=2), _player(player_number=3, villagers=85)]
	tags = {t["tag"] for t in module.derive_tags(row, group)}
	assert "Boom carry" in tags
