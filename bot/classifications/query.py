# -*- coding: utf-8 -*-
"""Read side for /insights. Pure roster() + winners_vs_losers() over matched player-games;
fetch_results() pulls them (with their full metrics dict) from the derived-global
game_labels table.

Stage 5c moved this read off cls_results/cls_result_metrics. The two tables held the same
facts in a shape this command had to re-derive on every call: one row per matched trigger
plus a second table of loose (metric, value) rows to re-associate with it. game_labels
stores one row per (game, player, label) with its evidence already gathered, so the metrics
dict is a column rather than a second query and a join in Python."""
import json
import time

from nammaoe2bot.runtime.database import db


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


def metrics_of(label_row):
	"""One label row's evidence as a dict, whether it arrived as the MEDIUMTEXT JSON string a
	SELECT returns or as the dict label_rows built.

	A malformed blob RAISES rather than degrading to {}, the same call as
	bot/derived/rollups.py's _decode and for a sharper reason here: this dict feeds the
	winners-vs-losers averages, and a row that quietly contributes no metric is not a
	missing row — it is a row silently dropped from a denominator nobody was told about."""
	raw = label_row.get("evidence")
	if isinstance(raw, str):
		raw = json.loads(raw)
	return raw or {}


async def fetch_results(use_case, days, profile_ids=None):
	"""Matched player-games for `use_case` in the window, each with its full metrics dict.

	`use_case` is a game_labels label. Its KIND is looked up rather than trusted: a key
	outside both of game_labels' allowlists was never stored (luck_baseline is the live
	example — it fires for every player in every valid Nomad game and is deliberately
	stored nowhere), so there is nothing to select and no query worth issuing. The kind is
	then constrained in the SQL as well, so this read can only ever return the category the
	allowlist assigned to the key it was asked for.

	Three tables, each for exactly one thing it alone can answer:
	  game_labels    — which (game, player) earned this label, when, and its evidence.
	  game_stats     — the profile behind that slot and whether it won. game_labels
	                   deliberately stores neither (see bot/derived/__init__.py), and this
	                   is the join that gets them: its PK is (replay_match_id,
	                   player_number), exactly game_labels' grain minus the label, so the
	                   join can neither drop nor duplicate a row.
	  replay_players — the in-game name to print. Joined on (match, profile_id), its own
	                   PK. Same source and same reason as bot/derived/refresh.py's board
	                   queries: the name on a leaderboard row has to describe the account
	                   that played the game, not a Discord nickname that moves with a guild
	                   membership."""
	from bot.derived.game_labels import kind_for

	kind = kind_for(use_case)
	if kind is None:
		return []
	since = int(time.time()) - days * 86400
	args = [use_case, kind, since]
	pid_clause = ""
	if profile_ids:
		pid_clause = " AND gs.profile_id IN ({})".format(", ".join(["%s"] * len(profile_ids)))
		args.extend(profile_ids)
	res = await db.fetchall(
		"SELECT gl.replay_match_id, gl.player_number, gl.evidence, "
		"gs.profile_id, gs.winner, rp.identity "
		"FROM game_labels gl "
		"JOIN game_stats gs ON gs.replay_match_id=gl.replay_match_id "
		"AND gs.player_number=gl.player_number "
		"LEFT JOIN replay_players rp ON rp.replay_match_id=gs.replay_match_id "
		"AND rp.profile_id=gs.profile_id "
		"WHERE gl.label=%s AND gl.kind=%s AND gl.played_at >= %s" + pid_clause, args)
	return [
		{
			"replay_match_id": r["replay_match_id"],
			"player_number": r["player_number"],
			"profile_id": r["profile_id"],
			"identity": r.get("identity"),
			"winner": r["winner"],
			"metrics": metrics_of(r),
		}
		for r in (res or [])
	]


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
