#!/usr/bin/env python3
"""Layer 1 of the quiz engine: materialize the QUESTION BANK.

Enumerates every valid, good question that the data supports and stores each as a
ready-to-render row in data/replay_quiz.db -> table `question_bank` (also exported
to data/question_bank.json). The weekly generator (weekly.py) just samples this.

For each metric it generates:
  - top4_best                : options = leaderboard top 4; answer = the best       ("who is THE best")
  - top4_worst (timing only) : options = the 4 slowest/latest; answer = the worst   ("who is slowest")
  - elo_best  / elo_worst    : every tight Elo-peer window (<=250 band); answer =
                               the best / the worst among those 4 peers             ("among equals, who?")

Each row carries: question text, 4 options (identity+value+Elo), the answer, the
top-3 reference games (civ + match id), the Elo band, and a `closeness` score
(0=blowout .. 1=photo-finish) so the weekly picker can favor exciting races.

Run (no replay parsing needed, just the DB):  python utils/replay_quiz/build_questions.py
"""
import json
import os
import sqlite3

from quiz import _fmt   # shared value formatter (seconds -> m:ss, else number)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "replay_quiz.db")
JSON_OUT = os.path.join(ROOT, "data", "question_bank.json")
MAX_ELO_SPREAD = 250

# leading adjective in a metric label -> (best phrasing, worst phrasing)
ADJ = {"Most": ("most", "fewest"), "Fastest": ("fastest", "slowest"),
       "Earliest": ("earliest", "latest"), "Biggest": ("biggest", "smallest"),
       "Fewest": ("fewest", None)}
IS_VERB = {"fastest", "slowest", "earliest", "latest"}


def _subject(label):
    first = label.split()[0]
    rest = label[len(first) + 1:]
    # drop trailing parentheticals like "(per game)" / "(military / game)"
    if "(" in rest:
        rest = rest[:rest.index("(")].strip()
    return first, rest


def phrase(label, ask):
    """Natural question fragment, ask in {'best','worst'}."""
    first, subj = _subject(label)
    best_adj, worst_adj = ADJ.get(first, (first.lower(), None))
    adj = best_adj if ask == "best" else worst_adj
    if adj is None:
        return None
    connector = "is" if adj in IS_VERB else "makes the"
    return f"{connector} **{adj} {subj}**"


def _closeness(values, answer_val):
    """1.0 = answer barely edges its nearest rival (exciting); ~0 = runaway."""
    others = [v for v in values if v != answer_val]
    if not others:
        return 0.0
    nearest = min(others, key=lambda v: abs(v - answer_val))
    scale = max(abs(answer_val), abs(nearest), 1)
    return round(1 - abs(answer_val - nearest) / scale, 3)


def _opts(rows, ratings):
    return [{"identity": i, "value": None, "raw": v, "elo": ratings.get(i)} for i, v, n in rows]


def _finalize(opts, unit):
    out = []
    for o in opts:
        out.append({"identity": o["identity"], "value": _fmt(o["raw"], unit), "elo": o["elo"]})
    return out


def build():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    ratings = {i: r for i, r in c.execute("SELECT identity,rating FROM players WHERE rating IS NOT NULL")}
    c.executescript("""
        DROP TABLE IF EXISTS question_bank;
        CREATE TABLE question_bank(
            question_id TEXT PRIMARY KEY, category TEXT, format TEXT, ask TEXT,
            metric_id TEXT, label TEXT, question TEXT, options_json TEXT, answer TEXT,
            refs_json TEXT, elo_lo INT, elo_hi INT, closeness REAL);
    """)
    metrics = c.execute("SELECT id,label,category,direction,unit FROM metrics").fetchall()
    bank = []
    for mid, label, cat, direction, unit in metrics:
        full = c.execute("SELECT identity,avg_value,n_games FROM leaderboards WHERE metric_id=? ORDER BY rank",
                         (mid,)).fetchall()
        if len(full) < 4:
            continue
        refs = [{"identity": x[0], "civ": x[1], "value": _fmt(x[2], unit), "match_id": x[3]}
                for x in c.execute("SELECT identity,civ,value,aoe2_match_id FROM metric_top_games WHERE metric_id=? ORDER BY rank", (mid,))]
        best = max if direction == "max" else min

        def add(qid, fmt, ask, opts_rows, answer_id, elo_lo=None, elo_hi=None):
            q = phrase(label, ask)
            if q is None:
                return
            vals = [r[1] for r in opts_rows]
            ans_val = next(r[1] for r in opts_rows if r[0] == answer_id)
            bank.append(dict(
                question_id=qid, category=cat, format=fmt, ask=ask, metric_id=mid, label=label,
                question=f"Who {q}?" if fmt == "top4" else
                         f"Among these four similarly-rated players (Elo ~{elo_lo}-{elo_hi}), who {q}?",
                options_json=json.dumps(_finalize(_opts(opts_rows, ratings), unit)),
                answer=answer_id, refs_json=json.dumps(refs), elo_lo=elo_lo, elo_hi=elo_hi,
                closeness=_closeness(vals, ans_val)))

        # top4 BEST: leaderboard rank 1..4, answer = rank1
        top = full[:4]
        if top[0][1] != top[-1][1] and not (unit == "count" and top[0][1] == 0):
            add(f"{mid}|top4|best", "top4", "best", top, top[0][0])
        # top4 WORST (timing only -> "slowest/latest"): the 4 worst, answer = the extreme worst
        if unit == "seconds":
            bot = full[-4:]
            worst_id = bot[-1][0]  # largest time = slowest
            if bot[0][1] != bot[-1][1]:
                add(f"{mid}|top4|worst", "top4", "worst", bot, worst_id)
        # ELO windows
        rated = sorted([(i, v, n) for i, v, n in full if i in ratings], key=lambda x: ratings[x[0]])
        for k in range(len(rated) - 3):
            w = rated[k:k + 4]
            lo, hi = ratings[w[0][0]], ratings[w[3][0]]
            if hi - lo > MAX_ELO_SPREAD:
                continue
            vals = [x[1] for x in w]
            if len(set(vals)) < 2:
                continue
            bv = best(vals)
            if vals.count(bv) == 1:                    # clear best -> elo_best
                aid = next(x[0] for x in w if x[1] == bv)
                add(f"{mid}|elo|{lo}-{hi}|best", "elo_peers", "best", w, aid, lo, hi)
            wv = (min if direction == "max" else max)(vals)
            if vals.count(wv) == 1 and phrase(label, "worst"):   # clear worst -> elo_worst
                aid = next(x[0] for x in w if x[1] == wv)
                add(f"{mid}|elo|{lo}-{hi}|worst", "elo_peers", "worst", w, aid, lo, hi)

    for r in bank:
        c.execute("INSERT OR REPLACE INTO question_bank VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (r["question_id"], r["category"], r["format"], r["ask"], r["metric_id"], r["label"],
                   r["question"], r["options_json"], r["answer"], r["refs_json"], r["elo_lo"], r["elo_hi"], r["closeness"]))
    conn.commit()
    json.dump(bank, open(JSON_OUT, "w", encoding="utf-8"), indent=1)

    # stats
    print(f"question_bank: {len(bank)} questions -> {DB} (+ {JSON_OUT})")
    from collections import Counter
    print("  by format:", dict(Counter(r["format"] for r in bank)))
    print("  by ask:   ", dict(Counter(r["ask"] for r in bank)))
    print("  by category:")
    for cat, n in Counter(r["category"] for r in bank).most_common():
        print(f"      {cat:18} {n}")
    conn.close()


if __name__ == "__main__":
    build()
