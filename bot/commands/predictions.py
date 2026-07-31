# -*- coding: utf-8 -*-
"""Slash-command handlers for audience predictions. Thin: the vote lifecycle
lives in bot.predictions.flow, persistence in bot.predictions.store, rendering in
bot.predictions.embeds. All bot.predictions imports are lazy (inside the handlers)
so this module loads during the `from . import commands` step without pulling
nextcord-heavy modules early — the bot.commands.quiz pattern."""
from nextcord import Member

import bot

__all__ = ['predictions_leaderboard', 'predictions_me']


async def predictions_leaderboard(ctx, page: int = 1):
	""" Audience prediction standings — one point per correctly called match. """
	from bot.predictions import embeds, store

	rows = await store.leaderboard(ctx.qc.id)
	await ctx.reply(embed=embeds.leaderboard_embed(rows, page=max(1, int(page or 1))))


async def predictions_me(ctx, player: Member = None):
	""" Your prediction record (or another member's). """
	from bot.predictions import store
	from bot.predictions.view import rank_field

	target = ctx.author if not player else await ctx.get_member(player)
	if not target:
		raise bot.Exc.SyntaxError(ctx.qc.gt("Specified user not found."))
	correct, total = await store.user_stats(target.id, ctx.qc.id)
	summary = rank_field(correct, total)
	if summary is None:
		await ctx.success(ctx.qc.gt("{member} has not predicted a match yet.").format(
			member=target.display_name))
		return
	await ctx.success(f"**{target.display_name}** — {summary}")
