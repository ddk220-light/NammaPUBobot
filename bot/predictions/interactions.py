# -*- coding: utf-8 -*-
"""Global component-interaction router for bet buttons. Registered as an
additional on_interaction listener (the client supports multiple handlers per
event). DB-driven — never relies on a live View object, so the buttons keep
working across a Railway redeploy. Foreign interactions fall straight through:
we only act on custom_ids starting with 'bet:'. Imported at runtime by
bot.events, and directly by tests/test_predictions_interactions.py, which runs
the handler for real against fake collaborators."""
import time
import traceback

import nextcord

from core.console import log

from . import embeds, flow, gold, store, view
from .scoring import SEED_AMOUNT, parse_bet_custom_id, parse_cancel_custom_id, pools

# The two things the last-resort handler can truthfully say, kept side by side
# because the difference between them is the difference between one charge and
# two. Neither invites a retry it cannot honour.
BET_FAILED_NOTICE = ("Something went wrong placing that bet — nothing was charged if you "
					 "didn't get a confirmation. Try again.")
BET_LANDED_NOTICE = ("Your bet went through — the gold is staked and your side is locked "
					 "in. Only the confirmation failed, so **don't press again** unless you "
					 "mean to stake more.")


async def on_bet_interaction(interaction):
	# THE POINT-OF-NO-RETURN FLAG. gold.place_bet() commits the balance
	# decrement, the bet row and the ledger row in ONE transaction, so the
	# instant it answers 'ok' the money has moved and pressing the button again
	# would stake it twice. Everything after that call — reading the pools,
	# rendering the confirmation, the Discord round-trip itself — can still
	# fail, and the outer except below is the only thing the user hears from.
	# It therefore has to know which side of the commit it is on: before it,
	# "nothing was charged, try again" is true and reassuring; after it, that
	# same sentence is a false statement that instructs the user to double-pay.
	charged = False
	try:
		if interaction.type != nextcord.InteractionType.component:
			return
		custom_id = (interaction.data or {}).get("custom_id", "")
		cancel_post_id = parse_cancel_custom_id(custom_id)
		if cancel_post_id is not None:
			return await _handle_cancel(interaction, cancel_post_id, int(time.time()))
		route = parse_bet_custom_id(custom_id)
		if route is None:
			return
		post_id, side, stake = route
		now = int(time.time())
		post = await store.get_post(post_id)
		if not post or post["status"] != "open" or now >= post["freezes_at"]:
			return await _eph(interaction, "Betting on this match is closed.")
		# Participants may bet, but only on themselves: a player who could take
		# the opposing side could profit by losing. Spectators are unrestricted.
		team0, team1 = flow._team_ids(post["match_id"])
		is_player = interaction.user.id in team0 or interaction.user.id in team1
		if is_player and interaction.user.id not in (team0 if side == 0 else team1):
			return await _eph(interaction, "You can only bet on yourself — back your own team.")

		from bot import community
		community_id = await community.community_for_channel(post["channel_id"])
		if community_id is None:
			return await _eph(interaction, "This channel keeps no stats — there is no gold here.")

		seeded_now = await gold.ensure_seeded(community_id, interaction.user.id, now)
		status, value = await gold.place_bet(
			community_id, interaction.user.id, post_id, side, stake, _nick(interaction.user), now,
			is_player=is_player)
		if status == "closed":
			# The gate at the top of this handler read the post row before any
			# of the above; place_bet re-read it FOR UPDATE inside the
			# transaction, which is the only place the answer is authoritative.
			# A sweep that closed the book in between wins, and nothing was
			# charged — the whole transaction rolled back.
			return await _eph(interaction, "Betting on this match is closed.")
		if status == "insufficient":
			return await _eph(interaction,
				f"Not enough gold — you hold **{value}** {view.GOLD}. "
				"Playing matches tops you back up.")
		if status == "side_locked":
			locked = post["team0_name"] if value == 0 else post["team1_name"]
			return await _eph(interaction,
				f"You're on **{locked}** this match — bets add up, they don't switch sides.")

		# Committed. (The two rejections above roll the whole transaction back,
		# and ensure_seeded is a grant rather than a charge and is idempotent —
		# a retry that stops short of here costs the user nothing.)
		charged = True

		bets = await store.bets_for(post_id)
		pool0, pool1 = pools(bets)
		mine = next((b for b in bets if b["user_id"] == interaction.user.id), None)
		team = post["team0_name"] if side == 0 else post["team1_name"]
		lines = view.bet_confirm_lines(team, stake, mine["stake"] if mine else stake,
									   pool0, pool1, value)
		if seeded_now:
			lines.insert(0, f"Welcome to the betting floor — you started with {SEED_AMOUNT} {view.GOLD}.")
		await _eph(interaction, "\n".join(lines), component_view=embeds.cancel_view(post_id))
		await _refresh_card(post, pool0, pool1, now)
	except Exception as e:
		log.error(f"bet interaction error: {e}\n{traceback.format_exc()}")
		try:
			# Silence is right when we have already responded: the user is
			# holding their confirmation and a second ephemeral would only
			# muddy it. It is only the no-response-yet case that needs a word,
			# and which word depends entirely on `charged`.
			if not interaction.response.is_done():
				await interaction.response.send_message(
					BET_LANDED_NOTICE if charged else BET_FAILED_NOTICE, ephemeral=True)
		except Exception:
			pass


async def _handle_cancel(interaction, post_id, now):
	"""Back out before the freeze.

	The status/deadline check below is a fast path for a friendly message on a
	stale button — it is NOT the guard. gold.cancel_bet re-reads and row-locks
	the post inside its own transaction and is the only authority on whether
	the book is still open; a status read out here is stale the moment it
	returns.
	"""
	post = await store.get_post(post_id)
	if not post or post["status"] != "open" or now >= post["freezes_at"]:
		return await _eph(interaction, "Betting on this match is closed — bets can no longer be cancelled.")
	from bot import community
	community_id = await community.community_for_channel(post["channel_id"])
	if community_id is None:
		return await _eph(interaction, "This channel keeps no stats — there is no gold here.")
	status, amount = await gold.cancel_bet(community_id, interaction.user.id, post_id, now)
	if status == "closed":
		return await _eph(interaction, "Betting on this match is closed — bets can no longer be cancelled.")
	if status == "nothing":
		return await _eph(interaction, view.NOTHING_TO_CANCEL_NOTICE)
	balance = await gold.balance(community_id, interaction.user.id)
	await _eph(interaction, "\n".join(view.bet_cancelled_lines(amount, balance)))
	bets = await store.bets_for(post_id)
	pool0, pool1 = pools(bets)
	await _refresh_card(post, pool0, pool1, now)


async def _refresh_card(post, pool0, pool1, now):
	"""Best-effort embed update so the card shows the live pools. The buttons
	(the View) are left untouched by omitting `view` from the edit."""
	try:
		from core.client import dc
		channel = dc.get_channel(post["channel_id"])
		if channel is None or not post.get("message_id"):
			return
		message = await channel.fetch_message(post["message_id"])
		minutes = max(0, (post["freezes_at"] - now) // 60)
		await message.edit(embed=embeds.open_embed(
			post["team0_name"], post["team1_name"], minutes, post["match_id"], pool0, pool1))
	except Exception as e:
		log.warning(f"bet card refresh failed (post {post['id']}): {e}")


def _nick(user):
	return getattr(user, "display_name", None) or getattr(user, "name", None) or str(user.id)


async def _eph(interaction, text, component_view=nextcord.utils.MISSING):
	# nextcord 2.6.0's send_message/followup.send treat `view=None` as a real
	# view and dereference it (`view.to_components()` / `view.timeout`) — the
	# sentinel default lets callers omit the view entirely. Named
	# `component_view`, not `view`: this module imports `. import view` and a
	# same-named parameter would shadow that module inside this function.
	if not interaction.response.is_done():
		await interaction.response.send_message(text, ephemeral=True, view=component_view)
	else:
		await interaction.followup.send(text, ephemeral=True, view=component_view)
