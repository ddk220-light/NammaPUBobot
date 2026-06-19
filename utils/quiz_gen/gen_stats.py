"""Category: STAT SHOWDOWN — "among these <similar units>, which has the <extreme> <stat>?"

REFERENCE GENERATOR. The other gen_*.py mirror this shape:

    generate(rng, limit=None) -> list[candidate dict]   (see spec.py for the schema)

The whole point of the redesign lives here: options are not random, they are a TIGHT
set drawn from one grouping strategy, and every candidate is scored for
interestingness (tightness + surprise) so build.py can keep only the best.

Grouping strategies used (sources.py):
  - by_class        : all Archers / all Cavalry (civ-invariant)        -> grouping "archetype"
  - archetype JSON  : curated "feels-similar" clusters                 -> grouping "uu_cluster"
  - lines_cross_civ : same unit, different civ (Franks vs Teuton ...)  -> grouping "line_cross_civ"
"""
from __future__ import annotations

import sources as S
import spec

# (stat key, want_max, superlative word, human label). attack speed = reload_time,
# where LOWER is faster, so want_max=False.
STAT_DIMS = [
    ("hp", True, "highest", "HP"),
    ("hp", False, "lowest", "HP"),
    ("attack", True, "highest", "attack"),
    ("pierce_armor", True, "highest", "pierce armor"),
    ("melee_armor", True, "highest", "melee armor"),
    ("range", True, "longest", "attack range"),
    ("speed", True, "fastest", "movement speed"),
    ("reload_time", False, "fastest", "attack rate (shortest reload)"),
]


def _cost(rec, pref=""):
    c = [rec.get(pref + k) for k in ("cost_food", "cost_wood", "cost_gold")]
    return sum(x for x in c if x) if any(c) else None


def _superlative(pool, dim, want_max, word, label, *, grouping, group_label, src, rng):
    """Build one tight superlative question from `pool` (list of records each with
    'name', the stat under `dim`, and 'halo' = a cost proxy for surprise). Returns a
    candidate dict or None when the data can't make a clean, unambiguous question."""
    have = [u for u in pool if u.get(dim) is not None]
    if len(have) < spec.N_OPTIONS:
        return None
    vals = [u[dim] for u in have]
    full_range = max(vals) - min(vals)
    if not full_range:
        return None
    # STANDING closeness rule: distractors are the tightest cluster around the answer,
    # never far throwaways (see spec.pick_tight_options).
    four, best = spec.pick_tight_options(have, key=lambda u: u[dim], want_max=want_max)
    if not four:
        return None
    extreme = best[dim]
    distract = sorted((u for u in four if u is not best), key=lambda u: abs(u[dim] - extreme))

    tight = spec.tightness(extreme, [u[dim] for u in distract], full_range)
    if all(u.get("halo") is not None for u in four):
        ranked = sorted(four, key=lambda u: u["halo"], reverse=True)    # 1 = priciest
        answer_rank = ranked.index(best) + 1
    else:
        answer_rank = 1
    score = spec.combine(tight, spec.surprise(answer_rank, len(four)))

    runner = distract[0]
    return spec.make_question(
        qid=f"stats_{grouping}_{spec._slug(dim)}_{word}_{spec._slug(best['name'])}",
        category="stats", question_type="superlative", grouping=grouping,
        prompt=f"Among these {group_label}, which has the {word} {label}?",
        options=[u["name"] for u in four], correct=best["name"],
        explanation=(f"{best['name']} has the {word} {label} ({extreme:g}); "
                     f"next is {runner['name']} ({runner[dim]:g})."),
        source=src, score=score,
        values={u["name"]: u[dim] for u in four}, rng=rng)


def _pluralize(cls):
    return {"Cavalry": "cavalry units", "Cavalry Archer": "cavalry archers",
            "Infantry": "infantry units", "Archer": "archers",
            "Siege Weapon": "siege weapons"}.get(cls, f"{cls} units")


def generate(rng, limit=None):
    out = []

    # 1) by class (civ-invariant) ------------------------------------------------
    classes = S.by_class()
    flat = {}                       # name -> record, for archetype resolution
    for cls, recs in classes.items():
        if not cls or cls in ("Unknown",):
            continue
        for r in recs:
            r["name"] = r["unit_name"]
            r["halo"] = _cost(r)
            flat[r["unit_name"].lower()] = r
        for dim, wmax, word, label in STAT_DIMS:
            q = _superlative(recs, dim, wmax, word, label, grouping="archetype",
                             group_label=_pluralize(cls),
                             src=f"ref_units.final_{dim} by class", rng=rng)
            if q:
                out.append(q)

    # 2) curated archetype clusters ---------------------------------------------
    for key, cl in S.archetypes().items():
        if key.startswith("_"):
            continue
        members = []
        for frag in cl.get("members", []):
            rec = flat.get(frag.lower())
            if not rec:                                   # fuzzy: substring match
                rec = next((v for k, v in flat.items() if frag.lower() in k), None)
            if rec:
                members.append(rec)
        members = list({m["unit_name"]: m for m in members}.values())
        if len(members) < spec.N_OPTIONS:
            continue
        for dim, wmax, word, label in STAT_DIMS:
            q = _superlative(members, dim, wmax, word, label, grouping="uu_cluster",
                             group_label=cl["label"],
                             src=f"ref_units.final_{dim}, archetype:{key}", rng=rng)
            if q:
                out.append(q)

    # 3) same unit line, across civs --------------------------------------------
    CIV_DIMS = [d for d in STAT_DIMS if d[0] in ("hp", "attack", "melee_armor",
                                                 "pierce_armor", "speed")]
    for line, civ_rows in S.lines_cross_civ().items():
        pool = []
        for r in civ_rows:
            rec = {"name": f"{r['civ_name']} {line}", "halo": _cost(r, "final_")}
            for s in ("hp", "attack", "melee_armor", "pierce_armor", "speed"):
                rec[s] = r.get(f"final_{s}")
            pool.append(rec)
        for dim, wmax, word, label in CIV_DIMS:
            q = _superlative(pool, dim, wmax, word, label, grouping="line_cross_civ",
                             group_label=f"fully-upgraded {line}s",
                             src=f"ref_units.final_{dim} across civs", rng=rng)
            if q:
                out.append(q)

    out.sort(key=lambda q: q["score"], reverse=True)
    return out[:limit] if limit else out


if __name__ == "__main__":
    import random
    qs = generate(random.Random(1))
    print(f"generated {len(qs)} stat questions")
    for q in qs[:8]:
        print(f"\n[{q['difficulty']} score={q['score']}] {q['prompt']}")
        for i, o in enumerate(q["options"]):
            mark = " *" if i in q["correct_indices"] else "  "
            print(f"   {mark} {o}  ({q['meta']['values'][o]})")
