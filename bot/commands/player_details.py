# -*- coding: utf-8 -*-
"""User-facing /player_details: a player's averaged production-timeline graph over the last N days.
Thin handler — the data + rendering live in bot.replay_stats (query.gather_growth_curve +
chart.render_growth_curve), lazily imported so this module loads cheap. The growth curve is drawn
from the per-event rs_player_events series, so it covers each linked player's standard-map games
whose replays have been parsed for per-event data."""
__all__ = ["player_details"]

from nextcord import Member, File

from core.utils import get_nick

import bot


async def player_details(ctx, player: Member = None, days: int = 90):
    target = ctx.author if not player else await ctx.get_member(player)
    if not target:
        raise bot.Exc.NotFoundError(ctx.qc.gt("Specified user not found."))
    try:
        days = max(1, min(int(days), 365))
    except (TypeError, ValueError):
        days = 90

    # Querying several tables + rendering can exceed the 3s ack window; defer first.
    interaction = getattr(ctx, "interaction", None)
    if interaction is not None and not interaction.response.is_done():
        await interaction.response.defer()

    from bot.replay_stats import query, chart
    profile_ids = await query.resolve_profile_ids(target.id)
    curve = await query.gather_growth_curve(profile_ids, days=days)
    if not curve:
        return await ctx.error(
            f"No replay stats for {get_nick(target)} in the last {days} days. The production "
            "timeline covers linked players' standard-map games once their replays have been "
            "parsed for per-event data.",
            title="Player details")
    png = chart.render_growth_curve(get_nick(target), curve, days)
    await ctx.reply(file=File(fp=png, filename="production_timeline.png"))
