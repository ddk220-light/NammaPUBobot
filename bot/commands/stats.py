__all__ = ['last_game', 'stats', 'top', 'rank', 'leaderboard', 'leaderboard_alternate', 'mapstats', 'activity']

import io
import re
import asyncio
from time import time
from math import ceil
from nextcord import Member, Embed, Colour, File

from core.utils import get, find, seconds_to_str, get_nick, discord_table  # noqa: F401
from core.database import db
from core.console import log
from core.config import cfg

import bot
from bot import alt_ratings


async def last_game(ctx, queue: str = None, player: Member = None, match_id: int = None):
	lg = None

	if match_id:
		lg = await db.select_one(
			['*'], "qc_matches", where=dict(channel_id=ctx.qc.id, match_id=match_id), order_by="match_id", limit=1
		)

	elif queue:
		if queue := find(lambda q: q.name.lower() == queue.lower(), ctx.qc.queues):
			lg = await db.select_one(
				['*'], "qc_matches", where=dict(channel_id=ctx.qc.id, queue_id=queue.id), order_by="match_id", limit=1
			)

	elif player and (member := await ctx.get_member(player)) is not None:
		if match := await db.select_one(
			['match_id'], "qc_player_matches", where=dict(channel_id=ctx.qc.id, user_id=member.id),
			order_by="match_id", limit=1
		):
			lg = await db.select_one(
				['*'], "qc_matches", where=dict(channel_id=ctx.qc.id, match_id=match['match_id'])
			)

	else:
		lg = await db.select_one(
			['*'], "qc_matches", where=dict(channel_id=ctx.qc.id), order_by="match_id", limit=1
		)

	if not lg:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Nothing found"))

	players = await db.select(
		['user_id', 'nick', 'team'], "qc_player_matches",
		where=dict(match_id=lg['match_id'])
	)
	embed = Embed(colour=Colour(0x50e3c2))
	embed.add_field(name=lg['queue_name'], value=seconds_to_str(int(time()) - lg['at']) + " ago")
	if len(team := [p['nick'] for p in players if p['team'] == 0]):
		embed.add_field(name=lg['alpha_name'], value="`" + ", ".join(team) + "`")
	if len(team := [p['nick'] for p in players if p['team'] == 1]):
		embed.add_field(name=lg['beta_name'], value="`" + ", ".join(team) + "`")
	if len(team := [p['nick'] for p in players if p['team'] is None]):
		embed.add_field(name=ctx.qc.gt("Players"), value="`" + ", ".join(team) + "`")
	if lg['ranked']:
		if lg['winner'] is None:
			winner = ctx.qc.gt('Draw')
		else:
			winner = [lg['alpha_name'], lg['beta_name']][lg['winner']]
		embed.add_field(name=ctx.qc.gt("Winner"), value=winner)
	await ctx.reply(embed=embed)


async def stats(ctx, player: Member = None):
	if player:
		if (member := await ctx.get_member(player)) is not None:
			data = await bot.stats.user_stats(ctx.qc.id, member.id)
			target = get_nick(member)
		else:
			raise bot.Exc.NotFoundError(ctx.qc.gt("Specified user not found."))
	else:
		data = await bot.stats.qc_stats(ctx.qc.id)
		target = f"#{ctx.channel.name}"

	embed = Embed(
		title=ctx.qc.gt("Stats for __{target}__").format(target=target),
		colour=Colour(0x50e3c2),
		description=ctx.qc.gt("**Total matches: {count}**").format(count=data['total'])
	)
	for q in data['queues']:
		embed.add_field(name=q['queue_name'], value=str(q['count']), inline=True)

	await ctx.reply(embed=embed)


async def top(ctx, period=None):
	if period in ["day", ctx.qc.gt("day")]:
		time_gap = int(time()) - (60 * 60 * 24)
	elif period in ["week", ctx.qc.gt("week")]:
		time_gap = int(time()) - (60 * 60 * 24 * 7)
	elif period in ["month", ctx.qc.gt("month")]:
		time_gap = int(time()) - (60 * 60 * 24 * 30)
	elif period in ["year", ctx.qc.gt("year")]:
		time_gap = int(time()) - (60 * 60 * 24 * 365)
	else:
		time_gap = None

	data = await bot.stats.top(ctx.qc.id, time_gap=time_gap)
	embed = Embed(
		title=ctx.qc.gt("Top 10 players for __{target}__").format(target=f"#{ctx.channel.name}"),
		colour=Colour(0x50e3c2),
		description=ctx.qc.gt("**Total matches: {count}**").format(count=data['total'])
	)
	for p in data['players']:
		embed.add_field(name=p['nick'], value=str(p['count']), inline=True)
	await ctx.reply(embed=embed)


async def rank(ctx, player: Member = None):
	# Defer — gathering the profile + rendering the ELO chart can exceed the
	# 3-second interaction window.
	interaction = getattr(ctx, 'interaction', None)
	if interaction is not None and not interaction.response.is_done():
		await interaction.response.defer()

	target = ctx.author if not player else await ctx.get_member(player)
	if not target:
		raise bot.Exc.SyntaxError(ctx.qc.gt("Specified user not found."))

	data = await ctx.qc.get_lb()
	if p := find(lambda i: i['user_id'] == target.id, data):
		place = data.index(p) + 1
	else:
		data = await db.select(
			['user_id', 'rating', 'deviation', 'channel_id', 'wins', 'losses', 'draws', 'is_hidden', 'streak'],
			"qc_players",
			where={'channel_id': ctx.qc.rating.channel_id}
		)
		p = find(lambda i: i['user_id'] == target.id, data)
		place = "?"

	if not p:
		raise bot.Exc.ValueError(ctx.qc.gt("No rating data found."))

	from bot import player_profile
	profile_url = player_profile.web_profile_url(getattr(cfg, "WS_ROOT_URL", ""), target.id)

	# Rich profile bits (best-effort — any piece with no data is simply omitted).
	prof = {}
	try:
		prof = await player_profile.gather_profile(ctx.qc.rating.channel_id, target.id)
	except Exception as e:
		log.error(f"gather_profile failed for {target.id}: {e}")

	# Dashboard-overview pieces (persona/scout description + duo quadrants),
	# same server-side data the web profile page shows. Best-effort too.
	snapshot = {}
	commentary = None
	try:
		from bot import web as web_dashboard
		snapshot = await web_dashboard.player_overview_snapshot(target.id)
	except Exception as e:
		log.error(f"player_overview_snapshot failed for {target.id}: {e}")
	try:
		from bot.commentary import query as commentary_query
		commentary = await commentary_query.player_commentary(target.id, "all")
	except Exception as e:
		log.error(f"player_commentary failed for {target.id}: {e}")

	# Mini version of the web profile: summary strip up top, then grouped
	# sections mirroring the dashboard's layout.
	matches = p['wins'] + p['losses'] + p['draws']
	winrate = int(p['wins'] * 100 / (p['wins'] + p['losses'] or 1))
	rank_str = ctx.qc.rating_rank(p['rating'])['rank'] if p['rating'] else "〈?〉"
	rating_str = f"**{p['rating']}** ±{p['deviation']}" if p['rating'] else "**?**"

	headline = [
		f"{rank_str} {rating_str} · **#{place}** " + ctx.qc.gt("on the leaderboard"),
		f"**{matches}** " + ctx.qc.gt("matches") + f" · **{winrate}%** " + ctx.qc.gt("winrate"),
	]
	if profile_url:
		headline.append(f"🔗 [{ctx.qc.gt('View full web profile')}]({profile_url})")

	embed = Embed(title=f"__{get_nick(target)}__", colour=Colour(0x7289DA), description="\n".join(headline))
	if target.display_avatar:
		embed.set_thumbnail(url=target.display_avatar.url)

	streak = p['streak'] or 0
	streak_badge = f"🔥 {streak}" if streak >= 3 else (f"🧊 {abs(streak)}" if streak <= -3 else str(streak))
	embed.add_field(
		name="⚔️ " + ctx.qc.gt("Record"),
		value=f"**{p['wins']}**W / **{p['losses']}**L / **{p['draws']}**D\n" +
			ctx.qc.gt("Streak") + f": {streak_badge}",
		inline=True
	)
	if prof.get("peak_rating"):
		embed.add_field(
			name="📈 " + ctx.qc.gt("Peak"),
			value=f"**{prof['peak_rating']}**\n{seconds_to_str(int(time() - prof['peak_at']))} ago",
			inline=True
		)
	civs = prof.get("civs") or {}
	if civs.get("most_played"):
		mp = civs["most_played"]
		embed.add_field(name="🏰 " + ctx.qc.gt("Most played"), value=f"`{mp['civ']}`\n{mp['games']} games", inline=True)

	if prof.get("recent_form"):
		sq = {"W": "🟩", "L": "🟥", "D": "⬛"}
		embed.add_field(
			name=ctx.qc.gt("Recent form"),
			value="".join(sq[r] for r in prof["recent_form"]) + f"  `last {len(prof['recent_form'])}`",
			inline=False
		)

	# Player description: the persona line always leads (same as the overview
	# page banner), then the stored bot commentary prose. The generated scout
	# read is only a fallback, with its tag enumeration stripped — commentary
	# text is what we want here, not tag counts.
	desc_lines = []
	persona = snapshot.get("persona") or {}
	scout = snapshot.get("scout_report") or {}
	if persona.get("name") and persona.get("key") != "unscouted":
		label = persona["name"]
		if persona.get("epithet"):
			label += f" · {persona['epithet']}"
		desc_lines.append(f"**{label}**")
		if persona.get("tagline"):
			desc_lines.append(persona["tagline"])
	c = (commentary or {}).get("commentary") or {}
	body = c.get("summary") or c.get("read") or c.get("description")
	if isinstance(body, (list, tuple)):
		body = " ".join(str(b) for b in body if b)
	if body:
		if c.get("headline"):
			desc_lines.append(f"**{c['headline']}**")
		desc_lines.append(str(body))
	elif snapshot.get("parsed_matches") and scout.get("description"):
		desc_lines.append(re.sub(r"\s*Recurring tags:[^.]*\.", "", scout["description"]).strip())
	if desc_lines:
		text = "\n".join(desc_lines)
		if len(text) > 1000:
			text = text[:997] + "…"
		embed.add_field(name="📜 " + ctx.qc.gt("Scouting report"), value=text, inline=False)

	if civs.get("best"):
		embed.add_field(
			name="🟢 " + ctx.qc.gt("Best civs"),
			value="\n".join(f"`{c['civ']}` {int(c['wr'] * 100)}% ({c['games']})" for c in civs["best"]),
			inline=True
		)
	if civs.get("worst"):
		embed.add_field(
			name="🔴 " + ctx.qc.gt("Worst civs"),
			value="\n".join(f"`{c['civ']}` {int(c['wr'] * 100)}% ({c['games']})" for c in civs["worst"]),
			inline=True
		)

	# Duo/rival quadrants, same cards as the overview page. Falls back to the
	# lighter teammate/nemesis aggregation when the snapshot is unavailable.
	def _rel_line(label, rel, suffix):
		return f"{label}: `{rel['nick']}` · {rel['winrate']}% {suffix} ({rel['games']} games)"

	mates = []
	if snapshot.get("best_ally"):
		mates.append(_rel_line("💞 " + ctx.qc.gt("Dream duo"), snapshot["best_ally"], ctx.qc.gt("together")))
	if snapshot.get("worst_ally"):
		mates.append(_rel_line("💔 " + ctx.qc.gt("Cursed duo"), snapshot["worst_ally"], ctx.qc.gt("together")))
	if snapshot.get("worst_enemy"):
		mates.append(_rel_line("😤 " + ctx.qc.gt("Nemesis"), snapshot["worst_enemy"], ctx.qc.gt("vs them")))
	if snapshot.get("easiest_enemy"):
		mates.append(_rel_line("💰 " + ctx.qc.gt("Free Elo"), snapshot["easiest_enemy"], ctx.qc.gt("vs them")))
	if not mates:
		if prof.get("best_mate"):
			matenick, wins, games = prof["best_mate"]
			mates.append(ctx.qc.gt("Best teammate") + f": `{matenick}` · {int(wins * 100 / games)}% of {games}")
		if prof.get("nemesis"):
			nemnick, losses = prof["nemesis"]
			mates.append(ctx.qc.gt("Nemesis") + f": `{nemnick}` · {losses} losses")
	if mates:
		embed.add_field(name="🤝 " + ctx.qc.gt("Duos & rivals"), value="\n".join(mates), inline=False)

	changes = await db.select(
		('at', 'rating_change', 'match_id', 'reason'),
		'qc_rating_history', where=dict(user_id=target.id, channel_id=ctx.qc.rating.channel_id),
		order_by='id', limit=5
	)
	if len(changes):
		embed.add_field(
			name="🕑 " + ctx.qc.gt("Last changes:"),
			value="\n".join(("**{change}** · {ago} ago · {reason}{match_id}".format(
				ago=seconds_to_str(int(time() - c['at'])),
				reason=c['reason'],
				match_id=f" (__{c['match_id']}__)" if c['match_id'] else "",
				change=("+" if c['rating_change'] >= 0 else "") + str(c['rating_change'])
			) for c in changes)),
			inline=False
		)

	# ELO-over-time chart, rendered off the event loop and attached to the embed.
	file = None
	pts = prof.get("elo_points") or []
	if len(pts) >= 2:
		try:
			png = await asyncio.get_running_loop().run_in_executor(
				None, player_profile.render_elo_chart, pts, get_nick(target)
			)
			file = File(io.BytesIO(png), filename="elo.png")
			embed.set_image(url="attachment://elo.png")
		except Exception as e:
			log.error(f"ELO chart render failed for {target.id}: {e}")

	if file is not None:
		await ctx.reply(embed=embed, file=file)
	else:
		await ctx.reply(embed=embed)


async def leaderboard(ctx, page: int = 1):
	page = (page or 1) - 1

	full = await ctx.qc.get_lb()
	pages = ceil(len(full) / 10) or 1
	data = full[page * 10:(page + 1) * 10]
	if not len(data):
		raise bot.Exc.NotFoundError(ctx.qc.gt("Leaderboard is empty."))

	# Always an embed: profile links and rank emojis only render there (the old
	# md-table mode lived inside a code block where neither can work).
	from bot import player_profile
	root_url = getattr(cfg, "WS_ROOT_URL", "")

	medals = {1: "🥇", 2: "🥈", 3: "🥉"}
	names, ratings, records = [], [], []
	for n, row in enumerate(data):
		place = (page * 10) + n + 1
		marker = medals.get(place, f"**{place}.**")
		nick = player_profile.web_profile_link(root_url, row['user_id'], row['nick'].strip()[:14])
		names.append(f"{marker} {nick}")

		streak = row['streak'] or 0
		badge = f" 🔥{streak}" if streak >= 3 else (f" 🧊{abs(streak)}" if streak <= -3 else "")
		ratings.append(ctx.qc.rating_rank(row['rating'])['rank'] + f" **{row['rating']}**{badge}")

		winrate = int(row['wins'] * 100 / ((row['wins'] + row['losses']) or 1))
		records.append(f"{row['wins']} / {row['losses']} / {row['draws']} · **{winrate}%**")

	embed = Embed(title="🏆 " + ctx.qc.gt("Leaderboard"), colour=Colour(0xf1c40f))
	embed.add_field(name=ctx.qc.gt("Player"), value="\n".join(names), inline=True)
	embed.add_field(name=ctx.qc.gt("Rating"), value="\n".join(ratings), inline=True)
	embed.add_field(name="W / L / D", value="\n".join(records), inline=True)
	embed.set_footer(text="Page {page} of {pages} · {count} ranked players".format(
		page=page + 1, pages=pages, count=len(full)
	))
	await ctx.reply(embed=embed)


async def leaderboard_alternate(ctx, page: int = 1):
	""" What-if leaderboard: Elo without the blanket weekly uncertainty (sigma) decay.

	Reads the precomputed snapshot in data/alt_ratings.csv (regenerate with
	utils/compute_alt_ratings.py) and shows it next to live ratings so players can
	see how a decay-policy change would feel before anything is actually changed.
	"""
	page = (page or 1) - 1

	alt_map = alt_ratings.load_alt_ratings()
	if not alt_map:
		raise bot.Exc.NotFoundError(ctx.qc.gt("No alternate-rating snapshot is available yet."))

	rows = alt_ratings.build_alt_leaderboard(await ctx.qc.get_lb(), alt_map)
	pages = ceil(len(rows) / 10) or 1
	rows = rows[page * 10:(page + 1) * 10]
	if not len(rows):
		raise bot.Exc.NotFoundError(ctx.qc.gt("Leaderboard is empty."))

	meta = alt_ratings.load_snapshot_meta()
	note = (
		"📊 **Alternate Elo — a what-if, not your live rating.**\n"
		f"This is what the leaderboard would look like if the weekly *uncertainty (σ) decay* — which "
		f"bumped **every** player's volatility up every week since {meta.get('branch_date', 'late 2025')} "
		f"and stopped active players ever settling — had instead only applied to inactive players.\n"
		f"`Δ` = alternate − current. Snapshot as of {meta.get('computed_date', 'now')} · page {page + 1} of {pages}.\n"
		"Take a look and let us know how it feels before we decide whether to change anything.\n"
	)
	table = discord_table(
		["№", "Nickname", "Elo", "Alt Elo", "Δ"],
		[[
			(page * 10) + (n + 1),
			rows[n]['nick'].strip(),
			rows[n]['current'],
			rows[n]['alt'],
			("+" if rows[n]['delta'] > 0 else "") + str(rows[n]['delta']),
		] for n in range(len(rows))]
	)
	await ctx.reply(note + table)


async def mapstats(ctx, period: str = None):
	""" Channel-wide map popularity as a horizontal bar chart. """
	_period_days = {'1M': 30, '6M': 180, '1Y': 365}
	days = _period_days.get(period) if period else None
	ts_from = int(time()) - days * 86400 if days else None

	at_filter = " AND at >= %s" if ts_from is not None else ""
	params = [ctx.qc.id]
	if ts_from is not None:
		params.append(ts_from)

	# `maps` is stored on qc_matches as a newline-joined string (see
	# bot/stats/stats.py register_match_*). Split it back into rows with a
	# recursive CTE so we can count map frequency in SQL.
	rows = await db.fetchall(
		f"""
		WITH RECURSIVE map_split AS (
			SELECT
				match_id,
				TRIM(SUBSTRING_INDEX(maps, '\n', 1)) AS map_name,
				IF(LOCATE('\n', maps) > 0, SUBSTRING(maps, LOCATE('\n', maps) + 1), NULL) AS remaining
			FROM qc_matches
			WHERE channel_id = %s AND maps IS NOT NULL AND maps != ''{at_filter}
			UNION ALL
			SELECT
				match_id,
				TRIM(SUBSTRING_INDEX(remaining, '\n', 1)),
				IF(LOCATE('\n', remaining) > 0, SUBSTRING(remaining, LOCATE('\n', remaining) + 1), NULL)
			FROM map_split
			WHERE remaining IS NOT NULL
		)
		SELECT map_name, COUNT(*) AS played
		FROM map_split
		WHERE map_name != ''
		GROUP BY map_name
		ORDER BY played DESC
		""",
		params
	)
	if not rows:
		raise bot.Exc.NotFoundError(ctx.qc.gt("No map data yet."))

	period_label = f" ({period})" if days else ""
	title = f"Map stats{period_label}"

	def _render():
		from matplotlib.figure import Figure
		from matplotlib.backends.backend_agg import FigureCanvasAgg

		names = [r['map_name'] for r in rows[:15]]
		counts = [r['played'] for r in rows[:15]]
		fig = Figure(figsize=(8, max(2, 0.4 * len(names) + 1)), dpi=120)
		FigureCanvasAgg(fig)
		ax = fig.add_subplot(111)
		y_pos = range(len(names))
		bars = ax.barh(y_pos, counts, color='#5865f2')
		ax.set_yticks(y_pos)
		ax.set_yticklabels(names)
		ax.invert_yaxis()
		ax.set_xlabel('Matches played')
		ax.set_title(title)
		ax.spines['top'].set_visible(False)
		ax.spines['right'].set_visible(False)
		for bar, count in zip(bars, counts):
			ax.text(bar.get_width(), bar.get_y() + bar.get_height() / 2, f' {count}', va='center', fontsize=9)
		fig.tight_layout()

		out = io.BytesIO()
		fig.savefig(out, format='png')
		out.seek(0)
		return out

	buf = await asyncio.to_thread(_render)
	await ctx.reply(file=File(fp=buf, filename='mapstats.png'))


async def activity(ctx, player: Member = None):
	""" Activity heatmap by weekday x hour (IST), last 28 days. """
	interaction = getattr(ctx, 'interaction', None)
	if interaction is not None and not interaction.response.is_done():
		await interaction.response.defer()

	target = None
	if player is not None and (target := await ctx.get_member(player)) is None:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Specified user not found."))

	ts_from = int(time()) - 28 * 86400

	# Day/hour bucketed in IST (UTC+5:30) via CONVERT_TZ on fixed offsets so
	# it doesn't depend on the MySQL server session timezone. With a player
	# we join qc_player_matches to scope to their participations; otherwise
	# we count distinct matches channel-wide.
	if target:
		rows = await db.fetchall(
			"""
			SELECT
				DAYOFWEEK(CONVERT_TZ(FROM_UNIXTIME(m.at), '+00:00', '+05:30')) AS dow,
				HOUR(CONVERT_TZ(FROM_UNIXTIME(m.at), '+00:00', '+05:30')) AS hr,
				COUNT(DISTINCT m.match_id) AS count
			FROM qc_matches m
			JOIN qc_player_matches pm ON pm.match_id = m.match_id AND pm.channel_id = m.channel_id
			WHERE m.channel_id = %s AND m.at >= %s AND pm.user_id = %s
			GROUP BY dow, hr
			""",
			[ctx.qc.id, ts_from, target.id]
		)
	else:
		rows = await db.fetchall(
			"""
			SELECT
				DAYOFWEEK(CONVERT_TZ(FROM_UNIXTIME(at), '+00:00', '+05:30')) AS dow,
				HOUR(CONVERT_TZ(FROM_UNIXTIME(at), '+00:00', '+05:30')) AS hr,
				COUNT(*) AS count
			FROM qc_matches
			WHERE channel_id = %s AND at >= %s
			GROUP BY dow, hr
			""",
			[ctx.qc.id, ts_from]
		)

	if not rows:
		raise bot.Exc.NotFoundError(ctx.qc.gt("No activity data yet."))

	def _to_idx(dow):  # MySQL DAYOFWEEK 1=Sun..7=Sat -> 0=Mon..6=Sun
		return (int(dow) + 5) % 7

	grid = [[0] * 24 for _ in range(7)]
	for r in rows:
		grid[_to_idx(r['dow'])][int(r['hr'])] += int(r['count'])

	day_labels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

	def _render():
		from matplotlib.figure import Figure
		from matplotlib.backends.backend_agg import FigureCanvasAgg

		fig = Figure(figsize=(12, 4), dpi=120)
		FigureCanvasAgg(fig)
		ax = fig.add_subplot(111)
		im = ax.imshow(grid, aspect='auto', cmap='magma', origin='upper')
		ax.set_xticks(range(24))
		ax.set_xticklabels([f"{h:02d}" for h in range(24)], fontsize=8)
		ax.set_yticks(range(7))
		ax.set_yticklabels(day_labels)
		ax.set_xlabel('Hour of day (IST)')
		ax.set_ylabel('Day of week')
		scope = f" — {get_nick(target)}" if target else ""
		ax.set_title(f"Activity heatmap by weekday × hour (IST, last 28 days){scope}")
		max_v = max((max(row) for row in grid), default=0)
		threshold = max_v * 0.6
		for d in range(7):
			for h in range(24):
				v = grid[d][h]
				if v:
					ax.text(h, d, str(v), ha='center', va='center',
					        color='black' if v >= threshold else 'white', fontsize=6)
		fig.colorbar(im, ax=ax, label='Matches')
		fig.tight_layout()

		out = io.BytesIO()
		fig.savefig(out, format='png')
		out.seek(0)
		return out

	buf = await asyncio.to_thread(_render)
	await ctx.reply(file=File(fp=buf, filename='activity.png'))
