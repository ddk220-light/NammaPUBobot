"""Pure question-template functions: unit dicts -> question dicts.

The correct answer is COMPUTED from the same data the distractors come from, so it
is correct by construction. Each template returns a list of question dicts (possibly
empty when the data does not support a clean question). No DB, no IO. Distractor
selection is randomised through an injected random.Random for deterministic tests
(the bot/team_insights.py convention)."""
import random as _random


def _slug(text):
    return "".join(c if c.isalnum() else "_" for c in text.lower()).strip("_")


def _make_question(qid, category, difficulty, prompt, correct_name, distractors,
                   explanation, source, rng):
    """Assemble a 4-option question. Needs >= 3 distinct distractors that differ
    from the correct answer. Returns None if it cannot."""
    pool = [d for d in dict.fromkeys(distractors) if d != correct_name]
    if len(pool) < 3:
        return None
    rng.shuffle(pool)
    options = [correct_name] + pool[:3]
    rng.shuffle(options)
    return {
        "id": qid,
        "category": category,
        "difficulty": difficulty,
        "prompt": prompt,
        "options": options,
        "correct_index": options.index(correct_name),
        "explanation": explanation,
        "source": source,
    }


def superlative(units, stat, label, category, difficulty="medium", rng=None,
                want_max=True):
    """'Which of these has the highest/lowest <label>?' over units that carry the
    stat. units: list of {unit_name, <stat>}. Emits one question (the true extreme
    plus three other units as distractors)."""
    rng = rng or _random.Random()
    have = [u for u in units if u.get(stat) is not None]
    if len(have) < 4:
        return []
    pick = max if want_max else min
    top = pick(have, key=lambda u: u[stat])
    word = "highest" if want_max else "lowest"
    # Distractors must be STRICTLY beyond the extreme so the answer is unambiguous —
    # a tie at the extreme would make two options correct. Skip such foursomes.
    if want_max:
        others = [u["unit_name"] for u in have if u[stat] < top[stat]]
    else:
        others = [u["unit_name"] for u in have if u[stat] > top[stat]]
    if len(others) < 3:
        return []
    q = _make_question(
        f"superlative_{word}_{_slug(stat)}_{_slug(top['unit_name'])}",
        category, difficulty,
        f"Which of these has the {word} {label}?",
        top["unit_name"], others,
        f"{top['unit_name']} has the {word} {label} ({top[stat]}).",
        f"unit_stats.{stat}", rng)
    return [q] if q else []


def bonus_membership(units, armor_class_id, class_name, category, difficulty="medium",
                     rng=None):
    """'Which of these deals bonus damage vs <class_name>?' One unit that DOES (the
    answer) and three that do NOT. units: list of {unit_name, attacks:{class_id:val}}.
    Emits at most one question; [] if fewer than one yes or three no units."""
    rng = rng or _random.Random()
    key = str(armor_class_id)
    yes = [u["unit_name"] for u in units
           if (u.get("attacks") or {}).get(key, 0) and float(u["attacks"][key]) > 0]
    no = [u["unit_name"] for u in units
          if float((u.get("attacks") or {}).get(key, 0) or 0) <= 0]
    if not yes or len(no) < 3:
        return []
    correct = rng.choice(yes)
    q = _make_question(
        f"bonus_{key}_{_slug(correct)}",
        category, difficulty,
        f"Which of these deals bonus damage vs {class_name}?",
        correct, no,
        f"{correct} has a bonus-damage entry against {class_name} (armor class {key}).",
        "unit_stats.attacks_json", rng)
    return [q] if q else []


def only_one_with_mechanic(special_names, normal_names, mechanic_label, category,
                           difficulty="hard", rng=None):
    """'Which of these is the only one with <mechanic_label>?' One special unit (the
    answer) and three normal ones. Emits at most one question."""
    rng = rng or _random.Random()
    specials = [s for s in dict.fromkeys(special_names)]
    normals = [n for n in dict.fromkeys(normal_names) if n not in set(specials)]
    if not specials or len(normals) < 3:
        return []
    correct = rng.choice(specials)
    q = _make_question(
        f"mechanic_{_slug(mechanic_label)}_{_slug(correct)}",
        category, difficulty,
        f"Which of these is the only one with {mechanic_label}?",
        correct, normals,
        f"{correct} is the one with {mechanic_label}; the others do not have it.",
        "ref_special_effects", rng)
    return [q] if q else []
