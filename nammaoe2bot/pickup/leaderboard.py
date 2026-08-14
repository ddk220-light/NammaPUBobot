# -*- coding: utf-8 -*-
"""Pure public-leaderboard eligibility rules.

The rating board supplies no additional activity. A feature-specific board may
provide another honest participation timestamp, but it can refresh only the
recency gate; it cannot unhide a player or manufacture ranked match history.
"""


def eligible_rows(rows, cfg, now, additional_activity=None):
	"""Filter rating rows through hidden, recency, and minimum-match gates."""
	additional_activity = additional_activity or {}
	return [
		row for row in rows
		if row['rating'] is not None
		and not row['is_hidden']
		and (not cfg.lb_last_match_limit or (
			max(row['last_ranked_match_at'] or 0,
				additional_activity.get(row['user_id'], 0))
			+ cfg.lb_last_match_limit > now))
		and not (cfg.lb_min_matches and cfg.lb_min_matches > sum(
			(row['wins'], row['losses'], row['draws'])))
	]
