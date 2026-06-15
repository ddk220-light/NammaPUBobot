# -*- coding: utf-8 -*-
"""Post a fun "what the civs say" wrap-up once a finished match's civs land.

The bot doesn't know which civs were played until bot/civ_matcher.py resolves a
completed match to its aoe2companion game (a few minutes after /report) and
writes each player's civ to qc_match_civs. At that moment we know who won, who
lost, and what everyone played — so this module ties the result to the civ
meta and calls out the interesting bits:

  * "No wonder X won — they had <civ>, a top-3 win-rate civ."
  * "X won with <civ> (one of the weakest picks) — pure skill."  (an upset)
  * "X had <civ> (top tier) and *still* lost."                    (a choke)
  * Team civ-pool comparison: did the meta favour the winners, or was it an
    underdog win with the weaker pool?

Civ win-rates come from bot/civ_stats (live qc_match_civs aggregate, seed CSV
fallback). Design mirrors bot/team_insights.py: one cheap data pull, pure
Python analysis (unit-testable without a DB), and build_post_game_embed returns
an Embed or None. Heavy imports (nextcord / civ_stats / db) are deferred so the
pure helpers import cleanly under CI.
"""
import math  # noqa: F401  (kept for parity / future scoring tweaks)
import random

# ── Tunables ─────────────────────────────────────────────────────────────
# Civs need a decent sample before "win-rate" means anything; that gate lives
# in civ_stats (MIN_CIV_GAMES). Tiering on top of that only makes sense once
# there are enough qualifying civs to have a top/bottom at all.
MIN_CIVS_FOR_TIERS = 10
TOP_RANKS = 3       # "top-3 win-rate civ"
TOP_PCT = 0.15      # ...or inside the top 15% by rank
BOT_PCT = 0.15      # bottom 15% counts as a weak pick
TEAM_GAP_MIN = 0.03  # min avg-winrate gap between team civ pools to comment on
MAX_BULLETS = 4


# ── Pure analysis helpers (no DB / Discord — unit tested) ────────────────
def _civ_index(civ_data):
	"""Rank civs by overall win-rate. ``civ_data`` is civ_stats' shape
	(``{civ: {"civ","games","winrate"}}``). Returns a lowercase-keyed lookup
	``{civ: {"civ","rank","total","winrate","games"}}`` (rank 1 = best)."""
	ranked = sorted(civ_data.values(), key=lambda c: c["winrate"], reverse=True)
	total = len(ranked)
	idx = {}
	for i, c in enumerate(ranked):
		idx[c["civ"].lower()] = {
			"civ": c["civ"], "rank": i + 1, "total": total,
			"winrate": c["winrate"], "games": c["games"],
		}
	return idx


def _is_top(info):
	return info["rank"] <= TOP_RANKS or (info["rank"] / info["total"]) <= TOP_PCT


def _is_bottom(info):
	return info["rank"] > info["total"] - TOP_RANKS or (info["rank"] / info["total"]) >= (1 - BOT_PCT)


def _topness(info):
	"""0..1, higher the closer to the #1 civ."""
	return (info["total"] - info["rank"]) / max(info["total"] - 1, 1)


def _bottomness(info):
	"""0..1, higher the closer to the worst civ."""
	return (info["rank"] - 1) / max(info["total"] - 1, 1)


def _collect_observations(players, winner, civ_index, team_names=None):
	"""Build scored observations. ``players`` is a list of
	``{"nick","civ","team"}`` (team 0/1). ``winner`` is the winning team idx."""
	team_names = team_names or {0: "Alpha", 1: "Beta"}
	obs = []

	# Tier callouts only make sense with enough qualifying civs.
	tiers_ok = False
	for p in players:
		info = civ_index.get((p.get("civ") or "").lower())
		if info:
			tiers_ok = info["total"] >= MIN_CIVS_FOR_TIERS
			break

	for p in players:
		if p.get("team") not in (0, 1) or not tiers_ok:
			continue
		info = civ_index.get((p.get("civ") or "").lower())
		if not info:
			continue
		win = p["team"] == winner
		if _is_top(info):
			if win:
				obs.append({"type": "winner_top", "nick": p["nick"], "info": info,
				            "score": 0.8 + 1.2 * _topness(info)})
			else:
				obs.append({"type": "loser_top", "nick": p["nick"], "info": info,
				            "score": 1.2 + 1.3 * _topness(info)})
		elif _is_bottom(info):
			if win:
				obs.append({"type": "winner_bottom", "nick": p["nick"], "info": info,
				            "score": 1.2 + 1.5 * _bottomness(info)})
			else:
				obs.append({"type": "loser_bottom", "nick": p["nick"], "info": info,
				            "score": 0.4 + 0.6 * _bottomness(info)})

	# Team civ-pool comparison (independent of tiers — just average win-rates).
	if winner in (0, 1):
		team_wr = {0: [], 1: []}
		for p in players:
			if p.get("team") not in (0, 1):
				continue
			info = civ_index.get((p.get("civ") or "").lower())
			if info:
				team_wr[p["team"]].append(info["winrate"])
		if team_wr[0] and team_wr[1]:
			win_avg = sum(team_wr[winner]) / len(team_wr[winner])
			lose_avg = sum(team_wr[1 - winner]) / len(team_wr[1 - winner])
			gap = win_avg - lose_avg
			common = {"winner": winner, "win_avg": win_avg, "lose_avg": lose_avg,
			          "gap": gap, "team_names": team_names}
			if gap >= TEAM_GAP_MIN:
				obs.append({"type": "team_favored", "score": 0.7 + min(gap, 0.2) * 4, **common})
			elif gap <= -TEAM_GAP_MIN:
				obs.append({"type": "team_upset", "score": 1.4 + min(-gap, 0.2) * 4, **common})

	return obs


def _select(obs, limit=MAX_BULLETS):
	"""Highest score first; at most one line per player and one team-pool line."""
	chosen = []
	used_players = set()
	used_team = False
	for c in sorted(obs, key=lambda c: c["score"], reverse=True):
		if len(chosen) >= limit:
			break
		if c["type"].startswith("team_"):
			if used_team:
				continue
			used_team = True
		else:
			if c["nick"] in used_players:
				continue
			used_players.add(c["nick"])
		chosen.append(c)
	return chosen


# ── Phrasing (pure, but cosmetic — not unit tested) ──────────────────────
def _tier_desc(info):
	"""A descriptor that folds in rank and win-rate, e.g. "a top-3 win-rate
	civ (#2 of 20, **55%**)" — so callers don't append a second (...)."""
	rank, total = info["rank"], info["total"]
	wr = round(info["winrate"] * 100)
	if rank == 1:
		return f"the #1 win-rate civ (**{wr}%**)"
	if rank <= TOP_RANKS:
		return f"a top-{TOP_RANKS} win-rate civ (#{rank} of {total}, **{wr}%**)"
	if rank / total <= TOP_PCT:
		return f"a top-tier civ (#{rank} of {total}, **{wr}%**)"
	if rank == total:
		return f"the lowest win-rate civ there is (#{total} of {total}, **{wr}%**)"
	return f"one of the weakest civs (#{rank} of {total}, **{wr}%**)"


def _phrase(c):
	if c["type"] in ("winner_top", "loser_top", "winner_bottom", "loser_bottom"):
		info = c["info"]
		civ = info["civ"]
		desc = _tier_desc(info)
		nick = f"**{c['nick']}**"
		if c["type"] == "winner_top":
			opts = [
				f"🏆 No wonder {nick} won — **{civ}** is {desc}.",
				f"📈 {nick} rode **{civ}** to the win — {desc}.",
			]
		elif c["type"] == "loser_top":
			opts = [
				f"😵 {nick} had **{civ}** — {desc} — and *still* lost. Brutal.",
				f"🎭 Wasted gift: {nick}'s **{civ}** is {desc}, yet they still went down.",
			]
		elif c["type"] == "winner_bottom":
			opts = [
				f"💪 Pure skill: {nick} won with **{civ}** — {desc}.",
				f"🔥 {nick} dragged **{civ}** to a win — {desc}. Respect.",
			]
		else:  # loser_bottom
			opts = [
				f"🪨 {nick} never stood a chance — **{civ}** is {desc}.",
				f"📉 {nick}'s **{civ}** lived down to its billing — {desc}.",
			]
		return random.choice(opts)

	# team comparison
	names = c["team_names"]
	w = c["winner"]
	win_name = names.get(w, "The winners")
	lose_name = names.get(1 - w, "the losers")
	wa = round(c["win_avg"] * 100)
	la = round(c["lose_avg"] * 100)
	if c["type"] == "team_favored":
		opts = [
			f"⚖️ The meta did its job — {win_name}'s civs averaged **{wa}%** vs {lose_name}'s **{la}%**.",
			f"🎯 {win_name} were favoured on paper (**{wa}%** avg civ win-rate to **{la}%**) and delivered.",
		]
	else:
		opts = [
			f"🐉 Upset! {win_name} won with the *weaker* pool — **{wa}%** avg civ win-rate vs {lose_name}'s **{la}%**.",
			f"🧗 {win_name} beat the odds: their civs averaged just **{wa}%** vs {lose_name}'s **{la}%**.",
		]
	return random.choice(opts)


# ── DB read + embed building (deferred heavy imports) ────────────────────
async def _team_names(channel_id, bot_match_id):
	from core.database import db
	row = await db.fetchone(
		"SELECT alpha_name, beta_name FROM qc_matches WHERE match_id=%s AND channel_id=%s",
		[bot_match_id, channel_id]
	)
	if row:
		return {0: row.get("alpha_name") or "Alpha", 1: row.get("beta_name") or "Beta"}
	return {0: "Alpha", 1: "Beta"}


async def build_post_game_embed(channel_id, bot_match_id, player_civ_rows, winner):
	"""Civ-vs-result wrap-up for a finished match.

	``player_civ_rows`` is the qc_match_civs rows just recorded (dicts with
	``nick``/``civ``/``team``). ``winner`` is the winning team idx (0/1) or
	None. Returns an ``Embed`` or ``None`` (unranked, too little civ data, or
	nothing interesting to say).
	"""
	if winner not in (0, 1):
		return None

	players = []
	for r in player_civ_rows:
		team = r.get("team")
		civ = (r.get("civ") or "").strip()
		if team in (0, 1) and civ:
			players.append({"nick": r.get("nick") or "someone", "civ": civ, "team": int(team)})
	if len(players) < 2:
		return None

	from bot.civ_stats import civ_elo_from_db, get_all_civs
	civ_data = await civ_elo_from_db() or get_all_civs()
	if not civ_data:
		return None
	civ_index = _civ_index(civ_data)

	team_names = await _team_names(channel_id, bot_match_id)
	chosen = _select(_collect_observations(players, winner, civ_index, team_names))
	if not chosen:
		return None

	from nextcord import Colour, Embed

	lines = [_phrase(c) for c in chosen]
	title = random.choice([
		"🏁 Post-Game: What the Civs Say",
		"🔎 The Civ Report",
		"📊 Civ Breakdown",
		"🧐 Result vs Meta",
	])
	embed = Embed(title=title, colour=Colour(0x9b59b6), description="\n\n".join(lines))
	embed.set_footer(text="Civ win-rates from community match history · just for fun")
	return embed
