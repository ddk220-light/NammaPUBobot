"""Unit tests for the pure aggregation in bot/replay_stats/persona_store.py."""
from bot.replay_stats.persona_store import PERIODS, aggregate_player_stats


def _row(uid, pnum, team, **kw):
	base = {
		"user_id": uid, "player_number": pnum, "team": team, "identity": f"p{pnum}",
		"villagers": 90, "vil_pre_castle": 22, "military": 80, "mil_pre_castle": 5,
		"feudal_s": 720, "castle_s": 1200, "imperial_s": 2400,
	}
	base.update(kw)
	return base


def _match(at, focus_kw=None):
	rows = [
		_row(1, 1, "1", **(focus_kw or {})),
		_row(2, 2, "1"),
		_row(3, 3, "2"),
		_row(4, 4, "2", villagers=85),
	]
	return (at, rows)


def test_periods_match_web_windows():
	# Compare against the literal in bot/web.py itself (parsed via ast, since
	# importing bot.web pulls aiohttp) so drift between the two dicts fails CI.
	import ast
	from pathlib import Path

	src = (Path(__file__).resolve().parent.parent / "bot" / "web.py").read_text()
	tree = ast.parse(src)
	web_periods = None
	for node in ast.walk(tree):
		if isinstance(node, ast.Assign) and any(
			isinstance(t, ast.Name) and t.id == "MATCH_STAT_PERIODS" for t in node.targets
		):
			web_periods = ast.literal_eval(node.value)
	assert web_periods is not None, "MATCH_STAT_PERIODS not found in bot/web.py"
	assert web_periods == PERIODS


def test_aggregates_only_matches_in_window():
	groups = [_match(at=1000), _match(at=5000)]
	stats_all = aggregate_player_stats(groups, 1, None)
	stats_late = aggregate_player_stats(groups, 1, 2000)
	assert stats_all["matches"] == 2
	assert stats_late["matches"] == 1


def test_none_when_player_absent_or_out_of_window():
	groups = [_match(at=1000)]
	assert aggregate_player_stats(groups, 99, None) is None
	assert aggregate_player_stats(groups, 1, 2000) is None


def test_unknown_played_at_counts_in_every_window():
	# A match whose bot-match join is missing (played_at None) must still
	# count toward sliced windows instead of vanishing from week/month.
	groups = [_match(at=None)]
	stats = aggregate_player_stats(groups, 1, 999999)
	assert stats is not None and stats["matches"] == 1


def test_carry_rate_counts_team_top():
	# Focus player dominates every component -> team-top in their team.
	groups = [_match(at=1000, focus_kw={"villagers": 130, "military": 120, "castle_s": 1000})]
	stats = aggregate_player_stats(groups, 1, None)
	assert stats["carry_rate"] == 100


def test_tag_rates_are_per_match_percentages():
	# Two matches, focus player flat in both -> the same fallback tag both
	# times, so its rate must be exactly 100 (per-match percentage, not count).
	flat = [(1000, [_row(1, 1, "1"), _row(2, 2, "1"), _row(3, 3, "2"), _row(4, 4, "2")]) for _ in range(2)]
	stats = aggregate_player_stats(flat, 1, None)
	assert stats["matches"] == 2
	assert list(stats["tag_rates"].values()) == [100.0]


def test_multi_profile_user_counts_match_once():
	# A user whose two linked profiles both appear in one match must
	# contribute a single game, not two.
	at, rows = _match(at=1000)
	rows.append(_row(1, 5, "2", identity="smurf", villagers=10, military=5))
	stats = aggregate_player_stats([(at, rows)], 1, None)
	assert stats["matches"] == 1


def test_feeds_derive_persona():
	from bot.replay_stats.persona import derive_persona
	groups = [_match(at=1000 + i, focus_kw={"villagers": 130, "military": 120}) for i in range(12)]
	stats = aggregate_player_stats(groups, 1, None)
	p = derive_persona(stats)
	assert p["key"] != "unscouted"
	assert p["role"] is not None
