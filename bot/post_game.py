# -*- coding: utf-8 -*-
"""Replay-derived post-game analysis: the Match Cards embed and the APM chart.

Once bot/replay_stats/ has parsed a finished match's replay, post_match_analysis
posts one embed field per team — each player's civ, strategy labels, production
and activity counts, and match-wide medals — with the APM chart attached.

Everything here is replay-derived and describes production and activity, never
combat outcomes. The win/loss storylines live elsewhere: bot/team_insights.py
teases them when teams are formed, and bot/storyline_payoff.py settles them at
report time.

Design: one cheap data pull, pure Python rendering (unit-testable without a DB),
and the builders return an Embed or None. Heavy imports (nextcord / card_scoring
/ db) are deferred so the pure helpers import cleanly under CI.
"""


def _clip(text, limit=28):
	text = str(text or "?")
	return text if len(text) <= limit else text[:max(1, limit - 1)] + "…"


def _card_payload(row, group, signals):
	"""Render payload for one player on the Match Cards.

	Scores this match's players through card_scoring.py. Component scores here
	are internal — they drive sort order, the carry crown and tag thresholds,
	and are never displayed.
	"""
	from bot.replay_stats import card_scoring

	pnum = row.get("player_number")
	scores = card_scoring.component_scores(row, group)
	buildings = (signals.get("buildings") or {}).get(pnum) or {}
	comp = (signals.get("composition") or {}).get(pnum) or {}
	produced = (row.get("villagers") or 0) + (row.get("military") or 0)
	return {
		"player_number": pnum,
		"nick": row.get("nick") or row.get("identity") or str(row.get("user_id") or ""),
		"civ": row.get("civ"),
		"team": int(row["bot_team"]) if row.get("bot_team") in (0, 1, "0", "1") else None,
		"result": row.get("result") or ("W" if row.get("winner") else "L" if row.get("winner") is not None else None),
		"strategies": (signals.get("strategies") or {}).get(pnum) or [],
		"spawn": (signals.get("spawn") or {}).get(pnum),
		"villagers": row.get("villagers"),
		"military": row.get("military"),
		"farms": buildings.get("farms"),
		"tcs": buildings.get("tcs"),
		"eapm": row.get("eapm"),
		"peak_eapm": (signals.get("peak_eapm") or {}).get(pnum),
		"has_production": bool(produced),
		"composition": comp.get("composition") or {},
		"unit_names": comp.get("unit_names") or {},
		"military_post_imperial": comp.get("post_imperial"),
		"production_coverage": card_scoring.production_coverage(
			(signals.get("clicks") or {}).get(pnum), row.get("duration_s")),
		"impact_score": scores["impact"],
		"army_score": scores["army"],
		"eco_score": scores["eco"],
		"early_eco_score": scores["early_eco"],
		"reboom_score": scores["reboom"],
	}


async def _card_signals_for(rows):
	"""Card-only signals for the match these rows belong to, or empty dicts."""
	from bot.replay_stats.card_query import fetch_card_signals

	aoe2_id = next((r.get("replay_match_id") for r in rows if r.get("replay_match_id")), None)
	if aoe2_id is None:
		return {}
	duration = next((r.get("duration_s") for r in rows if r.get("duration_s")), None)
	return await fetch_card_signals(aoe2_id, duration)


async def _medals_for(rows):
	"""player_number -> {"military_medal", "villager_medal"} for this match,
	read from the derived-global game_stats table rather than recomputed here.

	game_stats.replay_match_id and the raw replay_players.replay_match_id it is
	computed from are the same value under the same name — 007_raw_renames
	unified the two spellings.

	{} when there is no known match id, or when the match has no stored rows
	yet. The second case is not expected to happen in steady state --
	jobs.ingest_one writes game_stats inside store.write_match strictly before
	this renders, and bot/derived/backfill.py heals any gap within a tick --
	but there is deliberately no live-computation fallback here (see this
	file's module doc), so a genuinely absent row must render medal-less
	rather than raise.
	"""
	from nammaoe2bot.runtime.database import db

	aoe2_id = next((r.get("replay_match_id") for r in rows if r.get("replay_match_id")), None)
	if aoe2_id is None:
		return {}
	medal_rows = await db.fetchall(
		"SELECT player_number, military_medal, villager_medal FROM game_stats "
		"WHERE replay_match_id=%s",
		[aoe2_id])
	return {
		r["player_number"]: {
			"military_medal": r.get("military_medal"),
			"villager_medal": r.get("villager_medal"),
		}
		for r in medal_rows or [] if r.get("player_number") is not None
	}


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


def _tag_chip(tag):
	return f"`{tag}`"


MEDAL_GLYPHS = (("military_medal", "⚔"), ("villager_medal", "🌾"))


def _medal_text(medals):
	"""Top-three medals as repeated glyphs: 1st gets three, 3rd gets one."""
	parts = []
	for field, glyph in MEDAL_GLYPHS:
		place = (medals or {}).get(field)
		if place:
			parts.append(glyph * (4 - place))
	return " ".join(parts)


def _stats_text(player):
	"""Raw counts plus activity. Every element omits silently when absent —
	never a placeholder, never a zero standing in for missing data."""
	bits = []
	if player.get("villagers") is not None:
		bits.append(f"{player['villagers']} vils")
	if player.get("military") is not None:
		bits.append(f"{player['military']} military")
	if player.get("farms") is not None:
		bits.append(f"{player['farms']} farms")
	if player.get("tcs") is not None:
		bits.append(f"{player['tcs']} TC")
	# Average is the stored replay_players.eapm, never derived from the APM
	# buckets (those are sparse, so any mean over them divides by active
	# minutes). Peak is forward-only, so it is simply absent on older matches.
	if player.get("eapm") is not None:
		peak = player.get("peak_eapm")
		bits.append(f"{player['eapm']} eAPM" + (f" (pk {peak})" if peak else ""))
	if player.get("spawn"):
		bits.append(player["spawn"])
	return " · ".join(bits)


def _player_card_line(player, carry=False, medals=None, tags=None, with_stats=True):
	head = "👑 " if carry else "• "
	name = f"{head}**{_clip(player.get('nick'), 24)}** — **{_clip(player.get('civ'), 18)}**"
	strategies = ", ".join(player.get("strategies") or [])
	if strategies:
		name += f" · {strategies}"
	if not player.get("has_production"):
		# Ranking them last and showing them bare would read as "played badly"
		# when the truth is "not measured".
		return f"{name} · ⚠ partial replay data"
	lines = [name]
	badge = " · ".join(x for x in (_medal_text(medals),
	                               " ".join(_tag_chip(t) for t in (tags or [])))
	                   if x)
	if badge:
		lines.append(f"  {badge}")
	if with_stats:
		stats = _stats_text(player)
		if stats:
			lines.append(f"  {stats}")
	return "\n".join(lines)


_NO_MEDALS = {"military_medal": None, "villager_medal": None}


def _team_card_fields(player_rows, team_names=None, medals_by_player=None):
	"""One embed field per team. Medals are read from ``medals_by_player``
	(player_number -> {"military_medal", "villager_medal"}, see _medals_for --
	they are match-wide facts computed once at ingest, not re-ranked here).
	Tags are still computed here and are team-scoped."""
	from bot.replay_stats import card_scoring

	team_names = team_names or {0: "Alpha", 1: "Beta"}
	medals_by_player = medals_by_player or {}
	medals = [medals_by_player.get(p.get("player_number")) or _NO_MEDALS for p in player_rows]
	awards = card_scoring.assign_team_tags(player_rows)
	# Descriptive tags are facts and come first; the team-scoped awards follow.
	tags = [card_scoring.descriptive_tags(p) + awards[i]
	        for i, p in enumerate(player_rows)]
	extras = {id(p): (medals[i], tags[i]) for i, p in enumerate(player_rows)}
	teams = _team_impact_rows(player_rows)
	fields = []
	for team in sorted(teams):
		rows = sorted(teams[team], key=card_scoring.carry_sort_key)
		result = next((p.get("result") for p in rows if p.get("result")), None)
		icon = "🟩" if result == "W" else "🟥" if result == "L" else "⬜"

		def render(with_stats, rows=rows):
			return "\n\n".join(
				_player_card_line(
					p, carry=(p is rows[0]),
					medals=extras[id(p)][0], tags=extras[id(p)][1],
					with_stats=with_stats)
				for p in rows)

		value = render(True)
		if len(value) > 1024:
			# Drop the stats line before dropping a player.
			value = render(False)
		fields.append({
			"name": f"{icon} {team_names.get(team, f'Team {team}')} · {result or '?'}",
			"value": value[:1024] or "No players",
			"inline": True,
		})
	return fields


# ── DB read + embed building (deferred heavy imports) ────────────────────
async def _team_names(channel_id, bot_match_id):
	from nammaoe2bot.runtime.database import db
	row = await db.fetchone(
		"SELECT alpha_name, beta_name FROM matches WHERE match_id=%s AND channel_id=%s",
		[bot_match_id, channel_id]
	)
	if row:
		return {0: row.get("alpha_name") or "Alpha", 1: row.get("beta_name") or "Beta"}
	return {0: "Alpha", 1: "Beta"}


async def _match_channel_id(bot_match_id):
	from nammaoe2bot.runtime.database import db
	row = await db.fetchone("SELECT channel_id FROM matches WHERE match_id=%s", [bot_match_id])
	return row.get("channel_id") if row else None


async def _analysis_rows(bot_match_id):
	from nammaoe2bot.runtime.database import db
	pm_rows = await db.fetchall(
		"SELECT pm.user_id, MAX(pm.nick) AS nick, pm.team AS bot_team, "
		"CASE WHEN m.winner=pm.team THEN 'W' "
		"WHEN m.winner IS NOT NULL AND m.winner<>pm.team THEN 'L' ELSE NULL END AS result "
		"FROM match_players pm JOIN matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"WHERE pm.match_id=%s AND pm.team IN (0, 1) "
		"GROUP BY pm.user_id, pm.team, m.winner "
		"ORDER BY pm.team, nick",
		[bot_match_id])
	mc_rows = await db.fetchall(
		"SELECT user_id, nick, team AS bot_team, civ, result "
		"FROM civ_picks "
		"WHERE bot_match_id=%s AND team IN (0, 1) AND result IN ('W', 'L') "
		"ORDER BY team, nick",
		[bot_match_id])
	# replay_match_id + player_number are the join key for every card-only signal
	# (buildings, events, cls_results, replay_apm) — profile_id is a nullable
	# denormalisation on those tables and would silently drop players.
	# age_reliable and eapm are read by card_scoring / the stats line.
	# feudal_s and castle_s are NOT read by anything in this file any more — the
	# embed that scored timing was deleted with the replay-derived Tale of the
	# Tape. They stay because test_replay_scoring.py::
	# test_impact_queries_select_every_scoring_column checks every replay_players
	# query here against scoring.REQUIRED_COLUMNS, which folds in TIMING_MIX.
	replay_rows = await db.fetchall(
		"SELECT g.user_id, g.identity, g.civ, g.team AS replay_team, g.winner, "
		"g.replay_match_id, g.player_number, g.age_reliable, g.eapm, "
		"g.villagers, g.vil_pre_castle, g.vil_pre_imperial, g.military, g.mil_pre_castle, g.mil_pre_imperial, "
		"g.feudal_s, g.castle_s, g.imperial_s, rm.duration_s "
		"FROM replay_matches rm "
		"JOIN replay_players g ON g.replay_match_id=rm.replay_match_id "
		"WHERE rm.bot_match_id=%s "
		"ORDER BY g.team, g.identity",
		[bot_match_id])
	return _merge_analysis_rows(mc_rows, replay_rows, pm_rows)


async def build_match_cards_embed(channel_id, bot_match_id, rows=None, team_names=None):
	"""Card-like team summary for Discord. Uses embed fields to mimic side-by-side cards."""
	if rows is None:
		rows = await _analysis_rows(bot_match_id)
	if not rows:
		return None
	signals = await _card_signals_for(rows)
	medals_by_player = await _medals_for(rows)
	player_rows = [_card_payload(row, rows, signals) for row in rows]
	if team_names is None:
		team_names = await _team_names(channel_id, bot_match_id)
	fields = _team_card_fields(player_rows, team_names, medals_by_player)
	if not fields:
		return None

	from nextcord import Colour, Embed

	embed = Embed(
		title="🧾 Match Cards",
		colour=Colour(0x2ecc71),
		description=(
			"Medals rank the whole match on raw counts — ⚔ military, 🌾 villagers.\n"
			"Tags describe the shape of a player's game against their own team."
		),
	)
	for f in fields[:2]:
		embed.add_field(name=f["name"], value=f["value"], inline=f["inline"])
	embed.set_footer(text="Replay-derived · production and activity, never combat outcomes")
	return embed


async def _apm_chart_file(bot_match_id):
	"""Rendered APM chart for a match, or None. Best-effort: every failure path
	returns None so the cards still post without an image."""
	try:
		from nammaoe2bot.runtime.database import db
		from bot.replay_stats import render
		from bot.replay_stats.apm_query import apm_series, fetch_match_apm

		row = await db.fetchone(
			"SELECT replay_match_id FROM replay_matches WHERE bot_match_id=%s", [bot_match_id])
		if not row:
			return None
		aoe2_id = row["replay_match_id"]
		rows = await fetch_match_apm(aoe2_id)
		if not rows:
			return None
		meta = await db.fetchall(
			"SELECT player_number, identity, team FROM replay_players WHERE replay_match_id=%s",
			[aoe2_id])
		names = {m["player_number"]: m.get("identity") for m in meta or []}
		sides = sorted({m.get("team") for m in meta or [] if m.get("team") is not None})
		teams = {m["player_number"]: sides.index(m["team"])
		         for m in meta or [] if m.get("team") in sides}
		series = apm_series(rows, names)
		buf = await render.render_apm(series, teams)
		if buf is None:
			# render_apm returns None both for "too short to chart" (normal, silent) and for a
			# genuine failure. should_render is the pure gate it uses, so re-asking it separates
			# the two — otherwise a permanently broken renderer looks exactly like an old match.
			if render.should_render(series):
				from nammaoe2bot.runtime.console import log
				log.error(f"APM chart render produced nothing for {len(series)} series "
				          f"(bot match {bot_match_id})")
			return None
		from nextcord import File
		return File(fp=buf, filename="apm.png")
	except Exception as e:
		from nammaoe2bot.runtime.console import log
		log.error(f"APM chart build failed (bot match {bot_match_id}): {e}")
		return None


async def post_match_analysis(bot_match_id):
	"""Best-effort Discord post once replay analysis is stored."""
	try:
		from nammaoe2bot.runtime.client import dc
		from nammaoe2bot.runtime.console import log

		channel_id = await _match_channel_id(bot_match_id)
		if channel_id is None:
			return False
		channel = dc.get_channel(channel_id)
		if channel is None:
			return False
		# The Tale of the Tape now posts at report time from
		# bot/storyline_payoff.py, so only the cards are left here.
		rows = await _analysis_rows(bot_match_id)
		team_names = await _team_names(channel_id, bot_match_id)
		cards = await build_match_cards_embed(channel_id, bot_match_id, rows, team_names)
		if cards is None:
			return False
		chart_file = await _apm_chart_file(bot_match_id)
		if chart_file is not None:
			cards.set_image(url="attachment://apm.png")
		embeds = [cards]
		if chart_file is not None:
			try:
				await channel.send(embeds=embeds, file=chart_file)
			except Exception as e:
				# A failed attachment (most likely: no ATTACH_FILES in this channel) must never
				# cost the whole post — retry without the file. The embed's set_image then points
				# at an absent attachment, which Discord renders as simply no image.
				log.error(f"APM chart attach failed (bot match {bot_match_id}): {e} — "
				          f"posting the cards without it")
				await channel.send(embeds=embeds)
		else:
			await channel.send(embeds=embeds)
		return True
	except Exception as e:
		log.error(f"Replay post-game analysis send failed (bot match {bot_match_id}): {e}")
		return False
