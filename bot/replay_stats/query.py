# -*- coding: utf-8 -*-
"""Read-only aggregation over the rs_* tables for /player_details's build-timeline chart.

Reuses the daily-quiz approach so the card lines up with the quiz: standard-map games only,
age/timing metrics gated on age_reliable + a real Feudal click, and each tracked upgrade placed
in the phase where the player researches it on average. phase_bucket()/build_timeline() are pure
(no DB, no matplotlib) and unit-tested; gather_timeline_data() is the thin async DB layer.
"""
import datetime
import time

from core.database import db

STANDARD_MAPS = ("Land Nomad", "Nomad")

# Eco vs military upgrade groupings for the two annotated bars.
ECO_TIMELINE = ["Loom", "Double-Bit Axe", "Horse Collar", "Gold Mining", "Wheelbarrow",
                "Bow Saw", "Hand Cart", "Heavy Plow", "Stone Mining", "Two-Man Saw", "Caravan"]
MIL_TIMELINE = ["Fletching", "Forging", "Bloodlines", "Bodkin Arrow", "Husbandry",
                "Ballistics", "Bracer", "Chemistry"]


def phase_bucket(t, feudal, castle, imperial):
    """Phase column (0=before Feudal .. 3=post-Imperial) a research at avg time t falls in,
    given the player's avg age-up click times. Missing age times push the boundary later."""
    if feudal is not None and t < feudal:
        return 0
    if castle is not None and t < castle:
        return 1
    if imperial is not None and t < imperial:
        return 2
    return 3


def build_timeline(games, techs):
    """Pure: per-phase villager/military means, age-up means, and eco/military upgrades bucketed
    into their phase column. `games` are age-reliable rows; `techs` are {tech, t} avg click times."""
    def mean(key):
        xs = [g[key] for g in games if g.get(key) is not None]
        return sum(xs) / len(xs) if xs else None

    vil = [mean("vil_pre_feudal"), mean("vil_pre_castle"), mean("vil_pre_imperial"), mean("villagers")]
    mil = [mean("mil_pre_feudal"), mean("mil_pre_castle"), mean("mil_pre_imperial"), mean("military")]
    f, c, i = mean("feudal_s"), mean("castle_s"), mean("imperial_s")
    tavg = {t["tech"]: t["t"] for t in techs if t.get("t") is not None}

    def buckets(names):
        cols = {0: [], 1: [], 2: [], 3: []}
        for name in names:
            if name in tavg:
                cols[phase_bucket(tavg[name], f, c, i)].append((name, tavg[name]))
        for k in cols:
            cols[k].sort(key=lambda r: r[1])
        return cols

    return dict(n=len(games), vil=vil, mil=mil, ages=(f, c, i),
                eco=buckets(ECO_TIMELINE), mil_upg=buckets(MIL_TIMELINE))


async def resolve_profile_ids(user_id):
    """All AoE2 profile_ids that have games linked to this Discord user_id (covers alts)."""
    rows = await db.fetchall(
        "SELECT DISTINCT profile_id FROM rs_player_games WHERE user_id=%s", [user_id])
    return [r["profile_id"] for r in rows]


async def gather_timeline_data(profile_ids, days=90):
    """DB layer for build_timeline(): age-reliable standard-map games in the window + avg tech
    click times. Returns build_timeline()'s dict, or None if there are no age-reliable games."""
    if not profile_ids:
        return None
    cutoff = (datetime.date.fromtimestamp(time.time()) - datetime.timedelta(days=days)).isoformat()
    pids = ",".join(["%s"] * len(profile_ids))
    smaps = ",".join(["%s"] * len(STANDARD_MAPS))
    games = await db.fetchall(
        f"SELECT g.aoe2_match_id, g.feudal_s, g.castle_s, g.imperial_s, g.villagers, "
        f"g.vil_pre_feudal, g.vil_pre_castle, g.vil_pre_imperial, "
        f"g.military, g.mil_pre_feudal, g.mil_pre_castle, g.mil_pre_imperial "
        f"FROM rs_player_games g JOIN rs_matches m ON m.aoe2_match_id=g.aoe2_match_id "
        f"WHERE g.profile_id IN ({pids}) AND m.played_at >= %s AND m.map IN ({smaps}) "
        f"AND g.age_reliable=1 AND g.feudal_s IS NOT NULL",
        [*profile_ids, cutoff, *STANDARD_MAPS])
    if not games:
        return None
    mids = [g["aoe2_match_id"] for g in games]
    mph = ",".join(["%s"] * len(mids))
    techs = await db.fetchall(
        f"SELECT tech, AVG(c) t FROM (SELECT tech, MIN(click_s) c FROM rs_player_techs "
        f"WHERE profile_id IN ({pids}) AND aoe2_match_id IN ({mph}) AND click_s IS NOT NULL "
        f"GROUP BY aoe2_match_id, tech) x GROUP BY tech", [*profile_ids, *mids])
    return build_timeline(games, techs)
