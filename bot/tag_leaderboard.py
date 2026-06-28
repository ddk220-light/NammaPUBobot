# -*- coding: utf-8 -*-
"""Pure scoring helpers for tag leaderboards."""


def tag_leaderboard_score(tag_games, wins, losses, tag_rate, avg_impact=None):
	"""Blend quality + quantity so tiny 100% samples do not dominate."""
	tag_games = max(0, int(tag_games or 0))
	wins = max(0, int(wins or 0))
	losses = max(0, int(losses or 0))
	tag_rate = max(0.0, min(100.0, float(tag_rate or 0)))
	impact = 50.0 if avg_impact is None else max(0.0, min(100.0, float(avg_impact)))

	# 50% prior over eight virtual decided games.
	win_score = 100.0 * ((wins + 4.0) / (wins + losses + 8.0))
	volume_score = min(tag_games / 20.0, 1.0) * 100.0
	return round(
		(win_score * 0.40)
		+ (impact * 0.25)
		+ (volume_score * 0.20)
		+ (tag_rate * 0.15),
		1,
	)

