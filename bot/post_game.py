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

from bot.replay_stats import scoring

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
MAX_ANALYSIS_LINES = 6
MAX_CARD_TAGS = 3


def _clip(text, limit=28):
	text = str(text or "?")
	return text if len(text) <= limit else text[:max(1, limit - 1)] + "…"


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


def _impact_payload(row, group):
	scores = scoring.impact_scores(row, group)
	return {
		"nick": row.get("nick") or row.get("identity") or str(row.get("user_id") or ""),
		"civ": row.get("civ"),
		"team": int(row["bot_team"]) if row.get("bot_team") in (0, 1, "0", "1") else None,
		"result": row.get("result") or ("W" if row.get("winner") else "L" if row.get("winner") is not None else None),
		"impact_score": scores["impact"],
		"army_score": scores["army"],
		"eco_score": scores["eco"],
		"timing_score": scores["timing"],
		"recovery_score": scores["reboom"],
		"impact_tags": scoring.impact_tag_names_with_fallback(scores, row)[:3],
		"strength_glyphs": scoring.strength_glyphs(scores),
	}


def _tag_word(tags):
	if not tags:
		return "solid fundamentals"
	if "Boom carry" in tags:
		return "greedy boom into castle-age invoice"
	if "Low-eco pressure" in tags:
		return "all-in pressure, farms optional"
	if "Army pressure" in tags:
		return "map control and villager anxiety"
	if "Eco carry" in tags:
		return "eco carry with banker energy"
	if "Timing edge" in tags:
		return "age-up tempo into power-window play"
	if "Recovery" in tags:
		return "hold-and-reboom anchor work"
	if "High impact" in tags:
		return "high-impact flex"
	return ", ".join(tags).lower()


def _team_impact_rows(player_rows):
	by_team = {}
	for p in player_rows:
		if p.get("team") not in (0, 1):
			continue
		by_team.setdefault(p["team"], []).append(p)
	return by_team


def _analysis_key(row, nick_key="nick"):
	user_id = row.get("user_id")
	if user_id is not None:
		return ("user", str(user_id))
	name = (row.get(nick_key) or row.get("identity") or "").strip().lower()
	return ("name", name) if name else None


def _infer_replay_team_map(roster_rows, replay_rows):
	roster_by_key = {_analysis_key(r): r for r in roster_rows if _analysis_key(r)}
	votes = {}
	for g in replay_rows:
		key = _analysis_key(g, "identity")
		roster = roster_by_key.get(key)
		if not roster:
			continue
		try:
			bot_team = int(roster.get("bot_team"))
		except (TypeError, ValueError):
			continue
		replay_team = g.get("replay_team")
		if replay_team is None:
			continue
		votes.setdefault(str(replay_team), {})
		votes[str(replay_team)][bot_team] = votes[str(replay_team)].get(bot_team, 0) + 1
	return {
		replay_team: sorted(team_votes.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
		for replay_team, team_votes in votes.items()
	}


def _merge_analysis_rows(mc_rows, replay_rows, pm_rows=None):
	"""Full post-game roster: bot roster first, civ rows + replay rows fill details."""
	pm_rows = pm_rows or []
	mc_by_key = {_analysis_key(r): r for r in mc_rows if _analysis_key(r)}
	replay_by_key = {_analysis_key(r, "identity"): r for r in replay_rows if _analysis_key(r, "identity")}
	team_map = _infer_replay_team_map(pm_rows or mc_rows, replay_rows)
	merged = []
	seen = set()
	roster_rows = pm_rows or mc_rows
	for base in roster_rows:
		key = _analysis_key(base)
		mc = mc_by_key.get(key) or {}
		g = replay_by_key.get(key)
		seen.add(key)
		merged.append({
			**(g or {}),
			"user_id": base.get("user_id") if base.get("user_id") is not None else (g or {}).get("user_id"),
			"identity": (g or {}).get("identity"),
			"nick": base.get("nick") or mc.get("nick") or (g or {}).get("identity"),
			"civ": mc.get("civ") or (g or {}).get("civ"),
			"bot_team": base.get("bot_team"),
			"result": mc.get("result") or base.get("result"),
			"winner": (g or {}).get("winner"),
		})

	# Best-effort display merge for replay rows without a known profile/user mapping.
	# If one bot roster player and one replay player are unmatched on the same team,
	# assume they are the same participant for this card only. This avoids dropping
	# players like a private/new profile whose profile->Discord mapping is missing.
	unmatched_by_team = {}
	for row in merged:
		if row.get("identity"):
			continue
		try:
			team = int(row.get("bot_team"))
		except (TypeError, ValueError):
			continue
		unmatched_by_team.setdefault(team, []).append(row)
	for g in replay_rows:
		key = _analysis_key(g, "identity")
		if key in seen:
			continue
		bot_team = team_map.get(str(g.get("replay_team")))
		if bot_team not in (0, 1):
			continue
		candidates = unmatched_by_team.get(bot_team) or []
		if len(candidates) == 1:
			target = candidates.pop()
			target.update(g)
			target["identity"] = g.get("identity")
			target["civ"] = target.get("civ") or g.get("civ")
			target["winner"] = g.get("winner")
			seen.add(key)
			continue
		merged.append({
			**g,
			"nick": g.get("identity"),
			"civ": g.get("civ"),
			"bot_team": bot_team,
			"result": "W" if g.get("winner") else "L" if g.get("winner") is not None else None,
		})
	return merged


def _team_tag_summary(team_rows):
	counts = {}
	for p in team_rows:
		for tag in p.get("impact_tags") or []:
			counts[tag] = counts.get(tag, 0) + 1
	return [tag for tag, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]


def _tag_chip(tag):
	return f"`{tag}`"


def _player_card_line(player, carry=False):
	tags = [_tag_chip(t) for t in (player.get("impact_tags") or [])[:MAX_CARD_TAGS]]
	chips = " ".join(tags) if tags else "`No tags`"
	carry_badge = " 👑 **CARRY**" if carry else ""
	score = player.get("impact_score")
	score_text = f"`{score}`" if score is not None else "`?`"
	glyphs = player.get("strength_glyphs")
	glyph_text = f" · {glyphs}" if glyphs else ""
	return (
		f"{'⭐ ' if carry else '• '}**{_clip(player.get('nick'), 24)}**{carry_badge} — "
		f"**{_clip(player.get('civ'), 18)}** · {score_text}{glyph_text}\n"
		f"  {chips}"
	)


def _team_card_fields(player_rows, team_names=None):
	team_names = team_names or {0: "Alpha", 1: "Beta"}
	teams = _team_impact_rows(player_rows)
	fields = []
	for team in sorted(teams):
		rows = sorted(teams[team], key=scoring.carry_sort_key)
		result = next((p.get("result") for p in rows if p.get("result")), None)
		icon = "🟩" if result == "W" else "🟥" if result == "L" else "⬜"
		lines = [_player_card_line(p, carry=(p is rows[0])) for p in rows]
		fields.append({
			"name": f"{icon} {team_names.get(team, f'Team {team}')} · {result or '?'}",
			"value": "\n".join(lines)[:1024] or "No players",
			"inline": True,
		})
	return fields


def _match_analysis_lines(player_rows, team_names=None):
	team_names = team_names or {0: "Alpha", 1: "Beta"}
	teams = _team_impact_rows(player_rows)
	if not teams:
		return []
	lines = []
	for team in sorted(teams):
		rows = sorted(teams[team], key=scoring.carry_sort_key)
		result = next((p.get("result") for p in rows if p.get("result")), None)
		icon = "🟩" if result == "W" else "🟥" if result == "L" else "⬜"
		tags = _team_tag_summary(rows)
		carry = rows[0]
		team_name = team_names.get(team, f"Team {team}")
		lines.append(
			f"{icon} **{team_name}** ({result or '?'}) — "
			f"team read: {_tag_word(tags)}. "
			f"Top pop: **{carry['nick']}** on **{carry.get('civ') or '?'}** "
			f"({carry['impact_score']}, {_tag_word(carry.get('impact_tags') or [])})."
		)
	if 0 in teams and 1 in teams:
		all_rows = sorted(player_rows, key=scoring.carry_sort_key)
		carries = all_rows[:2]
		if len(carries) >= 2:
			lines.append(
				f"👑 Carry check: **{carries[0]['nick']}** edged **{carries[1]['nick']}** "
				f"{carries[0]['impact_score']}–{carries[1]['impact_score']}. "
				"GG, villagers had opinions."
			)
	return lines[:MAX_ANALYSIS_LINES]


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


async def _match_channel_id(bot_match_id):
	from core.database import db
	row = await db.fetchone("SELECT channel_id FROM qc_matches WHERE match_id=%s", [bot_match_id])
	return row.get("channel_id") if row else None


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


async def _analysis_rows(bot_match_id):
	from core.database import db
	pm_rows = await db.fetchall(
		"SELECT pm.user_id, MAX(pm.nick) AS nick, pm.team AS bot_team, "
		"CASE WHEN m.winner=pm.team THEN 'W' "
		"WHEN m.winner IS NOT NULL AND m.winner<>pm.team THEN 'L' ELSE NULL END AS result "
		"FROM qc_player_matches pm JOIN qc_matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"WHERE pm.match_id=%s AND pm.team IN (0, 1) "
		"GROUP BY pm.user_id, pm.team, m.winner "
		"ORDER BY pm.team, nick",
		[bot_match_id])
	mc_rows = await db.fetchall(
		"SELECT user_id, nick, team AS bot_team, civ, result "
		"FROM qc_match_civs "
		"WHERE bot_match_id=%s AND team IN (0, 1) AND result IN ('W', 'L') "
		"ORDER BY team, nick",
		[bot_match_id])
	replay_rows = await db.fetchall(
		"SELECT g.user_id, g.identity, g.civ, g.team AS replay_team, g.winner, "
		"g.villagers, g.vil_pre_castle, g.vil_pre_imperial, g.military, g.mil_pre_castle, g.mil_pre_imperial, "
		"g.feudal_s, g.castle_s, g.imperial_s "
		"FROM rs_matches rm "
		"JOIN rs_player_games g ON g.aoe2_match_id=rm.aoe2_match_id "
		"WHERE rm.bot_match_id=%s "
		"ORDER BY g.team, g.identity",
		[bot_match_id])
	return _merge_analysis_rows(mc_rows, replay_rows, pm_rows)


async def build_match_analysis_embed(channel_id, bot_match_id):
	"""Replay-derived post-game team read. Built only after rs_* rows exist."""
	rows = await _analysis_rows(bot_match_id)
	if not rows:
		return None
	player_rows = [_impact_payload(row, rows) for row in rows]
	if not any(p.get("result") in ("W", "L") for p in player_rows):
		return None
	lines = _match_analysis_lines(player_rows, await _team_names(channel_id, bot_match_id))
	if not lines:
		return None

	from nextcord import Colour, Embed

	embed = Embed(
		title=random.choice([
			"⚔️ Final Tale of the Tape",
			"🧾 Post-Imp Damage Report",
			"🏰 After-Action Scout Report",
		]),
		colour=Colour(0xe67e22),
		description="\n\n".join(lines),
	)
	embed.set_footer(text="Replay-derived tags · impact is relative inside this match")
	return embed


async def build_match_cards_embed(channel_id, bot_match_id):
	"""Card-like team summary for Discord. Uses embed fields to mimic side-by-side cards."""
	rows = await _analysis_rows(bot_match_id)
	if not rows:
		return None
	player_rows = [_impact_payload(row, rows) for row in rows]
	fields = _team_card_fields(player_rows, await _team_names(channel_id, bot_match_id))
	if not fields:
		return None

	from nextcord import Colour, Embed

	embed = Embed(
		title="🧾 Match Cards",
		colour=Colour(0x2ecc71),
		description=(
			"Impact score is relative inside this match. Tags come from replay timing, eco, and army signals.\n"
			"⚔ army · 🌾 eco · ⏱ age-up — ▲ above match average, ▼ below, · around average."
		),
	)
	for f in fields[:2]:
		embed.add_field(name=f["name"], value=f["value"], inline=f["inline"])
	embed.set_footer(text="Score = replay impact; carry = highest impact on that team")
	return embed


async def post_match_analysis(bot_match_id):
	"""Best-effort Discord post once replay analysis is stored."""
	try:
		from core.client import dc
		from core.console import log

		channel_id = await _match_channel_id(bot_match_id)
		if channel_id is None:
			return False
		channel = dc.get_channel(channel_id)
		if channel is None:
			return False
		cards = await build_match_cards_embed(channel_id, bot_match_id)
		embed = await build_match_analysis_embed(channel_id, bot_match_id)
		if cards is None and embed is None:
			return False
		if cards is not None and embed is not None:
			await channel.send(embeds=[cards, embed])
		else:
			await channel.send(embed=cards or embed)
		return True
	except Exception as e:
		log.error(f"Replay post-game analysis send failed (bot match {bot_match_id}): {e}")
		return False
