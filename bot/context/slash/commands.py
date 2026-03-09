from typing import Callable
from asyncio import wait_for, shield
from asyncio.exceptions import TimeoutError as aTimeoutError
from nextcord import Interaction, SlashOption, Member, TextChannel, Embed, Colour
import re
import traceback
import time

from core.client import dc
from core.utils import error_embed, ok_embed, parse_duration, get_nick
from core.console import log
from core.config import cfg

import bot
from bot.civ_stats import get_player_civs, pick_balanced_teams, get_today_civs
from bot.redo_teams import parse_embed_match, parse_text_match, captain_matchmaking, Player, embed_contains_match_id, get_all_embed_text


from . import SlashContext, autocomplete, groups


guild_kwargs = dict(guild_ids=cfg.DC_SLASH_SERVERS) if len(cfg.DC_SLASH_SERVERS) else dict()


def _parse_duration(ctx: SlashContext, s: str):
	try:
		return parse_duration(s)
	except ValueError:
		raise bot.Exc.SyntaxError(ctx.qc.gt("Invalid duration format. Syntax: 3h2m1s or 03:02:01."))


async def run_slash(coro: Callable, interaction: Interaction, **kwargs):
	# get passed time since interaction was created, convert snowflake into timestamp
	passed_time = time.time() - (((int(interaction.id) >> 22) + 1420070400000) / 1000.0)

	if passed_time >= 3.0:  # Interactions must be answered within 3 seconds or they time out
		log.error('Skipping an outdated interaction.')
		return

	if not bot.bot_ready:
		await interaction.response.send_message(
			embed=error_embed("Bot is under connection, please try agian later...", title="Error")
		)
		return
	qc = bot.queue_channels.get(interaction.channel_id)
	if qc is None:
		await interaction.response.send_message(embed=error_embed("Not in a queue channel.", title="Error"))
		return

	ctx = SlashContext(qc, interaction)
	try:
		await wait_for(shield(run_slash_coro(ctx, coro, **kwargs)), timeout=max(2.5 - passed_time, 0))
	except (TimeoutError, aTimeoutError):
		log.info('Deferring /slash command')
		await interaction.response.defer()


async def run_slash_coro(ctx: SlashContext, coro: Callable, **kwargs):
	log.command("{} | #{} | {}: /{} {}".format(
		ctx.channel.guild.name, ctx.channel.name, get_nick(ctx.author), coro.__name__, kwargs
	))

	try:
		await coro(ctx, **kwargs)
	except bot.Exc.PubobotException as e:
		await ctx.error(str(e), title=e.__class__.__name__)
	except Exception as e:
		await ctx.error(str(e), title="RuntimeError")
		log.error("\n".join([
			f"Error processing /slash command {coro.__name__}.",
			f"QC: {ctx.channel.guild.name}>#{ctx.channel.name} ({qc.id}).",
			f"Member: {ctx.author} ({ctx.author.id}).",
			f"Kwargs: {kwargs}.",
			f"Exception: {str(e)}. Traceback:\n{traceback.format_exc()}=========="
		]))


@groups.admin_queue.subcommand(name='create_pickup', description='Create new pickup queue.')
async def _create_pickup(
	interaction: Interaction,
	name: str = SlashOption(
		name="name",
		description="Queue name."),
	size: int = SlashOption(
		name="size",
		description="Queue size.",
		required=False,
		default=8
	)
): await run_slash(bot.commands.create_pickup, interaction=interaction, name=name, size=size)


# queue -> ...

@groups.admin_queue.subcommand(name='list', description='List all queues on the channel.')
async def _show_queues(
	interaction: Interaction
): await run_slash(bot.commands.show_queues, interaction=interaction)


@groups.admin_queue.subcommand(name='show', description='Show a queue configuration.')
async def _cfg_queue(
		interaction: Interaction,
		queue: str
): await run_slash(bot.commands.cfg_queue, interaction=interaction, queue=queue)
_cfg_queue.on_autocomplete("queue")(autocomplete.queues)


@groups.admin_queue.subcommand(name='set', description='Configure a queue variable.')
async def _set_queue(
		interaction: Interaction,
		queue: str,
		variable: str,
		value: str
): await run_slash(bot.commands.set_queue, interaction=interaction, queue=queue, variable=variable, value=value)
_set_queue.on_autocomplete("queue")(autocomplete.queues)
_set_queue.on_autocomplete("variable")(autocomplete.queue_variables)


@groups.admin_queue.subcommand(name='delete', description='Delete a queue.')
async def _delete_queue(
	interaction: Interaction,
	queue: str = SlashOption(name="queue", description="Queue name.")
): await run_slash(bot.commands.delete_queue, interaction=interaction, queue=queue)
_delete_queue.on_autocomplete("queue")(autocomplete.queues)


@groups.admin_queue.subcommand(name='add_player', description='Add a player to a queue.')
async def _add_player(
	interaction: Interaction,
	player: Member = SlashOption(name="player", description="Member to add to the queue", verify=False),
	queue: str = SlashOption(name="queue", description="Queue to add to.")
): await run_slash(bot.commands.add_player, interaction=interaction, player=player, queue=queue)


@groups.admin_queue.subcommand(name='remove_player', description='Remove a player from queues.')
async def _remove_player(
	interaction: Interaction,
	player: Member = SlashOption(name="player", description="Member to remove from the queues", verify=False),
	queues: str = SlashOption(name="queues", description="Queues to remove the player from.", required=False)
): await run_slash(bot.commands.remove_player, interaction=interaction, player=player, queues=queues)


@groups.admin_queue.subcommand(name='clear', description='Remove players from the queues.')
async def _reset(
		interaction: Interaction,
		queue: str = SlashOption(name="queue", description="Only clear this queue.", required=False)
): await run_slash(bot.commands.reset, interaction=interaction, queue=queue)
_reset.on_autocomplete("queue")(autocomplete.queues)


@groups.admin_queue.subcommand(name='start', description='Start the queue.')
async def _start_queue(
	interaction: Interaction,
	queue: str
): await run_slash(bot.commands.start, interaction=interaction, queue=queue)
_start_queue.on_autocomplete("queue")(autocomplete.queues)


@groups.admin_queue.subcommand(name='split', description='Split the queue into N separate matches.')
async def _split_queue(
	interaction: Interaction,
	queue: str = SlashOption(),
	group_size: int = SlashOption(description="Amount of players per match", required=False),
	sort_by_rating: bool = SlashOption(description="Sort groups by players ratings", required=False)
): await run_slash(bot.commands.split, interaction=interaction, queue=queue, group_size=group_size, sort_by_rating=sort_by_rating)
_split_queue.on_autocomplete("queue")(autocomplete.queues)


# channel -> ...

@groups.admin_channel.subcommand(name='enable', description='Enable the bot on this channel.')
async def enable_channel(
		interaction: Interaction
):
	if not isinstance(interaction.channel, TextChannel):
		return await interaction.response.send_message(
			embed=error_embed('Must be used on a text channel.'), ephemeral=True
		)
	if not interaction.user.guild_permissions.administrator:
		return await interaction.response.send_message(
			embed=error_embed('You must possess server administrator permissions.'), ephemeral=True
		)
	if bot.queue_channels.get(interaction.channel_id) is not None:
		return await interaction.response.send_message(
			embed=error_embed('This channel is already enabled.'), ephemeral=True
		)

	await interaction.response.send_message(embed=ok_embed('The bot has been enabled.'))
	bot.queue_channels[interaction.channel.id] = await bot.QueueChannel.create(interaction.channel)


@groups.admin_channel.subcommand(name='disable', description='Disable the bot on this channel.')
async def disable_channel(
		interaction: Interaction
):
	if not interaction.user.guild_permissions.administrator:
		return await interaction.response.send_message(
			embed=error_embed('You must possess server administrator permissions.'), ephemeral=True
		)
	if (qc := bot.queue_channels.get(interaction.channel_id)) is None:
		return await interaction.response.send_message(
			embed=error_embed('This channel is not enabled.'), ephemeral=True
		)

	bot.queue_channels.pop(qc.id)
	await interaction.response.send_message(embed=ok_embed('The bot has been disabled.'))


@groups.admin_channel.subcommand(name='delete', description='Delete stats/configs and disable the bot on this channel.')
async def delete_channel(
		interaction: Interaction
):
	if not interaction.user.guild_permissions.administrator:
		return await interaction.response.send_message(
			embed=error_embed('You must possess server administrator permissions.'), ephemeral=True
		)
	if (qc := bot.queue_channels.get(interaction.channel_id)) is None:
		return await interaction.response.send_message(
			embed=error_embed('This channel is not enabled.'), ephemeral=True
		)

	for queue in qc.queues:
		await queue.cfg.delete()
	await qc.cfg.delete()
	bot.queue_channels.pop(qc.id)
	await interaction.response.send_message(embed=ok_embed('The bot has been disabled.'))


@groups.admin_channel.subcommand(name='show', description='List channel configuration.')
async def cfg_qc(
		interaction: Interaction
): await run_slash(bot.commands.cfg_qc, interaction=interaction)


@groups.admin_channel.subcommand(name='set', description='Configure a channel variable.')
async def _set_qc(
		interaction: Interaction,
		variable: str,
		value: str
): await run_slash(bot.commands.set_qc, interaction=interaction, variable=variable, value=value)
_set_qc.on_autocomplete("variable")(autocomplete.qc_variables)


# match -> ...

@groups.admin_match.subcommand(name='report', description='Report a match result as a moderator.')
async def _report_admin(
		interaction: Interaction,
		match_id: int,
		winner_team: str = SlashOption(required=False),
		draw: bool = SlashOption(required=False, default=False),
		abort: bool = SlashOption(required=False, default=False)
): await run_slash(
	bot.commands.report_admin, interaction=interaction, match_id=match_id, winner_team=winner_team, draw=draw, abort=abort
)
_report_admin.on_autocomplete('winner_team')(autocomplete.teams_by_match_id)
_report_admin.on_autocomplete('match_id')(autocomplete.match_ids)


@groups.admin_match.subcommand(name='create', description='Report a rating match manually.')
async def _report_manual(
		interaction: Interaction,
		queue: str,
		winners: str = SlashOption(description="List of won team players separated by space."),
		losers: str = SlashOption(description="List of lost team players separated by space."),
		draw: bool = SlashOption(required=False)
):
	async def _run(ctx, *args, _winners, _losers, **kwargs):
		_winners = [await ctx.get_member(i) for i in _winners.split(" ")]
		_losers = [await ctx.get_member(i) for i in _losers.split(" ")]
		if None in _winners or None in _losers:
			raise bot.Exc.ValueError("Failed to parse teams arguments.")
		await bot.commands.report_manual(ctx, *args, winners=_winners, losers=_losers, **kwargs)
	await run_slash(_run, interaction=interaction, queue=queue, _winners=winners, _losers=losers, draw=draw)


@groups.admin_match.subcommand(name='sub_player', description='Substitute a player in a match.')
async def _sub_force(
		interaction: Interaction,
		player1: Member = SlashOption(name="player1", description="The player to substitute for.", verify=False),
		player2: Member = SlashOption(name="player2", description="The player to substitute with.", verify=False)
): await run_slash(bot.commands.sub_force, interaction=interaction, player1=player1, player2=player2)


@groups.admin_match.subcommand(name='put', description='Put a player in a team.')
async def _put(
		interaction: Interaction,
		match_id: int,
		player: Member,
		team_name: str = SlashOption(name='team', description='Team name or unpicked')
): await run_slash(bot.commands.put, interaction=interaction, match_id=match_id, player=player, team_name=team_name)
_put.on_autocomplete('team_name')(autocomplete.teams_by_match_id)
_put.on_autocomplete('match_id')(autocomplete.match_ids)


# noadds -> ...

@groups.admin_noadds.subcommand(name='list', description='Show noadds list.')
async def _noadds(
		interaction: Interaction
): await run_slash(bot.commands.noadds, interaction=interaction)


@groups.admin_noadds.subcommand(name='add', description='Ban a player from participating in the queues.')
async def _noadd(
		interaction: Interaction,
		player: Member = SlashOption(verify=False),
		duration: str = SlashOption(required=False),
		reason: str = SlashOption(required=False)
):
	async def _run(ctx, *args, _duration=None, **kwargs):
		if _duration:
			_duration = _parse_duration(ctx, _duration)
		await bot.commands.noadd(ctx, *args, duration=_duration, **kwargs)

	await run_slash(_run, interaction=interaction, player=player, _duration=duration, reason=reason)


@groups.admin_noadds.subcommand(name='remove', description='Remove a player from the noadds list.')
async def _forgive(
		interaction: Interaction,
		player: Member = SlashOption(verify=False)
): await run_slash(bot.commands.forgive, interaction=interaction, player=player)


# phrases -> ...

@groups.admin_phrases.subcommand(name='add', description='Add a player phrase.')
async def _phrases_add(
		interaction: Interaction,
		player: Member = SlashOption(verify=False),
		phrase: str = SlashOption()
): await run_slash(bot.commands.phrases_add, interaction=interaction, player=player, phrase=phrase)


@groups.admin_phrases.subcommand(name='clear', description='Clear player phrases.')
async def _phrases_clear(
		interaction: Interaction,
		player: Member = SlashOption(verify=False),
): await run_slash(bot.commands.phrases_clear, interaction=interaction, player=player)


# rating -> ...

@groups.admin_rating.subcommand(name='seed', description='Set player rating and deviation')
async def _rating_seed(
		interaction: Interaction,
		player: str = SlashOption(verify=False),
		rating: int = SlashOption(),
		deviation: int = SlashOption(required=False)
): await run_slash(bot.commands.rating_seed, interaction=interaction, player=player, rating=rating, deviation=deviation)


@groups.admin_rating.subcommand(name='penality', description='Subtract points from player rating.')
async def _rating_penality(
		interaction: Interaction,
		player: str = SlashOption(verify=False),
		points: int = SlashOption(),
		reason: str = SlashOption(required=False)
): await run_slash(bot.commands.rating_penality, interaction=interaction, player=player, penality=points, reason=reason)


@groups.admin_rating.subcommand(name='hide_player', description='Hide player from the leaderboard.')
async def _rating_hide(
		interaction: Interaction,
		player: str = SlashOption(),
): await run_slash(bot.commands.rating_hide, interaction=interaction, player=player, hide=True)


@groups.admin_rating.subcommand(name='reset', description='Reset rating data on the channel.')
async def _rating_reset(
		interaction: Interaction
): await run_slash(bot.commands.rating_reset, interaction=interaction)


@groups.admin_rating.subcommand(name='snap', description='Snap players ratings to rank values.')
async def _rating_snap(
		interaction: Interaction
): await run_slash(bot.commands.rating_snap, interaction=interaction)


# stats -> ...

@groups.admin_stats.subcommand(name='show', description='Show channel or player stats.')
async def _stats(
		interaction: Interaction,
		player: Member = SlashOption(required=False, verify=False),
): await run_slash(bot.commands.stats, interaction=interaction, player=player)


@groups.admin_stats.subcommand(name='reset', description='Reset all stats data on the channel.')
async def _stats_reset(
		interaction: Interaction
): await run_slash(bot.commands.stats_reset, interaction=interaction)


@groups.admin_stats.subcommand(name='reset_player', description='Reset player stats.')
async def _stats_reset_player(
		interaction: Interaction,
		player: str = SlashOption(verify=False)
): await run_slash(bot.commands.stats_reset_player, interaction=interaction, player=player)


@groups.admin_stats.subcommand(name='stats_replace_player', description='Replace player1 with player2.')
async def _stats_replace_player(
		interaction: Interaction,
		player1: str = SlashOption(verify=False),
		player2: str = SlashOption(verify=False)
): await run_slash(bot.commands.stats_replace_player, interaction=interaction, player1=player1, player2=player2)


@groups.admin_stats.subcommand(name='undo_match', description='Undo a finished match.')
async def _stats_undo_match(
		interaction: Interaction,
		match_id: int
): await run_slash(bot.commands.undo_match, interaction=interaction, match_id=match_id)


# root commands

@dc.slash_command(name='namma_add', description='Add yourself to the channel queues.', **guild_kwargs)
async def _add(
	interaction: Interaction,
	queues: str = SlashOption(
		name="queues",
		description="Queues you want to add to.",
		required=False)
): await run_slash(bot.commands.add, interaction=interaction, queues=queues)
_add.on_autocomplete("queues")(autocomplete.queues)


@dc.slash_command(name='namma_remove', description='Remove yourself from the channel queues.', **guild_kwargs)
async def _remove(
	interaction: Interaction,
	queues: str = SlashOption(
		name="queues",
		description="Queues you want to add to.",
		required=False)
): await run_slash(bot.commands.remove, interaction=interaction, queues=queues)
_remove.on_autocomplete("queues")(autocomplete.queues)


@dc.slash_command(name='namma_who', description='List added players.', **guild_kwargs)
async def _who(
	interaction: Interaction,
	queues: str = SlashOption(
		name="queues",
		description="Specify queues to list.",
		required=False)
): await run_slash(bot.commands.who, interaction=interaction, queues=queues)
_who.on_autocomplete("queues")(autocomplete.queues)


@dc.slash_command(name='namma_promote', description='Promote a queue.', **guild_kwargs)
async def promote(
		interaction: Interaction,
		queue: str = SlashOption(required=False)
): await run_slash(bot.commands.promote, interaction=interaction, queue=queue)
promote.on_autocomplete("queue")(autocomplete.queues)


@dc.slash_command(name='namma_subscribe', description='Subscribe to a queue promotion role.', **guild_kwargs)
async def subscribe(
		interaction: Interaction,
		queues: str
): await run_slash(bot.commands.subscribe, interaction=interaction, queues=queues, unsub=False)
subscribe.on_autocomplete("queues")(autocomplete.queues)


@dc.slash_command(name='namma_unsubscribe', description='Unsubscribe from a queue promotion role.', **guild_kwargs)
async def unsubscribe(
		interaction: Interaction,
		queues: str
): await run_slash(bot.commands.subscribe, interaction=interaction, queues=queues, unsub=True)
unsubscribe.on_autocomplete("queues")(autocomplete.queues)


@dc.slash_command(name='namma_server', description='Show queue server.', **guild_kwargs)
async def server(
		interaction: Interaction,
		queue: str
): await run_slash(bot.commands.server, interaction=interaction, queue=queue)
server.on_autocomplete("queue")(autocomplete.queues)


@dc.slash_command(name='namma_maps', description='List a queue maps.', **guild_kwargs)
async def maps(
		interaction: Interaction,
		queue: str
): await run_slash(bot.commands.maps, interaction=interaction, queue=queue, one=False)
maps.on_autocomplete("queue")(autocomplete.queues)


@dc.slash_command(name='namma_map', description='Print a random map.', **guild_kwargs)
async def _map(
		interaction: Interaction,
		queue: str
): await run_slash(bot.commands.maps, interaction=interaction, queue=queue, one=True)
_map.on_autocomplete("queue")(autocomplete.queues)


@dc.slash_command(name='namma_matches', description='Show active matches on the channel.', **guild_kwargs)
async def _matches(
		interaction: Interaction
): await run_slash(bot.commands.show_matches, interaction=interaction)


@dc.slash_command(name='namma_teams', description='Show teams on your current match.', **guild_kwargs)
async def _teams(
		interaction: Interaction
): await run_slash(bot.commands.show_teams, interaction=interaction)


@dc.slash_command(name='namma_ready', description='Confirm participation during the check-in stage.', **guild_kwargs)
async def _ready(
		interaction: Interaction
): await run_slash(bot.commands.set_ready, interaction=interaction, is_ready=True)


@dc.slash_command(name='namma_notready', description='Abort participation during the check-in stage.', **guild_kwargs)
async def _not_ready(
		interaction: Interaction
): await run_slash(bot.commands.set_ready, interaction=interaction, is_ready=False)


@dc.slash_command(name='namma_subme', description='Request a substitute', **guild_kwargs)
async def _sub_me(
		interaction: Interaction
): await run_slash(bot.commands.sub_me, interaction=interaction)


@dc.slash_command(name='namma_subfor', description='Become a substitute', **guild_kwargs)
async def _sub_for(
		interaction: Interaction,
		player: Member = SlashOption(name="player", description="The player to substitute for.", verify=False)
): await run_slash(bot.commands.sub_for, interaction=interaction, player=player)


@dc.slash_command(name='namma_capme', description="Leave captain's position.")
async def _cap_me(
		interaction: Interaction,
): await run_slash(bot.commands.cap_me, interaction=interaction)


@dc.slash_command(name='namma_capfor', description='Become a captain', **guild_kwargs)
async def _cap_for(
		interaction: Interaction,
		team: str
): await run_slash(bot.commands.cap_for, interaction=interaction, team_name=team)
_cap_for.on_autocomplete('team')(autocomplete.teams_by_author)


# TODO: make possible to pick multiple players within singe command
@dc.slash_command(name='namma_pick', description='Pick a player.', **guild_kwargs)
async def _pick(
		interaction: Interaction,
		player: Member = SlashOption(name="player", verify=False),
): await run_slash(bot.commands.pick, interaction=interaction, players=[player])


@dc.slash_command(name='namma_report', description='Report match result.', **guild_kwargs)
async def _report(
		interaction: Interaction,
		result: str = SlashOption(choices=['loss', 'draw', 'abort'])
): await run_slash(bot.commands.report, interaction=interaction, result=result)


@dc.slash_command(name='namma_lastgame', description='Show last game details.', **guild_kwargs)
async def _last_game(
		interaction: Interaction,
		queue: str = SlashOption(required=False),
		player: Member = SlashOption(required=False, verify=False),
		match_id: int = SlashOption(required=False)
): await run_slash(bot.commands.last_game, interaction=interaction, queue=queue, player=player, match_id=match_id)
_last_game.on_autocomplete("queue")(autocomplete.queues)


@dc.slash_command(name='namma_top', description='Show top players on the channel.', **guild_kwargs)
async def _top(
		interaction: Interaction,
		period: str = SlashOption(required=False, choices=['day', 'week', 'month', 'year']),
): await run_slash(bot.commands.top, interaction=interaction, period=period)


@dc.slash_command(name='namma_rank', description='Show rating profile.', **guild_kwargs)
async def _rank(
		interaction: Interaction,
		player: Member = SlashOption(required=False, verify=False),
): await run_slash(bot.commands.rank, interaction=interaction, player=player)


@dc.slash_command(name='namma_leaderboard', description='Show rating leaderboard.', **guild_kwargs)
async def _leaderboard(
		interaction: Interaction,
		page: int = SlashOption(required=False),
): await run_slash(bot.commands.leaderboard, interaction=interaction, page=page)


@dc.slash_command(name='player_civ_stats', description='Show best and worst civs for a player.', **guild_kwargs)
async def _player_civ_stats(
		interaction: Interaction,
		player: Member = SlashOption(required=False, verify=False),
):
	target = player or interaction.user
	nick = get_nick(target)

	result = get_player_civs(nick)
	if result is None:
		await interaction.response.send_message(
			embed=error_embed(f"No civ stats found for **{nick}**. They may not have enough matched games."),
			ephemeral=True
		)
		return

	best, worst, total = result

	def format_civs(civs):
		lines = []
		for i, c in enumerate(civs, 1):
			pct = f"{c['winrate'] * 100:.1f}%"
			lines.append(f"**{i}.** {c['civ']} — {pct} ({c['wins']}W / {c['losses']}L, {c['games']} games)")
		return "\n".join(lines)

	embed = Embed(title=f"Civ Stats for {nick}", colour=Colour(0x7289DA))
	embed.add_field(name="Best Civs", value=format_civs(best), inline=False)
	if worst:
		embed.add_field(name="Worst Civs", value=format_civs(worst), inline=False)
	embed.set_footer(text=f"{total} civs with 3+ games")

	if target.display_avatar:
		embed.set_thumbnail(url=target.display_avatar.url)

	await interaction.response.send_message(embed=embed)


@groups.admin_rating.subcommand(name='unhide_player', description='Unhide player from the leaderboard.')
async def _rating_unhide(
		interaction: Interaction,
		player: str = SlashOption(verify=False)
): await run_slash(bot.commands.rating_hide, interaction=interaction, player=player, hide=False)


@dc.slash_command(name='namma_auto_ready', description='Confirm next match check-in automatically.', **guild_kwargs)
async def _auto_ready(
		interaction: Interaction,
		duration: str = SlashOption(required=False),
):
	async def _run(ctx, *args, _duration=None, **kwargs):
		if _duration:
			_duration = _parse_duration(ctx, _duration)
		await bot.commands.auto_ready(ctx, *args, duration=_duration, **kwargs)

	await run_slash(_run, interaction=interaction, _duration=duration)


@dc.slash_command(name='namma_expire', description='Set or show your current expire timer.', **guild_kwargs)
async def _expire(
		interaction: Interaction,
		duration: str = SlashOption(required=False)
):
	async def _run(ctx, *args, _duration=None, **kwargs):
		if _duration:
			_duration = _parse_duration(ctx, _duration)
		await bot.commands.expire(ctx, *args, duration=_duration, **kwargs)

	await run_slash(_run, interaction=interaction, _duration=duration)


@dc.slash_command(name='namma_expire_default', description='Set or show your default expire timer.', **guild_kwargs)
async def _default_expire(
		interaction: Interaction,
		duration: str = SlashOption(required=False),
		afk: bool = SlashOption(required=False),
		clear: bool = SlashOption(required=False)
):
	async def _run(ctx, *args, _duration=None, **kwargs):
		if _duration:
			_duration = _parse_duration(ctx, _duration)
		await bot.commands.default_expire(ctx, *args, duration=_duration, **kwargs)

	await run_slash(_run, interaction=interaction, _duration=duration, afk=afk, clear=clear)


@dc.slash_command(name='namma_allow_offline', description='Switch your offline status immunity.', **guild_kwargs)
async def _allow_offline(
		interaction: Interaction,
): await run_slash(bot.commands.allow_offline, interaction=interaction)


@dc.slash_command(name='namma_switch_dms', description='Toggles DMs on queue start.', **guild_kwargs)
async def _switch_dms(
		interaction: Interaction,
): await run_slash(bot.commands.switch_dms, interaction=interaction)


@dc.slash_command(name='namma_cointoss', description='Toss a coin.', **guild_kwargs)
async def _cointoss(
		interaction: Interaction,
		side: str = SlashOption(choices=['heads', 'tails'], required=False)
): await run_slash(bot.commands.cointoss, interaction=interaction, side=side)


@dc.slash_command(name='namma_help', description='Show channel or queue help.', **guild_kwargs)
async def _help(
		interaction: Interaction,
		queue: str = SlashOption(name="queue", required=False)
): await run_slash(bot.commands.show_help, interaction=interaction, queue=queue)
_help.on_autocomplete("queue")(autocomplete.queues)


@dc.slash_command(name='namma_commands', description='Show commands list.', **guild_kwargs)
async def _commands(
		interaction: Interaction,
): await interaction.response.send_message(cfg.COMMANDS_URL, ephemeral=True)


@dc.slash_command(name='namma_nick', description='Change your nickname with the rating prefix.', **guild_kwargs)
async def _nick(
		interaction: Interaction,
		nick: str
): await run_slash(bot.commands.set_nick, interaction=interaction, nick=nick)


@dc.slash_command(name='namma_randomize_civs', description='Generate balanced random civ pools for two teams.', **guild_kwargs)
async def _randomize_civs(
	interaction: Interaction,
):
	# Defer immediately — channel history fetch can be slow
	await interaction.response.defer()

	if not bot.bot_ready:
		await interaction.followup.send(
			embed=error_embed("Bot is still starting up, please try again later.")
		)
		return

	try:
		# Scan today's matches in this channel
		played_civs = await get_today_civs(interaction.channel)

		# Generate balanced teams
		result = pick_balanced_teams(excluded_civs=played_civs)
		if result is None:
			await interaction.followup.send(
				embed=error_embed("No civ data available. Check that data/civ_elo_stats.csv exists.")
			)
			return

		team_a, team_b = result

		def format_team(civs):
			lines = []
			for c in civs:
				pct = f"{c['winrate'] * 100:.0f}%"
				lines.append(f"{c['civ']} ({pct})")
			return "\n".join(lines)

		avg_a = sum(c["winrate"] for c in team_a) / len(team_a) * 100
		avg_b = sum(c["winrate"] for c in team_b) / len(team_b) * 100

		embed = Embed(title="Randomized Civ Pools", colour=Colour(0x50e3c2))
		embed.add_field(
			name=f"Team A  —  avg {avg_a:.1f}%",
			value=format_team(team_a),
			inline=True
		)
		embed.add_field(
			name=f"Team B  —  avg {avg_b:.1f}%",
			value=format_team(team_b),
			inline=True
		)

		if played_civs:
			embed.set_footer(text=f"Excluded {len(played_civs)} civs played today")
		else:
			embed.set_footer(text="No matches found today — all civs available")

		await interaction.followup.send(embed=embed)

	except Exception as e:
		await interaction.followup.send(
			embed=error_embed(f"Error: {str(e)}", title="Randomize Civs Error")
		)


@dc.slash_command(name='namma_redo_teams', description='Compare old match teams with captain-based matchmaking.', **guild_kwargs)
async def _redo_teams(
	interaction: Interaction,
	match_id: int = SlashOption(description="Match ID from the bot message to compare."),
):
	await interaction.response.defer()

	if not bot.bot_ready:
		await interaction.followup.send(embed=error_embed("Bot is still starting up."))
		return

	qc = bot.queue_channels.get(interaction.channel_id)
	if qc is None:
		await interaction.followup.send(embed=error_embed("Not in a queue channel."))
		return

	# Search channel history for the match message
	target_str = str(match_id)
	found_msg = None
	found_embed = None
	parsed_teams = None

	async for msg in interaction.channel.history(limit=5000):
		# Check embeds (PUBobot sends embeds)
		for emb in msg.embeds:
			if embed_contains_match_id(emb, target_str):
				parsed_teams = parse_embed_match(emb)
				if not parsed_teams:
					# Try parsing all embed text as plain text
					parsed_teams = parse_text_match(get_all_embed_text(emb))
				found_msg = msg
				found_embed = emb
				break
		if found_msg:
			break

		# Check plain text content (case-insensitive)
		content = msg.content or ''
		if target_str in content and re.search(r'match\s*id', content, re.IGNORECASE):
			parsed_teams = parse_text_match(content)
			found_msg = msg
			break

	if not found_msg:
		await interaction.followup.send(
			embed=error_embed(f"Could not find a message with match ID {match_id} in the last 5000 messages.")
		)
		return

	if not parsed_teams:
		# Found the message but couldn't parse teams — show debug info
		debug_parts = []
		if found_embed:
			if found_embed.title:
				debug_parts.append(f"Title: {found_embed.title[:100]}")
			for i, f in enumerate(found_embed.fields):
				debug_parts.append(f"Field {i} name: {(f.name or '')[:80]}")
				debug_parts.append(f"Field {i} value: {(f.value or '')[:80]}")
			if found_embed.footer:
				debug_parts.append(f"Footer: {(found_embed.footer.text or '')[:100]}")
		else:
			debug_parts.append(f"Content: {(found_msg.content or '')[:200]}")
		debug_text = "\n".join(debug_parts) or "No parseable content"
		await interaction.followup.send(
			embed=error_embed(
				f"Found the message but couldn't parse teams.\n```\n{debug_text}\n```",
				title="Parse Error"
			)
		)
		return

	# Resolve player names and collect all players
	guild = interaction.guild
	all_players = []
	name_map = {}

	for team in parsed_teams:
		for p in team['players']:
			uid = p['user_id']
			member = guild.get_member(uid)
			if member is None:
				try:
					member = await guild.fetch_member(uid)
				except Exception:
					pass
			name = get_nick(member) if member else f"User#{uid}"
			name_map[uid] = name
			all_players.append(Player(id=uid, name=name))

	if len(all_players) < 4:
		await interaction.followup.send(
			embed=error_embed(f"Found only {len(all_players)} players. Need at least 4 for team comparison.")
		)
		return

	# Get ratings from our system
	rating_data = await qc.rating.get_players(p.id for p in all_players)
	ratings = {p['user_id']: p['rating'] for p in rating_data}

	# Run captain-based matchmaking
	new_team_a, new_team_b, captains, method = captain_matchmaking(all_players, ratings)

	# Build comparison embed
	def format_old_team(team_data):
		lines = []
		for p in team_data['players']:
			uid = p['user_id']
			rank = p['rank']
			name = name_map.get(uid, '?')
			rating = ratings.get(uid, '?')
			lines.append(f"`〈{rank}〉` {name} ({rating})")
		total = sum(ratings.get(p['user_id'], 0) for p in team_data['players'])
		avg = total // len(team_data['players']) if team_data['players'] else 0
		return "\n".join(lines), total, avg

	def format_new_team(team_players, cap_ids):
		lines = []
		for p in team_players:
			r = ratings[p.id]
			cap = " **[C]**" if p.id in cap_ids else ""
			lines.append(f"{p.name} ({r}){cap}")
		total = sum(ratings[p.id] for p in team_players)
		avg = total // len(team_players) if team_players else 0
		return "\n".join(lines), total, avg

	captain_ids = {c.id for c in captains}

	old_a_text, old_a_total, old_a_avg = format_old_team(parsed_teams[0])
	old_b_text, old_b_total, old_b_avg = format_old_team(parsed_teams[1])
	old_diff = abs(old_a_total - old_b_total)

	new_a_text, new_a_total, new_a_avg = format_new_team(new_team_a, captain_ids)
	new_b_text, new_b_total, new_b_avg = format_new_team(new_team_b, captain_ids)
	new_diff = abs(new_a_total - new_b_total)

	embed = Embed(
		title=f"Team Comparison — Match {match_id}",
		colour=Colour(0x7289DA)
	)

	# Old teams
	embed.add_field(
		name=f"{parsed_teams[0]['emoji']} Old {parsed_teams[0]['name']} 〈{old_a_avg}〉",
		value=old_a_text,
		inline=True
	)
	embed.add_field(
		name=f"{parsed_teams[1]['emoji']} Old {parsed_teams[1]['name']} 〈{old_b_avg}〉",
		value=old_b_text,
		inline=True
	)
	embed.add_field(name="\u200b", value=f"**Elo diff: {old_diff}**", inline=False)

	# New teams
	embed.add_field(
		name=f"🔵 New A 〈{new_a_avg}〉",
		value=new_a_text,
		inline=True
	)
	embed.add_field(
		name=f"🔴 New B 〈{new_b_avg}〉",
		value=new_b_text,
		inline=True
	)
	embed.add_field(name="\u200b", value=f"**Elo diff: {new_diff}** ({method})", inline=False)

	# Summary
	if new_diff < old_diff:
		embed.set_footer(text=f"Captain matchmaking improves balance by {old_diff - new_diff} rating points")
	elif new_diff > old_diff:
		embed.set_footer(text=f"Captain matchmaking is {new_diff - old_diff} rating points worse")
	else:
		embed.set_footer(text="Same balance")

	await interaction.followup.send(embed=embed)

