# -*- coding: utf-8 -*-
"""Slash-command handlers for audience predictions and the gold economy. Thin:
the vote lifecycle lives in nammaoe2bot.features.betting.flow, persistence in
nammaoe2bot.features.betting.store, rendering in nammaoe2bot.features.betting.embeds. All nammaoe2bot.features.betting
imports are lazy (inside the handlers) so this module loads during the
`from . import commands` step without pulling nextcord-heavy modules early —
the nammaoe2bot.features.quiz.commands pattern.

These two commands absorbed /gold and /gold_top, which were deleted: the four
formed a 2x2 of {personal, community} x {gold, accuracy} over one activity.
/predictions me now carries the balance and the recent ledger movements,
/predictions leaderboard the standings.

/predictions leaderboard DEFAULTS TO GOLD, and shows it as two columns —
richest and poorest — rather than one ranking. Once gold is the thing people
spend, "who is up and who is broke" is the standing everybody is actually
asking about, and it is a two-ended question: the bottom of a gold board is
as interesting as the top, which is never true of an accuracy board. Accuracy
did not go anywhere; it is `sort:accuracy`, unchanged and still paged.

ELIGIBILITY IS DEFINED ONCE, by ctx.qc.get_lb(). The leaderboard SQL never
joined player_ratings, so this board applied NONE of the three gates the rating
leaderboard uses -- is_hidden, lb_min_matches, lb_last_match_limit -- while the
deleted /gold_top filtered is_hidden itself. Folding onto the unfiltered command
without this would have deleted a protection that existed. Rather than restate
the predicate here (a second definition to drift), we intersect against the
channel's own leaderboard, which is the authority for "who appears on a board".
"""
from nextcord import Member

from nammaoe2bot.exceptions import Exceptions as Exc

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


async def _display_names(rows):
	"""{user_id: name}, preferring the player's linked AoE2 name over whatever
	nick a Discord client happened to report — the same substitution the
	accuracy board has always made, lifted out so both boards make it."""
	from nammaoe2bot.features.identity import resolver as identity

	names = await identity.profiles_and_names_by_user()
	out = {}
	for r in rows:
		aoe2 = (names.get(r["user_id"]) or {}).get("aoe2_names") or []
		if aoe2:
			out[r["user_id"]] = aoe2[0]
	return out


async def predictions_leaderboard(ctx, sort: str = "gold", page: int = 1):
	""" Audience standings — gold held by default, prediction accuracy on request. """
	if (sort or "gold") == "gold":
		return await _gold_leaderboard(ctx)
	return await _accuracy_leaderboard(ctx, page)


async def _gold_leaderboard(ctx):
	"""Who is rich and who is broke, in two columns.

	BUILT FROM THE CHANNEL'S OWN LEADERBOARD, not from the gold table, and the
	direction matters. gold_balances holds a row for everyone the boot seed
	ever touched — including ids that are not players at all — while get_lb() is
	the channel's standing answer to "who may appear on a public board",
	carrying is_hidden and the two activity gates with it. Starting from the
	board and annotating it with gold cannot leak somebody the rating board
	would have hidden; starting from the balances and filtering would depend on
	the filter never being dropped.

	A rated player with no balance row is left off rather than shown as zero:
	they have never been seeded, and "holds nothing" and "has no account here"
	are different statements — the same distinction me_lines already draws.
	"""
	from nammaoe2bot import community
	from nammaoe2bot.features.betting import embeds
	from nammaoe2bot.features.betting import gold as bank

	community_id = await community.community_for_channel(ctx.channel.id)
	if community_id is None:
		raise Exc.NotFoundError(ctx.qc.gt("This channel keeps no stats — there is no gold here."))

	eligible = await ctx.qc.get_lb()
	balances = await bank.balances_by_user(community_id)
	preferred = await _display_names(eligible)

	rows = [
		dict(user_id=r["user_id"],
			 nick=preferred.get(r["user_id"]) or r["nick"],
			 balance=balances[r["user_id"]])
		for r in eligible if r["user_id"] in balances
	]
	# Nick as the tie-break, so the long flat band of players still sitting on
	# the untouched 500 seed keeps a stable order between calls instead of
	# reshuffling on every MySQL whim.
	rows.sort(key=lambda r: (-r["balance"], str(r["nick"]).casefold()))

	await ctx.reply(embed=embeds.gold_leaderboard_embed(rows))


async def _accuracy_leaderboard(ctx, page: int = 1):
	""" One point per correctly called match, plus gold held. """
	from nammaoe2bot import community
	from nammaoe2bot.features.betting import embeds, store
	from nammaoe2bot.features.betting import gold as bank

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

	preferred = await _display_names(rows)
	for r in rows:
		r["nick"] = preferred.get(r["user_id"]) or r["nick"]
		r["balance"] = balances.get(r["user_id"])

	await ctx.reply(embed=embeds.leaderboard_embed(rows, page=max(1, int(page or 1))))


async def predictions_me(ctx, player: Member = None):
	""" Your prediction record, gold balance and recent movements (or another member's). """
	import time

	from nammaoe2bot import community
	from nammaoe2bot.features.betting import embeds, store
	from nammaoe2bot.features.betting import gold as bank

	target = ctx.author if not player else await ctx.get_member(player)
	if not target:
		raise Exc.SyntaxError(ctx.qc.gt("Specified user not found."))

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
