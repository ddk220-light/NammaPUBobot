"""Convert the approved replay-based player bank (data/question_bank.json, produced by
utils/replay_quiz/build_questions.py) into the unified quiz record schema used by the
game bank, written to data/quiz_bank_player.json (source="player").

Accuracy-critical: player `top4` records list options answer-first and each option
carries the metric value. Options are rendered with IDENTITY (+Elo) ONLY so the answer
is not given away; the metric values go into the reveal/explanation. Options are
shuffled (deterministically per question) so the answer slot moves. When
data/replay_quiz.db is present each answer is independently re-derived from the
leaderboards table and any mismatch is dropped (the "accuracy is paramount" gate).

    python utils/quiz_gen/convert_player_bank.py
"""
from __future__ import annotations

import json
import os
import random
import sqlite3

_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_IN = os.path.join(_REPO, "data", "question_bank.json")
_DB = os.path.join(_REPO, "data", "replay_quiz.db")
_OUT = os.path.join(_REPO, "data", "quiz_bank_player.json")
SEED = 20260618


def _opt_label(o):
    return f"{o['identity']} (Elo {o['elo']})" if o.get("elo") is not None else str(o["identity"])


def _difficulty(closeness):
    return "hard" if closeness >= 0.85 else "medium" if closeness >= 0.6 else "easy"


def _explain(order, answer, refs):
    field = " · ".join(f"{o['identity']} {o['value']}" for o in order)
    line = f"**{answer}** is the answer. Career averages — {field}."
    if refs:
        g = refs[0]
        # The reference is the all-time single-game record for this stat (any player, for
        # verification via the replay), NOT one of the four options — label it as such so
        # naming a non-option player in the reveal isn't confusing.
        line += (f" Single-game record for this stat: {g['identity']} {g['value']} "
                 f"({g['civ']}, match #{g['match_id']}).")
    return line


def convert_record(rec, rng):
    """Pure: one player bank row -> one unified record (id assigned by caller)."""
    order = json.loads(rec["options_json"])[:]
    rng.shuffle(order)                                   # move the answer off slot A (top4)
    refs = json.loads(rec["refs_json"]) if rec.get("refs_json") else []
    options = [_opt_label(o) for o in order]
    answer_idx = next(i for i, o in enumerate(order) if o["identity"] == rec["answer"])
    return {
        "id": None,
        "category": rec["category"],
        "question_type": rec["format"],
        "grouping": rec["ask"],
        "difficulty": _difficulty(rec["closeness"]),
        "prompt": rec["question"],
        "options": options,
        "correct_indices": [answer_idx],
        "correct_index": answer_idx,
        "multi": False,
        "explanation": _explain(order, rec["answer"], refs),
        "source": "player",
        "score": round(float(rec["closeness"]), 4),
        "meta": {
            "metric_id": rec["metric_id"], "format": rec["format"], "ask": rec["ask"],
            "closeness": rec["closeness"], "elo_lo": rec.get("elo_lo"), "elo_hi": rec.get("elo_hi"),
            "answer": rec["answer"],
            "values": {o["identity"]: o["value"] for o in order},
        },
    }


def _verify_against_db(q, con):
    """Independent re-derivation: the marked answer must hold the extreme metric value
    among the option identities (best, or worst when grouping=='worst'). Tie-robust
    (compares VALUES, not argmax identity) so legitimately-marked questions are not
    over-dropped. Returns True to keep; True (keep) when the metric/identities can't be
    fully re-derived from the DB."""
    if con is None:
        return True
    row = con.execute("SELECT direction FROM metrics WHERE id=?", (q["meta"]["metric_id"],)).fetchone()
    if not row:
        return True
    direction = row[0]
    idents = list(q["meta"]["values"].keys())
    lb = dict(con.execute(
        "SELECT identity, avg_value FROM leaderboards WHERE metric_id=?", (q["meta"]["metric_id"],)).fetchall())
    vals = {i: lb[i] for i in idents if i in lb}
    if len(vals) < len(idents):
        return True                                      # incomplete re-derivation -> don't over-drop
    pick = max if direction == "max" else min           # the "best" extreme
    if q["meta"]["ask"] == "worst":
        pick = min if direction == "max" else max
    extreme = pick(vals.values())
    return vals.get(q["meta"]["answer"]) == extreme


def build():
    rng = random.Random(SEED)
    with open(_IN, encoding="utf-8") as f:
        rows = json.load(f)
    con = sqlite3.connect(_DB) if os.path.exists(_DB) else None
    out, dropped = [], 0
    try:
        for rec in rows:
            q = convert_record(rec, rng)
            if not _verify_against_db(q, con):
                dropped += 1
                continue
            q["id"] = f"player_{len(out):05d}"
            out.append(q)
    finally:
        if con is not None:
            con.close()
    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    by_cat = {}
    for q in out:
        by_cat[q["category"]] = by_cat.get(q["category"], 0) + 1
    print(f"PLAYER BANK: {len(out)} questions -> {_OUT} (dropped by DB re-derivation: {dropped})")
    print(f"  by category: {by_cat}")


if __name__ == "__main__":
    build()
