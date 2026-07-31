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

Everything personal is EPHEMERAL (the instructions, the current-link view and
both refusals); only the success is public, because a visible "linked" is what
nudges the next player to run it. See _reply_privately for why that is not
ctx.error().

nextcord imports stay inside the functions (bot.commands.changelog's pattern)
so this module loads under a pytest-only CI.
"""
__all__ = ['link']

import bot
from core.console import log

# The player-verifiable profile page. This exact shape is proven in production
# by bot/civ_sync.py:295, which parses it back out of live LobbyBOT embeds --
# which is also why a lobby card is a second place a player can read their id.
INSIGHTS_URL = "https://www.aoe2insights.com/user/relic/{profile_id}/"

# Where anyone can search their own in-game name, in any community.
INSIGHTS_HOME = "https://www.aoe2insights.com/"

# The worked example in the instructions. A real, long-lived profile, so a
# player who pastes the example URL to check sees a genuine page.
EXAMPLE_PROFILE_ID = 612690


async def link(ctx, profile_id: int = None):
	""" Show, explain, or create the caller's AoE2 profile link. """
	owned = (await bot.identity.profiles_for_users([ctx.author.id])).get(ctx.author.id) or []
	if owned:
		# View only -- deliberately before any use of `profile_id`. A player who
		# passes an id here has not made an error, they just cannot act on it.
		await _reply_privately(ctx, await _current_link_embed(ctx, owned))
		return

	if profile_id is None:
		await _reply_privately(ctx, _instructions_embed(ctx))
		return

	profile_id = int(profile_id)
	if profile_id < 1:
		# Verified live 2026-07-30: the API answers `-5` with a 400 and `0` with
		# a 404, and fetch_profile maps a 400 to "unavailable" -- so without this
		# guard a negative id would be reported as the SERVICE being broken. To
		# the player both are one thing, a number that cannot exist, so both are
		# refused here with the one wrong-number message.
		await _refuse(ctx, "Couldn't find that profile", _unknown_id_message(ctx, profile_id))
		return

	from bot.lobby import api as lobby_api

	status, data = await lobby_api.fetch_profile(profile_id)
	if status == "not_found":
		await _refuse(ctx, "Couldn't find that profile", _unknown_id_message(ctx, profile_id))
		return
	if status != "ok":
		await _refuse(ctx, "Couldn't check that just now", ctx.qc.gt(
			"I couldn't reach the AoE2 profile service just now, so nothing was linked. "
			"This isn't a problem with your number — please try again in a minute."
		))
		return

	# From here on the API's own id is canonical, not the number that was typed:
	# it is what the service says the profile IS. They agree in every normal
	# case; if they ever disagreed (an alias, a redirect), writing the typed one
	# would bind an id nothing ever validated.
	profile_id = data["profile_id"]

	# `or None` matters: link_self reads None as "not observed, keep the stored
	# name", while "" would blank a name somebody else's replay had recorded.
	if not await bot.identity.link_self(profile_id, ctx.author.id, data["name"] or None):
		# link_self has already recorded the losing claim in identity_conflicts.
		await _refuse(ctx, "That profile is already linked", ctx.qc.gt(
			"Profile `{profile_id}` is already linked to another player, so I haven't "
			"changed anything. If it really is yours, ask an admin to sort it out."
		).format(profile_id=profile_id))
		return

	# Public, deliberately: the only social proof that this command exists.
	await ctx.success(ctx.qc.gt(
		"You're now linked to AoE2 profile `{profile_id}`{name}.\n{url}\n\n"
		"Open that page and check it's really you — if it isn't, ask an admin to fix it, "
		"as you can't change this yourself."
	).format(
		profile_id=profile_id,
		name=f" — {_bold_name(data['name'])}" if data["name"] else "",
		url=INSIGHTS_URL.format(profile_id=profile_id),
	))

	# A new link is a new constraint for the deduction solver (spec section 4):
	# knowing this player rules them out as a candidate for every profile they
	# share a game with, which can immediately resolve a teammate. Deliberately
	# AFTER the reply, and guarded twice -- run_for_channel already swallows its
	# own failures and skips an unenrolled channel quietly, but by this point the
	# player IS linked and has been told so, so nothing here may turn their
	# successful /link into a red error embed.
	try:
		from bot import identity_solver

		await identity_solver.run_for_channel(ctx.qc.id)
	except Exception as e:
		log.error(f"identity solver trigger failed after /link of profile {profile_id}: {e}")


async def _reply_privately(ctx, embed):
	""" Only the caller sees it, on BOTH interaction paths.

	SlashContext.reply forwards kwargs to interaction.response.send_message
	before a defer and to interaction.followup.send after one, and both honour
	ephemeral=True -- so the flag is passed explicitly rather than relying on a
	context method that only happens to be private. ctx.error() would NOT do:
	it drops ephemeral on its post-defer branch, and /link can reach that branch
	whenever the profile API is slow (fetch_profile waits up to 15s, run_slash
	defers at 2.5s), which is exactly when a failure would go public. """
	await ctx.reply(embed=embed, ephemeral=True)


async def _refuse(ctx, title, message):
	""" A player-facing refusal, private, under a human heading.

	Deliberately not `raise bot.Exc.ValueError`: run_slash_coro renders a
	PubobotException with title=e.__class__.__name__, so every one of these --
	the messages a first-time player is most likely to see -- would arrive under
	a red "ValueError". Nothing here is an internal error; they are ordinary
	answers, so the handler renders them itself. """
	from core.utils import error_embed

	await _reply_privately(ctx, error_embed(message, title=ctx.qc.gt(title)))


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
	""" How to find the number, for somebody who has never heard of it.

	The universal method leads: a name search works in every community, on a
	site anyone can open. Lobby cards are LobbyBOT's, a third-party bot the
	flagship server happens to run -- leading with them would describe something
	most readers of this message will never find, so they are a qualified
	second option. A worked URL shows which number to read: `/user/<id>/` and
	`/user/relic/<id>/` both float around and only the latter is real. """
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
			"**Look yourself up** — search your in-game name on {home} (aoe2companion.com "
			"works too) and open your own profile page. Its address looks like\n"
			"{example_url}\nYour profile id is the number at the end — `{example_id}` in "
			"that example.\n\n"
			"**If your server posts lobby cards** — your name in one is also a link to "
			"your profile page, so you can read your id out of that address the same way."
		).format(
			home=INSIGHTS_HOME,
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
