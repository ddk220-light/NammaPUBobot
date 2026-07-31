"""Unit tests for the derived-global game_stats pure compute (bot/derived/game_stats.py)."""

from bot.derived.game_stats import compute_game_stats


def _p(pnum, profile_id, **kw):
	row = dict(player_number=pnum, profile_id=profile_id, civ="Franks", team="1",
	           winner=True, eapm=50, villagers=100, military=20)
	row.update(kw)
	return row


def test_medals_rank_match_wide_on_raw_counts():
	players = [_p(1, 11, villagers=120, military=10), _p(2, 22, villagers=90, military=40),
	           _p(3, 33, villagers=80, military=30)]
	rows = compute_game_stats(players, [], [], 1000)
	by_pnum = {r["player_number"]: r for r in rows}
	assert by_pnum[1]["villager_medal"] == 1
	assert by_pnum[2]["military_medal"] == 1
	assert by_pnum[3]["military_medal"] == 2


def test_player_with_no_production_gets_no_medal():
	players = [_p(1, 11, villagers=0, military=0), _p(2, 22, villagers=50, military=5)]
	rows = compute_game_stats(players, [], [], 1000)
	by_pnum = {r["player_number"]: r for r in rows}
	assert by_pnum[1]["military_medal"] is None
	assert by_pnum[1]["villager_medal"] is None
	assert by_pnum[2]["villager_medal"] == 1


def test_exact_tie_breaks_on_player_number_not_name():
	# Identical counts; the loser must be decided by player_number so a stored
	# medal never depends on a mutable display name.
	players = [_p(2, 22, villagers=50, military=50, identity="zzz"),
	           _p(1, 11, villagers=50, military=50, identity="aaa")]
	rows = compute_game_stats(players, [], [], 1000)
	by_pnum = {r["player_number"]: r for r in rows}
	assert by_pnum[1]["villager_medal"] == 1
	assert by_pnum[2]["villager_medal"] == 2


def test_top_units_is_military_only_top_three_by_total():
	units = [
		dict(player_number=1, unit="Villager", category="economy", is_military=False, total=999),
		dict(player_number=1, unit="Knight", category="cavalry", is_military=True, total=30),
		dict(player_number=1, unit="Spearman", category="infantry", is_military=True, total=20),
		dict(player_number=1, unit="Archer", category="archer", is_military=True, total=10),
		dict(player_number=1, unit="Scout Cavalry", category="cavalry", is_military=True, total=5),
	]
	rows = compute_game_stats([_p(1, 11)], units, [], 1000)
	assert [u["unit"] for u in rows[0]["top_units"]] == ["Knight", "Spearman", "Archer"]


def test_top_units_empty_when_no_military_rows():
	units = [dict(player_number=1, unit="Villager", category="economy", is_military=False, total=99)]
	rows = compute_game_stats([_p(1, 11)], units, [], 1000)
	assert rows[0]["top_units"] == []


def test_avg_eapm_passes_through_and_is_not_a_bucket_mean():
	# 3 buckets averaging 20, but eapm says 50. The stored value must be 50 --
	# bucket rows are absent for zero-action minutes, so their mean overstates.
	apm = [dict(player_number=1, minute=0, actions=10),
	       dict(player_number=1, minute=1, actions=20),
	       dict(player_number=1, minute=2, actions=30)]
	rows = compute_game_stats([_p(1, 11, eapm=50)], [], apm, 1000)
	assert rows[0]["avg_eapm"] == 50
	assert rows[0]["peak_eapm"] == 30


def test_peak_eapm_is_none_without_buckets():
	rows = compute_game_stats([_p(1, 11)], [], [], 1000)
	assert rows[0]["peak_eapm"] is None
