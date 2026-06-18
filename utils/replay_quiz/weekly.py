#!/usr/bin/env python3
"""Layer 2 of the quiz engine: the WEEKLY GENERATOR.

Deterministically selects a fresh, varied 7-day quiz (one question per day) from
the question bank (data/replay_quiz.db -> question_bank, built by build_questions.py).

Properties:
  - Deterministic per week: same week ordinal -> same 7 questions (everyone sees the
    same "quiz of the day"); a different week -> a different set.
  - Varied within a week: each day is a different THEME (economy, age speed, army,
    tech, buildings, aggression, signature) with a preferred format, giving a mix of
    "who's THE best" (top4) and "among Elo peers, who?" (elo_peers). No metric or
    answer-player repeats inside a week.
  - Refreshing across weeks: each theme's pool (its most exciting questions, by
    closeness) is rotated by the week number, so consecutive weeks differ and a
    given question won't recur for many weeks.

API:  generate_week(week_ordinal:int|None) -> list[dict]  (7 days)
CLI:  python utils/replay_quiz/weekly.py            # this week
      python utils/replay_quiz/weekly.py --demo     # show 3 different weeks
"""
import datetime
import hashlib
import json
import os
import random
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "replay_quiz.db")
POOL_PER_THEME = 60          # keep this many most-exciting questions per theme to rotate through

# (weekday, theme name, row-filter, preferred format)
THEMES = [
    ("Monday", "Economy", lambda r: r["category"] == "Villagers", "top4"),
    ("Tuesday", "Age-up speed", lambda r: r["category"] == "Age speed", "elo_peers"),
    ("Wednesday", "Army composition", lambda r: r["category"] == "Military by type" and r["metric_id"].endswith("_total"), "top4"),
    ("Thursday", "Technology timing", lambda r: r["category"] == "Tech timing", "elo_peers"),
    ("Friday", "Buildings", lambda r: r["category"] == "Buildings", "top4"),
    ("Saturday", "Early aggression", lambda r: "_pre_" in r["metric_id"] and r["category"] in ("Military", "Military by type"), "elo_peers"),
    ("Sunday", "Signature stats", lambda r: r["category"] == "Military" or r["metric_id"] in ("unique_other_total", "scout_total"), "top4"),
]


def _seed(s):
    return int(hashlib.md5(s.encode()).hexdigest(), 16) % (2 ** 32)


def load_bank():
    c = sqlite3.connect(DB).cursor()
    cols = [d[0] for d in c.execute("SELECT * FROM question_bank LIMIT 1").description]
    rows = []
    for vals in c.execute("SELECT * FROM question_bank"):
        r = dict(zip(cols, vals))
        r["options"] = json.loads(r.pop("options_json"))
        r["refs"] = json.loads(r.pop("refs_json"))
        rows.append(r)
    return rows


def _theme_pool(bank, filt, pref_fmt):
    pool = [r for r in bank if filt(r) and r["format"] == pref_fmt]
    if not pool:                                        # fall back to any format only if none
        pool = [r for r in bank if filt(r)]
    # most "exciting" first (closest races), but exclude near-ties (<0.985) for fairness
    pool = [r for r in pool if r["closeness"] <= 0.985]
    pool.sort(key=lambda r: -r["closeness"])
    return pool[:POOL_PER_THEME]


def generate_week(week_ordinal=None):
    if week_ordinal is None:
        iso = datetime.date.today().isocalendar()
        week_ordinal = iso[0] * 53 + iso[1]
    bank = load_bank()
    used_ids, used_metrics, used_answers = set(), set(), set()
    week = []
    for day, theme, filt, pref_fmt in THEMES:
        pool = _theme_pool(bank, filt, pref_fmt)
        if not pool:
            continue
        order = pool[:]
        random.Random(_seed(theme)).shuffle(order)      # fixed per-theme order (stable across weeks)
        picked = None
        for off in range(len(order)):
            cand = order[(week_ordinal + off) % len(order)]
            if cand["question_id"] in used_ids or cand["metric_id"] in used_metrics or cand["answer"] in used_answers:
                continue
            picked = cand
            break
        picked = picked or order[week_ordinal % len(order)]
        used_ids.add(picked["question_id"]); used_metrics.add(picked["metric_id"]); used_answers.add(picked["answer"])
        # shuffle option order deterministically per (week, question) so the answer slot moves
        opts = picked["options"][:]
        random.Random(_seed(f"{week_ordinal}:{picked['question_id']}")).shuffle(opts)
        week.append({"day": day, "theme": theme, "format": picked["format"], "metric_id": picked["metric_id"],
                     "question": picked["question"], "options": opts, "answer": picked["answer"],
                     "reference_games": picked["refs"], "closeness": picked["closeness"]})
    return week


def _print_week(week, ordinal):
    print(f"\n================  QUIZ WEEK #{ordinal}  ================")
    for d in week:
        tag = "best-overall" if d["format"] == "top4" else "Elo-peers"
        print(f"\n{d['day']} — {d['theme']} [{tag}]")
        print(f"  {d['question']}")
        for j, o in enumerate(d["options"]):
            elo = f", Elo {o['elo']}" if o.get("elo") else ""
            star = "  <-- answer" if o["identity"] == d["answer"] else ""
            print(f"     {chr(65+j)}. {o['identity']}{elo} ({o['value']}){star}")
        refs = "; ".join(f"{g['identity']} {g['value']} ({g['civ']}, #{g['match_id']})" for g in d["reference_games"])
        print(f"     reveal/reference games: {refs}")


def main():
    import sys
    if "--demo" in sys.argv:
        base = datetime.date.today().isocalendar()
        base = base[0] * 53 + base[1]
        for w in (base, base + 1, base + 2):
            _print_week(generate_week(w), w)
        # variety self-check
        wk = generate_week(base)
        print("\n[check] distinct categories:", len({d['theme'] for d in wk}),
              "| formats:", {d['format'] for d in wk},
              "| distinct answers:", len({d['answer'] for d in wk}), "/", len(wk))
        # overlap between consecutive weeks
        a = {d['question'] for d in generate_week(base)}
        b = {d['question'] for d in generate_week(base + 1)}
        print(f"[check] questions shared between week {base} and {base+1}: {len(a & b)} / 7")
    else:
        iso = datetime.date.today().isocalendar()
        _print_week(generate_week(), iso[0] * 53 + iso[1])


if __name__ == "__main__":
    main()
