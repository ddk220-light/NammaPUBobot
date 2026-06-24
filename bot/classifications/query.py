# -*- coding: utf-8 -*-
"""Read side for /insights. Pure roster() + winners_vs_losers() over matched player-games;
fetch_results() pulls them (with their full metrics dict) from cls_results + cls_result_metrics."""
import time

from core.database import db


def roster(results):
	"""results: [{identity, profile_id, winner}]. -> leaderboard rows sorted by games desc."""
	by = {}
	for r in results:
		p = by.setdefault(r["profile_id"], {"identity": r["identity"], "games": 0, "wins": 0, "known": 0})
		p["games"] += 1
		if r["winner"] in (0, 1, True, False) and r["winner"] is not None:
			p["known"] += 1
			if r["winner"]:
				p["wins"] += 1
	rows = list(by.values())
	for p in rows:
		p["win_pct"] = round(100 * p["wins"] / p["known"]) if p["known"] else None
	rows.sort(key=lambda p: (-p["games"], p["identity"] or ""))
	return rows


def _avg(vals):
	vals = [v for v in vals if v is not None]
	return sum(vals) / len(vals) if vals else None


def winners_vs_losers(results, factor_specs):
	"""For each spec metric, average its value over winners vs losers. Each result carries a
	'metrics' dict. Games with unknown result (winner None) are excluded from both sides."""
	W = [r for r in results if r["winner"] in (1, True)]
	L = [r for r in results if r["winner"] in (0, False)]
	factors = []
	for s in factor_specs:
		m = s["metric"]
		factors.append({"metric": m, "label": s["label"], "kind": s["kind"],
		                "winners": _avg([r["metrics"].get(m) for r in W]),
		                "losers": _avg([r["metrics"].get(m) for r in L])})
	return {"n_winners": len(W), "n_losers": len(L), "factors": factors}


async def fetch_results(use_case, days, profile_ids=None):
	"""Matched player-games for `use_case` in the window, each with its full metrics dict."""
	since = int(time.time()) - days * 86400
	args = [use_case, since]
	pid_clause = ""
	if profile_ids:
		pid_clause = " AND profile_id IN ({})".format(", ".join(["%s"] * len(profile_ids)))
		args.extend(profile_ids)
	res = await db.fetchall(
		"SELECT aoe2_match_id, player_number, profile_id, identity, winner FROM cls_results "
		"WHERE `key`=%s AND played_at >= %s" + pid_clause, args)
	res = [dict(r) for r in (res or [])]
	if not res:
		return []
	mids = sorted({r["aoe2_match_id"] for r in res})
	mets = await db.fetchall(
		"SELECT aoe2_match_id, player_number, metric, value FROM cls_result_metrics "
		"WHERE `key`=%s AND aoe2_match_id IN ({})".format(", ".join(["%s"] * len(mids))),
		[use_case] + mids)
	mmap = {}
	for m in (mets or []):
		mmap.setdefault((m["aoe2_match_id"], m["player_number"]), {})[m["metric"]] = m["value"]
	for r in res:
		r["metrics"] = mmap.get((r["aoe2_match_id"], r["player_number"]), {})
	return res


async def resolve_profile_ids(user_id):
	"""Reuse the replay-stats resolver: discord user_id -> the AoE2 profile_ids linked to it."""
	from bot.replay_stats import query as rs_query
	return await rs_query.resolve_profile_ids(user_id)


def leaderboard_line(p):
	return "{:<18} {:>3} {:>3} {:>5}".format(
		(p["identity"] or "?")[:18], p["games"], p["wins"],
		("{}%".format(p["win_pct"]) if p["win_pct"] is not None else "-"))


def leaderboard_text(board, max_chars):
	"""Render the roster into ONE ```code block``` whose total length stays <= max_chars. If it
	doesn't all fit, stop and append a '...and N more' line. Returns (text, n_hidden)."""
	header = "{:<18} {:>3} {:>3} {:>5}".format("player", "g", "w", "win%")
	lines, used, shown = [header], len(header) + 8, 0   # +8 leaves room for the ``` fences
	for p in board:
		line = leaderboard_line(p)
		if used + len(line) + 1 > max_chars:
			break
		lines.append(line)
		used += len(line) + 1
		shown += 1
	hidden = len(board) - shown
	if hidden > 0:
		lines.append("...and {} more".format(hidden))
	return "```\n" + "\n".join(lines) + "\n```", hidden
