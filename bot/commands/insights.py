# -*- coding: utf-8 -*-
"""/insights <use_case> [days] [player]: a shareable leaderboard + winners-vs-losers aggregate
facts for a play-style classification (e.g. archer_rush). Reads cls_* via bot.classifications.query;
factor labels/order/formatting come from the classification's factor_specs."""
__all__ = ["insights"]

from nextcord import Member, Embed

from core.database import db

import bot


def _fmt(kind, v):
	if v is None:
		return "-"
	if kind == "seconds":
		s = int(round(v))
		return "{}:{:02d}".format(s // 60, s % 60)
	if kind == "percent":
		return "{:.0f}%".format(100 * v)
	return "{:.1f}".format(v)


async def insights(ctx, use_case: str = "archer_rush", days: int = 90, player: Member = None):
	from bot.classifications import query
	from utils.classifications.registry import REGISTRY

	try:
		days = max(1, min(int(days), 3650))
	except (TypeError, ValueError):
		days = 90

	interaction = getattr(ctx, "interaction", None)
	if interaction is not None and not interaction.response.is_done():
		await interaction.response.defer()

	reg = await db.select_one(["*"], "cls_classifications", {"key": use_case})
	if not reg:
		return await ctx.error("Unknown use case '{}'.".format(use_case), title="Insights")
	title = reg.get("title") or use_case
	specs = REGISTRY[use_case].factor_specs if use_case in REGISTRY else []

	profile_ids = None
	if player:
		target = await ctx.get_member(player)
		if not target:
			raise bot.Exc.NotFoundError(ctx.qc.gt("Specified user not found."))
		profile_ids = await query.resolve_profile_ids(target.id)
		if not profile_ids:
			return await ctx.error("No AoE2 data linked for that player.", title=title)

	results = await query.fetch_results(use_case, days, profile_ids=profile_ids)
	if not results:
		return await ctx.error("No {} games found in the last {} days.".format(title, days), title=title)

	board = query.roster(results)
	wl = query.winners_vs_losers(results, specs)

	embed = Embed(title="{} - insights (last {}d)".format(title, days))
	embed.description = "{} games | {} players | {} winners / {} losers".format(
		len(results), len(board), wl["n_winners"], wl["n_losers"])

	# Leaderboard, capped to fit Discord's 1024-char field limit (code block keeps alignment).
	lines = ["{:<18} {:>3} {:>3} {:>5}".format("player", "g", "w", "win%")]
	used = len(lines[0])
	shown = 0
	for p in board:
		line = "{:<18} {:>3} {:>3} {:>5}".format(
			(p["identity"] or "?")[:18], p["games"], p["wins"],
			("{}%".format(p["win_pct"]) if p["win_pct"] is not None else "-"))
		if used + len(line) + 1 > 960:
			break
		lines.append(line)
		used += len(line) + 1
		shown += 1
	if shown < len(board):
		lines.append("...and {} more".format(len(board) - shown))
	embed.add_field(name="Leaderboard (by games)", value="```\n" + "\n".join(lines) + "\n```", inline=False)

	flines = ["{:<28} {:>8} {:>8}".format("fact", "winners", "losers")]
	for f in wl["factors"]:
		flines.append("{:<28} {:>8} {:>8}".format(
			f["label"][:28], _fmt(f["kind"], f["winners"]), _fmt(f["kind"], f["losers"])))
	embed.add_field(name="Winners vs losers (averages)", value="```\n" + "\n".join(flines) + "\n```", inline=False)

	await ctx.reply(embed=embed)
