__all__ = ['rank', 'leaderboard']

import io
import asyncio
from time import time
from math import ceil
from nextcord import Member, Embed, Colour, File

from nammaoe2bot.runtime.utils import get, find, seconds_to_str, get_nick  # noqa: F401
from nammaoe2bot.runtime.database import db
from nammaoe2bot.runtime.console import log
from nammaoe2bot.runtime.config import cfg

from nammaoe2bot.exceptions import Exceptions as Exc


async def rank(ctx, player: Member = None, detailed: bool = False):
	"""Rating profile. Slim by default: headline, record, recent form, scouting
	report and the ELO chart. `detailed` adds streak, peak, civs, duos & rivals
	and recent rating changes.

	`detailed` was /rank_detailed, a separate command that called this same
	function with the flag flipped. That is an argument, not a command.
	"""
	await _rank_profile(ctx, player, detailed=bool(detailed))


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
		raise Exc.SyntaxError(ctx.qc.gt("Specified user not found."))

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
		raise Exc.ValueError(ctx.qc.gt("No rating data found."))

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
		raise Exc.NotFoundError(ctx.qc.gt("Leaderboard is empty."))

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


# The explainer's copy, as data so it can be asserted on rather than eyeballed.
# Every claim here is checked against mgz's own source (mgz/model/__init__.py):
#
#   AI_ACTIONS = [ActionEnum.AI_ORDER]
#   if 'player_id' in action_data and action_data['player_id'] in players:
#       if action_type not in AI_ACTIONS:
#           eapm[action_data['player_id']] += 1
#   ...
#   players[player_id].eapm = int(round(eapm[player_id] / ((timestamp/1000)/60)))
#
# — i.e. every recorded ACTION belonging to a real player except AI_ORDER, over
# the game's length in minutes. Do not paraphrase this looser: the whole point
# of the command is that somebody can check their own number against it.
_EAPM_EXPLAINER = (
	(
		"✅ What counts",
		"Every **command** the replay recorded for you:\n"
		"• move, attack, patrol, garrison, set stance or formation\n"
		"• queue units, research techs, place or repair buildings\n"
		"• delete, set gather points, flare, tribute, sell or buy\n"
		"One command = one action, whether it moved a single villager or forty knights.",
	),
	(
		"❌ What does not count",
		"• **Camera movement and unit selection.** The replay never records them, so no "
		"tool can count them — this measures decisions, not mouse activity.\n"
		"• **Orders the AI issued for you** (auto-scout and the like). Excluding those is "
		"the only thing the \"effective\" in eAPM refers to here.",
	),
	(
		"🧮 The arithmetic",
		"**eAPM = commands ÷ game length in minutes**, rounded — measured over the whole "
		"game, not just the busy part, so a long game with a quiet late phase pulls it down.",
	),
	(
		"📊 What the bot shows you",
		"• `/rank` and `/eapm` show your **median** across games in the last "
		"{days} days — the middle game, not the average, so one frantic night cannot "
		"move your figure.\n"
		"• **Peak** is your busiest single minute in a game, and `/eapm metric:peak` "
		"ranks the **median** of those — a typical hard moment, never a personal best.",
	),
	(
		"⚠️ One caveat",
		"Other tools use the name \"eAPM\" for slightly different filters, so this number "
		"will not always match one you have seen elsewhere. It is measured the same way "
		"for everybody here, which is what makes the ranking fair.",
	),
)

