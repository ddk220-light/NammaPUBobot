# -*- coding: utf-8 -*-
"""/insights <use_case> [days] [aggregate_stats] [player]: a shareable leaderboard (+ optional
winners-vs-losers aggregate facts) for a play-style classification (e.g. archer_rush). The full
leaderboard is one tap away via a 'Show all players' button routed through the global
on_insights_interaction handler (redeploy-safe, like the quiz). Reads cls_* via
bot.classifications.query; factor labels/order/formatting come from the classification's factor_specs."""
__all__ = ["insights"]

import nextcord
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


def _full_button(use_case, days, n):
	# auto_defer=False + custom_id routed via bot.events -> on_insights_interaction (redeploy-safe,
	# never relies on a live View object). See bot/quiz/embeds.py card_view for the same pattern.
	v = nextcord.ui.View(timeout=None, auto_defer=False)
	v.add_item(nextcord.ui.Button(
		style=nextcord.ButtonStyle.secondary, label="Show all {} players".format(n),
		custom_id="insights:full:{}:{}".format(use_case, days)))
	return v


async def insights(ctx, use_case: str = "archer_rush", days: int = 90,
                   aggregate_stats: bool = False, player: Member = None):
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
	condition = (REGISTRY[use_case].trigger_spec if use_case in REGISTRY else reg.get("trigger_spec")) or ""

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
	prev, hidden = query.leaderboard_text(board, 980)

	embed = Embed(title="{} - insights (last {}d)".format(title, days))
	embed.description = "{} games | {} players | {} winners / {} losers".format(
		len(results), len(board), wl["n_winners"], wl["n_losers"])
	if condition:
		embed.set_footer(text="{}: {}".format(title, condition))
	embed.add_field(name="Leaderboard (by games)", value=prev, inline=False)
	if aggregate_stats:
		flines = ["{:<28} {:>8} {:>8}".format("fact", "winners", "losers")]
		for f in wl["factors"]:
			flines.append("{:<28} {:>8} {:>8}".format(
				f["label"][:28], _fmt(f["kind"], f["winners"]), _fmt(f["kind"], f["losers"])))
		embed.add_field(name="Winners vs losers (averages)",
		                value="```\n" + "\n".join(flines) + "\n```", inline=False)

	kwargs = {"embed": embed}
	if hidden > 0 and not player:
		kwargs["view"] = _full_button(use_case, days, len(board))
	await ctx.reply(**kwargs)
