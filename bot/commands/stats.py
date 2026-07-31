__all__ = [
	'last_game', 'stats', 'top', 'rank', 'rank_detailed', 'leaderboard',
	'eapm', 'mapstats', 'activity'
]

import io
import asyncio
from time import time
from math import ceil
from nextcord import Member, Embed, Colour, File

from core.utils import get, find, seconds_to_str, get_nick  # noqa: F401
from core.database import db
from core.console import log
from core.config import cfg

import bot


async def last_game(ctx, queue: str = None, player: Member = None, match_id: int = None):
	lg = None

	if match_id:
		lg = await db.select_one(
			['*'], "matches", where=dict(channel_id=ctx.qc.id, match_id=match_id), order_by="match_id", limit=1
		)

	elif queue:
		if queue := find(lambda q: q.name.lower() == queue.lower(), ctx.qc.queues):
			lg = await db.select_one(
				['*'], "matches", where=dict(channel_id=ctx.qc.id, queue_id=queue.id), order_by="match_id", limit=1
			)

	elif player and (member := await ctx.get_member(player)) is not None:
		if match := await db.select_one(
			['match_id'], "match_players", where=dict(channel_id=ctx.qc.id, user_id=member.id),
			order_by="match_id", limit=1
		):
			lg = await db.select_one(
				['*'], "matches", where=dict(channel_id=ctx.qc.id, match_id=match['match_id'])
			)

	else:
		lg = await db.select_one(
			['*'], "matches", where=dict(channel_id=ctx.qc.id), order_by="match_id", limit=1
		)

	if not lg:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Nothing found"))

	players = await db.select(
		['user_id', 'nick', 'team'], "match_players",
		where=dict(match_id=lg['match_id'])
	)
	embed = Embed(colour=Colour(0x50e3c2))
	embed.add_field(name=lg['queue_name'], value=seconds_to_str(int(time()) - lg['reported_at']) + " ago")
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
	""" Slim rating profile: headline, record, recent form, scouting report and the ELO chart. """
	await _rank_profile(ctx, player, detailed=False)


async def rank_detailed(ctx, player: Member = None):
	""" Everything /rank shows, plus streak, peak, civs, duos & rivals and recent rating changes. """
	await _rank_profile(ctx, player, detailed=True)


async def _scouting_report(ctx, user_id):
	""" The scouting-report field's text for `user_id`, read out of this
	channel's community rollup — or None when there is no field to render.

	Three outcomes, and they are three different statements:

	  channel not enrolled in a community -> None. Nothing was ever measured
	    here and nothing is pending; an unenrolled channel is the ordinary
	    state for most channels (bot/community.py), not a linking gap.
	  no rollup row                       -> "Statistics pending linking".
	    The absence IS the signal: an unlinked player gets no row rather than
	    a row of zeros (bot/derived/rollups.py delete(), identity v2 §5).
	  a rollup                            -> its measured lines, each one
	    carrying the sample it rests on (bot/scouting_report.py).

	Split out of _rank_profile so the wiring between those three states is
	testable without driving a two-hundred-line command, its leaderboard
	reads, its prediction lookup and its chart render. """
	from bot import community, scouting_report
	from bot.derived import rollups

	community_id = await community.community_for_channel(ctx.channel.id)
	if community_id is None:
		return None
	return scouting_report.render(await rollups.fetch(community_id, user_id), ctx.qc.gt)


async def _rank_profile(ctx, player: Member = None, detailed: bool = False):
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
			"player_ratings",
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

	record = f"**{p['wins']}**W / **{p['losses']}**L / **{p['draws']}**D"
	if detailed:
		streak = p['streak'] or 0
		streak_badge = f"🔥 {streak}" if streak >= 3 else (f"🧊 {abs(streak)}" if streak <= -3 else str(streak))
		record += "\n" + ctx.qc.gt("Streak") + f": {streak_badge}"
	embed.add_field(name="⚔️ " + ctx.qc.gt("Record"), value=record, inline=True)

	# Audience-prediction record. Omitted for anyone who has never voted, so the
	# profile doesn't grow a "0/0" field for most players. Best-effort: a
	# predictions hiccup must not cost the whole profile.
	try:
		from bot.predictions import store as predictions_store
		from bot.predictions.view import rank_field
		correct, total = await predictions_store.user_stats(target.id, ctx.qc.id)
		if (summary := rank_field(correct, total)) is not None:
			embed.add_field(name="🔮 " + ctx.qc.gt("Predictions"), value=summary, inline=True)
	except Exception:
		pass

	civs = prof.get("civs") or {}
	if detailed:
		if prof.get("peak_rating"):
			embed.add_field(
				name="📈 " + ctx.qc.gt("Peak"),
				value=f"**{prof['peak_rating']}**\n{seconds_to_str(int(time() - prof['peak_at']))} ago",
				inline=True
			)
		if civs.get("most_played"):
			mp = civs["most_played"]
			embed.add_field(
				name="🏰 " + ctx.qc.gt("Most played"), value=f"`{mp['civ']}`\n{mp['games']} games", inline=True
			)

	if prof.get("recent_form"):
		sq = {"W": "🟩", "L": "🟥", "D": "⬛"}
		embed.add_field(
			name=ctx.qc.gt("Recent form"),
			value="".join(sq[r] for r in prof["recent_form"]) + f"  `last {len(prof['recent_form'])}`",
			inline=False
		)

	# The scouting report: measured facts out of this community's
	# player_rollups row, each carrying the sample it rests on. Best-effort
	# like every other piece here — a rollup read that fails costs this field,
	# not the whole profile.
	try:
		scouting = await _scouting_report(ctx, target.id)
	except Exception as e:
		log.error(f"scouting report failed for {target.id}: {e}")
		scouting = None
	if scouting:
		embed.add_field(name="📜 " + ctx.qc.gt("Scouting report"), value=scouting, inline=False)

	if detailed:
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

		# Teammate/nemesis aggregation from gather_profile. The four dashboard
		# duo/rival quadrant cards used to lead here, read off bot/web.py's
		# player_overview_snapshot; that function was deleted with the persona
		# stack it also carried (stage 5a), and the web overview page still
		# renders the quadrants from its own handler.
		mates = []
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
			'rating_history', where=dict(user_id=target.id, channel_id=ctx.qc.rating.channel_id),
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

	# Elo candles for the last 60 days (Mon-Thu / Fri / Sat / Sun slots),
	# rendered off the event loop. Needs two played slots to be worth a picture.
	file = None
	candles = prof.get("elo_candles") or []
	if sum(1 for c in candles if c["games"]) >= 2:
		try:
			png = await asyncio.get_running_loop().run_in_executor(
				None, player_profile.render_elo_candles, candles, get_nick(target)
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


_EAPM_TITLES = {
	"average": "Median eAPM",
	"peak": "Median peak eAPM",
}

# Why a metric can be empty while the other one is full, said in the copy
# rather than left as a blank board. `peak` is the only one that can be empty
# on a busy community, and the reason is not "nobody qualifies" -- it is that
# the figure is derived from per-minute buckets that only games ingested after
# the +4 parser carry, so it fills in as new games are played rather than by
# anybody doing anything.
_EAPM_EMPTY = {
	"average": "No player has {floor} games with a recorded eAPM in the last {days} days.",
	# NAMES THE WORKING BOARD, not just the missing one. Saying only "peak is
	# not recorded" while /rank visibly prints an eAPM reads as a contradiction
	# -- the reader has no way to know those are two different measurements from
	# two different sources, so the message has to say which one they are already
	# looking at. Written after exactly that question was asked.
	"peak": ("Peak eAPM is not recorded yet. It is the median of each game's busiest MINUTE, "
	         "which needs per-minute detail that only games played from now on carry — it fills "
	         "in as those are played ({floor}+ games needed, over the last {days} days).\n"
	         "The eAPM on `/rank` is a different measurement — one figure for a whole game — "
	         "and every game has it. `/eapm` with no metric ranks that one today."),
}


async def eapm(ctx, metric: str = None, page: int = 1):
	""" The community's eAPM ranking over the scouting report's own window.

	Reads the SAME player_rollups rows `/rank` renders (see
	bot/scouting_report.eapm_board for why that matters rather than querying
	game_stats again), so a player's place here and the number on their own
	report can never disagree.

	Two metrics, each on its own sample: `average` ranks the median of a
	player's per-game eAPM, `peak` the median of their per-game busiest minute.
	Neither is a maximum -- see bot/scouting_report.py. """
	from bot import community, identity, player_profile, scouting_report
	from bot.derived import rollups

	metric = (metric or "average").lower()
	if metric not in scouting_report.EAPM_METRICS:
		raise bot.Exc.SyntaxError(ctx.qc.gt("Unknown metric '{metric}'.").format(metric=metric))

	community_id = await community.community_for_channel(ctx.channel.id)
	if community_id is None:
		# The ordinary state for most channels, and NOT a linking gap: nothing
		# was ever measured here, so there is no board rather than an empty one.
		raise bot.Exc.NotFoundError(ctx.qc.gt("This channel is not part of a community with stats."))

	# Hidden players are hidden from THIS leaderboard too. Same read
	# bot/web.py's boards use; `/rating hide_player` that only hid one table
	# would not be hiding anybody.
	hidden = {r["user_id"] for r in await db.fetchall(
		"SELECT DISTINCT user_id FROM player_ratings WHERE is_hidden=1") or []}

	board = scouting_report.eapm_board(
		await rollups.fetch_community(community_id), metric, exclude=hidden)
	if not board["rows"]:
		raise bot.Exc.NotFoundError(ctx.qc.gt(_EAPM_EMPTY[metric]).format(
			floor=scouting_report.MIN_GAMES, days=board["window_days"] or rollups.WINDOW_DAYS))

	page = (page or 1) - 1
	pages = ceil(len(board["rows"]) / 10) or 1
	page = max(0, min(page, pages - 1))
	data = board["rows"][page * 10:(page + 1) * 10]

	names = await identity.profiles_and_names_by_user()
	root_url = getattr(cfg, "WS_ROOT_URL", "")
	medals = {1: "🥇", 2: "🥈", 3: "🥉"}

	places, values, samples = [], [], []
	for n, row in enumerate(data):
		place = (page * 10) + n + 1
		# The in-game name, not a Discord nickname -- the same rule
		# bot/derived/boards.py follows: a board entry describes the account
		# that played the games (bot/identity.py's profiles_and_names_by_user).
		aoe2_names = (names.get(row["user_id"]) or {}).get("aoe2_names") or []
		nick = (aoe2_names[0] if aoe2_names else str(row["user_id"]))[:16]
		places.append(f"{medals.get(place, f'**{place}.**')} "
		              + player_profile.web_profile_link(root_url, row["user_id"], nick))
		values.append(f"**{row['label']}**")
		samples.append(str(row["games"]))

	embed = Embed(title="⚡ " + ctx.qc.gt(_EAPM_TITLES[metric]), colour=Colour(0x3498db))
	embed.add_field(name=ctx.qc.gt("Player"), value="\n".join(places), inline=True)
	embed.add_field(name=ctx.qc.gt("eAPM"), value="\n".join(values), inline=True)
	# The sample sits beside every row for the same reason it does on the
	# report: these are medians over different numbers of games, and a column
	# of them with no counts invites reading 12 games and 120 as equal evidence.
	embed.add_field(name=ctx.qc.gt("Games"), value="\n".join(samples), inline=True)
	embed.set_footer(text=ctx.qc.gt(
		"Page {page} of {pages} · {count} players with {floor}+ games in the last {days} days"
	).format(page=page + 1, pages=pages, count=len(board["rows"]),
	         floor=scouting_report.MIN_GAMES,
	         days=board["window_days"] or rollups.WINDOW_DAYS))
	await ctx.reply(embed=embed)


async def mapstats(ctx, period: str = None):
	""" Channel-wide map popularity as a horizontal bar chart. """
	_period_days = {'1M': 30, '6M': 180, '1Y': 365}
	days = _period_days.get(period) if period else None
	ts_from = int(time()) - days * 86400 if days else None

	at_filter = " AND reported_at >= %s" if ts_from is not None else ""
	params = [ctx.qc.id]
	if ts_from is not None:
		params.append(ts_from)

	# `maps` is stored on matches as a newline-joined string (see
	# bot/stats/stats.py register_match_*). Split it back into rows with a
	# recursive CTE so we can count map frequency in SQL.
	rows = await db.fetchall(
		f"""
		WITH RECURSIVE map_split AS (
			SELECT
				match_id,
				TRIM(SUBSTRING_INDEX(maps, '\n', 1)) AS map_name,
				IF(LOCATE('\n', maps) > 0, SUBSTRING(maps, LOCATE('\n', maps) + 1), NULL) AS remaining
			FROM matches
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
	# we join match_players to scope to their participations; otherwise
	# we count distinct matches channel-wide.
	if target:
		rows = await db.fetchall(
			"""
			SELECT
				DAYOFWEEK(CONVERT_TZ(FROM_UNIXTIME(m.reported_at), '+00:00', '+05:30')) AS dow,
				HOUR(CONVERT_TZ(FROM_UNIXTIME(m.reported_at), '+00:00', '+05:30')) AS hr,
				COUNT(DISTINCT m.match_id) AS count
			FROM matches m
			JOIN match_players pm ON pm.match_id = m.match_id AND pm.channel_id = m.channel_id
			WHERE m.channel_id = %s AND m.reported_at >= %s AND pm.user_id = %s
			GROUP BY dow, hr
			""",
			[ctx.qc.id, ts_from, target.id]
		)
	else:
		rows = await db.fetchall(
			"""
			SELECT
				DAYOFWEEK(CONVERT_TZ(FROM_UNIXTIME(reported_at), '+00:00', '+05:30')) AS dow,
				HOUR(CONVERT_TZ(FROM_UNIXTIME(reported_at), '+00:00', '+05:30')) AS hr,
				COUNT(*) AS count
			FROM matches
			WHERE channel_id = %s AND reported_at >= %s
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
