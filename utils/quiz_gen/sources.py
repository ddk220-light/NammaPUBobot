"""Read-only loaders over the aoe2_matchup golden SQLite DBs + the full matchup DB.

Offline only — the bot never imports this; the generator does. Every category
generator (gen_*.py) gets its data through here so none of them touch a raw DB
handle or hard-code a path. Paths are overridable by env var:

    AOE2_GOLDEN    golden dir   (default D:\\AI\\aoe2_matchup\\data\\golden)
    AOE2_MATCHUP_DB matchup db  (default D:\\AI\\matchup_db.db, 530k unit-vs-unit sims)

The matchup DB is volatile upstream; the generator snapshots it (see build.py) so a
regen there never disturbs question generation.
"""
from __future__ import annotations

import functools
import json
import os
import sqlite3

GOLDEN = os.environ.get("AOE2_GOLDEN", r"D:\AI\aoe2_matchup\data\golden")
MATCHUP_DB = os.environ.get("AOE2_MATCHUP_DB", r"D:\AI\matchup_db.db")
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
ARCHETYPES_PATH = os.path.join(_REPO, "data", "quiz_archetypes.json")


def _ro(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def golden(db_file):
    """Read-only connection to a golden DB (aoe2_units.db / aoe2_reference.db /
    derived_data.db)."""
    return _ro(os.path.join(GOLDEN, db_file))


def matchup():
    """Read-only connection to the full matchup_db.db."""
    return _ro(MATCHUP_DB)


def rows(con, sql, params=()):
    return [dict(r) for r in con.execute(sql, params)]


# --------------------------------------------------------------------------- #
# Name resolution: slug -> display name.
# --------------------------------------------------------------------------- #
@functools.lru_cache(maxsize=1)
def _slug_names():
    """slug -> display_name for generic units (units table). UU slugs in matchup_db
    are civ-suffixed (elite_berserk_vikings); resolve those via ref_units."""
    m = {}
    with golden("aoe2_units.db") as c:
        for r in c.execute("SELECT slug, display_name FROM units WHERE slug IS NOT NULL"):
            m[r["slug"]] = r["display_name"]
    return m


def display_name(slug):
    """Best-effort readable name for a matchup_db / units slug. Falls back to a
    title-cased de-slug if the slug is unknown."""
    m = _slug_names()
    if slug in m:
        return m[slug]
    # civ-suffixed UU: strip a trailing civ token and retry, else de-slug.
    parts = slug.split("_")
    for cut in range(len(parts) - 1, 0, -1):
        cand = "_".join(parts[:cut])
        if cand in m:
            return m[cand]
    return " ".join(w.capitalize() for w in parts)


# --------------------------------------------------------------------------- #
# Per-civ reference rows (base_* and final_* for every unit/civ).
# --------------------------------------------------------------------------- #
_REF_STATS = ("hp", "attack", "melee_armor", "pierce_armor", "speed", "range",
              "reload_time", "accuracy", "los", "train_time",
              "cost_food", "cost_wood", "cost_gold")


@functools.lru_cache(maxsize=1)
def ref_units():
    """Every ref_units row as a dict, with base_* and final_* stats, unit_class_name,
    unit_type (standard|naval|unique), civ_name, unit_name, unit_slug. This is the
    per-civ source — the basis for cross-civ grouping."""
    cols = (["civ_name", "unit_name", "unit_slug", "unit_type", "unit_class",
             "unit_class_name", "age", "is_ranged"]
            + [f"base_{s}" for s in _REF_STATS]
            + [f"final_{s}" for s in _REF_STATS]
            + ["base_attacks_json", "base_armors_json",
               "final_attacks_json", "final_armors_json", "applied_bonuses_summary"])
    with golden("aoe2_reference.db") as c:
        return rows(c, f"SELECT {', '.join(cols)} FROM ref_units")


def lines_cross_civ(min_civs=4, unit_types=("standard",)):
    """Group ref_units into cross-civ lines keyed by unit_name (e.g. 'Paladin' ->
    one row per civ that has it). Only lines with >= min_civs variants are returned,
    so there is a real spread of civ bonuses to ask about.

    Returns {unit_name: [civ_row, ...]}."""
    out = {}
    for r in ref_units():
        if r["unit_type"] not in unit_types:
            continue
        out.setdefault(r["unit_name"], []).append(r)
    return {k: v for k, v in out.items() if len(v) >= min_civs}


def by_class(unit_types=("standard",), use_final=True):
    """Civ-invariant unit records grouped by unit_class_name (Archer, Cavalry, ...).
    Collapses each unit's per-civ rows to the generic (modal) stat so 'highest pierce
    armor among archers' compares units, not civ variants.

    Returns {unit_class_name: [unit_record, ...]} where a record carries the chosen
    stat set under plain keys (hp, attack, ...)."""
    pref = "final_" if use_final else "base_"
    groups = {}
    for r in ref_units():
        if r["unit_type"] not in unit_types:
            continue
        groups.setdefault(r["unit_name"], []).append(r)
    out = {}
    for name, rs in groups.items():
        cls = _mode([r["unit_class_name"] for r in rs])
        rec = {"unit_name": name, "unit_class_name": cls,
               "is_ranged": _mode([r["is_ranged"] for r in rs])}
        for s in _REF_STATS:
            rec[s] = _mode([r[pref + s] for r in rs])
        out.setdefault(cls, []).append(rec)
    return out


def _mode(values):
    from collections import Counter
    vals = [v for v in values if v is not None]
    return Counter(vals).most_common(1)[0][0] if vals else None


# --------------------------------------------------------------------------- #
# Curated archetype clusters ("feels-similar" unique units).
# --------------------------------------------------------------------------- #
@functools.lru_cache(maxsize=1)
def archetypes():
    """Load the hand-maintained archetype clusters. Returns {} if the file is
    absent so the generator degrades gracefully to data-only groupings."""
    try:
        with open(ARCHETYPES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# --------------------------------------------------------------------------- #
# Matchups (combat). Aggregated across the dedup/runs already in matchup_db.
# --------------------------------------------------------------------------- #
def matchup_vs(opp_slug, scale="3k", con=None):
    """Every unit's outcome against opp_slug at the given scale. Returns
    [{unit_slug, winner(1=unit), my_hp_pct, opp_hp_pct, my_value_lost,
    opp_value_lost, game_time_s}], one row per distinct my_unit_slug (best/most
    representative run). Use for 'which unit kills X fastest / wins with most HP'."""
    own = con or matchup()
    try:
        q = """SELECT my_unit_slug, winner, team1_hp_pct, team2_hp_pct,
                      team1_value_lost, team2_value_lost, game_time_s
               FROM matchup_battles
               WHERE opp_unit_slug = ? AND scale = ?
               GROUP BY my_unit_slug"""
        return [{"unit_slug": r["my_unit_slug"], "winner": r["winner"],
                 "my_hp_pct": r["team1_hp_pct"], "opp_hp_pct": r["team2_hp_pct"],
                 "my_value_lost": r["team1_value_lost"],
                 "opp_value_lost": r["team2_value_lost"],
                 "game_time_s": r["game_time_s"]}
                for r in own.execute(q, (opp_slug, scale))]
    finally:
        if con is None:
            own.close()


def matchup_pair(a_slug, b_slug, scale="3k", con=None):
    """Single head-to-head a vs b (a = team1). Returns the row dict or None."""
    own = con or matchup()
    try:
        r = own.execute(
            """SELECT * FROM matchup_battles
               WHERE my_unit_slug=? AND opp_unit_slug=? AND scale=? LIMIT 1""",
            (a_slug, b_slug, scale)).fetchone()
        return dict(r) if r else None
    finally:
        if con is None:
            own.close()


def distinct_matchup_units(con=None):
    own = con or matchup()
    try:
        return [r[0] for r in own.execute(
            "SELECT DISTINCT my_unit_slug FROM matchup_battles ORDER BY 1")]
    finally:
        if con is None:
            own.close()


# --------------------------------------------------------------------------- #
# Opponent validity — the matchup DB brute-forces EVERY civ x unit pairing,
# including tech-tree-impossible ones (e.g. Persian Arbalester). A combat
# question's named opponent MUST be a unit the civ actually fields.
# --------------------------------------------------------------------------- #
_REF_FINAL = ("final_hp", "final_attack", "final_melee_armor", "final_pierce_armor",
              "final_range", "final_reload_time")


@functools.lru_cache(maxsize=1)
def _ref_index():
    """{unit_name: {civ_name: (final stat tuple)}} over ref_units (which lists only
    units a civ actually fields)."""
    idx = {}
    for r in ref_units():
        idx.setdefault(r["unit_name"], {})[r["civ_name"]] = tuple(
            r.get(k) for k in _REF_FINAL)
    return idx


def fielding_civs(unit_name):
    """Civs that actually field unit_name (tech-tree valid)."""
    return set(_ref_index().get(unit_name, {}))


def civ_fields(civ_name, unit_name):
    return civ_name in _ref_index().get(unit_name, {})


def baseline_civ(unit_name):
    """A fielding civ whose fully-upgraded stats are the MODE — i.e. a 'standard'
    version with no special civ bonus to that unit. Used to name a fair, valid
    opponent. Returns (civ_name, stat_tuple) or (None, None)."""
    from collections import Counter
    per = _ref_index().get(unit_name, {})
    if not per:
        return None, None
    modal = Counter(per.values()).most_common(1)[0][0]
    for civ, stats in sorted(per.items()):
        if stats == modal:
            return civ, stats
    return sorted(per)[0], None
