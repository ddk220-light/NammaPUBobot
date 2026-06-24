# -*- coding: utf-8 -*-
"""Read aggregations for /classification. summarize() is pure (unit-tested); fetch_games()
pulls a classification's matched player-games (with the two metrics summarize needs) from
cls_results + cls_result_metrics over a date window."""
import time

from core.database import db

_BUCKETS = [(1, 3, "1-3"), (4, 10, "4-10"), (11, 20, "11-20"), (21, 10 ** 9, "21+")]


def _winrate(games):
	known = [g for g in games if g.get("winner") in (0, 1, True, False) and g.get("winner") is not None]
	wins = sum(1 for g in known if g["winner"]) if known else 0
	return {"wins": wins, "known": len(known), "rate": round(wins / len(known), 3) if known else 0.0}


def summarize(games):
	"""games: list of dicts {identity, profile_id, winner(bool|None), archers_pre_castle(float),
	fletching_pre_castle(float 0/1)}. Returns the report structure the command renders."""
	by_player = {}
	for g in games:
		p = by_player.setdefault(g["profile_id"], {"identity": g["identity"], "rows": []})
		p["rows"].append(g)

	top = []
	for pid, p in by_player.items():
		wr = _winrate(p["rows"])
		top.append({"identity": p["identity"], "profile_id": pid, "games": len(p["rows"]),
		            "wins": wr["wins"], "known": wr["known"], "rate": wr["rate"]})
	top.sort(key=lambda t: (-t["games"], t["identity"]))

	by_commit = []
	for lo, hi, label in _BUCKETS:
		sub = [g for g in games if lo <= (g.get("archers_pre_castle") or 0) <= hi]
		if sub:
			wr = _winrate(sub)
			by_commit.append({"bucket": label, "games": len(sub), **wr})

	with_f = [g for g in games if (g.get("fletching_pre_castle") or 0) >= 1]
	without_f = [g for g in games if (g.get("fletching_pre_castle") or 0) < 1]

	return {
		"n_games": len(games),
		"n_players": len(by_player),
		"overall": _winrate(games),
		"by_commit": by_commit,
		"by_fletching": {"with": _winrate(with_f), "without": _winrate(without_f)},
		"top_players": top[:10],
	}


async def fetch_games(key, days, profile_ids=None):
	"""Matched player-games for `key` in the last `days`, with the archers_pre_castle and
	fletching_pre_castle metrics joined in. profile_ids: optional filter (a single player)."""
	since = int(time.time()) - days * 86400
	args = [key, since]
	pid_clause = ""
	if profile_ids:
		pid_clause = " AND r.profile_id IN ({})".format(", ".join(["%s"] * len(profile_ids)))
		args.extend(profile_ids)
	rows = await db.fetchall(
		"SELECT r.aoe2_match_id, r.player_number, r.profile_id, r.identity, r.winner, "
		"MAX(CASE WHEN m.metric='archers_pre_castle' THEN m.value END) AS archers_pre_castle, "
		"MAX(CASE WHEN m.metric='fletching_pre_castle' THEN m.value END) AS fletching_pre_castle "
		"FROM cls_results r LEFT JOIN cls_result_metrics m "
		"ON m.`key`=r.`key` AND m.aoe2_match_id=r.aoe2_match_id AND m.player_number=r.player_number "
		"WHERE r.`key`=%s AND r.played_at >= %s" + pid_clause +
		" GROUP BY r.aoe2_match_id, r.player_number, r.profile_id, r.identity, r.winner", args)
	return [dict(r) for r in (rows or [])]
