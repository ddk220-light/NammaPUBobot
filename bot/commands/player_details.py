# -*- coding: utf-8 -*-
"""User-facing /player_details: a player's averaged production-timeline graph over the last N days.
Thin handler — the data + rendering live in bot.replay_stats (query.gather_growth_curve +
chart.render_growth_curve), lazily imported so this module loads cheap. The growth curve is drawn
from the per-event replay_events series, so it covers each linked player's standard-map games
whose replays have been parsed for per-event data.

SWEPT SOURCE, accepted trade: replay_events and replay_techs (the curve's series and its
upgrade annotations) are both retention="sweepable" — bot/derived/sweeper.py deletes their
rows for a community that opted out of keeping raw replay detail once the derived summaries
have been computed, and no summary reconstructs a per-second series. For such a community
gather_growth_curve finds no series to anchor, returns None, and this command answers with
the "No replay stats" message below rather than an empty or misleading chart. That is the
designed outcome, not a bug to fix with a fallback: this chart is one of the things a lean
community gives up. tests/test_replay_stats_query.py pins the None. The sweeper still ships
with DRY_RUN = True."""
__all__ = ["player_details"]

import asyncio

from nextcord import Member, File

from core.utils import get_nick

import bot


async def player_details(ctx, player: Member = None, player2: Member = None, days: int = 90):
    target = ctx.author if not player else await ctx.get_member(player)
    if not target:
        raise bot.Exc.NotFoundError(ctx.qc.gt("Specified user not found."))
    try:
        days = max(1, min(int(days), 365))
    except (TypeError, ValueError):
        days = 90
    other = await ctx.get_member(player2) if player2 else None

    # Querying several tables + rendering can exceed the 3s ack window; defer first.
    interaction = getattr(ctx, "interaction", None)
    if interaction is not None and not interaction.response.is_done():
        await interaction.response.defer()

    from bot.replay_stats import query, chart

    async def _curve(member):
        return await query.gather_growth_curve(await query.resolve_profile_ids(member.id), days=days)

    def _no_stats(member):
        return ctx.error(
            f"No replay stats for {get_nick(member)} in the last {days} days. The production "
            "timeline covers linked players' standard-map games once their replays have been "
            "parsed for per-event data.",
            title="Player details")

    curve = await _curve(target)
    if not curve:
        return await _no_stats(target)
    curve2 = name2 = None
    if other:
        curve2 = await _curve(other)
        if not curve2:
            return await _no_stats(other)
        name2 = get_nick(other)
    # matplotlib is CPU-bound and blocks the 1s think() tick if run inline --
    # same offload as bot/commands/stats.py:496.
    png = await asyncio.to_thread(
        chart.render_growth_curve, get_nick(target), curve, days, curve2=curve2, name2=name2)
    await ctx.reply(file=File(fp=png, filename="production_timeline.png"))
