from bot.tag_leaderboard import aggregate_tag_rows_by_player, tag_leaderboard_score


def test_tag_score_does_not_over_reward_tiny_perfect_sample():
	tiny = tag_leaderboard_score(tag_games=1, wins=1, losses=0, tag_rate=100, avg_impact=70)
	steady = tag_leaderboard_score(tag_games=18, wins=11, losses=7, tag_rate=35, avg_impact=62)
	assert steady > tiny


def test_tag_score_rewards_impact_and_specialization():
	base = tag_leaderboard_score(tag_games=12, wins=6, losses=6, tag_rate=20, avg_impact=50)
	impact = tag_leaderboard_score(tag_games=12, wins=6, losses=6, tag_rate=45, avg_impact=72)
	assert impact > base


def test_all_tags_aggregate_collapses_to_one_row_per_player():
	rows = aggregate_tag_rows_by_player([
		{
			"user_id": "1",
			"nick": "Player",
			"avatar": None,
			"tag_key": "Boom carry",
			"tag_label": "Boom carry",
			"tag_type": "impact",
			"tag_games": 8,
			"parsed_games": 10,
			"wins": 5,
			"losses": 3,
			"avg_impact": 64,
			"score": 58,
			"last_tagged_at": 10,
		},
		{
			"user_id": "1",
			"nick": "Player",
			"avatar": None,
			"tag_key": "fast_castle",
			"tag_label": "Fast castle",
			"tag_type": "strategy",
			"tag_games": 6,
			"parsed_games": 10,
			"wins": 4,
			"losses": 2,
			"avg_impact": None,
			"score": 54,
			"last_tagged_at": 20,
		},
	])
	assert len(rows) == 1
	row = rows[0]
	assert row["tag_key"] == "all"
	assert row["tag_games"] == 14
	assert row["tag_rate"] == 100
	assert row["wins"] == 9
	assert row["losses"] == 5
	assert [t["label"] for t in row["top_tags"]] == ["Boom carry", "Fast castle"]
