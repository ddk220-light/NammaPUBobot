# -*- coding: utf-8 -*-
"""Read-only aggregation over the rs_* tables for /player_details's build-timeline chart.

Reuses the daily-quiz approach so the card lines up with the quiz: standard-map games only,
age/timing metrics gated on age_reliable + a real Feudal click, and each tracked upgrade placed
in the phase where the player researches it on average. phase_bucket()/build_timeline() are pure
(no DB, no matplotlib) and unit-tested; gather_timeline_data() is the thin async DB layer.
"""
import datetime
import statistics
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


# ── growth curve (B1): averaged cumulative villager/military over game time ────────────────────
def _cumulative_at(series, t):
    """series = sorted [(t_s, amount)…]; running sum of amount for events with t_s <= t."""
    total = 0
    for ts, amt in series:
        if ts <= t:
            total += amt
        else:
            break
    return total


def _percentile(values, p):
    if not values:
        return 0
    s = sorted(values)
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _mean_ci(values):
    """-> (mean, lo, hi, n) with a 95% CI (mean ± 1.96·σ/√n). n<2 -> band collapses to the mean."""
    n = len(values)
    if n == 0:
        return None, None, None, 0
    m = sum(values) / n
    if n < 2:
        return m, m, m, n
    half = 1.96 * statistics.stdev(values) / (n ** 0.5)
    return m, max(0.0, m - half), m + half, n


def _ann(names, techs):
    """[(tech, avg_click_s)…] for tracked upgrades present, earliest first. float() because MySQL
    AVG() returns decimal.Decimal, which won't mix with the float means in the chart's interpolation."""
    tavg = {t["tech"]: float(t["t"]) for t in techs if t.get("t") is not None}
    out = [(name, tavg[name]) for name in names if name in tavg]
    out.sort(key=lambda r: r[1])
    return out


def build_growth_curve(games, grid_step=30, cap_s=None, max_cap_s=5400):
    """Pure: average each game's cumulative villager/military count onto a common time grid,
    over the games still LIVE at each t (duration_s >= t). Returns per-point mean, 95% CI band,
    and n (games contributing) — so the chart can fade where data thins. `games`: list of dicts
    with duration_s:int, vil/mil: [(t_s, amount)…], and feudal_s/castle_s/imperial_s (int|None)."""
    prepared = []
    for g in games:
        if not g.get("duration_s"):
            continue
        prepared.append(dict(duration_s=g["duration_s"],
                             vil=sorted(g.get("vil", [])), mil=sorted(g.get("mil", [])),
                             feudal_s=g.get("feudal_s"), castle_s=g.get("castle_s"),
                             imperial_s=g.get("imperial_s")))
    if not prepared:
        return None
    durs = [g["duration_s"] for g in prepared]
    if cap_s is None:
        cap_s = _percentile(durs, 0.95)
    cap_s = int(min(cap_s, max_cap_s, max(durs)))
    grid = list(range(0, cap_s + 1, grid_step))   # last point <= cap_s <= max(durs): every point has n>=1
    if len(grid) < 2:
        return None

    cols = dict(vil_mean=[], vil_lo=[], vil_hi=[], vil_n=[], mil_mean=[], mil_lo=[], mil_hi=[], mil_n=[])
    for t in grid:
        live = [g for g in prepared if g["duration_s"] >= t]
        for tag, key in (("vil", "vil"), ("mil", "mil")):
            m, lo, hi, n = _mean_ci([_cumulative_at(g[key], t) for g in live])
            cols[f"{tag}_mean"].append(m)
            cols[f"{tag}_lo"].append(lo)
            cols[f"{tag}_hi"].append(hi)
            cols[f"{tag}_n"].append(n)

    def amean(key):
        xs = [g[key] for g in prepared if g.get(key) is not None]
        return sum(xs) / len(xs) if xs else None

    return dict(grid=grid, n=len(prepared), cap_s=cap_s, grid_step=grid_step,
                ages=(amean("feudal_s"), amean("castle_s"), amean("imperial_s")), **cols)


async def gather_growth_curve(profile_ids, days=90):
    """DB layer for build_growth_curve(): age-reliable standard-map games in the window that HAVE
    per-event data, each player's villager/military queue series, plus eco/military upgrade
    annotations (avg first-click, from rs_player_techs). Returns the curve dict or None."""
    if not profile_ids:
        return None
    cutoff = (datetime.date.fromtimestamp(time.time()) - datetime.timedelta(days=days)).isoformat()
    pids = ",".join(["%s"] * len(profile_ids))
    smaps = ",".join(["%s"] * len(STANDARD_MAPS))
    rows = await db.fetchall(
        f"SELECT g.aoe2_match_id, g.profile_id, m.duration_s, g.feudal_s, g.castle_s, g.imperial_s "
        f"FROM rs_player_games g JOIN rs_matches m ON m.aoe2_match_id=g.aoe2_match_id "
        f"WHERE g.profile_id IN ({pids}) AND m.played_at >= %s AND m.map IN ({smaps}) "
        f"AND g.age_reliable=1 AND g.feudal_s IS NOT NULL AND m.duration_s IS NOT NULL",
        [*profile_ids, cutoff, *STANDARD_MAPS])
    if not rows:
        return None
    mids = list({r["aoe2_match_id"] for r in rows})
    mph = ",".join(["%s"] * len(mids))
    evs = await db.fetchall(
        f"SELECT aoe2_match_id, profile_id, t_s, amount, is_military, category FROM rs_player_events "
        f"WHERE aoe2_match_id IN ({mph}) AND profile_id IN ({pids}) AND kind='queue' "
        f"AND t_s IS NOT NULL", [*mids, *profile_ids])
    series = {}
    for e in evs:
        d = series.setdefault((e["aoe2_match_id"], e["profile_id"]), {"vil": [], "mil": []})
        amt = e["amount"] or 1
        if e["is_military"]:                       # robust flag (parity-verified), symmetric with…
            d["mil"].append((e["t_s"], amt))
        elif e["category"] == "villager":          # …the derived category, not an exact name match
            d["vil"].append((e["t_s"], amt))
    games = []
    for r in rows:
        s = series.get((r["aoe2_match_id"], r["profile_id"]))
        if not s or not s["vil"]:
            # No villager series (replay unbackfilled, or a data artifact) -> can't anchor the curve.
            # Requiring villagers keeps the shared live-set honest: every averaged game has a real
            # villager series, so the villager mean is never diluted by a phantom 0. (Military may be
            # legitimately absent -> a true 0, which is correct to average in.)
            continue
        games.append(dict(duration_s=r["duration_s"], vil=s["vil"], mil=s["mil"],
                          feudal_s=r["feudal_s"], castle_s=r["castle_s"], imperial_s=r["imperial_s"]))
    curve = build_growth_curve(games)
    if not curve:
        return None
    techs = await db.fetchall(
        f"SELECT tech, AVG(c) t FROM (SELECT tech, MIN(click_s) c FROM rs_player_techs "
        f"WHERE profile_id IN ({pids}) AND aoe2_match_id IN ({mph}) AND click_s IS NOT NULL "
        f"GROUP BY aoe2_match_id, tech) x GROUP BY tech", [*profile_ids, *mids])
    curve["eco"] = _ann(ECO_TIMELINE, techs)
    curve["mil_upg"] = _ann(MIL_TIMELINE, techs)
    return curve
