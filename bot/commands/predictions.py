# -*- coding: utf-8 -*-
"""Slash-command handlers for audience predictions. Thin: the vote lifecycle
lives in bot.predictions.flow, persistence in bot.predictions.store, rendering in
bot.predictions.embeds. All bot.predictions imports are lazy (inside the handlers)
so this module loads during the `from . import commands` step without pulling
nextcord-heavy modules early — the bot.commands.quiz pattern."""
from nextcord import Member

import bot

__all__ = ['predictions_leaderboard', 'predictions_me', 'gold', 'gold_top']


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


async def gold(ctx):
	""" Your gold balance and recent movements (only you see the reply). """
	import time

	from bot import community
	from bot.predictions import embeds
	from bot.predictions import gold as bank

	community_id = await community.community_for_channel(ctx.channel.id)
	if community_id is None:
		raise bot.Exc.NotFoundError(ctx.qc.gt("This channel is not part of a community with stats."))
	now = int(time.time())
	seeded_now = await bank.ensure_seeded(community_id, ctx.author.id, now)
	balance = await bank.balance(community_id, ctx.author.id)
	entries = await bank.recent_entries(community_id, ctx.author.id, 8)
	# ephemeral works on the fast path (run_slash only defers after ~2.5s, and
	# these are three PK-keyed reads); a deferred reply degrades to public,
	# which leaks nothing but a balance the /gold_top board shows anyway.
	await ctx.reply(embed=embeds.gold_embed(balance, entries, seeded_now), ephemeral=True)


async def gold_top(ctx, page: int = 1):
	""" The community's richest bettors. """
	from core.database import db

	from bot import community, identity
	from bot.predictions import embeds
	from bot.predictions import gold as bank

	community_id = await community.community_for_channel(ctx.channel.id)
	if community_id is None:
		raise bot.Exc.NotFoundError(ctx.qc.gt("This channel is not part of a community with stats."))
	# Hidden players are hidden from THIS board too — same read /eapm uses.
	hidden = {r["user_id"] for r in await db.fetchall(
		"SELECT DISTINCT user_id FROM player_ratings WHERE is_hidden=1") or []}
	names = await identity.profiles_and_names_by_user()
	rows = []
	for r in await bank.top_balances(community_id):
		if r["user_id"] in hidden:
			continue
		aoe2 = (names.get(r["user_id"]) or {}).get("aoe2_names") or []
		rows.append(dict(nick=aoe2[0] if aoe2 else f"user {r['user_id']}", balance=r["balance"]))
	await ctx.reply(embed=embeds.gold_top_embed(rows, page=max(1, int(page or 1))))
