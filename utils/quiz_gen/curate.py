"""Taste-model rescorer + diversifier — turns the raw candidate pool into a ranked,
de-duplicated shortlist that reflects the owner's feedback.

The generators score for tightness+surprise (intrinsic). This layer applies the
LEARNED TASTE (data/quiz_taste.json) on top: shape/dimension/grouping weights, a
multi-select bonus, and penalties for textbook facts and same-line upgrade-tier
option sets. Editing the JSON re-tunes the picks without regenerating.

    python utils/quiz_gen/curate.py            # prints the curated top per category
"""
from __future__ import annotations

import json
import os
import re

import spec

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
_CANDS = os.path.join(_REPO, "data", "quiz_questions.candidates.json")
_TASTE = os.path.join(_REPO, "data", "quiz_taste.json")
_OUT = os.path.join(_REPO, "data", "quiz_questions.curated.json")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dim(source):
    m = re.search(r"final_(\w+?)(?:\b|_)", source or "")
    return m.group(1) if m else None


def _tier_clash(options, tier_lines):
    """True if 2+ options are DIFFERENT members of the same upgrade-tier line
    (e.g. Scorpion + Heavy Scorpion) — i.e. comparing a unit to its own upgrade."""
    for line in tier_lines:
        # a clash = 2+ options that are DIFFERENT tier names of the same line
        names = set()
        for o in options:
            for m in line:
                if o == m or o.endswith(" " + m):
                    names.add(m)
        if len(names) >= 2:
            return True
    return False


def _known_fact(q, known):
    blob = (q["prompt"] + " " + " ".join(q["options"])).lower()
    return any(all(s.lower() in blob for s in kf["match"]) for kf in known if "match" in kf)


def _spread_penalty(q):
    """STANDING closeness rule, enforced for EVERY category: options must be a tight
    cluster, not a clear answer beside obvious throwaways. Penalise by how wide the
    numeric option values spread (relative). Categorical questions (membership) are
    exempt (relative_spread returns None)."""
    rel = spec.relative_spread(q.get("meta", {}).get("values", {}).values())
    if rel is None:
        return 1.0
    if rel > 0.55:
        return 0.3
    if rel > 0.35:
        return 0.6
    if rel > 0.20:
        return 0.85
    return 1.0


def taste_score(q, t):
    s = q["score"]
    s *= t["shape_weights"].get(q.get("question_type"), 1.0)
    s *= t["grouping_weights"].get(q.get("grouping"), 1.0)
    if q["category"] == "stats":
        s *= t["dimension_weights"].get(_dim(q.get("source")), 1.0)
    if len(q.get("correct_indices", [])) > 1:
        s *= t["multi_select_bonus"]
    if _tier_clash(q["options"], t["tier_lines"]):          # any grouping, not just generic
        s *= t["tier_clash_penalty"]
    s *= _spread_penalty(q)
    if _known_fact(q, t["known_facts"]):
        s *= t["known_fact_penalty"]
    return round(s, 4)


def main(top_per_cat=6):
    cands = _load(_CANDS)
    t = _load(_TASTE)
    for q in cands:
        q["taste_score"] = taste_score(q, t)
    cands.sort(key=lambda q: q["taste_score"], reverse=True)
    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump(cands, f, indent=2, ensure_ascii=False)

    by = {}
    for q in cands:
        by.setdefault(q["category"], []).append(q)
    for cat in ("combat", "stats", "techgaps", "effects", "siege"):
        print("=" * 70)
        print(f"{cat.upper()}  (top {top_per_cat} by taste)")
        print("=" * 70)
        for q in by.get(cat, [])[:top_per_cat]:
            print(f"  [{q['question_type']}/{q['grouping']} base={q['score']} taste={q['taste_score']}]")
            print(f"   {q['prompt']}")
            for i, o in enumerate(q["options"]):
                mark = "*" if i in q["correct_indices"] else " "
                print(f"      {mark} {o}  {q['meta']['values'].get(o, '')}")
            print()
    print(f"\nWrote ranked pool to {_OUT}")


if __name__ == "__main__":
    main()
