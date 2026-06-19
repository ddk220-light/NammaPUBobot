#!/usr/bin/env python3
"""Randomized quiz-question generator over data/replay_quiz.db.

Two question formats:
  - "top4"     : options = the actual top-4 of a metric's leaderboard; answer = #1
                 ("Who is the best at X?")
  - "elo_peers": options = 4 players who are CLOSE in current Elo; answer = whoever
                 ranks best on the metric among those 4 ("Among these similar-rated
                 players, who has the most/fastest X?") — more interesting because
                 the four are peers and the metric outcome isn't obvious.

Each question carries the reveal: every option's value + the top-3 reference games
(player, civ, match id) so it can be checked against the actual replay.

CLI:  python utils/replay_quiz/quiz.py [n]
API:  generate_question(fmt=..., metric_id=..., seed=...) -> dict
"""
import os
import random
import sqlite3

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                  "data", "replay_quiz.db")
MAX_ELO_SPREAD = 250   # an "elo_peers" group's 4 players must be within this rating band


def _fmt(v, unit):
    if v is None:
        return "-"
    return f"{int(v)//60}:{int(v)%60:02d}" if unit == "seconds" else f"{v:g}"


def _phrase(label):
    """Natural-language fragment from a metric label (which already encodes direction)."""
    core = label.replace(" (per game)", "")
    lc = core[0].lower() + core[1:]
    if core.lower().startswith(("fastest", "earliest", "slowest", "latest")):
        return f"is **{lc}**"
    return f"makes the **{lc}**"


def _rows(c, mid):
    return c.execute("SELECT identity,avg_value,n_games FROM leaderboards WHERE metric_id=? ORDER BY rank",
                     (mid,)).fetchall()


def _ratings(c):
    return {i: r for i, r in c.execute("SELECT identity,rating FROM players WHERE rating IS NOT NULL").fetchall()}


def _refs(c, mid, unit):
    g = c.execute("SELECT identity,civ,value,aoe2_match_id FROM metric_top_games WHERE metric_id=? ORDER BY rank", (mid,)).fetchall()
    return [{"identity": x[0], "civ": x[1], "value": _fmt(x[2], unit), "match_id": x[3]} for x in g]


def _quality_metrics(c, fmt, ratings):
    out = []
    for mid, label, direction, unit in c.execute("SELECT id,label,direction,unit FROM metrics").fetchall():
        rows = _rows(c, mid)
        if fmt == "top4":
            if len(rows) >= 4 and rows[0][1] != rows[3][1] and not (unit == "count" and rows[0][1] == 0):
                out.append((mid, label, direction, unit, rows))
        else:  # elo_peers: need >=4 rated players on this metric, with value spread
            rated = [(i, v, n, ratings[i]) for i, v, n in rows if i in ratings]
            if len(rated) >= 4 and len({v for _, v, _, _ in rated}) >= 3:
                out.append((mid, label, direction, unit, rated))
    return out


def generate_question(fmt=None, metric_id=None, seed=None):
    rng = random.Random(seed)
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    ratings = _ratings(c)
    fmt = fmt or rng.choice(["top4", "elo_peers"])
    pool = _quality_metrics(c, fmt, ratings)
    if metric_id:
        pool = [p for p in pool if p[0] == metric_id] or pool
    if not pool:
        conn.close()
        return None
    mid, label, direction, unit, data = rng.choice(pool)
    best = max if direction == "max" else min

    if fmt == "top4":
        rows = data
        opts = rows[:4]
        answer = opts[0]
        shuffled = opts[:]
        rng.shuffle(shuffled)
        question = f"Who {_phrase(label)} (career average)?"
        options = [{"identity": o[0], "elo": ratings.get(o[0]), "value": _fmt(o[1], unit), "n_games": o[2]} for o in shuffled]
        answer_id = answer[0]
    else:
        rated = sorted(data, key=lambda x: x[3])           # by Elo (ascending)
        # windows of 4 consecutive-by-Elo players, kept only if the band is tight,
        # then prefer one with a single clear best on the metric.
        windows = [rated[i:i + 4] for i in range(len(rated) - 3)]
        tight = [w for w in windows if w[-1][3] - w[0][3] <= MAX_ELO_SPREAD]
        rng.shuffle(tight)
        win = next((w for w in tight if [x[1] for x in w].count(best([x[1] for x in w])) == 1), None)
        if win is None:
            win = tight[0] if tight else min(windows, key=lambda w: w[-1][3] - w[0][3])
        answer = best(win, key=lambda x: x[1])
        lo, hi = min(x[3] for x in win), max(x[3] for x in win)
        shuffled = win[:]
        rng.shuffle(shuffled)
        question = (f"Among these four similarly-rated players (Elo ~{lo}-{hi}), "
                    f"who {_phrase(label)}?")
        options = [{"identity": x[0], "elo": x[3], "value": _fmt(x[1], unit), "n_games": x[2]} for x in shuffled]
        answer_id = answer[0]

    refs = _refs(c, mid, unit)
    conn.close()
    return {"format": fmt, "metric_id": mid, "label": label, "question": question,
            "options": options, "answer": answer_id, "reference_games": refs}


def _print(q, n):
    print(f"\nQ{n} [{q['format']}]. {q['question']}")
    for j, o in enumerate(q["options"]):
        elo = f", Elo {o['elo']}" if o.get("elo") else ""
        mark = "   <-- ANSWER" if o["identity"] == q["answer"] else ""
        print(f"     {chr(65+j)}. {o['identity']}{elo}{mark}")
    print("     reveal:  " + " | ".join(f"{o['identity']}={o['value']}" for o in q["options"]))
    refs = "; ".join(f"{g['identity']} {g['value']} ({g['civ']}, #{g['match_id']})" for g in q["reference_games"])
    print(f"     top games: {refs}")


def main():
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    # a curated, interesting spread (specific units + techs + speed), mixing both formats
    plan = [
        ("top4", "archer_line_total"), ("elo_peers", "feudal_fast"),
        ("top4", "camel_line_total"), ("elo_peers", "tech_wheelbarrow"),
        ("top4", "scout_total"), ("elo_peers", "vil_total"),
        ("top4", "mil_pre_feudal"), ("elo_peers", "knight_line_total"),
        ("top4", "tech_loom"), ("elo_peers", "tech_bloodlines"),
        ("top4", "unique_other_total"), ("elo_peers", "castle_fast"),
    ]
    shown = 0
    for i, (fmt, mid) in enumerate(plan):
        if shown >= n:
            break
        q = generate_question(fmt=fmt, metric_id=mid, seed=200 + i)
        if q:
            shown += 1
            _print(q, shown)
    if shown == 0:
        print("No quality metrics — build the DB first.")


if __name__ == "__main__":
    main()
