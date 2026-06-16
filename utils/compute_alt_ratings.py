#!/usr/bin/env python3
"""Generate data/alt_ratings.csv — the "alternate Elo" snapshot behind the
/leaderboard_alternate command.

WHAT IT IS:
  A what-if replay. The bot ran a blanket weekly uncertainty (sigma) decay that
  added +rating_deviation_decay to EVERY player every week from ~2025-11-17,
  inflating volatility and never letting active players settle to the sigma floor
  (see bot/stats/decay.py). This script replays every ranked match forward from
  that branch point under TWO decay policies:
    - OLD: the blanket weekly sigma decay (reproduces today's live ratings)
    - NEW: decay gated on 1-month inactivity (bot.stats.decay.compute_decay)
  and reports, per player, alt_rating = current + (new - old). Taking the
  difference of the two replays cancels any common replay-engine error, so the
  reported shift is the pure policy effect, anchored to each player's REAL rating.

VALIDATION:
  The OLD replay should reproduce the live qc_players ratings. The script prints
  the control error (median should be a few points) so you can trust the output
  before it is shown to players.

  Reads DB_URI from config.cfg (gitignored). TrueSkill params and decay settings
  are read from the live qc_configs row so the replay matches production.

Usage:
    python utils/compute_alt_ratings.py            # writes data/alt_ratings.csv
"""
import os
import sys
import csv
import json
import asyncio
import datetime as dt
import importlib.util
import statistics

import trueskill

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db_helpers import create_pool  # noqa: E402

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT_PATH = os.path.join(PROJECT_ROOT, "data", "alt_ratings.csv")
# When the blanket weekly-decay regime began. We look for the first decay tick
# on or after this date and branch there.
REGIME_PROBE = dt.datetime(2025, 11, 1, tzinfo=dt.UTC)

# Load the real new-policy decision function without importing the heavy `bot`
# package (which would pull in nextcord et al.).
_spec = importlib.util.spec_from_file_location(
	"decay", os.path.join(PROJECT_ROOT, "bot", "stats", "decay.py")
)
_decay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_decay)
compute_decay = _decay.compute_decay


async def _fetchall(pool, sql, args=()):
	async with pool.acquire() as conn:
		async with conn.cursor() as cur:
			await cur.execute(sql, args)
			return await cur.fetchall()


async def main():
	pool = await create_pool()
	if pool is None:
		return 1

	# --- read the live rating config so the replay matches production ---
	cfgrow = await _fetchall(pool, "SELECT cfg_data FROM qc_configs LIMIT 1")
	cfg = json.loads(cfgrow[0]["cfg_data"])
	CH = (await _fetchall(pool, "SELECT channel_id FROM qc_configs LIMIT 1"))[0]["channel_id"]
	INIT = cfg["rating_deviation"]
	MINDEV = cfg["rating_min_deviation"]
	RDEC = cfg["rating_decay"]
	DDEC = cfg["rating_deviation_decay"]
	DRAW = (cfg.get("rating_draw_bonus") or 0) / 100.0
	RANKS = [r["rating"] for r in cfg["ranks"] if r["rating"]]
	BETA = int(INIT / 2)
	TAU = int(INIT / 100)
	TS = trueskill.TrueSkill(mu=cfg["rating_initial"], sigma=INIT, beta=BETA, tau=TAU)
	WEEK = 7 * 86400
	MONTH = _decay.MONTH

	# --- branch point: first weekly decay tick on/after the regime probe ---
	row = await _fetchall(
		pool,
		"SELECT MIN(at) a FROM qc_rating_history WHERE channel_id=%s AND reason='inactivity rating decay' AND at>=%s",
		(CH, int(REGIME_PROBE.timestamp())),
	)
	T0 = row[0]["a"]

	# --- state just before T0 + last match time per player ---
	pre = await _fetchall(
		pool,
		"SELECT user_id,rating_before,rating_change,deviation_before,deviation_change,at,match_id "
		"FROM qc_rating_history WHERE channel_id=%s AND at<%s ORDER BY at",
		(CH, T0),
	)
	init, lastplay = {}, {}
	for r in pre:
		init[r["user_id"]] = (
			r["rating_before"] + (r["rating_change"] or 0),
			r["deviation_before"] + (r["deviation_change"] or 0),
		)
		if r["match_id"] is not None:
			lastplay[r["user_id"]] = r["at"]

	# entrant lazy-init: actual pre-match state at a player's first post-T0 match
	fi = await _fetchall(
		pool,
		"SELECT match_id,user_id,rating_before,deviation_before FROM qc_rating_history "
		"WHERE channel_id=%s AND match_id IS NOT NULL AND at>=%s",
		(CH, T0),
	)
	firstinfo = {(r["match_id"], r["user_id"]): (r["rating_before"], r["deviation_before"]) for r in fi}

	# matches after T0: roster + winner + time
	mr = await _fetchall(
		pool,
		"SELECT pm.match_id,pm.user_id,pm.team,mm.winner,mm.at "
		"FROM qc_player_matches pm JOIN qc_matches mm "
		"ON mm.match_id=pm.match_id AND mm.channel_id=pm.channel_id "
		"WHERE pm.channel_id=%s AND mm.at>=%s",
		(CH, T0),
	)
	matches = {}
	for r in mr:
		mm = matches.setdefault(r["match_id"], {"at": r["at"], "winner": r["winner"], "players": []})
		mm["players"].append((r["user_id"], r["team"]))

	# decay ticks -> dedupe to one per ISO week (the bot fires once, next Monday)
	tk = await _fetchall(
		pool,
		"SELECT DISTINCT at FROM qc_rating_history WHERE channel_id=%s AND reason='inactivity rating decay' AND at>=%s ORDER BY at",
		(CH, T0),
	)
	byweek = {}
	for r in tk:
		k = dt.datetime.fromtimestamp(r["at"], dt.UTC).isocalendar()[:2]
		byweek.setdefault(k, r["at"])
	ticks = sorted(byweek.values())

	# admin events (seeding / penalty / snap): applied as absolute post-values
	admin = await _fetchall(
		pool,
		"SELECT user_id,at,rating_before,rating_change,deviation_before,deviation_change "
		"FROM qc_rating_history WHERE channel_id=%s AND at>=%s AND match_id IS NULL AND reason!='inactivity rating decay'",
		(CH, T0),
	)

	actual = {
		r["user_id"]: r
		for r in await _fetchall(
			pool,
			"SELECT user_id,nick,rating,deviation,wins,losses,draws FROM qc_players "
			"WHERE channel_id=%s AND rating IS NOT NULL",
			(CH,),
		)
	}
	pool.close()
	await pool.wait_closed()

	events = (
		[(m["at"], 0, "m", mid, m) for mid, m in matches.items()]
		+ [(t, 1, "d", None, None) for t in ticks]
		+ [(a["at"], 2, "a", None, a) for a in admin]
	)
	events.sort(key=lambda e: (e[0], e[1]))

	def run(policy):
		S = {u: {"mu": a, "sigma": b, "last": lastplay.get(u)} for u, (a, b) in init.items()}

		def ens(u, mid):
			if u not in S:
				rb, db = firstinfo.get((mid, u), (TS.mu, INIT))
				S[u] = {"mu": rb, "sigma": db, "last": None}

		for at, _, kind, mid, pl in events:
			if kind == "m":
				for u, _t in pl["players"]:
					ens(u, mid)
				teams = {0: [], 1: []}
				for u, t in pl["players"]:
					teams[t].append(u)
				draw = pl["winner"] is None
				win, lose = (teams[0], teams[1]) if draw else (teams[pl["winner"]], teams[1 - pl["winner"]])
				if not win or not lose:
					for u, _t in pl["players"]:
						S[u]["last"] = at
					continue
				gw = [TS.create_rating(mu=S[u]["mu"], sigma=min(INIT, S[u]["sigma"])) for u in win]
				gl = [TS.create_rating(mu=S[u]["mu"], sigma=min(INIT, S[u]["sigma"])) for u in lose]
				nw, nl = TS.rate((gw, gl), ranks=[0, 0] if draw else [0, 1])
				for u, res in list(zip(win, nw)) + list(zip(lose, nl)):
					raw = res.mu - S[u]["mu"]
					dsig = res.sigma - S[u]["sigma"]
					rc = raw + abs(raw) * DRAW if draw else raw
					S[u]["mu"] = max(0, round(S[u]["mu"] + rc))
					S[u]["sigma"] = max(MINDEV, round(S[u]["sigma"] + dsig))
					S[u]["last"] = at
			elif kind == "d":
				for s in S.values():
					if policy == "new":
						s["mu"], s["sigma"] = compute_decay(
							s["mu"], s["sigma"], s["last"], at, RDEC, DDEC, INIT, RANKS, MONTH
						)
					else:
						s["sigma"] = min(INIT, s["sigma"] + DDEC)
						fl = max([x for x in RANKS if x <= s["mu"]] + [0])
						if fl and s["last"] is not None and s["last"] < at - WEEK:
							s["mu"] = max(fl, s["mu"] - RDEC)
			else:
				a = pl
				u = a["user_id"]
				if u not in S:
					S[u] = {"mu": TS.mu, "sigma": INIT, "last": None}
				S[u]["mu"] = max(0, a["rating_before"] + (a["rating_change"] or 0))
				S[u]["sigma"] = a["deviation_before"] + (a["deviation_change"] or 0)
		return S

	old = run("old")
	new = run("new")

	# control: old replay should reproduce live ratings
	errs = [abs(old[u]["mu"] - actual[u]["rating"]) for u in actual if u in old]
	branch_date = dt.datetime.fromtimestamp(T0, dt.UTC).date().isoformat()
	computed_date = dt.datetime.now(dt.UTC).date().isoformat()
	print(f"branch={branch_date}  matches={len(matches)} ticks={len(ticks)} admin={len(admin)}")
	print(f"CONTROL rating |err| median={statistics.median(errs):.1f} mean={statistics.mean(errs):.1f} max={max(errs)}")

	out = []
	for u, r in actual.items():
		if u not in old or u not in new:
			continue
		delta = new[u]["mu"] - old[u]["mu"]
		out.append({
			"user_id": u,
			"nick": r["nick"],
			"current_rating": r["rating"],
			"alt_rating": max(0, r["rating"] + delta),
			"alt_deviation": new[u]["sigma"],
			"games": r["wins"] + r["losses"] + r["draws"],
			"branch_date": branch_date,
			"computed_date": computed_date,
		})
	out.sort(key=lambda x: -x["alt_rating"])

	os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
	with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
		w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
		w.writeheader()
		w.writerows(out)
	shifted = sum(1 for x in out if x["alt_rating"] != x["current_rating"])
	print(f"wrote {OUT_PATH}: {len(out)} players, {shifted} shifted")
	return 0


if __name__ == "__main__":
	sys.exit(asyncio.run(main()))
