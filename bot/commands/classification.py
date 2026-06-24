# -*- coding: utf-8 -*-
"""/classification <key> [days] [player]: who used a play-style classification (e.g. archer_rush)
in the last N days and whether it won. Reads cls_* via bot.classifications.query."""
__all__ = ["classification"]

from nextcord import Member, Embed

from core.database import db
from core.utils import get_nick

import bot


def _wr(d):
	return "{}/{} ({:.0%})".format(d["wins"], d["known"], d["rate"]) if d["known"] else "n/a"


async def classification(ctx, key: str = "archer_rush", days: int = 90, player: Member = None):
	from bot.classifications import query

	try:
		days = max(1, min(int(days), 365))
	except (TypeError, ValueError):
		days = 90

	interaction = getattr(ctx, "interaction", None)
	if interaction is not None and not interaction.response.is_done():
		await interaction.response.defer()

	reg = await db.select_one(["*"], "cls_classifications", {"key": key})
	if not reg:
		return await ctx.error("Unknown classification '{}'.".format(key), title="Classification")
	title = reg.get("title") or key

	profile_ids = None
	if player:
		target = await ctx.get_member(player)
		if not target:
			raise bot.Exc.NotFoundError(ctx.qc.gt("Specified user not found."))
		profile_ids = await query.resolve_profile_ids(target.id)
		if not profile_ids:
			return await ctx.error("No AoE2 data linked for {}.".format(get_nick(target)), title=title)

	games = await query.fetch_games(key, days, profile_ids=profile_ids)
	if not games:
		return await ctx.error("No {} games found in the last {} days.".format(title, days),
		                       title=title)

	s = query.summarize(games)
	embed = Embed(title="{} - last {} days".format(title, days))
	embed.add_field(name="Games / players", value="{} games, {} players".format(
		s["n_games"], s["n_players"]), inline=False)
	embed.add_field(name="Win rate (overall)", value=_wr(s["overall"]), inline=False)
	embed.add_field(name="By commitment (archers before Castle)",
	                value="\n".join("{}: {} games, {}".format(b["bucket"], b["games"], _wr(b))
	                                for b in s["by_commit"]) or "n/a", inline=False)
	embed.add_field(name="Fletching before Castle",
	                value="with: {}\nwithout: {}".format(_wr(s["by_fletching"]["with"]),
	                                                     _wr(s["by_fletching"]["without"])),
	                inline=False)
	embed.add_field(name="Top players",
	                value="\n".join("{} - {} games, {}".format(t["identity"], t["games"], _wr(t))
	                                for t in s["top_players"]) or "n/a",
	                inline=False)
	await ctx.reply(embed=embed)
