# -*- coding: utf-8 -*-
"""Global component-interaction router for the /insights 'Show all players' button.

THE COMMAND IS GONE and this router is not. /insights was removed in the
command consolidation, so nothing posts these buttons any more — but the ones
already sitting in channel history still work, and a press on one has to be
answered. Same reason the quiz reveal button survives as a transition
converter: a custom_id written into a live Discord message is a wire format,
and deleting its handler turns an old card into a silent failure rather than
into nothing.

DB-driven (re-queries game_labels on click) so it survives a Railway redeploy,
mirroring the quiz router. Registered as an extra on_interaction listener in
nammaoe2bot/discord/events.py. Only acts on custom_ids starting with 'insights:'; everything
else falls through. Runtime-only (imports nextcord)."""
import traceback

import nextcord

from nammaoe2bot.runtime.console import log


async def _eph(interaction, **kwargs):
	"""Respond ephemerally whether or not the interaction was already acknowledged (mirrors the
	quiz router's _eph) — so a click is never silently dropped."""
	if not interaction.response.is_done():
		await interaction.response.send_message(ephemeral=True, **kwargs)
	else:
		await interaction.followup.send(ephemeral=True, **kwargs)


async def on_insights_interaction(interaction):
	try:
		if interaction.type != nextcord.InteractionType.component:
			return
		cid = (interaction.data or {}).get("custom_id", "")
		# 'insights:full:<use_case>:<days>' — split off days from the right so a use_case key
		# is free to contain ':' in the future.
		if not cid.startswith("insights:full:"):
			return
		try:
			days = int(cid.rsplit(":", 1)[1])
		except (ValueError, IndexError):
			return
		use_case = cid[len("insights:full:"):cid.rfind(":")]
		from nammaoe2bot.derived.classifications import query
		from utils.classifications.registry import REGISTRY
		results = await query.fetch_results(use_case, days)
		board = query.roster(results)
		text, _ = query.leaderboard_text(board, 4000)
		c = REGISTRY.get(use_case)
		embed = nextcord.Embed(
			title="{} - full leaderboard ({} players, last {}d)".format(
				c.title if c else use_case, len(board), days),
			description=text)
		if c and c.trigger_spec:
			embed.set_footer(text="{}: {}".format(c.title, c.trigger_spec))
		await _eph(interaction, embed=embed)
	except Exception as e:
		log.error("insights interaction error: {}\n{}".format(e, traceback.format_exc()))
		try:
			await _eph(interaction, content="Couldn't load the full leaderboard - try again.")
		except Exception:
			pass
