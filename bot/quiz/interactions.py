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

from nammaoe2bot.runtime.console import log

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
		# A CHEAP FAST PATH, NOT THE AUTHORITY. The gate is the clock as well as
		# the status flag, and the clock half is the load-bearing one: the
		# status flip is the LAST thing the resolve does (money first, terminal
		# status last — see bot/quiz/jobs.py::_reveal), so between the vote
		# snapshot and the close the row still reads status='open'. What shuts
		# the door is store.clamp_closes_at, which _reveal calls before it
		# snapshots the votes.
		#
		# But this check reads a row that a resolve can flip a millisecond
		# later, so PASSING IT PROVES NOTHING — the press and the write are two
		# round-trips, and a clamp landing between them used to leave the vote
		# written after the snapshot: never graded, never paid, and undetectable
		# (the ledger and the balance cache still agree). The authority is
		# store.record_vote / record_vote_multi, which re-evaluate the gate
		# inside their own transaction against the post row locked FOR UPDATE —
		# the same lock the clamp takes — AND against a clock they read
		# themselves under that lock. `now` below is NOT handed to them, on
		# purpose: it is this instant's reading, and by the time the write's
		# transaction gets the row it can be seconds old and older than the
		# clamp it is supposed to lose to. This check survives only to spare the
		# common case (a press on a long-closed card) a transaction it does not
		# need; the refusal that matters is the one honoured below.
		if post["status"] != "open" or now >= int(post["closes_at"]):
			return await _eph(interaction, closed_notice())
		if kind == "reveal":
			# Transition-era card: the one post already open when this deploys
			# still shows the old "Reveal & start" button. Pressing it converts
			# that card in place into the poll format (idempotent) — it does
			# NOT record a vote.
			return await _rerender(interaction, post, voted=False)
		nick = _nick(interaction.user)
		if kind == "mselect":
			values = [int(v) for v in (interaction.data or {}).get("values", [])]
			if not values:
				return await _eph(interaction, "Pick at least one option.")
			landed = await store.record_vote_multi(post["id"], interaction.user.id, nick, values)
		else:
			landed = await store.record_vote(post["id"], interaction.user.id, nick, choice)
		if not landed:
			# The write was refused under the row lock: the poll shut between
			# the read above and this transaction. There is no row, so the card
			# has nothing new to show and "vote counted" would be a lie — the
			# user gets the same closed notice a late press gets.
			return await _eph(interaction, closed_notice())
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


async def _rerender(interaction, post, voted=True):
	"""Answer the press by re-rendering the shared card with the fresh tally —
	the card edit IS the feedback. Old-era EPHEMERAL answer views carry the
	same ans:/msel: custom_ids but live on a DIFFERENT message; blindly
	calling edit_message would paint the shared public card over someone's
	private ephemeral message, so those presses get a plain confirmation
	instead (the vote, if any, is already recorded either way).

	`voted` says whether this press actually wrote a vote, and the fallback
	line has to know: the transition-era 'reveal' press records nothing at
	all, so telling that user "Vote counted" would be a plain untruth about
	their own row. It is a narrow path — reachable only when the post has no
	message_id yet — but a message that lies is worse than no message."""
	from . import embeds
	votes = await store.answers_for_post(post["id"])
	options = json.loads(post["options_json"])
	msg = getattr(interaction, "message", None)
	if msg is not None and post.get("message_id") and msg.id == post["message_id"]:
		return await interaction.response.edit_message(
			embed=embeds.poll_embed(post, votes),
			view=embeds.vote_view(post["id"], options, is_multi_category(post["category"])))
	if voted:
		return await _eph(interaction, "Vote counted — see the quiz card for the tally.")
	return await _eph(
		interaction,
		"No vote recorded — this button only converts the card. "
		"Use the option buttons on the quiz card in the channel to vote.")


def _nick(user):
	return getattr(user, "display_name", None) or getattr(user, "name", None) or str(user.id)


async def _eph(interaction, text):
	if not interaction.response.is_done():
		await interaction.response.send_message(text, ephemeral=True)
	else:
		await interaction.followup.send(text, ephemeral=True)
