#!/usr/bin/env python3
"""Explore REAL ranked history to find genuinely interesting, recency-weighted
team-insight signals (vs. the current stale all-time head-to-head counts).

For the busiest channel, it replays the last N matches and, using only history
*before* each match, computes candidate insight types and prints concrete real
examples + a frequency summary, so we can see which signals actually fire (and
how often) before redesigning bot/team_insights.py.

Candidate types probed:
  - H2H recent streak   : opposing pair where one won the last K meetings
  - Teammate streak     : same-team pair on a K-game win/loss run together
  - Best/worst teammate : a player paired today with their highest/lowest win-rate teammate
  - Player form         : a player on a K-game personal win/loss streak
  - Revenge             : two players who were teammates last time, now enemies

Read-only. Reads DB_URI from config.cfg (same as the bot). Needs aiomysql.

Usage: python3 utils/insights_explore.py [N_MATCHES] [--channel ID]
"""
import argparse
import asyncio
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _THIS_DIR)
from db_helpers import create_pool  # noqa: E402


async def _fetchall(pool, sql, args=()):
	async with pool.acquire() as conn:
		async with conn.cursor() as cur:
			await cur.execute(sql, args)
			return await cur.fetchall()


# ── history structures ───────────────────────────────────────────────────
def load_history(rows):
	"""rows ordered by match_id asc -> (order, matches, nicks)."""
	matches, nicks, order = {}, {}, []
	for r in rows:
		mid = r["match_id"]
		if mid not in matches:
			matches[mid] = {"winner": r["winner"], "teams": {}}
			order.append(mid)
		matches[mid]["teams"][r["user_id"]] = int(r["team"])
		nicks[r["user_id"]] = r["nick"]
	return order, matches, nicks


def h2h_series(prior, matches, a, b):
	"""Ordered winners (a|b|None) for decisive games where a,b were opponents."""
	out = []
	for mid in prior:
		t = matches[mid]["teams"]
		if a in t and b in t and t[a] != t[b]:
			w = matches[mid]["winner"]
			out.append(None if w is None else (a if int(w) == t[a] else b))
	return out


def teammate_series(prior, matches, a, b):
	"""Ordered results (True win / False loss / None draw) for games a,b teamed."""
	out = []
	for mid in prior:
		t = matches[mid]["teams"]
		if a in t and b in t and t[a] == t[b]:
			w = matches[mid]["winner"]
			out.append(None if w is None else int(w) == t[a])
	return out


def form_series(prior, matches, p):
	out = []
	for mid in prior:
		t = matches[mid]["teams"]
		if p in t:
			w = matches[mid]["winner"]
			out.append(None if w is None else int(w) == t[p])
	return out


def trailing_streak(series):
	"""(length, value) of the trailing run of equal non-None values; a None
	(draw) at the end yields 0 (a draw breaks the narrative streak)."""
	clean = [v for v in series]
	if not clean or clean[-1] is None:
		return 0, None
	last = clean[-1]
	k = 0
	for v in reversed(clean):
		if v == last:
			k += 1
		else:
			break
	return k, last


def teammate_winrates(prior, matches, p, min_games):
	rec = {}
	for mid in prior:
		t = matches[mid]["teams"]
		if p not in t:
			continue
		w = matches[mid]["winner"]
		if w is None:
			continue
		won = int(w) == t[p]
		for q, tq in t.items():
			if q == p or tq != t[p]:
				continue
			wins, games = rec.get(q, (0, 0))
			rec[q] = (wins + (1 if won else 0), games + 1)
	return {q: (w, g) for q, (w, g) in rec.items() if g >= min_games}


def overall_winrate(prior, matches, p):
	wins = games = 0
	for mid in prior:
		t = matches[mid]["teams"]
		if p not in t:
			continue
		w = matches[mid]["winner"]
		if w is None:
			continue
		games += 1
		wins += int(int(w) == t[p])
	return wins, games


def last_shared(prior, matches, a, b):
	"""(mid, same_team, a_won) of the most recent game a,b both played, or None."""
	for mid in reversed(prior):
		t = matches[mid]["teams"]
		if a in t and b in t:
			w = matches[mid]["winner"]
			a_won = None if w is None else int(w) == t[a]
			return mid, t[a] == t[b], a_won
	return None


# ── per-match probe ──────────────────────────────────────────────────────
MIN_STREAK = 3
MIN_TEAMMATE_GAMES = 6
WR_SWING = 0.15   # best/worst teammate must move the player's WR by this much


def probe_match(order, matches, nicks, target, counters):
	prior = [m for m in order if m < target]
	t = matches[target]["teams"]
	t0 = [u for u, tm in t.items() if tm == 0]
	t1 = [u for u, tm in t.items() if tm == 1]
	if not t0 or not t1:
		return []
	nm = lambda u: nicks.get(u, str(u))  # noqa: E731
	lines = []

	# 1) H2H recent streak (opposing)
	for a in t0:
		for b in t1:
			s = h2h_series(prior, matches, a, b)
			k, who = trailing_streak(s)
			if k >= MIN_STREAK:
				counters["h2h_streak"] += 1
				lines.append(f"   [H2H streak] {nm(who)} has beaten {nm(b if who == a else a)} "
							 f"the last {k} meetings (series {len(s)}).")

	# 2) Teammate recent streak (same team)
	for team in (t0, t1):
		for i in range(len(team)):
			for j in range(i + 1, len(team)):
				a, b = team[i], team[j]
				s = teammate_series(prior, matches, a, b)
				k, val = trailing_streak(s)
				if k >= MIN_STREAK:
					counters["mate_streak"] += 1
					verb = "WON" if val else "LOST"
					lines.append(f"   [Mate streak] {nm(a)} & {nm(b)} have {verb} their last {k} "
								 f"games as teammates (of {len(s)}).")

	# 3) Best / worst teammate present today
	for team in (t0, t1):
		for p in team:
			ow, og = overall_winrate(prior, matches, p)
			if og < 10:
				continue
			base = ow / og
			wr = teammate_winrates(prior, matches, p, MIN_TEAMMATE_GAMES)
			mates_here = [q for q in team if q != p and q in wr]
			for q in mates_here:
				w, g = wr[q]
				r = w / g
				if r - base >= WR_SWING:
					counters["best_mate"] += 1
					lines.append(f"   [Best mate] {nm(p)} wins {round(r*100)}% with {nm(q)} "
								 f"(vs {round(base*100)}% overall, {g} games) — and they're teamed up.")
				elif base - r >= WR_SWING:
					counters["worst_mate"] += 1
					lines.append(f"   [Worst mate] {nm(p)} drops to {round(r*100)}% with {nm(q)} "
								 f"(vs {round(base*100)}% overall, {g} games) — paired again.")

	# 4) Player form streak
	for p in t0 + t1:
		s = form_series(prior, matches, p)
		k, val = trailing_streak(s)
		if k >= 4:
			counters["form"] += 1
			lines.append(f"   [Form] {nm(p)} is on a {k}-game {'win' if val else 'loss'} streak.")

	# 5) Revenge: teammates last shared game, now opponents
	for a in t0:
		for b in t1:
			ls = last_shared(prior, matches, a, b)
			if ls and ls[1]:  # were teammates last time
				counters["revenge"] += 1
				outcome = "won" if ls[2] else "lost"
				lines.append(f"   [Revenge] {nm(a)} & {nm(b)} {outcome} together last time they shared "
							 f"a game — today they're on opposite sides.")
	return lines


async def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("n", nargs="?", type=int, default=8)
	ap.add_argument("--channel", type=int, default=None)
	args = ap.parse_args()

	pool = await create_pool()
	if pool is None:
		return
	try:
		if args.channel:
			channel = args.channel
		else:
			busiest = await _fetchall(
				pool,
				"SELECT channel_id, COUNT(*) c FROM qc_matches WHERE ranked=1 "
				"GROUP BY channel_id ORDER BY c DESC LIMIT 1",
			)
			channel = busiest[0]["channel_id"]
		rows = await _fetchall(
			pool,
			"SELECT pm.match_id, pm.user_id, pm.nick, pm.team, m.winner "
			"FROM qc_player_matches pm JOIN qc_matches m "
			"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
			"WHERE pm.channel_id=%s AND m.ranked=1 AND pm.team IS NOT NULL "
			"ORDER BY pm.match_id ASC",
			(channel,),
		)
		order, matches, nicks = load_history(rows)
		print(f"Channel {channel}: {len(order)} ranked matches, {len(nicks)} players.\n")
		targets = order[-args.n:]
		counters = {k: 0 for k in
					["h2h_streak", "mate_streak", "best_mate", "worst_mate", "form", "revenge"]}
		for target in reversed(targets):
			lines = probe_match(order, matches, nicks, target, counters)
			print(f"=== match #{target} ===")
			print("\n".join(lines) if lines else "   (nothing surfaced)")
			print()
		print("── signal frequency across the sample ──")
		for k, v in counters.items():
			print(f"   {k:12s}: {v} hits over {len(targets)} matches "
				  f"({v/len(targets):.1f}/match)")
	finally:
		pool.close()
		await pool.wait_closed()


if __name__ == "__main__":
	asyncio.run(main())
