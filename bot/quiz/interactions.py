# -*- coding: utf-8 -*-
"""Global component-interaction router for the quiz poll. Registered as an
additional on_interaction listener (the bot's client supports multiple
handlers per event). DB-driven: it never relies on a live View object, so
vote buttons keep working across a Railway redeploy. Foreign interactions
(slash commands, other features) fall straight through — we only act on
custom_ids starting with 'quiz:'.
Only imported at runtime (by bot.events), never during unit tests."""
import json
import time
import traceback

import nextcord

from core.console import log

from . import store
from .scoring import is_multi_category, parse_custom_id
from .view import closed_notice


async def on_quiz_interaction(interaction):
	try:
		if interaction.type != nextcord.InteractionType.component:
			return
		cid = (interaction.data or {}).get("custom_id", "")
		route = parse_custom_id(cid)
		if route is None:
			return
		kind, post_id, choice = route
		post = await store.get_post(post_id)
		if not post:
			return await _eph(interaction, closed_notice())
		now = int(time.time())
		# The gate is the CLOCK, not the status flag: grading (a later task)
		# runs strictly after closes_at, so refusing presses from closes_at
		# onward — not only status != 'open' — leaves no window where a
		# press and a grade can race each other.
		if post["status"] != "open" or now >= int(post["closes_at"]):
			return await _eph(interaction, closed_notice())
		if kind == "reveal":
			# Transition-era card: the one post already open when this deploys
			# still shows the old "Reveal & start" button. Pressing it converts
			# that card in place into the poll format (idempotent) — it does
			# NOT record a vote.
			return await _rerender(interaction, post)
		nick = _nick(interaction.user)
		if kind == "mselect":
			values = [int(v) for v in (interaction.data or {}).get("values", [])]
			if not values:
				return await _eph(interaction, "Pick at least one option.")
			await store.record_vote_multi(post["id"], interaction.user.id, nick, values, now)
		else:
			await store.record_vote(post["id"], interaction.user.id, nick, choice, now)
		await _rerender(interaction, post)
	except Exception as e:
		log.error(f"quiz interaction error: {e}\n{traceback.format_exc()}")
		# Never fail silently — give the user a hint if we have not responded yet.
		try:
			if not interaction.response.is_done():
				await interaction.response.send_message(
					"Something went wrong opening the quiz — please try again.", ephemeral=True)
		except Exception:
			pass


async def _rerender(interaction, post):
	"""Answer the press by re-rendering the shared card with the fresh tally —
	the card edit IS the feedback. Old-era EPHEMERAL answer views carry the
	same ans:/msel: custom_ids but live on a DIFFERENT message; blindly
	calling edit_message would paint the shared public card over someone's
	private ephemeral message, so those presses get a plain confirmation
	instead (the vote, if any, is already recorded either way)."""
	from . import embeds
	votes = await store.answers_for_post(post["id"])
	options = json.loads(post["options_json"])
	msg = getattr(interaction, "message", None)
	if msg is not None and post.get("message_id") and msg.id == post["message_id"]:
		return await interaction.response.edit_message(
			embed=embeds.poll_embed(post, votes),
			view=embeds.vote_view(post["id"], options, is_multi_category(post["category"])))
	return await _eph(interaction, "Vote counted — see the quiz card for the tally.")


def _nick(user):
	return getattr(user, "display_name", None) or getattr(user, "name", None) or str(user.id)


async def _eph(interaction, text):
	if not interaction.response.is_done():
		await interaction.response.send_message(text, ephemeral=True)
	else:
		await interaction.followup.send(text, ephemeral=True)
