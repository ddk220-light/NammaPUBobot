from typing import Callable  # noqa: UP035
from asyncio import wait_for, shield
from asyncio.exceptions import TimeoutError as aTimeoutError
from nextcord import Interaction, SlashOption, Member, TextChannel
import traceback
import time

from nammaoe2bot.runtime.client import dc
from nammaoe2bot.runtime.utils import error_embed, ok_embed, parse_duration, get_nick
from nammaoe2bot.runtime.console import log
from nammaoe2bot.runtime.config import cfg

from bot import commands
from nammaoe2bot.exceptions import Exceptions as Exc
from nammaoe2bot.pickup.channel import QueueChannel
from nammaoe2bot.community import enroll_channel


from . import autocomplete, groups
from .context import SlashContext


guild_kwargs = dict(guild_ids=cfg.DC_SLASH_SERVERS) if len(cfg.DC_SLASH_SERVERS) else dict()


def _parse_duration(ctx: SlashContext, s: str):
	try:
		return parse_duration(s)
	except ValueError:
		raise Exc.SyntaxError(ctx.qc.gt("Invalid duration format. Syntax: 3h2m1s or 03:02:01."))


async def run_slash(coro: Callable, interaction: Interaction, **kwargs):
	# get passed time since interaction was created, convert snowflake into timestamp
	passed_time = time.time() - (((int(interaction.id) >> 22) + 1420070400000) / 1000.0)

	if passed_time >= 3.0:  # Interactions must be answered within 3 seconds or they time out
		log.error('Skipping an outdated interaction.')
		return

	if not dc.app.ready:
		await interaction.response.send_message(
			embed=error_embed("Bot is under connection, please try agian later...", title="Error")
		)
		return
	qc = dc.app.channels.get(interaction.channel_id)
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
	except Exc.BotException as e:
		await ctx.error(str(e), title=e.__class__.__name__)
	except Exception as e:
		await ctx.error(str(e), title="RuntimeError")
		log.error("\n".join([
			f"Error processing /slash command {coro.__name__}.",
			f"QC: {ctx.channel.guild.name}>#{ctx.channel.name} ({ctx.qc.id}).",
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
): await run_slash(commands.create_pickup, interaction=interaction, name=name, size=size)


# queue -> ...

@groups.admin_queue.subcommand(name='list', description='List all queues on the channel.')
async def _show_queues(
	interaction: Interaction
): await run_slash(commands.show_queues, interaction=interaction)


@groups.admin_queue.subcommand(name='show', description='Show a queue configuration.')
async def _cfg_queue(
		interaction: Interaction,
		queue: str
): await run_slash(commands.cfg_queue, interaction=interaction, queue=queue)
_cfg_queue.on_autocomplete("queue")(autocomplete.queues)


@groups.admin_queue.subcommand(name='set', description='Configure a queue variable.')
async def _set_queue(
		interaction: Interaction,
		queue: str,
		variable: str,
		value: str
): await run_slash(commands.set_queue, interaction=interaction, queue=queue, variable=variable, value=value)
_set_queue.on_autocomplete("queue")(autocomplete.queues)
_set_queue.on_autocomplete("variable")(autocomplete.queue_variables)


@groups.admin_queue.subcommand(name='delete', description='Delete a queue.')
async def _delete_queue(
	interaction: Interaction,
	queue: str = SlashOption(name="queue", description="Queue name.")
): await run_slash(commands.delete_queue, interaction=interaction, queue=queue)
_delete_queue.on_autocomplete("queue")(autocomplete.queues)


@groups.admin_queue.subcommand(name='add_player', description='Add a player to a queue.')
async def _add_player(
	interaction: Interaction,
	player: Member = SlashOption(name="player", description="Member to add to the queue", verify=False),
	queue: str = SlashOption(name="queue", description="Queue to add to.")
): await run_slash(commands.add_player, interaction=interaction, player=player, queue=queue)


@groups.admin_queue.subcommand(name='remove_player', description='Remove a player from queues.')
async def _remove_player(
	interaction: Interaction,
	player: Member = SlashOption(name="player", description="Member to remove from the queues", verify=False),
	queues: str = SlashOption(name="queues", description="Queues to remove the player from.", required=False)
): await run_slash(commands.remove_player, interaction=interaction, player=player, queues=queues)


@groups.admin_queue.subcommand(name='clear', description='Remove players from the queues.')
async def _reset(
		interaction: Interaction,
		queue: str = SlashOption(name="queue", description="Only clear this queue.", required=False)
): await run_slash(commands.reset, interaction=interaction, queue=queue)
_reset.on_autocomplete("queue")(autocomplete.queues)


@groups.admin_queue.subcommand(name='start', description='Start the queue.')
async def _start_queue(
	interaction: Interaction,
	queue: str
): await run_slash(commands.start, interaction=interaction, queue=queue)
_start_queue.on_autocomplete("queue")(autocomplete.queues)


@groups.admin_queue.subcommand(name='split', description='Split the queue into N separate matches.')
async def _split_queue(
	interaction: Interaction,
	queue: str = SlashOption(),
	group_size: int = SlashOption(description="Amount of players per match", required=False),
	sort_by_rating: bool = SlashOption(description="Sort groups by players ratings", required=False)
): await run_slash(commands.split, interaction=interaction, queue=queue, group_size=group_size, sort_by_rating=sort_by_rating)
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
	if dc.app.channels.get(interaction.channel_id) is not None:
		return await interaction.response.send_message(
			embed=error_embed('This channel is already enabled.'), ephemeral=True
		)

	await interaction.response.send_message(embed=ok_embed('The bot has been enabled.'))
	dc.app.channels[interaction.channel.id] = await QueueChannel.create(interaction.channel, dc.app)
	# Enroll into a community right away — on_ready's enrollment loop only
	# runs once at boot, so without this a channel enabled at runtime has
	# no community_id (community_for_channel() returns None) until the
	# next full restart. See nammaoe2bot/community.py.
	await enroll_channel(interaction.channel)


@groups.admin_channel.subcommand(name='disable', description='Disable the bot on this channel.')
async def disable_channel(
		interaction: Interaction
):
	if not interaction.user.guild_permissions.administrator:
		return await interaction.response.send_message(
			embed=error_embed('You must possess server administrator permissions.'), ephemeral=True
		)
	if (qc := dc.app.channels.get(interaction.channel_id)) is None:
		return await interaction.response.send_message(
			embed=error_embed('This channel is not enabled.'), ephemeral=True
		)

	dc.app.channels.pop(qc.id)
	await interaction.response.send_message(embed=ok_embed('The bot has been disabled.'))


@groups.admin_channel.subcommand(name='delete', description='Delete stats/configs and disable the bot on this channel.')
async def delete_channel(
		interaction: Interaction
):
	if not interaction.user.guild_permissions.administrator:
		return await interaction.response.send_message(
			embed=error_embed('You must possess server administrator permissions.'), ephemeral=True
		)
	if (qc := dc.app.channels.get(interaction.channel_id)) is None:
		return await interaction.response.send_message(
			embed=error_embed('This channel is not enabled.'), ephemeral=True
		)

	for queue in qc.queues:
		await queue.cfg.delete()
	await qc.cfg.delete()
	dc.app.channels.pop(qc.id)
	await interaction.response.send_message(embed=ok_embed('The bot has been disabled.'))


@groups.admin_channel.subcommand(name='show', description='List channel configuration.')
async def cfg_qc(
		interaction: Interaction
): await run_slash(commands.cfg_qc, interaction=interaction)


@groups.admin_channel.subcommand(name='set', description='Configure a channel variable.')
async def _set_qc(
		interaction: Interaction,
		variable: str,
		value: str
): await run_slash(commands.set_qc, interaction=interaction, variable=variable, value=value)
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
	commands.report_admin, interaction=interaction, match_id=match_id, winner_team=winner_team, draw=draw, abort=abort
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
			raise Exc.ValueError("Failed to parse teams arguments.")
		await commands.report_manual(ctx, *args, winners=_winners, losers=_losers, **kwargs)
	await run_slash(_run, interaction=interaction, queue=queue, _winners=winners, _losers=losers, draw=draw)


@groups.admin_match.subcommand(name='sub_player', description='Substitute a player in a match.')
async def _sub_force(
		interaction: Interaction,
		player1: Member = SlashOption(name="player1", description="The player to substitute for.", verify=False),
		player2: Member = SlashOption(name="player2", description="The player to substitute with.", verify=False)
): await run_slash(commands.sub_force, interaction=interaction, player1=player1, player2=player2)


@groups.admin_match.subcommand(name='put', description='Put a player in a team.')
async def _put(
		interaction: Interaction,
		match_id: int,
		player: Member,
		team_name: str = SlashOption(name='team', description='Team name or unpicked')
): await run_slash(commands.put, interaction=interaction, match_id=match_id, player=player, team_name=team_name)
_put.on_autocomplete('team_name')(autocomplete.teams_by_match_id)
_put.on_autocomplete('match_id')(autocomplete.match_ids)


# Undoing a finished match is a match operation, not a stats one — it lived at
# /stats undo_match until the /stats group was retired.
@groups.admin_match.subcommand(name='undo', description='Undo a finished match, reverting its rating changes.')
async def _undo_match(
		interaction: Interaction,
		match_id: int
): await run_slash(commands.undo_match, interaction=interaction, match_id=match_id)


# noadds -> ...

@groups.admin_noadds.subcommand(name='list', description='Show noadds list.')
async def _noadds(
		interaction: Interaction
): await run_slash(commands.show_noadds, interaction=interaction)


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
		await commands.noadd(ctx, *args, duration=_duration, **kwargs)

	await run_slash(_run, interaction=interaction, player=player, _duration=duration, reason=reason)


@groups.admin_noadds.subcommand(name='remove', description='Remove a player from the noadds list.')
async def _forgive(
		interaction: Interaction,
		player: Member = SlashOption(verify=False)
): await run_slash(commands.forgive, interaction=interaction, player=player)


# phrases -> ...

@groups.admin_profile_identity.subcommand(
	name='link', description='Link a Discord member to an AoE2 profile id, replacing any other link.'
)
async def _identity_link(
		interaction: Interaction,
		member: Member = SlashOption(verify=False),
		profile_id: int = SlashOption(description='AoE2 profile id.'),
		additional: bool = SlashOption(
			description='Add this as a second account instead of replacing the member\'s other profiles.',
			required=False, default=False
		)
): await run_slash(
	commands.identity_link, interaction=interaction, member=member, profile_id=profile_id,
	additional=additional
)


@groups.admin_profile_identity.subcommand(
	name='unlink', description='Remove a member\'s link to an AoE2 profile id, with no replacement.'
)
async def _identity_unlink(
		interaction: Interaction,
		member: Member = SlashOption(verify=False),
		profile_id: int = SlashOption(description='AoE2 profile id.')
): await run_slash(commands.identity_unlink, interaction=interaction, member=member, profile_id=profile_id)


@groups.admin_profile_identity.subcommand(
	name='conflicts', description='List open profile_id/Discord-user disagreements awaiting resolution.'
)
async def _identity_conflicts(
		interaction: Interaction,
): await run_slash(commands.identity_conflicts, interaction=interaction)


# rating -> ...

@groups.admin_rating.subcommand(name='seed', description='Set player rating and deviation')
async def _rating_seed(
		interaction: Interaction,
		player: str = SlashOption(verify=False),
		rating: int = SlashOption(),
		deviation: int = SlashOption(required=False)
): await run_slash(commands.rating_seed, interaction=interaction, player=player, rating=rating, deviation=deviation)


@groups.admin_rating.subcommand(name='hide_player', description='Hide player from the leaderboard.')
async def _rating_hide(
		interaction: Interaction,
		player: str = SlashOption(),
): await run_slash(commands.rating_hide, interaction=interaction, player=player, hide=True)


@dc.slash_command(
	name='profile_link', description='Link your Discord account to your AoE2 profile.', **guild_kwargs
)
async def _profile_link(
		interaction: Interaction,
		profile_id: int = SlashOption(
			description='Your AoE2 profile id. Leave empty and I will show you how to find it.',
			required=False, default=None)
): await run_slash(commands.link, interaction=interaction, profile_id=profile_id)


@dc.slash_command(name='add', description='Add yourself to the channel queues.', **guild_kwargs)
async def _add(
	interaction: Interaction,
	queues: str = SlashOption(
		name="queues",
		description="Queues you want to add to.",
		required=False)
): await run_slash(commands.add, interaction=interaction, queues=queues)
_add.on_autocomplete("queues")(autocomplete.queues)


@dc.slash_command(name='remove', description='Remove yourself from the channel queues.', **guild_kwargs)
async def _remove(
	interaction: Interaction,
	queues: str = SlashOption(
		name="queues",
		description="Queues you want to add to.",
		required=False)
): await run_slash(commands.remove, interaction=interaction, queues=queues)
_remove.on_autocomplete("queues")(autocomplete.queues)


@dc.slash_command(name='teams', description='Show teams on your current match.', **guild_kwargs)
async def _teams(
		interaction: Interaction
): await run_slash(commands.show_teams, interaction=interaction)


@dc.slash_command(name='subme', description='Request a substitute', **guild_kwargs)
async def _sub_me(
		interaction: Interaction
): await run_slash(commands.sub_me, interaction=interaction)


@dc.slash_command(
	name='subauto',
	description='Replace a player with the next in queue (yourself if no player given)',
	**guild_kwargs
)
async def _sub_auto(
		interaction: Interaction,
		player: Member = SlashOption(
			name="player", description="Player to replace (defaults to you).",
			required=False, default=None, verify=False
		)
): await run_slash(commands.sub_auto, interaction=interaction, player=player)


@dc.slash_command(
	name='lobby',
	description='Link your live AoE2 game id to this ranked match so the result posts automatically',
	**guild_kwargs
)
async def _lobby2(
		interaction: Interaction,
		gameid: str = SlashOption(name="gameid", description="AoE2 game id (the number in aoe2de://0/<id>)")
): await run_slash(commands.lobby2, interaction=interaction, gameid=gameid)


@dc.slash_command(name='subfor', description='Become a substitute', **guild_kwargs)
async def _sub_for(
		interaction: Interaction,
		player: Member = SlashOption(name="player", description="The player to substitute for.", verify=False)
): await run_slash(commands.sub_for, interaction=interaction, player=player)


@dc.slash_command(name='report', description='Report match result.', **guild_kwargs)
async def _report(
		interaction: Interaction,
		result: str = SlashOption(choices=['loss', 'draw', 'abort'])
): await run_slash(commands.report, interaction=interaction, result=result)


@dc.slash_command(name='rank', description='Show rating profile.', **guild_kwargs)
async def _rank(
		interaction: Interaction,
		player: Member = SlashOption(required=False, verify=False),
		detailed: bool = SlashOption(
			required=False, default=False,
			description='Also show streak, peak, civs, duos & rivals and recent rating changes.'),
): await run_slash(commands.rank, interaction=interaction, player=player, detailed=detailed)


@dc.slash_command(name='leaderboard', description='Show rating leaderboard.', **guild_kwargs)
async def _leaderboard(
		interaction: Interaction,
		page: int = SlashOption(required=False),
): await run_slash(commands.leaderboard, interaction=interaction, page=page)


@groups.admin_rating.subcommand(name='unhide_player', description='Unhide player from the leaderboard.')
async def _rating_unhide(
		interaction: Interaction,
		player: str = SlashOption(verify=False)
): await run_slash(commands.rating_hide, interaction=interaction, player=player, hide=False)


@groups.predictions.subcommand(name='leaderboard', description='Audience prediction standings.')
async def _predictions_leaderboard(
		interaction: Interaction,
		page: int = SlashOption(required=False, description="Page number.")
): await run_slash(commands.predictions_leaderboard, interaction=interaction, page=page or 1)


@groups.predictions.subcommand(name='me', description='Your audience prediction record.')
async def _predictions_me(
		interaction: Interaction,
		player: Member = SlashOption(required=False, description="Whose record to show.")
): await run_slash(commands.predictions_me, interaction=interaction, player=player)


@dc.slash_command(name='quiz_leaderboard', description="Show this week's AoE2 quiz leaderboard.", **guild_kwargs)
async def _quiz_leaderboard(
		interaction: Interaction
): await run_slash(commands.quiz_leaderboard, interaction=interaction)


@groups.admin_quiz.subcommand(name='disable', description='Disable the daily AoE2 quiz.')
async def _quiz_disable(
		interaction: Interaction
): await run_slash(commands.quiz_disable, interaction=interaction)

