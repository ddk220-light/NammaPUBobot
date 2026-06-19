"""Build the full question bank: run every format generator at volume, score by the
learned taste model, structurally validate, INDEPENDENTLY re-verify accuracy where
feasible (combat answers re-derived from matchup_db; stats from ref_units), then
write data/quiz_bank.json. Accuracy is the priority — anything that fails the
independent re-derivation is dropped, not shipped.

    python utils/quiz_gen/build_bank.py
"""
from __future__ import annotations

import importlib
import json
import os
import random
import re

import curate
import sources as S
import spec

_OUT = os.path.join(curate._REPO, "data", "quiz_bank.json")
GENERATORS = ("gen_combat2", "gen_stats", "gen_techgaps", "gen_effects2")
SEED = 20260618


def _quality_keep(q):
    """Drop question shapes the audit found ambiguous/buggy at the source:
    - stats: keep only civ-named cross-civ (no-civ archetype values are ambiguous —
      a civ-bonus variant can beat the 'generic' marked answer).
    - effects: gen_effects2 emits only accurate quantity-superlatives, nothing to drop."""
    if q["category"] == "stats" and q.get("grouping") != "line_cross_civ":
        return False
    return True


def _structural_ok(q):
    return (q["category"] in spec.CATEGORIES
            and len(q["options"]) == spec.N_OPTIONS == len(set(q["options"]))
            and q["correct_indices"] and all(0 <= i < 4 for i in q["correct_indices"])
            and q["multi"] == (len(q["correct_indices"]) > 1)
            and 0.0 <= q["score"] <= 1.0)


def verify_combat(bank, con, d2s_name):
    """Re-derive each combat answer straight from matchup_db (independent of the
    generator) and drop any that don't reproduce. Returns (kept, dropped)."""
    kept, dropped = [], 0
    name2slug = d2s_name
    for q in bank:
        if q["category"] != "combat":
            kept.append(q)
            continue
        m = re.search(r"(team1_\w+) scale=(\w+) vs ([^/]+)/(\S+)", q["source"])
        if not m:
            dropped += 1
            continue
        col, scale, opp_civ, opp_slug = m.groups()
        wmin = col == "team1_value_lost"
        vals = {}
        ok = True
        for opt in q["options"]:
            nm = opt.rsplit(" (", 1)[0]
            slug = name2slug.get(nm)
            if not slug:
                ok = False
                break
            r = con.execute(f"SELECT AVG({col}) v, AVG(CASE WHEN winner=1 THEN 1.0 ELSE 0 END) wr "
                            "FROM matchup_battles WHERE my_unit_slug=? AND opp_civ=? AND opp_unit_slug=? AND scale=?",
                            (slug, opp_civ, opp_slug, scale)).fetchone()
            if r["v"] is None or (r["wr"] or 0) < 0.999:     # every option must really win
                ok = False
                break
            vals[opt] = r["v"]
        if not ok:
            dropped += 1
            continue
        marked = q["options"][q["correct_indices"][0]]
        true_best = (min if wmin else max)(vals, key=vals.get)
        if true_best != marked:
            dropped += 1
            continue
        kept.append(q)
    return kept, dropped


def main():
    rng = random.Random(SEED)
    taste = curate._load(curate._TASTE)
    raw = []
    per_gen = {}
    for mod in GENERATORS:
        qs = importlib.import_module(mod).generate(rng)
        good = [q for q in qs if _structural_ok(q) and _quality_keep(q)]
        for q in good:
            q["taste_score"] = curate.taste_score(q, taste)
        per_gen[mod] = len(good)
        raw.extend(good)

    # independent accuracy re-derivation for combat (the primary, sim-based category)
    con = S.matchup()
    d2s_name = {}
    for sl in S.distinct_matchup_units(con):
        d2s_name.setdefault(S.display_name(sl), sl)
    raw, combat_dropped = verify_combat(raw, con, d2s_name)
    con.close()

    # dedup by content, id, sort by taste
    seen, bank = set(), []
    for q in sorted(raw, key=lambda q: q["taste_score"], reverse=True):
        sig = (q["prompt"], tuple(sorted(q["options"])))
        if sig in seen:
            continue
        seen.add(sig)
        q["id"] = f"{q['category']}_{len(bank):05d}"
        bank.append(q)

    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump(bank, f, indent=2, ensure_ascii=False)

    by_cat, by_diff = {}, {}
    for q in bank:
        by_cat[q["category"]] = by_cat.get(q["category"], 0) + 1
        by_diff[q["difficulty"]] = by_diff.get(q["difficulty"], 0) + 1
    print(f"BANK: {len(bank)} questions -> {_OUT}")
    print(f"  per generator (pre-dedup): {per_gen}")
    print(f"  combat dropped by accuracy re-derivation: {combat_dropped}")
    print(f"  by category: {by_cat}")
    print(f"  by difficulty: {by_diff}")
    print(f"  multi-select: {sum(1 for q in bank if q['multi'])}")


if __name__ == "__main__":
    main()
