# Unified Player + Game Quiz Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge the approved replay-based "player quiz" generator into `quiz-improvements`, convert its bank into the existing unified question schema, and build one master scheduler that alternates player-based and game-based questions (player first) each week — producing a single `data/quiz_schedule.json` the bot already consumes unchanged.

**Architecture:** Two independent offline banks in ONE shared record schema — `data/quiz_bank.json` (game, source=`game`) and `data/quiz_bank_player.json` (player, source=`player`, converted from the approved `data/question_bank.json`). A rewritten `build_schedule.py` reads both and interleaves them (odd schedule-day → player, even → game). The bot stays schema-driven; the only runtime change is carrying/showing a `source` tag.

**Tech Stack:** Python 3.11, stdlib `json`/`sqlite3`/`random`, pytest, ruff (4-space indent for `utils/`, tab indent for `bot/`).

---

## Shared record schema (both banks emit this; the bot already consumes it)

```
id              str   unique, e.g. "player_00042" or "combat_00010"
category        str   game: combat|techgaps|stats|effects ; player: Villagers|Age speed|Buildings|Military|Military by type|Tech timing
question_type   str   game: survive_hp|lack_both|... ; player: top4|elo_peers
grouping        str   game grouping ; player: best|worst
difficulty      str   easy|medium|hard
prompt          str   may contain Discord markdown (**bold**)
options         [str] exactly 4, all distinct
correct_indices [int] 1+ indices (player questions: exactly 1)
correct_index   int|None
multi           bool  == (len(correct_indices) > 1)  -> player questions are always False
explanation     str   the reveal text (player: includes the per-option metric VALUES + a reference game)
source          str   "game" | "player"   (NEW — game entries get it injected by the scheduler)
score           float 0..1 (player: closeness)
meta            obj   traceability (player: metric_id, format, ask, closeness, elo band, values, answer)
(+ seq, week, day, weekday  added by build_schedule.stamp)
```

**Leak rule (accuracy-critical):** player option strings contain ONLY `identity` (+ `(Elo N)` when present) — NEVER the metric value. For `top4`, the bank lists options in answer-first order, so options MUST be shuffled and `correct_index` recomputed.

---

## Task 1: Merge the approved replay-quizzes branch

**Files:** (merge brings in) `utils/replay_quiz/*`, `data/question_bank.json`, `data/replay_quiz.db`, `data/profile_resolved.csv`, `data/replay_manifest.csv`, `docs/replay-quiz-categories.md`, `.gitignore` (append).

- [ ] **Step 1: Confirm clean tree**

Run: `git status --short` — expect empty.

- [ ] **Step 2: Merge (verified conflict-free in analysis)**

```bash
git merge --no-ff origin/feat/replay-quizzes -m "merge(quiz): bring in approved replay-based player-quiz generator"
```

- [ ] **Step 3: Verify the files landed and the existing suite still passes**

Run: `python -c "import os;print(all(os.path.exists(p) for p in ['utils/replay_quiz/build_questions.py','data/question_bank.json','data/replay_quiz.db']))"` — expect `True`.
Run: `pytest tests/ -q` — expect all green (the merge is purely additive; no game-side test should change).
Run: `ruff check utils/quiz_gen bot` — expect clean (do NOT lint `utils/replay_quiz` — it is approved as-is and uses its own style).

---

## Task 2: Player-bank converter → unified schema

**Files:**
- Create: `utils/quiz_gen/convert_player_bank.py`
- Test: `tests/test_quiz_player_convert.py`

The converter reads `data/question_bank.json` (player), converts each record with a pure `convert_record(rec, rng)`, assigns stable ids, optionally re-verifies answers against `data/replay_quiz.db`, and writes `data/quiz_bank_player.json`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_quiz_player_convert.py
import importlib.util, os, random, pathlib
spec = importlib.util.spec_from_file_location(
    "convert_player_bank",
    pathlib.Path(__file__).resolve().parents[1] / "utils" / "quiz_gen" / "convert_player_bank.py")
cpb = importlib.util.module_from_spec(spec); spec.loader.exec_module(cpb)

_TOP4 = dict(
    question_id="vil_total|top4|best", category="Villagers", format="top4", ask="best",
    metric_id="vil_total", label="Most villagers / game",
    question="Who makes the **most villagers / game**?",
    options_json='[{"identity": "alice", "value": "211.19", "elo": 2759},'
                 ' {"identity": "bob", "value": "188.85", "elo": null},'
                 ' {"identity": "cara", "value": "182.61", "elo": 1566},'
                 ' {"identity": "dan", "value": "178.87", "elo": 1007}]',
    answer="alice",
    refs_json='[{"identity": "cara", "civ": "Celts", "value": "791", "match_id": 442000290}]',
    elo_lo=None, elo_hi=None, closeness=0.894)

def test_answer_index_is_correct_after_shuffle():
    q = cpb.convert_record(_TOP4, random.Random(1))
    assert q["correct_index"] == q["correct_indices"][0]
    assert q["options"][q["correct_index"]].startswith("alice")
    assert q["multi"] is False and q["source"] == "player"

def test_options_never_leak_the_metric_value():
    q = cpb.convert_record(_TOP4, random.Random(2))
    for opt in q["options"]:
        for val in ("211.19", "188.85", "182.61", "178.87"):
            assert val not in opt          # values live in the reveal, not the options

def test_explanation_shows_values_and_a_reference_game():
    q = cpb.convert_record(_TOP4, random.Random(3))
    assert "211.19" in q["explanation"] and "alice" in q["explanation"]
    assert "442000290" in q["explanation"]

def test_schema_is_structurally_valid():
    q = cpb.convert_record(_TOP4, random.Random(4))
    assert len(q["options"]) == 4 == len(set(q["options"]))
    assert 0 <= q["correct_index"] < 4
    assert q["difficulty"] in ("easy", "medium", "hard")
    assert q["category"] == "Villagers" and q["question_type"] == "top4"
```

- [ ] **Step 2: Run it to verify failure**

Run: `pytest tests/test_quiz_player_convert.py -q`
Expected: FAIL (module/`convert_record` not found).

- [ ] **Step 3: Implement the converter**

```python
# utils/quiz_gen/convert_player_bank.py
"""Convert the approved replay-based player bank (data/question_bank.json, produced by
utils/replay_quiz/build_questions.py) into the unified quiz record schema used by the
game bank, written to data/quiz_bank_player.json (source="player").

Accuracy-critical: player `top4` records list options answer-first and each option
carries the metric value. Options are rendered with IDENTITY (+Elo) ONLY so the answer
is not given away; the metric values go into the reveal/explanation. Options are
shuffled (deterministically per question) so the answer slot moves. When data/replay_quiz.db
is present each answer is independently re-derived from the leaderboards table and any
mismatch is dropped.

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
    line = f"**{answer}** is the answer. Values — {field}."
    if refs:
        g = refs[0]
        line += f" Top game: {g['identity']} {g['value']} ({g['civ']}, #{g['match_id']})."
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


def _verify_against_db(q):
    """Independent re-derivation: the marked option's identity must equal the metric's
    rank-1 leaderboard identity (top4) / be the best among the option set (elo_peers).
    Returns True to keep. No-op (keep) if the DB is missing."""
    if not os.path.exists(_DB):
        return True
    con = sqlite3.connect(_DB)
    try:
        row = con.execute("SELECT direction FROM metrics WHERE id=?", (q["meta"]["metric_id"],)).fetchone()
        if not row:
            return True
        direction = row[0]
        idents = list(q["meta"]["values"].keys())
        lb = dict(con.execute(
            "SELECT identity, avg_value FROM leaderboards WHERE metric_id=?", (q["meta"]["metric_id"],)).fetchall())
        vals = {i: lb[i] for i in idents if i in lb}
        if len(vals) < len(idents):
            return True                                  # can't fully re-derive -> don't over-drop
        pick = (max if direction == "max" else min)
        if q["grouping"] == "worst":
            pick = (min if direction == "max" else max)
        true_best = pick(vals, key=vals.get)
        return true_best == q["meta"]["answer"]
    finally:
        con.close()


def build():
    rng = random.Random(SEED)
    with open(_IN, encoding="utf-8") as f:
        rows = json.load(f)
    out, dropped = [], 0
    for rec in rows:
        q = convert_record(rec, rng)
        if not _verify_against_db(q):
            dropped += 1
            continue
        q["id"] = f"player_{len(out):05d}"
        out.append(q)
    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    by_cat = {}
    for q in out:
        by_cat[q["category"]] = by_cat.get(q["category"], 0) + 1
    print(f"PLAYER BANK: {len(out)} questions -> {_OUT} (dropped by DB re-derivation: {dropped})")
    print(f"  by category: {by_cat}")


if __name__ == "__main__":
    build()
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_quiz_player_convert.py -q` — expect PASS.
Run: `ruff check utils/quiz_gen/convert_player_bank.py tests/test_quiz_player_convert.py` — expect clean.

- [ ] **Step 5: Commit**

```bash
git add utils/quiz_gen/convert_player_bank.py tests/test_quiz_player_convert.py
git commit -m "feat(quiz): convert player replay bank into the unified question schema"
```

---

## Task 3: Player stream sampler (freshness + variety)

**Files:**
- Create: `utils/quiz_gen/player_sample.py`
- Test: `tests/test_quiz_player_sample.py`

A stateful taker that yields fresh player questions: prefer exciting races (closeness in `[0.5, 0.985]`), and within a single draw never repeat a `metric_id` or an answer (`meta.answer`). `take(theme=None)` filters to a theme's categories when given, else any.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_quiz_player_sample.py
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location(
    "player_sample",
    pathlib.Path(__file__).resolve().parents[1] / "utils" / "quiz_gen" / "player_sample.py")
ps = importlib.util.module_from_spec(spec); spec.loader.exec_module(ps)

def _q(i, metric, answer, closeness=0.8, cat="Villagers"):
    return dict(id=f"player_{i:05d}", category=cat, question_type="top4", grouping="best",
                difficulty="medium", prompt="Who?", options=["a", "b", "c", "d"],
                correct_indices=[0], correct_index=0, multi=False, explanation="x",
                source="player", score=closeness,
                meta=dict(metric_id=metric, answer=answer, closeness=closeness))

def test_take_skips_repeat_metric_and_answer():
    bank = [_q(0, "m1", "alice"), _q(1, "m1", "bob"), _q(2, "m2", "alice"), _q(3, "m3", "carl")]
    take, _ = ps.make_player_taker(bank)
    a = take(); b = take()
    assert a["meta"]["metric_id"] != b["meta"]["metric_id"]      # no metric repeat
    assert a["meta"]["answer"] != b["meta"]["answer"]            # no answer repeat

def test_take_returns_none_when_exhausted():
    take, _ = ps.make_player_taker([_q(0, "m1", "alice")])
    assert take() is not None
    assert take() is None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_quiz_player_sample.py -q` — Expected: FAIL (no module).

- [ ] **Step 3: Implement**

```python
# utils/quiz_gen/player_sample.py
"""Stateful picker for player-quiz questions used by the unified scheduler. Favors
exciting races (closeness band) and never repeats a metric or an answering player
within one draw. Mirrors sample_weeks' taker shape so build_schedule can treat the
two sources symmetrically."""
from __future__ import annotations

# player categories grouped into themes the scheduler rotates through
THEMES = {
    "Economy": ("Villagers",),
    "Age speed": ("Age speed",),
    "Buildings": ("Buildings",),
    "Army": ("Military", "Military by type"),
    "Tech": ("Tech timing",),
}
CLOSE_LO, CLOSE_HI = 0.5, 0.985


def make_player_taker(bank, blocklist=()):
    """Return (take, relaxed_count). take(theme=None) -> a fresh player question or None.
    relaxed_count() reports how many picks needed the relaxed (band-ignoring) fallback."""
    block = set(blocklist)
    pool = [q for q in bank if q["id"] not in block]
    pool.sort(key=lambda q: -q["meta"]["closeness"])         # most exciting first
    used_ids, used_metrics, used_answers = set(), set(), set()
    relaxed = [0]

    def take(theme=None):
        cats = set(sum((THEMES.get(theme, ()),), ())) if theme else None
        for strict in (True, False):
            for q in pool:
                if q["id"] in used_ids:
                    continue
                if cats is not None and q["category"] not in cats:
                    continue
                if q["meta"]["metric_id"] in used_metrics or q["meta"]["answer"] in used_answers:
                    continue
                if strict and not (CLOSE_LO <= q["meta"]["closeness"] <= CLOSE_HI):
                    continue
                used_ids.add(q["id"]); used_metrics.add(q["meta"]["metric_id"])
                used_answers.add(q["meta"]["answer"])
                if not strict:
                    relaxed[0] += 1
                return q
        return None

    return take, (lambda: relaxed[0])
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_quiz_player_sample.py -q` — expect PASS.
Run: `ruff check utils/quiz_gen/player_sample.py tests/test_quiz_player_sample.py` — clean.

- [ ] **Step 5: Commit**

```bash
git add utils/quiz_gen/player_sample.py tests/test_quiz_player_sample.py
git commit -m "feat(quiz): add player-question stream sampler (freshness + variety)"
```

---

## Task 4: Expose a reusable game taker from sample_weeks

**Files:**
- Modify: `utils/quiz_gen/sample_weeks.py`
- Test: `tests/test_quiz_sample_weeks.py` (add a case; keep existing behavior)

Extract the inner per-slot picker so the unified scheduler can take one game question at a time. Keep `draw()` working exactly as before (it must still pass any existing tests and reproduce its output).

- [ ] **Step 1: Add `make_game_taker` and refactor `draw` to use it**

Add to `sample_weeks.py` (above `draw`):

```python
def make_game_taker(bank, blocklist=()):
    """Return (take, relaxed_count). take(cat, prefer_fresh_dim=None) -> a fresh game
    question of that category or None. Same hard no-repeat facet logic draw() uses."""
    block = set(blocklist)
    pool = {}
    for q in bank:
        if q["id"] in block:
            continue
        pool.setdefault(q["category"], []).append(q)
    for c in pool:
        pool[c].sort(key=lambda q: q.get("taste_score", q["score"]), reverse=True)
    counts, relaxed = {}, [0]

    def take(cat, prefer_fresh_dim=None):
        for relaxed_pass in (False, True):
            for q in pool.get(cat, []):
                fac = _facets(q)
                check = fac[:1] if relaxed_pass else fac
                if any(counts.get(k, 0) >= cap for k, cap in check):
                    continue
                if not relaxed_pass and prefer_fresh_dim and q.get("meta", {}).get("effect") == prefer_fresh_dim:
                    continue
                for k, _ in fac:
                    counts[k] = counts.get(k, 0) + 1
                if relaxed_pass:
                    relaxed[0] += 1
                return q
        return None

    return take, (lambda: relaxed[0])
```

Then rewrite `draw` to delegate (behavior identical):

```python
def draw(bank, weeks, blocklist=()):
    take, relaxed = make_game_taker(bank, blocklist)
    out, last_dim = [], None
    for _ in range(weeks):
        week = []
        for slot in ROTATION:
            q = take(slot, prefer_fresh_dim=last_dim if slot == "effects" else None)
            if slot == "effects" and q:
                last_dim = q.get("meta", {}).get("effect")
            week.append(q)
        out.append(week)
    return out, relaxed()
```

- [ ] **Step 2: Run existing + new tests**

Run: `pytest tests/ -q -k quiz` — expect all green (draw output unchanged).
If no sample_weeks test exists, add `tests/test_quiz_sample_weeks.py` asserting `draw(bank, 1)` returns one 7-slot week and `make_game_taker` returns distinct questions.

- [ ] **Step 3: Run to confirm `sample_weeks.py` still runs standalone**

Run: `python utils/quiz_gen/sample_weeks.py 2` — expect it prints 2 weeks (smoke test).

- [ ] **Step 4: Commit**

```bash
git add utils/quiz_gen/sample_weeks.py tests/test_quiz_sample_weeks.py
git commit -m "refactor(quiz): expose a reusable per-slot game taker from sample_weeks"
```

---

## Task 5: Unified alternating scheduler

**Files:**
- Modify: `utils/quiz_gen/build_schedule.py`
- Test: `tests/test_quiz_build_schedule.py`

Rewrite `build_schedule.py` to read BOTH banks and alternate per day: day 1/3/5/7 = player, day 2/4/6 = game. Game entries get `source="game"` injected. `stamp` also records `source`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_quiz_build_schedule.py
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location(
    "build_schedule",
    pathlib.Path(__file__).resolve().parents[1] / "utils" / "quiz_gen" / "build_schedule.py")
bs = importlib.util.module_from_spec(spec); spec.loader.exec_module(bs)

def _game(i):
    return dict(id=f"combat_{i:05d}", category="combat", question_type="survive_hp",
                grouping="matchup", difficulty="hard", prompt="g?", options=["a","b","c","d"],
                correct_indices=[0], correct_index=0, multi=False, explanation="x",
                score=0.9, meta={"opp": f"o{i}", "cluster": "ranged_uu"})

def _player(i):
    return dict(id=f"player_{i:05d}", category="Villagers", question_type="top4",
                grouping="best", difficulty="medium", prompt="p?", options=["w","x","y","z"],
                correct_indices=[1], correct_index=1, multi=False, explanation="x",
                source="player", score=0.8, meta={"metric_id": f"m{i}", "answer": f"p{i}", "closeness": 0.8})

def test_week_alternates_player_first():
    game = [_game(i) for i in range(40)]
    player = [_player(i) for i in range(40)]
    sched = bs.build(game, player, weeks=2)
    for e in sched:
        assert e["source"] == ("player" if e["day"] % 2 == 1 else "game")
    # seq is monotonic and 1-based
    assert [e["seq"] for e in sched] == list(range(1, len(sched) + 1))
    # every entry validates structurally
    for e in sched:
        assert len(e["options"]) == 4 and 0 <= e["correct_index"] < 4 and e["source"] in ("player", "game")
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_quiz_build_schedule.py -q` — Expected: FAIL (no `build(...)`).

- [ ] **Step 3: Implement**

```python
# utils/quiz_gen/build_schedule.py
"""Offline master scheduler: interleave the GAME bank (data/quiz_bank.json) and the
PLAYER bank (data/quiz_bank_player.json) into one ordered, numbered
data/quiz_schedule.json the bot posts one entry per day.

Alternation (per week, resets each week): day 1/3/5/7 -> player, day 2/4/6 -> game,
so the first question of every week is player-based and the sources alternate.

    python utils/quiz_gen/build_schedule.py [weeks]      # default 26
"""
import json
import os
import sys

try:
    import player_sample
    import sample_weeks
except ModuleNotFoundError:
    from utils.quiz_gen import player_sample, sample_weeks

_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_GAME = os.path.join(_REPO, "data", "quiz_bank.json")
_PLAYER = os.path.join(_REPO, "data", "quiz_bank_player.json")
_BLOCK = os.path.join(_REPO, "data", "quiz_blocklist.json")
_OUT = os.path.join(_REPO, "data", "quiz_schedule.json")
_WEEKDAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# 3 game slots (even days) and 4 player themes (odd days), rotated by week.
GAME_SLOTS = ["combat", "techgaps", "stats", "combat", "techgaps", "effects"]
PLAYER_THEMES = ["Economy", "Age speed", "Army", "Tech", "Buildings", "Army", "Tech"]


def build(game_bank, player_bank, weeks=26, blocklist=()):
    """Return an ordered, stamped schedule alternating player/game. Pure: takes the two
    banks in memory so it is unit-testable."""
    g_take, _ = sample_weeks.make_game_taker(game_bank, blocklist)
    p_take, _ = player_sample.make_player_taker(player_bank, blocklist)
    out, seq, gi, pi, last_dim = [], 0, 0, 0, None
    for wi in range(1, weeks + 1):
        for day in range(1, 8):
            if day % 2 == 1:                                 # player day
                theme = PLAYER_THEMES[pi % len(PLAYER_THEMES)]; pi += 1
                q = p_take(theme) or p_take()                # fall back to any theme
                src = "player"
            else:                                            # game day
                cat = GAME_SLOTS[gi % len(GAME_SLOTS)]; gi += 1
                q = g_take(cat, prefer_fresh_dim=last_dim if cat == "effects" else None)
                if cat == "effects" and q:
                    last_dim = q.get("meta", {}).get("effect")
                src = "game"
            if not q:
                continue
            seq += 1
            out.append({**q, "source": q.get("source", src), "seq": seq,
                        "week": wi, "day": day, "weekday": _WEEKDAY[day - 1]})
    return out


def main():
    weeks = int(sys.argv[1]) if len(sys.argv) > 1 else 26
    with open(_GAME, encoding="utf-8") as f:
        game_bank = json.load(f)
    with open(_PLAYER, encoding="utf-8") as f:
        player_bank = json.load(f)
    block = set()
    if os.path.exists(_BLOCK):
        with open(_BLOCK, encoding="utf-8") as f:
            block = set(json.load(f))
    schedule = build(game_bank, player_bank, weeks, block)
    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump(schedule, f, indent=2, ensure_ascii=False)
    n_player = sum(1 for e in schedule if e["source"] == "player")
    print(f"Wrote {len(schedule)} questions ({weeks} weeks) to {_OUT}")
    print(f"  player: {n_player} | game: {len(schedule) - n_player} | blocklisted: {len(block)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests + standalone**

Run: `pytest tests/test_quiz_build_schedule.py -q` — expect PASS.
Run: `ruff check utils/quiz_gen/build_schedule.py tests/test_quiz_build_schedule.py` — clean.

- [ ] **Step 5: Commit**

```bash
git add utils/quiz_gen/build_schedule.py tests/test_quiz_build_schedule.py
git commit -m "feat(quiz): master scheduler alternates player/game questions (player first)"
```

---

## Task 6: Bot carries & displays the `source` tag

**Files:**
- Modify: `bot/quiz/__init__.py` (add `source` column), `bot/quiz/store.py` (`create_post`), `bot/quiz/view.py` (`card_lines`), `bot/quiz/embeds.py` (`card_embed`), `bot/quiz/jobs.py` (`_post_question`)
- Test: `tests/test_quiz_view.py` (add cases)

Additive, back-compatible: `ensure_table` auto-adds the column; a missing/None source renders no tag.

- [ ] **Step 1: Add the column** in `bot/quiz/__init__.py` `qc_quiz_posts` columns (tab indent):

```python
		dict(cname="source", ctype=db.types.str, notnull=False),  # "game" | "player"
```

- [ ] **Step 2: Store it** in `bot/quiz/store.py` `create_post` insert dict:

```python
		seq=q["seq"], week=q["week"], day=q["day"], source=q.get("source")))
```

(adjust the existing trailing line so `source` is included in the same `db.insert` dict).

- [ ] **Step 3: Render the tag.** In `bot/quiz/view.py` `card_lines`, accept `source=None` and prepend a mode line. Write the failing test first:

```python
# in tests/test_quiz_view.py
def test_card_lines_shows_source_tag():
    from bot.quiz import view
    g = "\n".join(view.card_lines("combat", "hard", 1, 1, 1, 24, source="game"))
    p = "\n".join(view.card_lines("Villagers", "medium", 2, 1, 3, 24, source="player"))
    assert "Game" in g and "Player" in p
```

Then implement in `view.py` (tab indent), matching the existing signature/format:

```python
def card_lines(category, difficulty, seq, week, day, closes_in_h, source=None):
	tag = {"game": "\U0001F3AE Game knowledge", "player": "\U0001F464 Player trivia"}.get(source)
	lines = []
	if tag:
		lines.append(f"**{tag}**")
	# ... existing lines unchanged ...
	return lines
```

- [ ] **Step 4: Thread it through embeds + jobs.** In `bot/quiz/embeds.py` `card_embed`, add `source=None` param and pass to `_v.card_lines(...)`. In `bot/quiz/jobs.py` `_post_question`, pass `q.get("source")`:

```python
			embed=embeds.card_embed(q["category"], q["difficulty"], q["seq"], q["week"],
									q["day"], open_window / 3600, source=q.get("source")),
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/ -q` — expect all green (existing `card_lines` callers still work via the default).
Run: `ruff check bot` — clean.

- [ ] **Step 6: Commit**

```bash
git add bot/quiz tests/test_quiz_view.py
git commit -m "feat(quiz): carry and show a Game/Player source tag on the quiz card"
```

---

## Task 7: Regenerate the banks + schedule and commit the data

**Files:** Create/refresh `data/quiz_bank_player.json`, `data/quiz_schedule.json`.

- [ ] **Step 1: Build the player bank**

Run: `cd utils/quiz_gen && python convert_player_bank.py && cd ../..`
Expected: prints `PLAYER BANK: N questions -> .../quiz_bank_player.json` with a category breakdown; few/zero dropped.

- [ ] **Step 2: Build the unified schedule**

Run: `cd utils/quiz_gen && python build_schedule.py 26 && cd ../..`
Expected: prints player/game counts; player ≈ game × (4/3).

- [ ] **Step 3: Verify alternation + freshness on the real data**

Run:
```bash
python -c "import json;s=json.load(open('data/quiz_schedule.json',encoding='utf-8'));bad=[e['seq'] for e in s if e['source']!=('player' if e['day']%2==1 else 'game')];print('weeks',max(e['week'] for e in s),'total',len(s),'alt_violations',bad)"
```
Expected: `alt_violations []`.

- [ ] **Step 4: Commit the data**

```bash
git add data/quiz_bank_player.json data/quiz_schedule.json
git commit -m "data(quiz): regenerate player bank + unified alternating schedule"
```

---

## Task 8: Adversarial accuracy audit of converted player questions

Run as a Workflow fan-out (sample ~40 converted player questions; for each, independently re-derive from `data/replay_quiz.db` and confirm the marked answer + check no value leakage in options). Record confirmed/refuted. This is the "accuracy is paramount" gate — any confirmed-wrong question gets its id added to `data/quiz_blocklist.json` and the schedule is rebuilt (Task 7 steps 2–4).

- [ ] **Step 1:** Sample player entries from `data/quiz_schedule.json`.
- [ ] **Step 2:** For each, query `leaderboards`/`metrics` in `replay_quiz.db`, recompute best/worst over the option identities, compare to `meta.answer`.
- [ ] **Step 3:** Assert no option string contains its metric value.
- [ ] **Step 4:** Blocklist any failures; rebuild; re-audit until clean.

---

## Task 9: Docs + memory

**Files:** Modify `CLAUDE.md` (quiz/data sources note), `docs/superpowers/plans/...` (this file), update auto-memory.

- [ ] **Step 1:** Add a short "Quiz: two sources" subsection to `CLAUDE.md` describing the game vs player banks and the alternating `build_schedule.py`.
- [ ] **Step 2:** Update `~/.claude/projects/D--AI-NammaPUBobot/memory/quiz-redesign-data-sources.md` with the player source + unified pipeline; refresh `MEMORY.md` hook.
- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/superpowers/plans/2026-06-18-unified-quiz-player-game.md
git commit -m "docs(quiz): document the unified two-source quiz pipeline"
```

---

## Final review

After all tasks: dispatch a holistic code reviewer over the full diff (`git diff main...HEAD`), focusing on the converter accuracy guard, the alternation invariant, and bot back-compat. Then run the whole suite + ruff, and use superpowers:finishing-a-development-branch.
