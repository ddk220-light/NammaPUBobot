"""Generate data/quiz_questions.json from the aoe2_matchup golden DBs.

Offline + reproducible (fixed seed) + reviewable: the bot consumes the committed
JSON, never these SQLite files. Run:

    python utils/quiz_gen/build.py            # uses the default golden dir
    AOE2_GOLDEN=/path/to/golden python utils/quiz_gen/build.py
"""
import importlib.util
import json
import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)          # import sibling db.py / templates.py
import db                          # noqa: E402
import templates as T              # noqa: E402

_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
_OUT = os.path.join(_REPO, "data", "quiz_questions.json")

# Armor classes worth a bonus-damage question (id -> friendly name).
BONUS_CLASSES = {
    "27": "Spearmen", "30": "Camels", "34": "Mamelukes", "35": "Heroes & Kings",
    "29": "Eagle Warriors", "26": "Castles", "23": "Gunpowder units", "15": "Archers",
    "28": "Cavalry Archers", "5": "War Elephants", "24": "Boars", "1": "Infantry",
    "8": "Cavalry", "20": "Siege Weapons",
}

# Special mechanics for "only one of these with X" questions. Keys are unit_stats
# mechanic columns (derived civ-invariantly in db.canonical_units) -> friendly label.
MECHANICS = {
    "bleed_dps": "a bleed (damage-over-time) effect",
    "trample_radius": "trample (area) damage",
    "damage_reflect_percent": "damage reflection",
    "hp_regen": "passive HP regeneration",
    "charge_recharge_time": "a charge attack",
    "attack_bonus_per_kill": "an attack bonus that grows per kill",
    "armor_strip_per_hit": "armor-stripping on hit",
}

STATS = [("hp", "HP"), ("attack", "attack"),
         ("pierce_armor", "pierce armor"), ("melee_armor", "melee armor")]


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "quiz_pool", os.path.join(_REPO, "bot", "quiz", "pool.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.validate


def main():
    rng = random.Random(20260617)          # fixed seed -> reproducible pool
    units = db.canonical_units()
    by_cat = {}
    for u in units:
        by_cat.setdefault(u["unit_category"], []).append(u)

    questions = []

    # 1) Superlatives "among these four" — sample random foursomes within a category
    #    so the correct answer (the extreme of the four) varies across questions.
    for cat, members in by_cat.items():
        if cat is None or len(members) < 4:
            continue
        for stat, label in STATS:
            have = [u for u in members if u.get(stat) is not None]
            if len(have) < 4:
                continue
            for _ in range(8):
                sample = rng.sample(have, 4)
                questions += T.superlative(
                    sample, stat=stat, label=label, category="stats",
                    rng=rng, want_max=(rng.random() < 0.5))

    # 2) Bonus-damage membership.
    for cls, name in BONUS_CLASSES.items():
        for _ in range(4):
            questions += T.bonus_membership(
                units, armor_class_id=cls, class_name=name, category="bonus", rng=rng)

    # 3) Only-one-with-mechanic (mechanics derived civ-invariantly from canonical units).
    for col, label in MECHANICS.items():
        special = [u["unit_name"] for u in units if u["mechanics"].get(col)]
        normal = [u["unit_name"] for u in units if not u["mechanics"].get(col)]
        if not special:
            continue
        for _ in range(3):
            questions += T.only_one_with_mechanic(
                special, normal, mechanic_label=label, category="mechanic", rng=rng)

    # Dedupe by content (re-sampled foursomes can collapse to the same question).
    seen, unique = set(), []
    for q in questions:
        sig = (q["prompt"], tuple(sorted(q["options"])))
        if sig in seen:
            continue
        seen.add(sig)
        unique.append(q)

    # Stable unique ids (distinct foursomes can share a base id on the same extreme).
    for i, q in enumerate(unique):
        q["id"] = f"{q['id']}_{i:04d}"

    _load_validator()(unique)

    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump(unique, f, indent=2, ensure_ascii=False)

    cats = {}
    for q in unique:
        cats[q["category"]] = cats.get(q["category"], 0) + 1
    print(f"Wrote {len(unique)} questions to {_OUT}")
    for c, n in sorted(cats.items()):
        print(f"  {c:10s}: {n}")


if __name__ == "__main__":
    main()
