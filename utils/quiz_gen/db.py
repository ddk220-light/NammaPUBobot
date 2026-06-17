"""Read-only loaders over the aoe2_matchup golden SQLite DBs.

Collapses the per-civ unit_stats rows into one canonical generic record per unit so
generated facts are civ-invariant: a civ bonus changes a minority of a unit's rows,
so the modal numeric stat and the majority armor/attack-class membership describe
the generic unit. Returns plain dicts — the pure templates never see a DB handle.

The golden DB directory defaults to the sibling aoe2_matchup repo; override with the
AOE2_GOLDEN environment variable."""
import json
import os
import sqlite3
from collections import Counter

GOLDEN = os.environ.get("AOE2_GOLDEN", r"D:\AI\aoe2_matchup\data\golden")


def _rows(db_file, sql, params=()):
    con = sqlite3.connect(os.path.join(GOLDEN, db_file))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()


def armor_classes():
    return {r["id"]: r["name"] for r in _rows("aoe2_units.db", "SELECT id, name FROM armor_classes")}


def _mode(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return Counter(vals).most_common(1)[0][0]


# Special-mechanic columns on unit_stats (intrinsic to the unit, not a civ bonus
# once we take the majority over the civs that actually field the unit).
MECH_COLS = ("bleed_dps", "trample_radius", "damage_reflect_percent", "hp_regen",
             "charge_recharge_time", "attack_bonus_per_kill", "armor_strip_per_hit")


def _pos(v):
    try:
        return float(v) > 0
    except (TypeError, ValueError):
        return False


def canonical_units():
    """One civ-invariant record per unit: {unit_name, unit_category, hp, attack,
    melee_armor, pierce_armor, cost_*, attacks:{class_id:1}, armors:{class_id:1},
    mechanics:{col:1}}. Only rows where the civ ACTUALLY fields the unit (has_unit=1)
    are considered — otherwise the zero-filled "civ doesn't have it" rows dominate
    the mode (e.g. HP would read 0). Numeric fields are the modal value across those
    real rows; an attack/armor class or mechanic is included when present (value>0)
    in more than half of them, so a single-civ bonus (e.g. Tupi's bleeding Arbalester)
    is excluded while a unique unit's intrinsic mechanic survives."""
    cols = ("hp", "attack", "melee_armor", "pierce_armor",
            "cost_food", "cost_wood", "cost_gold") + MECH_COLS
    rows = _rows("aoe2_units.db",
        "SELECT unit_name, unit_category, attacks_json, armors_json, "
        + ", ".join(cols) +
        " FROM unit_stats WHERE has_unit=1 AND unit_name IS NOT NULL AND unit_name <> ''")
    groups = {}
    for r in rows:
        groups.setdefault(r["unit_name"], []).append(r)
    num_fields = ("hp", "attack", "melee_armor", "pierce_armor",
                  "cost_food", "cost_wood", "cost_gold")
    out = []
    for name, rs in groups.items():
        n = len(rs)
        rec = {"unit_name": name, "unit_category": _mode([r["unit_category"] for r in rs])}
        for k in num_fields:
            rec[k] = _mode([r[k] for r in rs])
        atk = Counter()
        arm = Counter()
        for r in rs:
            for cls, val in json.loads(r["attacks_json"] or "{}").items():
                if val and float(val) > 0:
                    atk[cls] += 1
            for cls in json.loads(r["armors_json"] or "{}"):
                arm[cls] += 1
        rec["attacks"] = {cls: 1 for cls, c in atk.items() if c * 2 > n}
        rec["armors"] = {cls: 1 for cls, c in arm.items() if c * 2 > n}
        rec["mechanics"] = {col: 1 for col in MECH_COLS
                            if sum(1 for r in rs if _pos(r[col])) * 2 > n}
        out.append(rec)
    return out
