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


def _winrate(wins, losses):
	decided = int(wins or 0) + int(losses or 0)
	return round(int(wins or 0) * 100 / decided) if decided else None


def aggregate_tag_rows_by_player(tag_rows):
	"""Collapse per-player/per-tag rows into one all-tags row per player."""
	by_user = {}
	for row in tag_rows:
		uid = int(row["user_id"])
		cur = by_user.setdefault(uid, {
			"user_id": row["user_id"],
			"nick": row["nick"],
			"avatar": row.get("avatar"),
			"tag_key": "all",
			"tag_label": "All tags",
			"tag_type": "aggregate",
			"games": 0,
			"tag_games": 0,
			"parsed_games": row.get("parsed_games") or 0,
			"wins": 0,
			"losses": 0,
			"winrate": None,
			"tag_rate": 0,
			"avg_impact": None,
			"score": 0,
			"last_tagged_at": None,
			"top_tags": [],
			"_impact_sum": 0,
			"_impact_count": 0,
		})
		cur["tag_games"] += int(row.get("tag_games") or 0)
		cur["wins"] += int(row.get("wins") or 0)
		cur["losses"] += int(row.get("losses") or 0)
		cur["parsed_games"] = max(int(cur["parsed_games"] or 0), int(row.get("parsed_games") or 0))
		cur["last_tagged_at"] = max(cur["last_tagged_at"] or 0, int(row.get("last_tagged_at") or 0)) or None
		if row.get("avg_impact") is not None and row.get("tag_games"):
			cur["_impact_sum"] += float(row["avg_impact"]) * int(row["tag_games"])
			cur["_impact_count"] += int(row["tag_games"])
		cur["top_tags"].append({
			"key": row.get("tag_key"),
			"label": row.get("tag_label"),
			"type": row.get("tag_type"),
			"games": int(row.get("tag_games") or 0),
			"score": row.get("score"),
		})
	out = []
	for row in by_user.values():
		tag_games = int(row["tag_games"] or 0)
		row["games"] = tag_games
		row["winrate"] = _winrate(row["wins"], row["losses"])
		row["tag_rate"] = min(100, round(tag_games * 100 / row["parsed_games"], 1)) if row["parsed_games"] else 0
		row["avg_impact"] = round(row["_impact_sum"] / row["_impact_count"], 1) if row["_impact_count"] else None
		row["score"] = tag_leaderboard_score(
			tag_games, row["wins"], row["losses"], row["tag_rate"], row.get("avg_impact"))
		row["top_tags"] = sorted(row["top_tags"], key=lambda t: (-t["games"], -(t.get("score") or 0), t["label"]))[:4]
		row.pop("_impact_sum", None)
		row.pop("_impact_count", None)
		out.append(row)
	return sorted(out, key=lambda r: (-r["score"], -r["tag_games"], -(r["winrate"] or 0), r["nick"].lower()))
