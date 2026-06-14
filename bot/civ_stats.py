import csv
import random
from datetime import datetime, timezone, timedelta
from pathlib import Path

from nextcord import Embed, Colour

from core.database import db

# A civ needs this many games overall before it's used for balanced pools.
MIN_CIV_GAMES = 50

_ELO_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "civ_elo_stats.csv"

# Overall civ win-rates {civ: {"civ", "games", "winrate"}}. Used by the auto-post
# civ-pool suggestion. Seeded from the CSV at import as a fallback, but
# build_suggestion_embed pulls a LIVE copy from qc_match_civs each time.
_civ_elo_data = {}

IST = timezone(timedelta(hours=5, minutes=30))


def load_civ_elo_stats():
    """Seed overall civ winrate stats from CSV into memory (fallback only)."""
    _civ_elo_data.clear()
    try:
        with open(_ELO_DATA_PATH, newline="") as f:
            for row in csv.DictReader(f):
                civ = row["civ"].strip()
                games = int(row["games"])
                if not civ or games < MIN_CIV_GAMES:
                    continue
                _civ_elo_data[civ] = {
                    "civ": civ,
                    "games": games,
                    "winrate": float(row["winrate"]),
                }
    except FileNotFoundError:
        print(f"Warning: {_ELO_DATA_PATH} not found, civ randomization disabled")


async def civ_elo_from_db():
    """Overall civ win-rates aggregated LIVE from qc_match_civs (the table the
    reconcile job keeps current). Returns {civ: {"civ","games","winrate"}}; an
    empty dict if there's not enough data yet (caller falls back to the seed)."""
    rows = await db.fetchall(
        "SELECT civ, SUM(result='W') wins, COUNT(*) games FROM qc_match_civs "
        "WHERE civ IS NOT NULL GROUP BY civ HAVING games >= %s",
        [MIN_CIV_GAMES]
    )
    out = {}
    for r in rows:
        games = int(r["games"] or 0)
        if not games:
            continue
        out[r["civ"]] = {"civ": r["civ"], "games": games, "winrate": int(r["wins"] or 0) / games}
    return out


def get_all_civs():
    """Return dict of all civs with sufficient data (the in-memory seed)."""
    return dict(_civ_elo_data)


def pick_balanced_teams(excluded_civs=None, civ_data=None):
    """Pick 5 civs for each team, balanced by winrate with variety.

    civ_data: optional {civ: {...}} to use instead of the in-memory seed, so
    callers can pass a live DB copy. Returns (team_a, team_b) or None if no data.
    """
    all_civs = civ_data if civ_data is not None else get_all_civs()
    if not all_civs:
        return None

    # Remove excluded civs (case-insensitive match)
    excluded_lower = {c.lower() for c in (excluded_civs or [])}
    available = [c for c in all_civs.values() if c["civ"].lower() not in excluded_lower]

    # If not enough unique civs, allow repeats from the excluded set
    if len(available) < 10:
        available = list(all_civs.values())

    # Sort by winrate to create tiers
    by_wr = sorted(available, key=lambda c: c["winrate"], reverse=True)
    third = len(by_wr) // 3

    top_tier = by_wr[:third]
    mid_tier = by_wr[third:2 * third]
    bot_tier = by_wr[2 * third:]

    # Pick from each tier: 4 top, 2 mid, 4 bottom = 10 total
    def sample(pool, n):
        return random.sample(pool, min(n, len(pool)))

    top_picks = sample(top_tier, 4)
    mid_picks = sample(mid_tier, 2)
    bot_picks = sample(bot_tier, 4)

    all_picks = top_picks + mid_picks + bot_picks

    # If we got fewer than 10 (small pool), pad from remaining available
    if len(all_picks) < 10:
        used = {c["civ"] for c in all_picks}
        remaining = [c for c in available if c["civ"] not in used]
        all_picks += sample(remaining, 10 - len(all_picks))

    # Sort all 10 by winrate descending for snake draft
    all_picks.sort(key=lambda c: c["winrate"], reverse=True)

    # Snake draft: A, B, B, A, A, B, B, A, A, B
    team_a, team_b = [], []
    pattern = [0, 1, 1, 0, 0, 1, 1, 0, 0, 1]  # 0=A, 1=B
    for i, civ in enumerate(all_picks[:10]):
        if pattern[i] == 0:
            team_a.append(civ)
        else:
            team_b.append(civ)

    # Sort each team by winrate desc for display
    team_a.sort(key=lambda c: c["winrate"], reverse=True)
    team_b.sort(key=lambda c: c["winrate"], reverse=True)

    return team_a, team_b


async def get_today_civs(channel):
    """Civs already played in this channel today (IST).

    Reads the durable qc_match_civs record. Returns a set of civ name strings.
    """
    today_start = int(
        datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    )
    rows = await db.fetchall(
        "SELECT DISTINCT civ FROM qc_match_civs WHERE channel_id=%s AND at >= %s",
        [channel.id, today_start]
    )
    return {r["civ"] for r in rows if r["civ"]}


async def build_suggestion_embed(channel, title="Suggested Civ Pools"):
    """Balanced random civ pools (5 per team), excluding civs played today.

    Auto-posted when a match's teams are formed. Civ win-rates come LIVE from
    qc_match_civs (kept current by the reconcile job), falling back to the seed.
    Returns an Embed, or None if no civ data is available.
    """
    played = await get_today_civs(channel)
    civ_data = await civ_elo_from_db()
    result = pick_balanced_teams(excluded_civs=played, civ_data=civ_data or None)
    if result is None:
        return None
    team_a, team_b = result

    def _fmt(civs):
        return "\n".join(f"{c['civ']} ({c['winrate'] * 100:.0f}%)" for c in civs)

    avg_a = sum(c["winrate"] for c in team_a) / len(team_a) * 100
    avg_b = sum(c["winrate"] for c in team_b) / len(team_b) * 100

    embed = Embed(title=title, colour=Colour(0x50e3c2))
    embed.add_field(name=f"Team A  —  avg {avg_a:.1f}%", value=_fmt(team_a), inline=True)
    embed.add_field(name=f"Team B  —  avg {avg_b:.1f}%", value=_fmt(team_b), inline=True)
    embed.set_footer(
        text=(f"Excluded {len(played)} civ(s) played today" if played
              else "No civs played today — all available")
    )
    return embed


# Seed the in-memory fallback from CSV at import; build_suggestion_embed
# refreshes from the live DB on each call.
load_civ_elo_stats()
