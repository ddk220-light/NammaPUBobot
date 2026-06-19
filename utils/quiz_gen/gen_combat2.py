"""Category: COMBAT (matchups) — the primary category. Rewritten to encode every
rule learned during calibration, so questions are correct AND good with no manual
curation:

  1. CLOSE options   — pick_tight_options selects the tightest cluster of winners.
  2. SIMILAR units   — options are one archetype cluster, one engagement type
                       (all melee or all ranged; a ranged unit just kites = unfair).
  3. SENSIBLE foe    — opponent is a common unit the attacker type actually fights
                       (cavalry vs archers/spears, archers vs the melee they kite,
                       infantry vs other infantry) — never a niche UU.
  4. VALID foe       — the named opponent civ genuinely fields that unit
                       (sources.baseline_civ -> a fielding, no-bonus civ).
  5. DESCRIPTIVE     — prompt states the 3k cost-parity scale and names the
                       opponent's civ; every option is tagged with its civ.

generate(rng, limit=None) -> [candidate dict]  (spec schema).
"""
from __future__ import annotations

import collections

import sources as S
import spec

# Both scales are asked (stated in the prompt): 3k = cost parity, 30v30 = equal count.
SCALES = {"3k": "3,000-resource (cost-parity)", "30v30": "30-vs-30 (equal numbers)"}

# Opponent menus by the ATTACKER's engagement/role, given as the matchup SLUG actually
# simulated (units are stored by final-upgrade slug). Named via display_name(slug) so
# the label matches the real unit. Common, sensible foes only.
RANGED_OPP_SLUGS = ["champion", "halberdier", "paladin", "heavy_camel", "arbalester"]
MELEE_CAV_OPP_SLUGS = ["arbalester", "hand_cannoneer", "heavy_cav_archer"]   # ranged units cavalry close on
MELEE_INF_OPP_SLUGS = ["champion", "halberdier", "elite_eagle", "paladin"]
MAX_OPP_CIVS = 3            # vary the opponent civ (avoids "always Byzantines Arbalester")

# Correct display/validity name per slug — display_name() mislabels some final-upgrade
# slugs (paladin->'Cavalier', heavy_cav_archer->'Cavalry Archer'). These match ref_units.
OPP_NAME = {"champion": "Champion", "halberdier": "Halberdier", "arbalester": "Arbalester",
            "hand_cannoneer": "Hand Cannoneer", "paladin": "Paladin",
            "heavy_cav_archer": "Heavy Cavalry Archer", "heavy_camel": "Heavy Camel Rider",
            "elite_eagle": "Elite Eagle Warrior"}

# The opponent must be a civ's SIGNATURE unit (a recognizable pairing), never a random
# valid one like "Byzantine Paladin". Map each opponent unit -> civs famous for it.
SIGNATURE_OPP_CIVS = {
    "Champion": ["Aztecs", "Vikings", "Japanese", "Teutons", "Malians"],
    "Halberdier": ["Slavs", "Teutons", "Malians", "Bohemians"],
    "Paladin": ["Franks", "Teutons", "Lithuanians", "Magyars"],
    "Arbalester": ["Britons", "Mayans", "Ethiopians", "Vietnamese", "Italians"],
    "Hand Cannoneer": ["Turks", "Portuguese", "Italians", "Bohemians"],
    "Heavy Cavalry Archer": ["Mongols", "Tatars", "Cumans", "Magyars", "Huns"],
    "Heavy Camel Rider": ["Saracens", "Berbers", "Gurjaras", "Hindustanis", "Malians"],
    "Elite Eagle Warrior": ["Aztecs", "Mayans", "Incas"],
}

CLUSTER_LABEL = {"ranged_uu": "archer unique unit", "infantry_uu": "infantry unique unit",
                 "cavalry_uu": "cavalry unique unit", "gunpowder_uu": "gunpowder unique unit"}

METRICS = [  # (question_type, column, want_min, prompt verb)
    ("survive_hp", "team1_hp_pct", False, "survives the fight with the most HP remaining"),
    ("cost_value", "team1_value_lost", True, "wins while losing the least resources"),
]


def _ranged_map():
    rng = {}
    with S.golden("aoe2_units.db") as c:
        for r in c.execute("SELECT unit_name, COALESCE(attack_range,0) ar FROM unit_stats WHERE has_unit=1"):
            rng.setdefault(r["unit_name"], []).append(r["ar"])
    return {n: collections.Counter(v).most_common(1)[0][0] > 1 for n, v in rng.items()}


def _cost_map():
    cm = {}
    with S.golden("aoe2_units.db") as c:
        for r in c.execute("SELECT unit_name, COALESCE(cost_food,0)+COALESCE(cost_wood,0)+COALESCE(cost_gold,0) tc FROM unit_stats WHERE has_unit=1"):
            cm.setdefault(r["unit_name"], []).append(r["tc"])
    return {n: collections.Counter(v).most_common(1)[0][0] for n, v in cm.items()}


def _uu_civ(name):
    civs = S.fielding_civs(name)
    return next(iter(civs)) if len(civs) == 1 else None


def generate(rng, limit=None):
    ranged = _ranged_map()
    cost = _cost_map()
    d2s = {}
    con = S.matchup()
    for sl in S.distinct_matchup_units(con):
        d2s.setdefault(S.display_name(sl), sl)

    def is_ranged(n):
        return ranged.get(n, ranged.get(n.replace("Elite ", "")))

    # attacker subsets: (cluster, engagement) -> [(name, slug, civ)]
    subsets = {}
    for cl, meta in S.archetypes().items():
        if cl.startswith("_") or cl not in CLUSTER_LABEL:
            continue
        for m in meta["members"]:
            name = f"Elite {m}"
            slug = d2s.get(name) or d2s.get(m)
            r = is_ranged(name)
            if not slug or r is None:
                continue
            # Only slugs that map to EXACTLY ONE civ in the sim are true single-civ UUs.
            # Generic/regional units (Battle Elephant, Hand Cannoneer) span many civs and
            # would carry a meaningless/wrong civ label and cross-civ-averaged stats.
            civs = [x[0] for x in con.execute(
                "SELECT DISTINCT my_civ FROM matchup_battles WHERE my_unit_slug=?", (slug,))]
            if len(civs) != 1:
                continue
            subsets.setdefault((cl, r), []).append((name, slug, civs[0]))

    out = []
    for (cl, atk_ranged), members in subsets.items():
        if len(members) < spec.N_OPTIONS:
            continue
        if atk_ranged:
            opp_slugs = RANGED_OPP_SLUGS
        else:
            opp_slugs = MELEE_CAV_OPP_SLUGS if cl == "cavalry_uu" else MELEE_INF_OPP_SLUGS
        for opp_slug in opp_slugs:
            opp_name = OPP_NAME[opp_slug]
            # Opponent civ must be a SIGNATURE civ for this unit (recognizable pairing)
            # AND tech-tree valid AND present in the sim. No random civ+unit combos.
            valid = set(x[0] for x in con.execute(
                "SELECT DISTINCT opp_civ FROM matchup_battles WHERE opp_unit_slug=?", (opp_slug,))
                if S.civ_fields(x[0], opp_name))
            sig = [c for c in SIGNATURE_OPP_CIVS.get(opp_name, []) if c in valid]
            if not sig:
                continue
            for opp_civ in sig[:MAX_OPP_CIVS]:
              for scale, scale_label in SCALES.items():
                res = {r["my_unit_slug"]: r for r in con.execute(
                    "SELECT my_unit_slug, AVG(team1_hp_pct) team1_hp_pct, "
                    "AVG(team1_value_lost) team1_value_lost, "
                    "AVG(CASE WHEN winner=1 THEN 1.0 ELSE 0 END) wr "
                    "FROM matchup_battles WHERE opp_civ=? AND opp_unit_slug=? AND scale=? "
                    "GROUP BY my_unit_slug", (opp_civ, opp_slug, scale))}
                for qt, col, wmin, verb in METRICS:
                    winners = [{"name": n, "civ": c, col: res[s][col]}
                               for n, s, c in members
                               if s in res and (res[s]["wr"] or 0) >= 0.999 and res[s][col] is not None]
                    if len(winners) < spec.N_OPTIONS:
                        continue
                    for four, ans in spec.tight_windows(winners, key=lambda u, k=col: u[k],
                                                        want_max=not wmin):
                        # Skip noise-level finishes: the winner must beat its nearest
                        # rival by >2% (matchup sims have real run-to-run variance).
                        gap = min(abs(ans[col] - u[col]) for u in four if u is not ans)
                        if gap / (abs(ans[col]) or 1) < 0.02:
                            continue
                        by_cost = sorted(four, key=lambda u: cost.get(u["name"], 0), reverse=True)
                        tightv = 1 - (spec.relative_spread([u[col] for u in four]) or 0)
                        score = spec.combine(tightv, spec.surprise(by_cost.index(ans) + 1, len(four)))
                        opts = [f"{u['name']} ({u['civ']})" for u in four]
                        ans_opt = f"{ans['name']} ({ans['civ']})"
                        q = spec.make_question(
                            qid=(f"combat_{qt}_{scale}_{spec._slug(cl)}_{spec._slug(opp_name)}"
                                 f"_{spec._slug(ans['name'])}_{spec._slug(opts[0])}"),
                            category="combat", question_type=qt, grouping="matchup",
                            prompt=(f"In a {scale_label} fight, which {CLUSTER_LABEL[cl]} "
                                    f"{verb} against a {opp_civ} {opp_name}?"),
                            options=opts, correct=ans_opt,
                            explanation=(f"{ans_opt} {verb} vs a {opp_civ} {opp_name} "
                                         f"({col.replace('team1_', '')}={round(ans[col], 2)})."),
                            source=f"matchup_battles {col} scale={scale} vs {opp_civ}/{opp_slug}",
                            score=score,
                            values={f"{u['name']} ({u['civ']})": round(u[col], 3) for u in four},
                            rng=rng)
                        if q:
                            q["meta"].update({"cluster": cl, "opp": opp_name, "scale": scale})
                            out.append(q)
    con.close()
    out.sort(key=lambda q: q["score"], reverse=True)
    return out[:limit] if limit else out


if __name__ == "__main__":
    import random
    qs = generate(random.Random(7))
    print(f"generated {len(qs)} combat matchups\n")
    for q in qs[:8]:
        print(f"[{q['difficulty']} score={q['score']}] {q['prompt']}")
        for i, o in enumerate(q["options"]):
            print(f"    {'*' if i in q['correct_indices'] else ' '} {o}  {q['meta']['values'][o]}")
        print()
