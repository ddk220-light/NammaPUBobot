"""Draw a rotated, varied N-week sample from the bank with HARD no-repeat:
- no question (option-set) ever repeats,
- combat: each opponent unit used <=2x and each answer unit once,
- techgaps: each answer civ used once, each unit-line <=2x,
- stats: each unit-line once,
- effects: each answer unit once, effect dimension varied.

    python utils/quiz_gen/sample_weeks.py [weeks]
"""
from __future__ import annotations

import json
import os
import re
import sys

_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_BANK = os.path.join(_REPO, "data", "quiz_bank.json")
_OUT = os.path.join(_REPO, "data", "quiz_sample_weeks.json")

ROTATION = ["combat", "techgaps", "combat", "stats", "combat", "effects", "techgaps"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _unit(opt):
    return opt.rsplit(" (", 1)[0]


def _answers(q):
    return [q["options"][i] for i in q["correct_indices"]]


def _facets(q):
    """Freshness facets that must be UNUSED, plus per-facet caps."""
    optset = ("opt", tuple(sorted(q["options"])))
    f = [(optset, 1)]
    if q["category"] == "combat":
        f.append((("c_ans", _unit(_answers(q)[0])), 1))
        f.append((("c_opp", q["meta"].get("opp")), 2))               # opponent unit <=2x overall
        f.append((("c_co", (q["meta"].get("cluster"), q["meta"].get("opp"))), 1))  # each pairing once
    elif q["category"] == "techgaps":
        for civ in _answers(q):
            f.append((("t_ans", civ), 1))
        line = re.search(r"for the (.+?) line", q["prompt"])
        f.append((("t_line", line.group(1) if line else q["prompt"][:20]), 2))
    elif q["category"] == "stats":
        line = re.search(r"fully-upgraded (.+?), which", q["prompt"])
        f.append((("s_line", line.group(1) if line else q["prompt"][:20]), 1))
    elif q["category"] == "effects":
        f.append((("e_ans", _unit(_answers(q)[0])), 1))
        f.append((("e_dim", q["meta"].get("effect")), 2))
    return f


def draw(bank, weeks, blocklist=()):
    """Return `weeks` lists of 7 questions each, rotated (ROTATION) with hard
    no-repeat facets. `blocklist` is a set of question ids to exclude. Also returns
    the count of slots that needed the relaxed (option-set-unique-only) fallback."""
    block = set(blocklist)
    pool = {}
    for q in bank:
        if q["id"] in block:
            continue
        pool.setdefault(q["category"], []).append(q)
    for c in pool:
        pool[c].sort(key=lambda q: q.get("taste_score", q["score"]), reverse=True)
    counts, relaxed_hits = {}, [0]

    def take(cat, prefer_fresh_dim=None):
        for relaxed in (False, True):
            for q in pool.get(cat, []):
                fac = _facets(q)
                check = fac[:1] if relaxed else fac
                if any(counts.get(k, 0) >= cap for k, cap in check):
                    continue
                if not relaxed and prefer_fresh_dim and q.get("meta", {}).get("effect") == prefer_fresh_dim:
                    continue
                for k, _ in fac:
                    counts[k] = counts.get(k, 0) + 1
                if relaxed:
                    relaxed_hits[0] += 1
                return q
        return None

    out, last_dim = [], None
    for _ in range(weeks):
        week = []
        for slot in ROTATION:
            q = take(slot, prefer_fresh_dim=last_dim if slot == "effects" else None)
            if slot == "effects" and q:
                last_dim = q.get("meta", {}).get("effect")
            week.append(q)
        out.append(week)
    return out, relaxed_hits[0]


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    with open(_BANK, encoding="utf-8") as f:
        bank = json.load(f)
    weeks, _ = draw(bank, n)

    for wi, week in enumerate(weeks, 1):
        print("=" * 70)
        print(f"WEEK {wi}")
        print("=" * 70)
        for di, q in enumerate(week):
            print(f"\n{DAYS[di]} - {ROTATION[di]}")
            if not q:
                print("   (pool exhausted)")
                continue
            print(f"   {q['prompt']}")
            for i, o in enumerate(q["options"]):
                print(f"      {'*' if i in q['correct_indices'] else ' '} {o}")
            print(f"      -> {', '.join(_answers(q))}   [{q['difficulty']}]")
    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump(weeks, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {n} weeks to {_OUT}")


if __name__ == "__main__":
    main()
