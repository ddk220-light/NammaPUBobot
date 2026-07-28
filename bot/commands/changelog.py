# -*- coding: utf-8 -*-
"""Slash-command handler for /changelog. The history itself is baked into
data/changelog.json at build time (scripts/gen_changelog.py); bot.changelog just
reads it. nextcord-touching imports stay inside the handler, matching
bot.commands.quiz."""
__all__ = ['changelog']

DEFAULT_COUNT = 5
MAX_COUNT = 15


async def changelog(ctx, count: int = DEFAULT_COUNT):
	""" What shipped recently — the newest commits on main for this build. """
	from nextcord import Embed, Colour

	from bot import changelog as changelog_data

	count = min(max(1, int(count or DEFAULT_COUNT)), MAX_COUNT)
	entries = changelog_data.latest(count)
	embed = Embed(
		title="\U0001F4E6 " + ctx.qc.gt("What's new"),
		description="\n".join(changelog_data.format_lines(entries)),
		colour=Colour(0x3BA55D))
	if entries:
		embed.set_footer(text=f"Latest {len(entries)} changes on main")
	await ctx.reply(embed=embed)
