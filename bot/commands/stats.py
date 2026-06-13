__all__ = ['last_game', 'stats', 'top', 'rank', 'leaderboard', 'mapstats', 'activity']

import io
import asyncio
from time import time
from math import ceil
from nextcord import Member, Embed, Colour, File

from core.utils import get, find, seconds_to_str, get_nick, discord_table  # noqa: F401
from core.database import db

import bot


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
	target = ctx.author if not player else await ctx.get_member(player)
	if not target:
		raise bot.Exc.SyntaxError(ctx.qc.gt("Specified user not found."))

	data = await ctx.qc.get_lb()
	# Figure out leaderboard placement
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

	if p:
		embed = Embed(title=f"__{get_nick(target)}__", colour=Colour(0x7289DA))
		embed.add_field(name="№", value=f"**{place}**", inline=True)
		embed.add_field(name=ctx.qc.gt("Matches"), value=f"**{(p['wins'] + p['losses'] + p['draws'])}**", inline=True)
		if p['rating']:
			embed.add_field(name=ctx.qc.gt("Rank"), value=f"**{ctx.qc.rating_rank(p['rating'])['rank']}**", inline=True)
			embed.add_field(name=ctx.qc.gt("Rating"), value=f"**{p['rating']}**±{p['deviation']}")
		else:
			embed.add_field(name=ctx.qc.gt("Rank"), value="**〈?〉**", inline=True)
			embed.add_field(name=ctx.qc.gt("Rating"), value="**?**")
		embed.add_field(
			name="W/L/D/S",
			value="**{wins}**/**{losses}**/**{draws}**/**{streak}**".format(**p),
			inline=True
		)
		embed.add_field(name=ctx.qc.gt("Winrate"), value="**{}%**\n\u200b".format(
			int(p['wins'] * 100 / (p['wins'] + p['losses'] or 1))
		), inline=True)
		if target.display_avatar:
			embed.set_thumbnail(url=target.display_avatar.url)

		changes = await db.select(
			('at', 'rating_change', 'match_id', 'reason'),
			'qc_rating_history', where=dict(user_id=target.id, channel_id=ctx.qc.rating.channel_id),
			order_by='id', limit=5
		)
		if len(changes):
			embed.add_field(
				name=ctx.qc.gt("Last changes:"),
				value="\n".join(("\u200b \u200b **{change}** \u200b | {ago} ago | {reason}{match_id}".format(
					ago=seconds_to_str(int(time() - c['at'])),
					reason=c['reason'],
					match_id=f"(__{c['match_id']}__)" if c['match_id'] else "",
					change=("+" if c['rating_change'] >= 0 else "") + str(c['rating_change'])
				) for c in changes))
			)
		await ctx.reply(embed=embed)

	else:
		raise bot.Exc.ValueError(ctx.qc.gt("No rating data found."))


async def leaderboard(ctx, page: int = 1):
	page = (page or 1) - 1

	data = await ctx.qc.get_lb()
	pages = ceil(len(await ctx.qc.get_lb())/10)
	data = data[page * 10:(page + 1) * 10]
	if not len(data):
		raise bot.Exc.NotFoundError(ctx.qc.gt("Leaderboard is empty."))

	if ctx.qc.cfg.emoji_ranks:  # display as embed message
		embed = Embed(title=f"Leaderboard - page {page+1} of {pages}", colour=Colour(0x7289DA))
		embed.add_field(
			name="Nickname",
			value="\n".join((
				f'**{(page*10)+n+1}** ' + data[n]['nick'].strip()[:14]
				for n in range(len(data))
			)),
			inline=True
		)
		embed.add_field(
			name="W / L / D",
			value="\n".join((
				f"**{row['wins']}** / **{row['losses']}** / **{row['draws']}** (" +
				str(int(row['wins'] * 100 / ((row['wins'] + row['losses']) or 1))) + "%)"
				for row in data
			)),
			inline=True
		)
		embed.add_field(
			name="Rating",
			value="\n".join((
				ctx.qc.rating_rank(row['rating'])['rank'] + f" **{row['rating']}**"
				for row in data
			)),
			inline=True
		)
		await ctx.reply(embed=embed)
		return

	# display as md table
	await ctx.reply(
		discord_table(
			["№", "Rating〈Ξ〉", "Nickname", "Matches", "W/L/D"],
			[[
				(page * 10) + (n + 1),
				str(data[n]['rating']) + ctx.qc.rating_rank(data[n]['rating'])['rank'],
				data[n]['nick'].strip(),
				int(data[n]['wins'] + data[n]['losses'] + data[n]['draws']),
				"{0}/{1}/{2} ({3}%)".format(  # noqa: UP030
					data[n]['wins'],
					data[n]['losses'],
					data[n]['draws'],
					int(data[n]['wins'] * 100 / ((data[n]['wins'] + data[n]['losses']) or 1))
				)
			] for n in range(len(data))]
		)
	)


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
