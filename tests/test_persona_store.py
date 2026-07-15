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
	assert PERIODS == {"all": None, "year": 365, "month6": 183, "month3": 92, "month": 30, "week": 7}


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


def test_carry_rate_counts_team_top():
	# Focus player dominates every component -> team-top in their team.
	groups = [_match(at=1000, focus_kw={"villagers": 130, "military": 120, "castle_s": 1000})]
	stats = aggregate_player_stats(groups, 1, None)
	assert stats["carry_rate"] == 100


def test_tag_rates_present_thanks_to_fallback():
	# Even a flat profile gets a fallback tag, so tag_rates is never empty.
	stats = aggregate_player_stats([_match(at=1000)], 1, None)
	assert stats["tag_rates"]


def test_feeds_derive_persona():
	from bot.replay_stats.persona import derive_persona
	groups = [_match(at=1000 + i, focus_kw={"villagers": 130, "military": 120}) for i in range(12)]
	stats = aggregate_player_stats(groups, 1, None)
	p = derive_persona(stats)
	assert p["key"] != "unscouted"
	assert p["role"] is not None
