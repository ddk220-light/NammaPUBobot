# -*- coding: utf-8 -*-
"""Global component-interaction router for the quiz. Registered as an additional
on_interaction listener (the bot's client supports multiple handlers per event).
DB-driven: it never relies on a live View object, so reveal/answer buttons keep
working across a Railway redeploy. Foreign interactions (slash commands, other
features) fall straight through — we only act on custom_ids starting with 'quiz:'.
Only imported at runtime (by bot.events), never during unit tests."""
import json
import time

import nextcord

from core.console import log

from . import store
from .embeds import answer_view, question_embed
from .scoring import grade, parse_custom_id
from .view import already_answered_notice, closed_notice, too_late_notice


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
		if kind == "reveal":
			return await _handle_reveal(interaction, post, now)
		return await _handle_answer(interaction, post, choice, now)
	except Exception as e:
		log.error(f"quiz interaction error (ignored): {e}")


async def _handle_reveal(interaction, post, now):
	if post["status"] != "open" or now > post["closes_at"]:
		return await _eph(interaction, closed_notice())
	options = json.loads(post["options_json"])
	cfg = await store.get_config(post["channel_id"])
	window = int((cfg or {}).get("answer_window") or 180)
	deadline = now + window
	row, _created = await store.record_reveal(
		post["id"], interaction.user.id, _nick(interaction.user), now, deadline)
	if row is None:  # insert race lost and re-select failed; treat as transient
		return await _eph(interaction, "Try tapping reveal again.")
	if row.get("answered_at") is not None:
		return await _eph(interaction, already_answered_notice())
	seconds_left = max(0, int(row.get("deadline_at") or deadline) - now)
	if seconds_left == 0:
		return await _eph(interaction, too_late_notice())
	await interaction.response.send_message(
		embed=question_embed(post["prompt"], options, seconds_left),
		view=answer_view(post["id"], len(options)), ephemeral=True)


async def _handle_answer(interaction, post, choice, now):
	row = await store.get_answer(post["id"], interaction.user.id)
	if not row:
		return await _eph(interaction, "Tap **Reveal & start** first.")
	if row.get("answered_at") is not None:
		return await _eph(interaction, already_answered_notice())
	if post["status"] != "open" or now > int(row["deadline_at"]):
		return await _eph(interaction, too_late_notice())
	is_correct = grade(choice, post["correct_index"])
	response_ms = max(0, (now - int(row["revealed_at"])) * 1000)
	await store.record_answer(post["id"], interaction.user.id, choice, is_correct, now, response_ms)
	await _eph(interaction, "Locked in. The answer is revealed when the quiz closes.")


def _nick(user):
	return getattr(user, "display_name", None) or getattr(user, "name", None) or str(user.id)


async def _eph(interaction, text):
	if not interaction.response.is_done():
		await interaction.response.send_message(text, ephemeral=True)
	else:
		await interaction.followup.send(text, ephemeral=True)
