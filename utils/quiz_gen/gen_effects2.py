"""Category: EFFECTS — rebuilt as effect-QUANTITY superlatives (per owner feedback:
drop "which is the ONLY one with X" odd-one-out — that's common knowledge; ask how
MUCH of an effect instead). All values are intrinsic (civ-invariant) and taken from
the golden DB, verified accurate:

  - most projectiles per attack  (ref_projectiles primary projectile_count)
  - largest blast/splash radius  (ref_projectiles primary blast_radius)
  - largest trample (area) radius (ref_special_effects trample_radius)

generate(rng, limit=None) -> [candidate dict]
"""
from __future__ import annotations

import collections

import sources as S
import spec

# (question_type, prompt verb, source tag)
DIMS = {
    "projectiles": ("fires the most projectiles in a single attack", "ref_projectiles.primary projectile_count"),
    "blast": ("has the largest blast (splash) radius", "ref_projectiles.primary blast_radius"),
    "trample": ("has the largest trample (area-damage) radius", "ref_special_effects.trample_radius"),
}


def _modal_primary(field):
    """unit_name -> modal value of `field` on the PRIMARY projectile row (intrinsic)."""
    acc = {}
    sql = (f"SELECT ru.unit_name n, p.{field} v FROM ref_projectiles p "
           "JOIN ref_units ru ON ru.id=p.ref_unit_id "
           f"WHERE p.projectile_type='primary' AND p.{field} IS NOT NULL")
    with S.golden("aoe2_reference.db") as c:
        for r in c.execute(sql):
            acc.setdefault(r["n"], []).append(r["v"])
    return {n: collections.Counter(v).most_common(1)[0][0] for n, v in acc.items()}


def _modal_effect(prop):
    acc = {}
    with S.golden("aoe2_reference.db") as c:
        for r in c.execute("SELECT ru.unit_name n, e.property_value v FROM ref_special_effects e "
                           "JOIN ref_units ru ON ru.id=e.ref_unit_id WHERE e.property_name=?", (prop,)):
            try:
                acc.setdefault(r["n"], []).append(float(r["v"]))
            except (TypeError, ValueError):
                pass
    return {n: collections.Counter(v).most_common(1)[0][0] for n, v in acc.items()}


def _superlatives(pool, qt, verb, src, rng, min_val=0):
    """pool: {unit_name: value}. Emit tight 'which has the most X' questions."""
    recs = [{"name": n, "v": v} for n, v in pool.items() if v and v > min_val]
    out = []
    for four, ans in spec.tight_windows(recs, key=lambda u: u["v"], want_max=True,
                                        max_rel=0.55, k=6):
        vals = [u["v"] for u in four]
        if len(set(vals)) < 2:
            continue
        tightv = 1 - (spec.relative_spread(vals) or 0)
        q = spec.make_question(
            qid=f"effects_{qt}_{spec._slug(ans['name'])}",
            category="effects", question_type=qt, grouping="effect_value",
            prompt=f"Which of these units {verb}?",
            options=[u["name"] for u in four], correct=ans["name"],
            explanation=f"{ans['name']} {verb} ({ans['v']:g}); next is "
                        f"{sorted(four, key=lambda u: -u['v'])[1]['name']} "
                        f"({sorted(four, key=lambda u: -u['v'])[1]['v']:g}).",
            source=src, score=spec.combine(tightv, 0.4),
            values={u["name"]: u["v"] for u in four}, rng=rng)
        if q:
            q["meta"]["effect"] = qt
            out.append(q)
    return out


def generate(rng, limit=None):
    out = []
    out += _superlatives(_modal_primary("projectile_count"), "projectiles", *DIMS["projectiles"],
                         rng=rng, min_val=1)
    out += _superlatives(_modal_primary("blast_radius"), "blast", *DIMS["blast"], rng=rng)
    out += _superlatives(_modal_effect("trample_radius"), "trample", *DIMS["trample"], rng=rng)
    out.sort(key=lambda q: q["score"], reverse=True)
    return out[:limit] if limit else out


if __name__ == "__main__":
    import random
    qs = generate(random.Random(3))
    print(f"generated {len(qs)} effect questions\n")
    for q in qs[:10]:
        print(f"[{q['question_type']} score={q['score']}] {q['prompt']}")
        for i, o in enumerate(q["options"]):
            print(f"   {'*' if i in q['correct_indices'] else ' '} {o}  {q['meta']['values'][o]}")
        print()
