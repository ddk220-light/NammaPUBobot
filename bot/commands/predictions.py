# -*- coding: utf-8 -*-
"""Slash-command handlers for audience predictions and the gold economy. Thin:
the vote lifecycle lives in bot.predictions.flow, persistence in
bot.predictions.store, rendering in bot.predictions.embeds. All bot.predictions
imports are lazy (inside the handlers) so this module loads during the
`from . import commands` step without pulling nextcord-heavy modules early —
the bot.commands.quiz pattern.

These two commands absorbed /gold and /gold_top, which were deleted: the four
formed a 2x2 of {personal, community} x {gold, accuracy} over one activity.
/predictions me now carries the balance and the recent ledger movements,
/predictions leaderboard a gold column.

ELIGIBILITY IS DEFINED ONCE, by ctx.qc.get_lb(). The leaderboard SQL never
joined player_ratings, so this board applied NONE of the three gates the rating
leaderboard uses -- is_hidden, lb_min_matches, lb_last_match_limit -- while the
deleted /gold_top filtered is_hidden itself. Folding onto the unfiltered command
without this would have deleted a protection that existed. Rather than restate
the predicate here (a second definition to drift), we intersect against the
channel's own leaderboard, which is the authority for "who appears on a board".
"""
from nextcord import Member

import bot

__all__ = ['predictions_leaderboard', 'predictions_me']


async def _eligible_user_ids(ctx):
	"""The set of user_ids this channel is willing to show on a public board.

	None means "no opinion" -- an empty leaderboard is not the same statement as
	"everybody is ineligible", and blanking the board because the rating board
	happens to be empty would be a worse answer than showing it unfiltered.
	"""
	rows = await ctx.qc.get_lb()
	if not rows:
		return None
	return {r["user_id"] for r in rows}


async def predictions_leaderboard(ctx, page: int = 1):
	""" Audience standings — one point per correctly called match, plus gold held. """
	from bot import community, identity
	from bot.predictions import embeds, store
	from bot.predictions import gold as bank

	rows = await store.leaderboard(ctx.qc.id)

	eligible = await _eligible_user_ids(ctx)
	if eligible is not None:
		rows = [r for r in rows if r["user_id"] in eligible]

	# Gold is community-scoped, the board channel-scoped; a channel outside a
	# community still has a valid accuracy board, just no balances to show.
	balances = {}
	community_id = await community.community_for_channel(ctx.channel.id)
	if community_id is not None:
		balances = await bank.balances_by_user(community_id)

	names = await identity.profiles_and_names_by_user()
	for r in rows:
		aoe2 = (names.get(r["user_id"]) or {}).get("aoe2_names") or []
		if aoe2:
			r["nick"] = aoe2[0]
		r["balance"] = balances.get(r["user_id"])

	await ctx.reply(embed=embeds.leaderboard_embed(rows, page=max(1, int(page or 1))))


async def predictions_me(ctx, player: Member = None):
	""" Your prediction record, gold balance and recent movements (or another member's). """
	import time

	from bot import community
	from bot.predictions import embeds, store
	from bot.predictions import gold as bank

	target = ctx.author if not player else await ctx.get_member(player)
	if not target:
		raise bot.Exc.SyntaxError(ctx.qc.gt("Specified user not found."))

	correct, total = await store.user_stats(target.id, ctx.qc.id)

	balance, entries, seeded_now = None, [], False
	community_id = await community.community_for_channel(ctx.channel.id)
	if community_id is not None:
		now = int(time.time())
		# One of the two paths that grants a new player their opening stake --
		# it moved here from the deleted /gold. The boot-time bulk seed covers
		# everyone already rated; this catches whoever arrived since.
		seeded_now = await bank.ensure_seeded(community_id, target.id, now)
		balance = await bank.balance(community_id, target.id)
		# Movements are the caller's own ledger only. Reading them for someone
		# else would turn a record lookup into an audit of their betting.
		if target.id == ctx.author.id:
			entries = await bank.recent_entries(community_id, target.id, 8)

	await ctx.reply(embed=embeds.me_embed(
		target.display_name, correct, total, balance, entries, seeded_now))
