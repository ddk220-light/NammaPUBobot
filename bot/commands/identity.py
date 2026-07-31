# -*- coding: utf-8 -*-
""" `/link` -- the one identity action a player ever takes.

This is the only way a player in a brand-new community gets linked without an
admin (spec section 2 of
docs/superpowers/specs/2026-07-30-identity-v2-design.md), so its refusals matter
as much as its happy path:

  already linked   -> view only, whether or not an id was passed. Never writes.
  unlinked, no id  -> instructions for finding the id. Never writes.
  unlinked, id     -> validate against the AoE2 API FIRST, then link_self().

A mistyped or nonexistent id must never reach storage, and "no such profile"
must never be conflated with "the AoE2 service is down" -- only the first is
something the player can fix, and telling them the wrong one sends them hunting
for a number that was already correct. bot.lobby.api.fetch_profile's three-way
result exists for exactly that distinction.

Players can never CHANGE a link -- only an admin can, via `/identity link`.
That is stated in every message that could otherwise leave them looking for a
`/unlink` that does not exist.

nextcord imports stay inside the functions (bot.commands.changelog's pattern)
so this module loads under a pytest-only CI.
"""
__all__ = ['link']

import bot

# The player-verifiable profile page. This exact shape is proven in production
# by bot/civ_sync.py:295, which parses it back out of live LobbyBOT embeds --
# which is also why a player can always find their own id in a lobby post.
INSIGHTS_URL = "https://www.aoe2insights.com/user/relic/{profile_id}/"

# The worked example in the instructions. A real, long-lived profile, so a
# player who pastes the example URL to check sees a genuine page.
EXAMPLE_PROFILE_ID = 612690


async def link(ctx, profile_id: int = None):
	""" Show, explain, or create the caller's AoE2 profile link. """
	owned = (await bot.identity.profiles_for_users([ctx.author.id])).get(ctx.author.id) or []
	if owned:
		# View only -- deliberately before any use of `profile_id`. A player who
		# passes an id here has not made an error, they just cannot act on it.
		await ctx.reply(embed=await _current_link_embed(ctx, owned))
		return

	if profile_id is None:
		await ctx.reply(embed=_instructions_embed(ctx))
		return

	profile_id = int(profile_id)
	if profile_id < 1:
		# The API answers a non-positive id with a 400, which fetch_profile maps
		# to "unavailable" -- so without this the player would be told the
		# SERVICE is broken when it is their number that cannot exist.
		raise bot.Exc.ValueError(_unknown_id_message(ctx, profile_id))

	from bot.lobby import api as lobby_api

	status, data = await lobby_api.fetch_profile(profile_id)
	if status == "not_found":
		raise bot.Exc.ValueError(_unknown_id_message(ctx, profile_id))
	if status != "ok":
		raise bot.Exc.ValueError(ctx.qc.gt(
			"I couldn't reach the AoE2 profile service just now, so nothing was linked. "
			"This isn't a problem with your number — please try again in a minute."
		))

	# `or None` matters: link_self reads None as "not observed, keep the stored
	# name", while "" would blank a name somebody else's replay had recorded.
	if not await bot.identity.link_self(profile_id, ctx.author.id, data["name"] or None):
		# link_self has already recorded the losing claim in identity_conflicts.
		raise bot.Exc.ValueError(ctx.qc.gt(
			"Profile `{profile_id}` is already linked to another player, so I haven't "
			"changed anything. If it really is yours, ask an admin to sort it out."
		).format(profile_id=profile_id))

	await ctx.success(ctx.qc.gt(
		"You're now linked to AoE2 profile `{profile_id}`{name}.\n{url}\n\n"
		"Open that page and check it's really you — if it isn't, ask an admin to fix it, "
		"as you can't change this yourself."
	).format(
		profile_id=profile_id,
		name=f" — {_bold_name(data['name'])}" if data["name"] else "",
		url=INSIGHTS_URL.format(profile_id=profile_id),
	))


def _bold_name(name):
	""" An in-game name, bolded for display. Escaped first: names are
	player-chosen and would otherwise inject markdown into the bot's message. """
	from nextcord.utils import escape_markdown

	return f"**{escape_markdown(name)}**"


async def _current_link_embed(ctx, profile_ids):
	""" What the caller is linked to, and the fact that only an admin can change
	it. Every owned profile is listed -- multi-account players are real (see
	relink's `additional` flag), and showing one of three would read as if the
	others had been lost. """
	from nextcord import Embed, Colour

	names = await bot.identity.names_for_profiles(profile_ids)
	embed = Embed(
		title=ctx.qc.gt("Your AoE2 link"),
		description=ctx.qc.gt(
			"You're already linked, so there's nothing to do — your games are being "
			"credited to you. Only an admin can change a link, so ask one if anything "
			"below looks wrong."
		),
		colour=Colour(0x5865F2)
	)
	for pid in sorted(profile_ids):
		value = INSIGHTS_URL.format(profile_id=pid)
		if name := names.get(pid):
			value = f"{_bold_name(name)}\n{value}"
		embed.add_field(
			name=ctx.qc.gt("Profile {profile_id}").format(profile_id=pid),
			value=value,
			inline=False
		)
	return embed


def _instructions_embed(ctx):
	""" How to find the number, for somebody who has never heard of it. Concrete
	sources first (they already have a lobby post), a worked example second. """
	from nextcord import Embed, Colour

	embed = Embed(
		title=ctx.qc.gt("Link your AoE2 profile"),
		description=ctx.qc.gt(
			"You're not linked yet, so none of your games are being credited to you. "
			"It takes one command — I just need your AoE2 profile id, a number that "
			"identifies your game account."
		),
		colour=Colour(0x5865F2)
	)
	embed.add_field(
		name=ctx.qc.gt("Finding your profile id"),
		value=ctx.qc.gt(
			"**From a lobby post** — in the post for any game you played, each player's "
			"name is a link to a page like\n{example_url}\nThe number in that link "
			"({example_id}) is their profile id. Find your own name and read off yours.\n\n"
			"**Or look yourself up** — search your in-game name at "
			"https://www.aoe2insights.com/ and open your page. Your profile id is the "
			"number in the page address."
		).format(
			example_url=INSIGHTS_URL.format(profile_id=EXAMPLE_PROFILE_ID),
			example_id=EXAMPLE_PROFILE_ID
		),
		inline=False
	)
	embed.add_field(
		name=ctx.qc.gt("Then run"),
		value=ctx.qc.gt(
			"`/link profile_id: {example_id}` — with your own number in place of "
			"{example_id}.\n\nCheck the number is really yours first: once a link is "
			"set, only an admin can change it."
		).format(example_id=EXAMPLE_PROFILE_ID),
		inline=False
	)
	return embed


def _unknown_id_message(ctx, profile_id):
	""" Shared by the "no such profile" and "that number cannot exist" refusals
	-- to the player they are the same situation, and one wrong-number message
	is easier to act on than two. """
	return ctx.qc.gt(
		"I couldn't find an AoE2 profile with the id `{profile_id}`, so nothing was "
		"linked. Check the number on your aoe2insights.com page and try again — or run "
		"`/link` with no number and I'll show you where to find it."
	).format(profile_id=profile_id)
