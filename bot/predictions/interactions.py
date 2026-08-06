# -*- coding: utf-8 -*-
"""Global component-interaction router for bet buttons. Registered as an
additional on_interaction listener (the client supports multiple handlers per
event). DB-driven — never relies on a live View object, so the buttons keep
working across a Railway redeploy. Foreign interactions fall straight through:
we only act on custom_ids starting with 'bet:'. Only imported at runtime (by
bot.events), never during unit tests."""
import time
import traceback

import nextcord

from core.console import log

from . import embeds, flow, gold, store, view
from .scoring import SEED_AMOUNT, parse_bet_custom_id, pools


async def on_bet_interaction(interaction):
	try:
		if interaction.type != nextcord.InteractionType.component:
			return
		route = parse_bet_custom_id((interaction.data or {}).get("custom_id", ""))
		if route is None:
			return
		post_id, side, stake = route
		now = int(time.time())
		post = await store.get_post(post_id)
		if not post or post["status"] != "open" or now >= post["freezes_at"]:
			return await _eph(interaction, "Betting on this match is closed.")
		if interaction.user.id in flow._player_ids(post["match_id"]):
			return await _eph(interaction, "Players can't bet on their own match.")

		from bot import community
		community_id = await community.community_for_channel(post["channel_id"])
		if community_id is None:
			return await _eph(interaction, "This channel keeps no stats — there is no gold here.")

		seeded_now = await gold.ensure_seeded(community_id, interaction.user.id, now)
		status, value = await gold.place_bet(
			community_id, interaction.user.id, post_id, side, stake, _nick(interaction.user), now)
		if status == "insufficient":
			return await _eph(interaction,
				f"Not enough gold — you hold **{value}** {view.GOLD}. "
				"Playing matches tops you back up.")
		if status == "side_locked":
			locked = post["team0_name"] if value == 0 else post["team1_name"]
			return await _eph(interaction,
				f"You're on **{locked}** this match — bets add up, they don't switch sides.")

		bets = await store.bets_for(post_id)
		pool0, pool1 = pools(bets)
		mine = next((b for b in bets if b["user_id"] == interaction.user.id), None)
		team = post["team0_name"] if side == 0 else post["team1_name"]
		lines = view.bet_confirm_lines(team, stake, mine["stake"] if mine else stake,
									   pool0, pool1, value)
		if seeded_now:
			lines.insert(0, f"Welcome to the betting floor — you started with {SEED_AMOUNT} {view.GOLD}.")
		await _eph(interaction, "\n".join(lines))
		await _refresh_card(post, pool0, pool1, now)
	except Exception as e:
		log.error(f"bet interaction error: {e}\n{traceback.format_exc()}")
		try:
			if not interaction.response.is_done():
				await interaction.response.send_message(
					"Something went wrong placing that bet — nothing was charged if you "
					"didn't get a confirmation. Try again.", ephemeral=True)
		except Exception:
			pass


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


async def _eph(interaction, text):
	if not interaction.response.is_done():
		await interaction.response.send_message(text, ephemeral=True)
	else:
		await interaction.followup.send(text, ephemeral=True)
