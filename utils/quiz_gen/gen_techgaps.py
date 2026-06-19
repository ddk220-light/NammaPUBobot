"""Category: TECH GAPS — "which civilisation(s) miss an upgrade for a unit line?"

MULTI-SELECT category. Mirrors gen_stats.py's shape:

    generate(rng, limit=None) -> list[candidate dict]   (schema in spec.py)

Every question is built with spec.make_question(..., correct=[list of civ names]).
The correct answer (the set of civs lacking a tech) is always computed from the
DB — never hardcoded.

DATA MODEL
----------
For a unit LINE (unit_name, e.g. 'Paladin' = knight line, 'Arbalester' = archer
line, 'Halberdier' = spear line, 'Champion' = militia line, 'Hussar' = scout
line) the standard-upgrade set is the tech_names with tech_type='standard' seen
across all civs that field that line:

    ref_techs_applied(tech_name, tech_type='standard') JOIN ref_units(unit_name)

A civ MISSES upgrade T if it fields the line but T is absent for that civ's row.
We only ask about techs with PARTIAL coverage (some civs have it, some don't) —
techs every civ has are boring, and we drop the age/auto techs that aren't real
"missable upgrades" (Feudal/Imperial Age, Flemish Militia, auto Scout upgrade).

QUESTION FORMS
--------------
(1) lack_single  : "Which of these civs LACK <upgrade> for the <line>?"  — a
                   foursome where 1-3 of 4 are missing it (multi-select).
(2) lack_both    : "Which of these civs misses BOTH <A> AND <B>?"  — civs lacking
                   the intersection of two partial techs.
(3) only_without : "Which is the ONLY one of these civs WITHOUT <rare upgrade>?"
                   — single correct answer (still emitted via correct=[one]).

SCORING (spec.combine of tightness + surprise), documented inline below:
  tightness proxy = how PARTIAL/rare the gap is. A tech that almost everyone has
    (the missing civs are a tiny minority) is a needle-in-haystack fact -> high
    tightness. A 50/50 split is easy -> low tightness. We map the global "miss
    rate" of the tech to tightness so rarer gaps score harder.
  surprise        = whether a FAMOUS/strong civ is the one missing it (people
    assume top civs have everything). We rank the 4 option civs by a fame halo
    and feed the answer's fame rank into spec.surprise.
"""
from __future__ import annotations

import sources as S
import spec

# Standard techs that are NOT "missable upgrades" — age advances and auto/scenario
# techs. Excluding these keeps every asked tech a genuine blacksmith/uni upgrade.
_NON_UPGRADE = {
    "Feudal Age", "Imperial Age", "Auto upgrade Scout Feudal Age",
    "Flemish Militia Age3", "Flemish Militia Age4",
}

# Readable label for each line's "thing" so prompts read naturally.
_LINE_LABEL = {
    "Paladin": "Paladin (knight line)",
    "Cavalier": "Cavalier (knight line)",
    "Arbalester": "Arbalester (archer line)",
    "Crossbowman": "Crossbowman (archer line)",
    "Halberdier": "Halberdier (spear line)",
    "Pikeman": "Pikeman (spear line)",
    "Champion": "Champion (militia line)",
    "Two-Handed Swordsman": "Two-Handed Swordsman (militia line)",
    "Hussar": "Hussar (scout line)",
    "Light Cavalry": "Light Cavalry (scout line)",
    "Heavy Cavalry Archer": "Heavy Cavalry Archer line",
    "Cavalry Archer": "Cavalry Archer line",
    "Heavy Camel Rider": "Heavy Camel Rider line",
    "Elite Skirmisher": "Elite Skirmisher line",
}

# Fame/strength halo for SURPRISE only (never affects the correct answer, which is
# DB-derived). Higher = more famous/strong -> people assume it has every upgrade,
# so it lacking one is more counter-intuitive. Civs not listed get a low default.
_FAME = {
    "Franks": 10, "Britons": 10, "Mongols": 10, "Mayans": 9, "Aztecs": 9,
    "Huns": 9, "Khmer": 9, "Chinese": 8, "Vikings": 8, "Lithuanians": 8,
    "Berbers": 8, "Teutons": 8, "Byzantines": 7, "Persians": 7, "Spanish": 7,
    "Turks": 7, "Goths": 7, "Japanese": 7, "Magyars": 7, "Cumans": 7,
    "Poles": 6, "Bohemians": 6, "Ethiopians": 6, "Malians": 6, "Tatars": 6,
    "Italians": 6, "Indians": 6, "Hindustanis": 6, "Saracens": 6, "Celts": 6,
    "Slavs": 5, "Bulgarians": 5, "Burgundians": 5, "Sicilians": 5, "Koreans": 5,
    "Portuguese": 5, "Vietnamese": 5, "Incas": 5, "Malay": 5, "Bengalis": 5,
    "Gurjaras": 5, "Dravidians": 5, "Armenians": 5, "Georgians": 5,
}


def _fame(civ):
    return _FAME.get(civ, 3)


def _load_coverage(con):
    """Return {line: {"civs": set, "tech_civs": {tech: set(civs having it)}}}
    over standard lines fielded by enough civs, restricted to real upgrade techs."""
    # civs per line
    lines = {}
    for r in con.execute(
            "SELECT DISTINCT unit_name, civ_name FROM ref_units "
            "WHERE unit_type='standard'"):
        lines.setdefault(r["unit_name"], {"civs": set(), "tech_civs": {}})
        lines[r["unit_name"]]["civs"].add(r["civ_name"])
    # civs having each standard tech per line
    q = """SELECT u.unit_name, t.tech_name, u.civ_name
           FROM ref_techs_applied t JOIN ref_units u ON t.ref_unit_id = u.id
           WHERE u.unit_type='standard' AND t.tech_type='standard'"""
    for r in con.execute(q):
        if r["tech_name"] in _NON_UPGRADE:
            continue
        d = lines.get(r["unit_name"])
        if d is None:
            continue
        d["tech_civs"].setdefault(r["tech_name"], set()).add(r["civ_name"])
    return lines


def _tightness_from_missrate(miss_rate):
    """Rarer gap = harder. miss_rate in (0,1) is the fraction of fielding civs that
    LACK the tech. A tiny minority missing (miss_rate ~0.03, like Celts/Plate
    Barding) is a hard needle -> high tightness. A 50/50 split is easy.
    tightness = 1 - 2*|miss_rate - low| clamped; we treat the rarest gaps as
    tightest by using 1 - miss_rate scaled, but cap so a near-universal tech (one
    civ missing) stays the hardest."""
    # 1 - miss_rate gives: 1 civ of 30 missing -> ~0.97 (very tight/hard);
    # half missing -> 0.5 (medium); most missing -> low (the gap is common, easy).
    return max(0.0, min(1.0, 1.0 - miss_rate))


def _surprise_for(answer_civs, four_civs):
    """spec.surprise wants the answer's rank on a halo stat among the options. For
    multi-answer we take the BEST (most famous) missing civ — the most surprising
    single fact in the set drives the score."""
    ranked = sorted(four_civs, key=_fame, reverse=True)  # 1 = most famous
    best_rank = min(ranked.index(c) + 1 for c in answer_civs)
    return spec.surprise(best_rank, len(four_civs))


# --------------------------------------------------------------------------- #
# Form (1): which of these civs LACK <upgrade>?
# --------------------------------------------------------------------------- #
def _gen_lack_single(line, label, civs, tech, having, rng):
    missing = civs - having
    if not (1 <= len(missing) <= len(civs) - 1):
        return None
    miss_rate = len(missing) / len(civs)
    n_miss = rng.randint(1, min(3, len(missing)))      # 1-3 of the 4 are missing
    n_have = spec.N_OPTIONS - n_miss
    have_list = list(having)
    miss_list = list(missing)
    if len(have_list) < n_have or len(miss_list) < n_miss:
        return None
    rng.shuffle(have_list)
    rng.shuffle(miss_list)
    chosen_missing = miss_list[:n_miss]
    chosen_having = have_list[:n_have]
    four = chosen_missing + chosen_having
    if len(set(four)) != spec.N_OPTIONS:
        return None

    tight = _tightness_from_missrate(miss_rate)
    surp = _surprise_for(chosen_missing, four)
    score = spec.combine(tight, surp)

    return spec.make_question(
        qid=f"techgaps_lack_{spec._slug(line)}_{spec._slug(tech)}_"
            f"{spec._slug('_'.join(sorted(chosen_missing)))}",
        category="techgaps", question_type="lack_single", grouping="open",
        prompt=f"Which of these civilisations LACK {tech} for the "
               f"{label}?",
        options=four, correct=chosen_missing,
        explanation=(f"{', '.join(sorted(chosen_missing))} cannot research {tech} "
                     f"for the {line} line; "
                     f"{', '.join(sorted(chosen_having))} can. "
                     f"({len(having)}/{len(civs)} {line} civs have it.)"),
        source="ref_techs_applied(tech_type='standard') JOIN ref_units",
        score=score,
        values={c: ("missing" if c in missing else "has it") for c in four},
        rng=rng)


# --------------------------------------------------------------------------- #
# Form (2): which civs miss BOTH A AND B?
# --------------------------------------------------------------------------- #
def _gen_lack_both(line, label, civs, ta, hav_a, tb, hav_b, rng):
    miss_a, miss_b = civs - hav_a, civs - hav_b
    both = miss_a & miss_b                      # lacks A *and* B
    only = (miss_a | miss_b) - both             # lacks exactly one (distractors)
    has_both = civs - miss_a - miss_b           # full upgrades (distractors)
    if not (1 <= len(both) <= 2):
        return None
    distract_pool = list(only) + list(has_both)
    n_correct = len(both)
    n_distract = spec.N_OPTIONS - n_correct
    if len(distract_pool) < n_distract:
        return None
    rng.shuffle(distract_pool)
    correct = list(both)
    four = correct + distract_pool[:n_distract]
    if len(set(four)) != spec.N_OPTIONS:
        return None

    # tightness: the rarer the "missing both" intersection, the harder.
    miss_rate = len(both) / len(civs)
    tight = _tightness_from_missrate(miss_rate)
    surp = _surprise_for(correct, four)
    score = spec.combine(tight, surp)

    def _why(c):
        m = []
        if c in miss_a:
            m.append(f"no {ta}")
        if c in miss_b:
            m.append(f"no {tb}")
        return ", ".join(m) if m else "has both"

    return spec.make_question(
        qid=f"techgaps_both_{spec._slug(line)}_{spec._slug(ta)}_{spec._slug(tb)}_"
            f"{spec._slug('_'.join(sorted(correct)))}",
        category="techgaps", question_type="lack_both", grouping="open",
        prompt=f"Which of these civilisations miss BOTH {ta} AND {tb} "
               f"for the {label}?",
        options=four, correct=correct,
        explanation=(f"{', '.join(sorted(correct))} lack both {ta} and {tb} "
                     f"on the {line} line."),
        source="ref_techs_applied(tech_type='standard') JOIN ref_units",
        score=score,
        values={c: _why(c) for c in four}, rng=rng)


# --------------------------------------------------------------------------- #
# Form (3): the ONLY one of these 4 without a (near-universal) upgrade.
# --------------------------------------------------------------------------- #
def _gen_only_without(line, label, civs, tech, having, rng):
    missing = civs - having
    miss_rate = len(missing) / len(civs)
    # rare gap only: want a single famous-ish odd-one-out, so 1..~25% missing.
    if not (len(missing) >= 1) or miss_rate > 0.25:
        return None
    if len(having) < 3:
        return None
    odd = rng.choice(list(missing))
    have_list = list(having)
    rng.shuffle(have_list)
    three = have_list[:3]
    four = [odd] + three
    if len(set(four)) != spec.N_OPTIONS:
        return None

    tight = _tightness_from_missrate(miss_rate)      # rarer -> tighter/harder
    surp = _surprise_for([odd], four)
    score = spec.combine(tight, surp)

    return spec.make_question(
        qid=f"techgaps_only_{spec._slug(line)}_{spec._slug(tech)}_{spec._slug(odd)}",
        category="techgaps", question_type="only_without", grouping="open",
        prompt=f"Which is the ONLY one of these civilisations WITHOUT {tech} "
               f"for the {label}?",
        options=four, correct=[odd],
        explanation=(f"{odd} is the only one here lacking {tech} on the {line} "
                     f"line — the other three have it "
                     f"({len(having)}/{len(civs)} {line} civs do)."),
        source="ref_techs_applied(tech_type='standard') JOIN ref_units",
        score=score,
        values={c: ("missing" if c == odd else "has it") for c in four},
        rng=rng)


def generate(rng, limit=None):
    out = []
    con = S.golden("aoe2_reference.db")
    try:
        lines = _load_coverage(con)
    finally:
        con.close()

    for line, d in lines.items():
        civs = d["civs"]
        if len(civs) < spec.N_OPTIONS:
            continue
        label = _LINE_LABEL.get(line, f"{line} line")
        tech_civs = d["tech_civs"]
        # partial-coverage techs only (skip universal techs).
        partial = {t: hav for t, hav in tech_civs.items()
                   if 0 < len(civs - hav) < len(civs)}
        if not partial:
            continue

        for tech, having in partial.items():
            miss_rate = len(civs - having) / len(civs)
            # Form (1): emit a couple of foursomes per tech for variety.
            reps = 2 if miss_rate < 0.5 else 1
            for _ in range(reps):
                q = _gen_lack_single(line, label, civs, tech, having, rng)
                if q:
                    out.append(q)
            # Form (3): rare-gap odd-one-out.
            q = _gen_only_without(line, label, civs, tech, having, rng)
            if q:
                out.append(q)

        # Form (2): pair up two partial techs and ask "misses both".
        partial_items = sorted(partial.items())
        for i in range(len(partial_items)):
            for j in range(i + 1, len(partial_items)):
                ta, hav_a = partial_items[i]
                tb, hav_b = partial_items[j]
                q = _gen_lack_both(line, label, civs, ta, hav_a, tb, hav_b, rng)
                if q:
                    out.append(q)

    # de-dup by id (different reps can collide) and rank by interestingness.
    seen, uniq = set(), []
    for q in out:
        if q["id"] in seen:
            continue
        seen.add(q["id"])
        uniq.append(q)
    uniq.sort(key=lambda q: q["score"], reverse=True)
    return uniq[:limit] if limit else uniq


if __name__ == "__main__":
    import random
    qs = generate(random.Random(7))
    print(f"generated {len(qs)} techgaps questions")
    from collections import Counter
    print("by type:", Counter(q["question_type"] for q in qs))
    print("by difficulty:", Counter(q["difficulty"] for q in qs))
    for q in qs[:10]:
        print(f"\n[{q['difficulty']} score={q['score']} {q['question_type']}] "
              f"{q['prompt']}")
        for i, o in enumerate(q["options"]):
            mark = " *" if i in q["correct_indices"] else "  "
            print(f"   {mark} {o}  ({q['meta']['values'][o]})")
