# -*- coding: utf-8 -*-
"""User-facing /player_details: a player's in-game replay-stats card over the last N days,
reusing the daily-quiz metric categories. Thin handler — aggregation lives in
bot.replay_stats.query (lazily imported so this module loads cheaply with the others)."""
__all__ = ["player_details"]

from nextcord import Member, Embed, Colour

from core.utils import get_nick

import bot

_EMOJI = {"villagers": "\U0001F33E", "age": "⏱️", "tech": "\U0001F52C",
          "military": "⚔️", "by_type": "\U0001F5E1️", "buildings": "\U0001F3DB️"}


def _fmt(unit, v):
    if unit == "seconds":
        return f"{int(v) // 60}:{int(v) % 60:02d}"
    return f"{v:g}"


def _block(section):
    rows = section["rows"]
    wl = max(len(r[0]) for r in rows)
    lines = [f"{lbl:<{wl}}  {_fmt(section['unit'], v):>6}  n={n}" for lbl, v, n in rows]
    return "```\n" + "\n".join(lines) + "\n```"


def _build_embed(target, card):
    desc = [f"Last **{card['days']}** days · **{card['games']}** standard-map games "
            f"· win rate **{card['winrate']}%**"
            + (f" · avg eAPM **{card['eapm']}**" if card["eapm"] is not None else "")]
    if card["civs"]:
        desc.append("Civs: " + ", ".join(f"{c} ×{k}" for c, k in card["civs"]))
    embed = Embed(title=f"__{get_nick(target)}__ — Replay stats",
                  colour=Colour(0x50E3C2), description="\n".join(desc))
    avatar = getattr(target, "display_avatar", None)
    if avatar:
        embed.set_thumbnail(url=avatar.url)
    for s in card["sections"]:
        name = f"{_EMOJI.get(s['key'], '')} {s['title']}".strip()
        embed.add_field(name=name, value=_block(s), inline=False)
    embed.set_footer(text=f"Standard maps since {card['cutoff']} · "
                          f"{card['age_reliable']} age-reliable games for timings · "
                          f"all-map games: {card['all_games']}")
    return embed


async def player_details(ctx, player: Member = None, days: int = 90):
    target = ctx.author if not player else await ctx.get_member(player)
    if not target:
        raise bot.Exc.NotFoundError(ctx.qc.gt("Specified user not found."))
    try:
        days = max(1, min(int(days), 365))
    except (TypeError, ValueError):
        days = 90

    # Aggregation hits the DB across several tables; defer so we don't blow the 3s ack window.
    interaction = getattr(ctx, "interaction", None)
    if interaction is not None and not interaction.response.is_done():
        await interaction.response.defer()

    from bot.replay_stats import query
    profile_ids = await query.resolve_profile_ids(target.id)
    card = await query.gather_player_stats(profile_ids, days=days)
    if not card:
        return await ctx.error(
            f"No replay stats for {get_nick(target)} in the last {days} days. Replay stats "
            "cover linked players' standard-map games once their replays have been parsed.",
            title="Player details")
    await ctx.reply(embed=_build_embed(target, card))
