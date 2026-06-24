# -*- coding: utf-8 -*-
"""Global component-interaction router for /insights — the 'Show all players' button.
DB-driven (re-queries cls_* on click) so it survives a Railway redeploy, mirroring the quiz
router. Registered as an extra on_interaction listener in bot.events. Only acts on custom_ids
starting with 'insights:'; everything else falls through. Runtime-only (imports nextcord)."""
import traceback

import nextcord

from core.console import log


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
		from bot.classifications import query
		results = await query.fetch_results(use_case, days)
		board = query.roster(results)
		text, _ = query.leaderboard_text(board, 4000)
		embed = nextcord.Embed(
			title="{} - full leaderboard ({} players, last {}d)".format(use_case, len(board), days),
			description=text)
		await _eph(interaction, embed=embed)
	except Exception as e:
		log.error("insights interaction error: {}\n{}".format(e, traceback.format_exc()))
		try:
			await _eph(interaction, content="Couldn't load the full leaderboard - try again.")
		except Exception:
			pass
