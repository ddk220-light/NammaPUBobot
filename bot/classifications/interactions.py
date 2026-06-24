# -*- coding: utf-8 -*-
"""Global component-interaction router for /insights — the 'Show all players' button.
DB-driven (re-queries cls_* on click) so it survives a Railway redeploy, mirroring the quiz
router. Registered as an extra on_interaction listener in bot.events. Only acts on custom_ids
starting with 'insights:'; everything else falls through. Runtime-only (imports nextcord)."""
import traceback

import nextcord

from core.console import log


async def on_insights_interaction(interaction):
	try:
		if interaction.type != nextcord.InteractionType.component:
			return
		cid = (interaction.data or {}).get("custom_id", "")
		parts = cid.split(":")
		if len(parts) != 4 or parts[0] != "insights" or parts[1] != "full":
			return
		use_case = parts[2]
		try:
			days = int(parts[3])
		except ValueError:
			return
		from bot.classifications import query
		results = await query.fetch_results(use_case, days)
		board = query.roster(results)
		text, _ = query.leaderboard_text(board, 4000)
		embed = nextcord.Embed(
			title="{} - full leaderboard ({} players, last {}d)".format(use_case, len(board), days),
			description=text)
		if not interaction.response.is_done():
			await interaction.response.send_message(embed=embed, ephemeral=True)
	except Exception as e:
		log.error("insights interaction error: {}\n{}".format(e, traceback.format_exc()))
		try:
			if not interaction.response.is_done():
				await interaction.response.send_message(
					"Couldn't load the full leaderboard - try again.", ephemeral=True)
		except Exception:
			pass
