from nextcord import Interaction, Permissions

from nammaoe2bot.runtime.client import dc
from nammaoe2bot.runtime.config import cfg

guild_kwargs = dict(guild_ids=cfg.DC_SLASH_SERVERS) if len(cfg.DC_SLASH_SERVERS) else dict()

# Every subcommand under these groups already calls ctx.check_perms() in its
# handler, but that check runs AFTER a player has found and typed the command —
# Discord itself had no idea they were privileged, so all of them sat in every
# player's slash menu. Declaring the permission here hides the whole group from
# anyone without it, client-side. The in-handler checks stay: this controls
# visibility, not authority.
#
# manage_messages is the floor because it is what the bot's own Perms.MODERATOR
# tier maps to in practice; Discord admins pass it implicitly, and a server owner
# can still override per-role in Server Settings > Integrations.
_admin_kwargs = dict(
	default_member_permissions=Permissions(manage_messages=True),
	**guild_kwargs,
)


@dc.slash_command(name='channel', **_admin_kwargs)
async def admin_channel(interaction: Interaction):
	pass


@dc.slash_command(name='queue', **_admin_kwargs)
async def admin_queue(interaction: Interaction):
	pass


@dc.slash_command(name='match', **_admin_kwargs)
async def admin_match(interaction: Interaction):
	pass


@dc.slash_command(name='rating', **_admin_kwargs)
async def admin_rating(interaction: Interaction):
	pass


@dc.slash_command(name='noadds', **_admin_kwargs)
async def admin_noadds(interaction: Interaction):
	pass


@dc.slash_command(name='profile_identity', **_admin_kwargs)
async def admin_profile_identity(interaction: Interaction):
	pass


@dc.slash_command(name='quiz', **_admin_kwargs)
async def admin_quiz(interaction: Interaction):
	pass


# Public: /predictions me and /predictions leaderboard are player commands.
@dc.slash_command(name='predictions', **guild_kwargs)
async def predictions(interaction: Interaction):
	pass
