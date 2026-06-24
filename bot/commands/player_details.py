# -*- coding: utf-8 -*-
"""User-facing /player_details: a player's build-timeline chart over the last N days, built from
the daily-quiz metric categories. Thin handler — the data + rendering live in bot.replay_stats
(query.gather_timeline_data + chart.render_timeline), lazily imported so this module loads cheap."""
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
    data = await query.gather_timeline_data(profile_ids, days=days)
    if not data:
        return await ctx.error(
            f"No replay stats for {get_nick(target)} in the last {days} days. Replay stats "
            "cover linked players' standard-map games once their replays have been parsed.",
            title="Player details")
    png = chart.render_timeline(get_nick(target), data, days)
    await ctx.reply(file=File(fp=png, filename="build_timeline.png"))
