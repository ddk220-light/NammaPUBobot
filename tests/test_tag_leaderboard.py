from bot.tag_leaderboard import tag_leaderboard_score


def test_tag_score_does_not_over_reward_tiny_perfect_sample():
	tiny = tag_leaderboard_score(tag_games=1, wins=1, losses=0, tag_rate=100, avg_impact=70)
	steady = tag_leaderboard_score(tag_games=18, wins=11, losses=7, tag_rate=35, avg_impact=62)
	assert steady > tiny


def test_tag_score_rewards_impact_and_specialization():
	base = tag_leaderboard_score(tag_games=12, wins=6, losses=6, tag_rate=20, avg_impact=50)
	impact = tag_leaderboard_score(tag_games=12, wins=6, losses=6, tag_rate=45, avg_impact=72)
	assert impact > base
