# -*- coding: utf-8 -*-
"""Read-only aggregation over the rs_* tables for /player_details.

Reuses the quiz metric catalog + filter rules from utils/replay_quiz/build_db.py so a player's
card lines up with the daily quiz: standard-map games only, age/timing metrics gated on
age_reliable, timings skip games where the value is absent, and count averages exclude games
where the player didn't do it (a 0 isn't evidence of how much they make when they go for it).

build_card() is pure (no DB, no nextcord) so it is unit-tested in isolation; gather_player_stats()
is the thin async DB layer that feeds it.
"""
import datetime
import time

from core.database import db

STANDARD_MAPS = ("Land Nomad", "Nomad")
MILITARY_BUILDINGS = ("Barracks", "Archery Range", "Stable", "Castle", "Siege Workshop")

# Curated tech list for the "earliest to click X" timings (same order as the quiz).
TECHS = ["Loom", "Wheelbarrow", "Hand Cart", "Heavy Plow", "Horse Collar",
         "Double-Bit Axe", "Bow Saw", "Two-Man Saw", "Gold Mining", "Stone Mining",
         "Bloodlines", "Ballistics", "Husbandry", "Forging", "Fletching",
         "Bodkin Arrow", "Chemistry", "Caravan", "Bracer"]
UNIT_CATEGORIES = ["scout", "skirmisher", "archer_line", "spearman_line", "militia_line",
                   "knight_line", "camel_line", "cav_archer", "siege", "monk",
                   "unique_other", "elephant"]
PRETTY = {"scout": "Scouts", "skirmisher": "Skirmishers", "archer_line": "Archers",
          "spearman_line": "Spearmen", "militia_line": "Militia-line", "knight_line": "Knights",
          "camel_line": "Camels", "cav_archer": "Cav archers", "siege": "Siege",
          "monk": "Monks", "unique_other": "Unique units", "elephant": "Elephants"}

# Building label -> rs_player_buildings.building value.
BUILDINGS = [("Town Centers", "Town Center"), ("Barracks", "Barracks"),
             ("Archery Ranges", "Archery Range"), ("Stables", "Stable"), ("Castles", "Castle")]


def _avg(vals):
    return sum(vals) / len(vals) if vals else None


def build_card(games, units, techs, builds, all_games, days, cutoff):
    """Pure aggregation. Inputs are plain row dicts (already filtered to the player's
    standard-map games inside the window):
      games   : per-game facts (rs_player_games + map), one row per game
      units   : {aoe2_match_id, category, total} pre-summed per (match, category)
      techs   : {aoe2_match_id, tech, click_s} pre-reduced to earliest click per (match, tech)
      builds  : {aoe2_match_id, building, count} one row per (match, building)
      all_games, days, cutoff : context for the header
    Returns a structured dict the embed layer renders. Empty sections are dropped.
    """
    n = len(games)
    if not n:
        return None

    age_ok = [g for g in games if g.get("age_reliable") and g.get("feudal_s") is not None]
    age_ok_mids = {g["aoe2_match_id"] for g in age_ok}

    def avg_count(key, rows, exclude_zero=True):
        vals = [g.get(key) or 0 for g in rows]
        if exclude_zero:
            vals = [v for v in vals if v]
        a = _avg(vals)
        return None if a is None else (round(a, 1), len(vals))

    def avg_secs(key):
        vals = [g[key] for g in age_ok if g.get(key) is not None]
        a = _avg(vals)
        return None if a is None else (round(a), len(vals))

    def row(label, res):
        return None if res is None else (label, res[0], res[1])

    sections = []

    def add(key, title, unit, rows):
        rows = [r for r in rows if r is not None]
        if rows:
            sections.append(dict(key=key, title=title, unit=unit, rows=rows))

    # Villagers (total: all games; splits: age-gated)
    add("villagers", "Villagers", "count", [
        row("Total / game", avg_count("villagers", games)),
        row("before Feudal", avg_count("vil_pre_feudal", age_ok)),
        row("before Castle", avg_count("vil_pre_castle", age_ok)),
        row("before Imperial", avg_count("vil_pre_imperial", age_ok)),
    ])

    # Age speed (timings, age-gated, skip-null)
    add("age", "Age speed", "seconds", [
        row("Feudal", avg_secs("feudal_s")),
        row("Castle", avg_secs("castle_s")),
        row("Imperial", avg_secs("imperial_s")),
        row("First TC (order)", avg_secs("first_tc_s")),
    ])

    # Tech timing (earliest click; age-gated like the quiz)
    tech_vals = {}
    for t in techs:
        if t["aoe2_match_id"] in age_ok_mids and t.get("click_s") is not None:
            tech_vals.setdefault(t["tech"], []).append(t["click_s"])
    tech_rows = []
    for tech in TECHS:
        vals = tech_vals.get(tech)
        if vals:
            tech_rows.append((tech, round(_avg(vals)), len(vals)))
    add("tech", "Tech timing", "seconds", tech_rows)

    # Military aggregate (total: all games; splits: age-gated)
    add("military", "Military", "count", [
        row("Army / game", avg_count("military", games)),
        row("before Feudal", avg_count("mil_pre_feudal", age_ok)),
        row("before Castle", avg_count("mil_pre_castle", age_ok)),
        row("before Imperial", avg_count("mil_pre_imperial", age_ok)),
    ])

    # Military by unit type (whole-game totals, exclude games where unused)
    cat_vals = {}
    for u in units:
        if u.get("total"):
            cat_vals.setdefault(u["category"], []).append(u["total"])
    by_type = []
    for cat in UNIT_CATEGORIES:
        vals = cat_vals.get(cat)
        if vals:
            by_type.append((PRETTY[cat], round(_avg(vals), 1), len(vals)))
    add("by_type", "Military by type", "count", by_type)

    # Buildings (exclude games where not built)
    bld_vals = {}
    for b in builds:
        if b.get("count"):
            bld_vals.setdefault(b["building"], []).append(b["count"])
    bld_rows = []
    for label, key in BUILDINGS:
        vals = bld_vals.get(key)
        if vals:
            bld_rows.append((label, round(_avg(vals), 1), len(vals)))
    mil_b = []
    for mid in {b["aoe2_match_id"] for b in builds}:
        tot = sum(b["count"] for b in builds
                  if b["aoe2_match_id"] == mid and b["building"] in MILITARY_BUILDINGS and b.get("count"))
        if tot:
            mil_b.append(tot)
    if mil_b:
        bld_rows.insert(0, ("Military buildings", round(_avg(mil_b), 1), len(mil_b)))
    add("buildings", "Buildings", "count", bld_rows)

    civ_count = {}
    for g in games:
        if g.get("civ"):
            civ_count[g["civ"]] = civ_count.get(g["civ"], 0) + 1
    civs = sorted(civ_count.items(), key=lambda x: -x[1])[:6]

    eapms = [g["eapm"] for g in games if g.get("eapm") is not None]
    wins = sum(1 for g in games if g.get("winner"))

    return dict(
        days=days, cutoff=cutoff, games=n, all_games=all_games,
        wins=wins, winrate=round(100 * wins / n), eapm=round(_avg(eapms)) if eapms else None,
        age_reliable=len(age_ok), civs=civs, sections=sections,
    )


# ── build-timeline chart data (/player_details chart:true) ─────────────────
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


async def resolve_profile_ids(user_id):
    """All AoE2 profile_ids that have games linked to this Discord user_id (covers alts)."""
    rows = await db.fetchall(
        "SELECT DISTINCT profile_id FROM rs_player_games WHERE user_id=%s", [user_id])
    return [r["profile_id"] for r in rows]


async def gather_player_stats(profile_ids, days=90):
    """Aggregate a player's last-N-days replay card. Returns build_card()'s dict, or None
    if there are no standard-map games in the window."""
    if not profile_ids:
        return None
    cutoff = (datetime.date.fromtimestamp(time.time()) - datetime.timedelta(days=days)).isoformat()
    pids = ",".join(["%s"] * len(profile_ids))
    smaps = ",".join(["%s"] * len(STANDARD_MAPS))

    games = await db.fetchall(
        f"SELECT g.aoe2_match_id, g.civ, g.winner, g.eapm, g.age_reliable, "
        f"g.feudal_s, g.castle_s, g.imperial_s, g.first_tc_s, g.villagers, "
        f"g.vil_pre_feudal, g.vil_pre_castle, g.vil_pre_imperial, "
        f"g.military, g.mil_pre_feudal, g.mil_pre_castle, g.mil_pre_imperial "
        f"FROM rs_player_games g JOIN rs_matches m ON m.aoe2_match_id=g.aoe2_match_id "
        f"WHERE g.profile_id IN ({pids}) AND m.played_at >= %s AND m.map IN ({smaps})",
        [*profile_ids, cutoff, *STANDARD_MAPS])
    if not games:
        return None
    all_games = await db.fetchall(
        f"SELECT COUNT(*) n FROM rs_player_games g JOIN rs_matches m ON m.aoe2_match_id=g.aoe2_match_id "
        f"WHERE g.profile_id IN ({pids}) AND m.played_at >= %s", [*profile_ids, cutoff])
    mids = [g["aoe2_match_id"] for g in games]
    mph = ",".join(["%s"] * len(mids))

    units = await db.fetchall(
        f"SELECT aoe2_match_id, category, SUM(total) total FROM rs_player_units "
        f"WHERE profile_id IN ({pids}) AND aoe2_match_id IN ({mph}) "
        f"GROUP BY aoe2_match_id, category", [*profile_ids, *mids])
    techs = await db.fetchall(
        f"SELECT aoe2_match_id, tech, MIN(click_s) click_s FROM rs_player_techs "
        f"WHERE profile_id IN ({pids}) AND aoe2_match_id IN ({mph}) AND click_s IS NOT NULL "
        f"GROUP BY aoe2_match_id, tech", [*profile_ids, *mids])
    builds = await db.fetchall(
        f"SELECT aoe2_match_id, building, count FROM rs_player_buildings "
        f"WHERE profile_id IN ({pids}) AND aoe2_match_id IN ({mph})", [*profile_ids, *mids])

    return build_card(games, units, techs, builds, all_games[0]["n"], days, cutoff)
