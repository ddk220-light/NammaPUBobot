"""Shared question-generation contract: the candidate schema + the interestingness
scoring every category generator must use.

This is the *contract* the per-category generators (gen_*.py) build against, so the
pool stays consistent no matter which generator (or which agent) produced a question.
Pure functions only — no DB, no IO. Distractor selection is randomised through an
injected random.Random for reproducibility (the bot/team_insights.py convention).

A candidate question dict:
    {
      "id":            stable unique id,
      "category":      stats | techgaps | combat | effects | siege,
      "question_type": short slug for the template that made it,
      "grouping":      which grouping strategy assembled the options
                       (line_cross_civ | archetype | building | within_civ | uu_cluster | open),
      "difficulty":    easy | medium | hard      (DERIVED from the score, not hardcoded),
      "prompt":        the question text,
      "options":       list[str], exactly 4,
      "correct_indices": list[int], >= 1 entry (multi-select when > 1),
      "correct_index": int | None  (== correct_indices[0] when single-answer, else None),
      "multi":         bool,
      "explanation":   one-line human explanation,
      "source":        provenance string (table/column the fact came from),
      "score":         float 0..1 interestingness (tightness + surprise),
      "meta":          {"values": {option: shown_value}, ...} for review + verification
    }
"""
from __future__ import annotations

CATEGORIES = ("stats", "techgaps", "combat", "effects", "siege")
N_OPTIONS = 4


def _slug(text):
    return "".join(c if c.isalnum() else "_" for c in str(text).lower()).strip("_")


# --------------------------------------------------------------------------- #
# Closeness — a STANDING rule: the four options must be similar, tightly-clustered
# units, never a clear answer next to obvious throwaways. Generators must select
# distractors with pick_tight_options(); curate.py additionally penalises any
# numeric question whose options spread too wide.
# --------------------------------------------------------------------------- #
def relative_spread(values):
    """(max-min)/|largest| for a set of numbers; None if any value isn't numeric
    (e.g. categorical 'missing'/'has it' membership questions are exempt)."""
    nums = []
    for v in values:
        try:
            nums.append(float(str(v).replace("+", "").rstrip("s%").split()[0]))
        except (ValueError, IndexError):
            return None
    if len(nums) < 2:
        return None
    lo, hi = min(nums), max(nums)
    base = max(abs(lo), abs(hi)) or 1.0
    return (hi - lo) / base


def tight_windows(items, key, want_max=True, n=N_OPTIONS, max_rel=0.16, k=4):
    """Volume version of pick_tight_options: return up to k DISTINCT tight windows
    (each a [items], answer) with relative spread <= max_rel and a unique extreme.
    Lets one (cluster, opponent, metric) yield several good questions for the bank."""
    valued = sorted(((it, key(it)) for it in items if key(it) is not None),
                    key=lambda p: p[1])
    res = []
    for i in range(len(valued) - n + 1):
        w = valued[i:i + n]
        lo, hi = w[0][1], w[-1][1]
        if want_max and w[-1][1] == w[-2][1]:
            continue
        if not want_max and w[0][1] == w[1][1]:
            continue
        base = max(abs(lo), abs(hi)) or 1.0
        rel = (hi - lo) / base
        if rel <= max_rel:
            ans = w[-1][0] if want_max else w[0][0]
            res.append((rel, [it for it, _ in w], ans))
    res.sort(key=lambda x: x[0])
    return [(win, ans) for _, win, ans in res[:k]]


def pick_tight_options(items, key, want_max=True, n=N_OPTIONS):
    """THE distractor selector every generator should use. From `items` (already
    narrowed to *similar* units — same line/class/engagement), return the n whose
    `key` values form the TIGHTEST cluster that still has a unique extreme at the
    wanted end, so the wrong options hug the answer instead of being easy throwaways.

    Returns (chosen_items, answer_item) or (None, None) if no clean tight set exists.
    The answer is the extreme of the chosen window (the question is 'among these n,
    which is the most/least…', so it is correct by construction)."""
    valued = sorted(((it, key(it)) for it in items if key(it) is not None),
                    key=lambda p: p[1])
    if len(valued) < n:
        return None, None
    best = None
    for i in range(len(valued) - n + 1):
        window = valued[i:i + n]
        lo, hi = window[0][1], window[-1][1]
        if want_max and window[-1][1] == window[-2][1]:      # tie at the top → ambiguous
            continue
        if not want_max and window[0][1] == window[1][1]:    # tie at the bottom
            continue
        base = max(abs(lo), abs(hi)) or 1.0
        spread = (hi - lo) / base
        if best is None or spread < best[0]:
            ans = window[-1][0] if want_max else window[0][0]
            best = (spread, [it for it, _ in window], ans)
    if not best:
        return None, None
    return best[1], best[2]


# --------------------------------------------------------------------------- #
# Scoring — the definition of "interesting".
# --------------------------------------------------------------------------- #
def tightness(correct_value, distractor_values, full_range):
    """How hard is it to tell the answer from the nearest wrong option? 1.0 = the
    runner-up is basically tied with the answer; 0.0 = the answer is miles clear.

    correct_value      — the asked-stat value of the correct option
    distractor_values  — the same stat for the 3 wrong options
    full_range         — (max-min) of the stat across the whole candidate pool the
                         group was drawn from, so the gap is normalised to the stat.
    """
    if not distractor_values or not full_range:
        return 0.0
    nearest_gap = min(abs(correct_value - d) for d in distractor_values)
    return max(0.0, 1.0 - (nearest_gap / full_range))


def surprise(answer_rank_on_halo, n):
    """How counter-intuitive is the answer? The correct option is #1 on the *asked*
    stat; if it ranks LOW on a 'halo' stat people associate with winning (cost, HP,
    overall battle power, fame), the answer defies intuition → high surprise.

    answer_rank_on_halo — 1-based rank of the correct option on the halo stat among
                          the 4 options (1 = also the halo leader → no surprise;
                          n = halo loser but stat winner → max surprise).
    """
    if n <= 1:
        return 0.0
    return (answer_rank_on_halo - 1) / (n - 1)


def combine(tight, surp, w_tight=0.6, w_surprise=0.4):
    """Blend the two interestingness dimensions. Weights are tunable during
    generation (the pool is a reviewable JSON PR)."""
    return round(w_tight * tight + w_surprise * surp, 4)


def difficulty_from(score):
    """Derive difficulty from interestingness, fixing the old hardcoded-constant
    problem. Tight/surprising questions are harder."""
    if score >= 0.66:
        return "hard"
    if score >= 0.33:
        return "medium"
    return "easy"


# --------------------------------------------------------------------------- #
# Assembly.
# --------------------------------------------------------------------------- #
def make_question(*, qid, category, question_type, grouping, prompt, options,
                  correct, explanation, source, score, values=None, rng):
    """Assemble + shuffle a candidate. `correct` is one option string OR a list of
    option strings (multi-select). Returns None if the option set is malformed
    (needs exactly N_OPTIONS distinct options and >= 1 valid correct answer)."""
    opts = list(dict.fromkeys(options))
    if len(opts) != N_OPTIONS:
        return None
    correct_set = {correct} if isinstance(correct, str) else set(correct)
    if not correct_set or not correct_set.issubset(set(opts)):
        return None
    rng.shuffle(opts)
    idx = sorted(opts.index(c) for c in correct_set)
    multi = len(idx) > 1
    return {
        "id": qid,
        "category": category,
        "question_type": question_type,
        "grouping": grouping,
        "difficulty": difficulty_from(score),
        "prompt": prompt,
        "options": opts,
        "correct_indices": idx,
        "correct_index": None if multi else idx[0],
        "multi": multi,
        "explanation": explanation,
        "source": source,
        "score": score,
        "meta": {"values": values or {}},
    }
