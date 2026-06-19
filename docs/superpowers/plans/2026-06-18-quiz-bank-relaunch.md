# Quiz Bank Relaunch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch the live Discord quiz to serve every question from the new accuracy-verified bank (`data/quiz_bank.json`) via a pre-baked, numbered, rotated schedule — with multi-select answering, per-week leaderboards, and full referenceability (question #, week #, day #) for the testing phase.

**Architecture:** The bank is the *offline source of truth*. An offline tool (`build_schedule.py`) draws a long rotated, no-repeat sequence from the bank (minus a blocklist) into a committed, human-reviewable `data/quiz_schedule.json` where every entry is stamped with `seq` (global #), `week`, and `day`. The bot consumes only the schedule at runtime: each day it reveals the previous answer, posts a per-week leaderboard when a week completes, then posts the next un-posted `seq`. Single-answer questions use A/B/C/D buttons; "which civ(s) lack…" questions use a select-all dropdown. All state lives in MySQL (`qc_quiz_*`); pure logic is unit-tested, the nextcord/DB glue is verified manually (matching the existing quiz package — `jobs`/`interactions`/`store` have no unit tests because the conftest stubs the DB and nextcord).

**Tech Stack:** Python 3.11, nextcord, aiomysql (`core.database.db`), pytest. Offline tools use the stdlib + the existing `utils/quiz_gen` modules.

---

## Why a pre-baked schedule (not a runtime picker)

The approved quality comes from the offline rotation + hard no-repeat facets in `sample_weeks.py`. Re-deriving those facets at runtime would mean parsing past posts and re-implementing the selector against the DB — fragile and hard to review. Baking an ordered `quiz_schedule.json` instead gives: (1) deterministic, reviewable output (like the bank PR), (2) stable `seq`/`week`/`day` numbers for referencing questions during testing, (3) a trivial runtime ("post the next seq"), and (4) a clean quality loop — reject a bad question by id, regenerate, re-commit.

The no-repeat facets bind at roughly 8–12 weeks before they must relax (effects has only ~8 distinct questions; combat has ~32 cluster×opponent pairings). So the schedule is generated at a requested length and the generator **logs** when it falls back to the relaxed (option-set-unique-only) pass, so we know where freshness degrades.

---

## File Structure

**New files**
- `utils/quiz_gen/build_schedule.py` — offline: `quiz_bank.json` − blocklist → rotated, stamped `quiz_schedule.json`. Wraps the existing `sample_weeks` selector.
- `data/quiz_schedule.json` — committed schedule artifact (ordered list; each entry stamped `seq`/`week`/`day`).
- `data/quiz_blocklist.json` — array of question ids to exclude (the testing-phase reject list).
- `bot/quiz/schedule.py` — runtime: load + validate the schedule, `entry_for_seq`, `week_is_complete`.
- `tests/test_quiz_schedule.py` — pure tests for the schedule loader/selector.

**Modified files**
- `bot/quiz/__init__.py` — add columns `seq`, `week`, `day`, `correct_indices` (posts); `choice_indices` (answers). `ensure_table` auto-`ALTER`s them.
- `bot/quiz/store.py` — persist/read new columns; `next_seq`; `week_answers_by_week`.
- `bot/quiz/scoring.py` — `grade_multi`, multi route in `parse_custom_id`.
- `bot/quiz/view.py` — card/question/result lines carry `seq`/`week`/`day` and render multiple correct letters; select option labels.
- `bot/quiz/embeds.py` — `answer_view` branches single-buttons vs select-menu; embeds show the numbers.
- `bot/quiz/interactions.py` — handle the select submit; multi grading.
- `bot/quiz/jobs.py` — post by schedule `seq`; per-week leaderboard on week completion; reveal multiple correct.
- `bot/commands/quiz.py` + `bot/context/slash/{commands,groups}.py` — admin `/quiz status|skip|post_now|reveal_now`; testing cadence.
- `sample_weeks.py` — extract the draw loop into a reusable `draw(bank, weeks, blocklist)` the schedule builder imports.

**Retired (separate cleanup task)**
- `utils/quiz_gen/{build.py, db.py, templates.py}`, `data/quiz_questions.json`, `data/quiz_questions.sample.json`, `bot/quiz/pool.py`, `tests/test_quiz_templates.py`, `tests/test_quiz_pool.py`.

---

## Task 1: Schema — add the new columns

**Files:**
- Modify: `bot/quiz/__init__.py:14-47`

`ensure_table` adds new columns to existing tables on import (the lobby precedent), so this is a safe additive migration — no data loss, runs once at boot.

- [ ] **Step 1: Add post columns** — in the `qc_quiz_posts` column list (after `correct_index`):

```python
			dict(cname="correct_index", ctype=db.types.int, notnull=False),
			dict(cname="correct_indices", ctype=db.types.text, notnull=False),  # JSON list, multi-answer
			dict(cname="seq", ctype=db.types.int, notnull=False),               # global question number
			dict(cname="week", ctype=db.types.int, notnull=False),
			dict(cname="day", ctype=db.types.int, notnull=False),               # 1=Mon .. 7=Sun (schedule day)
```

- [ ] **Step 2: Add answer column** — in `qc_quiz_answers` (after `choice_index`):

```python
			dict(cname="choice_index", ctype=db.types.int, notnull=False),
			dict(cname="choice_indices", ctype=db.types.text, notnull=False),   # JSON list, multi-answer
```

- [ ] **Step 3: Verify import** — Run: `python -c "import tests.conftest" ; python -m pytest tests/test_quiz_scoring.py -q`
Expected: PASS (conftest stubs `ensure_table`, so this only proves the module still imports).

- [ ] **Step 4: Commit**

```bash
git add bot/quiz/__init__.py
git commit -m "feat(quiz): declare correct_indices/choice_indices/seq/week/day columns"
```

---

## Task 2: Multi-answer grading + route (pure, TDD)

**Files:**
- Modify: `bot/quiz/scoring.py`
- Test: `tests/test_quiz_scoring.py`

- [ ] **Step 1: Write failing tests** — append to `tests/test_quiz_scoring.py`:

```python
from bot.quiz.scoring import grade_multi, parse_custom_id

def test_grade_multi_exact_match_is_correct():
    assert grade_multi([2, 0], [0, 2]) is True          # order-independent
    assert grade_multi([1], [1]) is True

def test_grade_multi_subset_or_superset_is_wrong():
    assert grade_multi([0], [0, 2]) is False             # missed one
    assert grade_multi([0, 1, 2], [0, 2]) is False       # extra one
    assert grade_multi([], [0]) is False                 # empty

def test_grade_multi_dedups_repeats():
    assert grade_multi([2, 2, 0], [0, 2]) is True

def test_parse_custom_id_multiselect_route():
    assert parse_custom_id("quiz:7:msel") == ("mselect", 7, None)
    assert parse_custom_id("quiz:7:ans:2") == ("answer", 7, 2)
    assert parse_custom_id("quiz:7:reveal") == ("reveal", 7, None)
```

- [ ] **Step 2: Run to verify they fail** — Run: `python -m pytest tests/test_quiz_scoring.py -k "multi or multiselect" -v`
Expected: FAIL (`grade_multi` undefined; `msel` route returns None).

- [ ] **Step 3: Implement** — in `bot/quiz/scoring.py`, add `grade_multi` after `grade`:

```python
def grade_multi(chosen_indices, correct_indices):
	"""True iff the chosen option set EXACTLY equals the correct set (all-or-nothing).
	Order- and duplicate-independent."""
	return set(int(i) for i in chosen_indices) == set(int(i) for i in correct_indices)
```

and extend `parse_custom_id` — before the final `return None`:

```python
		if len(parts) == 3 and parts[2] == "msel":
			return ("mselect", int(parts[1]), None)
```

- [ ] **Step 4: Run to verify pass** — Run: `python -m pytest tests/test_quiz_scoring.py -q`
Expected: PASS (all, including the pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add bot/quiz/scoring.py tests/test_quiz_scoring.py
git commit -m "feat(quiz): all-or-nothing multi-answer grading + select route"
```

---

## Task 3: Extract a reusable schedule draw + build the schedule (pure-ish, TDD)

**Files:**
- Modify: `utils/quiz_gen/sample_weeks.py`
- Create: `utils/quiz_gen/build_schedule.py`, `data/quiz_blocklist.json`
- Test: `tests/test_quiz_schedule.py`

The `sample_weeks.py` selection loop already does rotation + hard no-repeat. Extract it so the schedule builder can reuse it with a blocklist and stamping.

- [ ] **Step 1: Refactor `sample_weeks.py`** — replace the body of `main()`'s week-building loop with a call to a new module-level function, and add `relaxed_used` tracking:

```python
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
```

Then `main()` becomes: `weeks, _ = draw(bank, n)` followed by the existing print/dump.

- [ ] **Step 2: Write failing schedule tests** — `tests/test_quiz_schedule.py`:

```python
import json, os
from utils.quiz_gen.sample_weeks import draw
from utils.quiz_gen import build_schedule

def _bank():
	with open(os.path.join("data", "quiz_bank.json"), encoding="utf-8") as f:
		return json.load(f)

def test_draw_no_repeated_question_within_run():
	weeks, _ = draw(_bank(), 4)
	sigs = [tuple(sorted(q["options"])) for wk in weeks for q in wk if q]
	assert len(sigs) == len(set(sigs))                 # no option-set ever repeats

def test_draw_respects_blocklist():
	bank = _bank()
	victim = next(q["id"] for q in bank if q["category"] == "stats")
	weeks, _ = draw(bank, 6, blocklist={victim})
	ids = [q["id"] for wk in weeks for q in wk if q]
	assert victim not in ids

def test_stamp_assigns_sequential_numbers():
	weeks = [[{"id": "a"}, {"id": "b"}], [{"id": "c"}, {"id": "d"}]]
	flat = build_schedule.stamp(weeks)
	assert [e["seq"] for e in flat] == [1, 2, 3, 4]
	assert [e["week"] for e in flat] == [1, 1, 2, 2]
	assert [e["day"] for e in flat] == [1, 2, 1, 2]
```

- [ ] **Step 3: Run to verify fail** — Run: `python -m pytest tests/test_quiz_schedule.py -v`
Expected: FAIL (`build_schedule` does not exist).

- [ ] **Step 4: Implement `build_schedule.py`**:

```python
"""Offline: bake data/quiz_bank.json (minus the blocklist) into an ordered, numbered
data/quiz_schedule.json the bot posts one entry per day.

    python utils/quiz_gen/build_schedule.py [weeks]      # default 26
"""
import json
import os
import sys

import sample_weeks

_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_BANK = os.path.join(_REPO, "data", "quiz_bank.json")
_BLOCK = os.path.join(_REPO, "data", "quiz_blocklist.json")
_OUT = os.path.join(_REPO, "data", "quiz_schedule.json")
_WEEKDAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def stamp(weeks):
	"""Flatten weeks->questions into an ordered list, stamping seq/week/day/weekday.
	Skips empty slots (a slot the selector could not fill)."""
	out, seq = [], 0
	for wi, week in enumerate(weeks, 1):
		for di, q in enumerate(week, 1):
			if not q:
				continue
			seq += 1
			out.append({**q, "seq": seq, "week": wi, "day": di, "weekday": _WEEKDAY[di - 1]})
	return out


def main():
	weeks = int(sys.argv[1]) if len(sys.argv) > 1 else 26
	with open(_BANK, encoding="utf-8") as f:
		bank = json.load(f)
	block = set(json.load(open(_BLOCK, encoding="utf-8"))) if os.path.exists(_BLOCK) else set()
	drawn, relaxed = sample_weeks.draw(bank, weeks, block)
	schedule = stamp(drawn)
	with open(_OUT, "w", encoding="utf-8") as f:
		json.dump(schedule, f, indent=2, ensure_ascii=False)
	print(f"Wrote {len(schedule)} questions ({weeks} weeks) to {_OUT}")
	print(f"  blocklisted: {len(block)} | relaxed-fallback slots: {relaxed}")
	if relaxed:
		print("  NOTE: freshness facets exhausted — later weeks reuse opponents/answers.")


if __name__ == "__main__":
	main()
```

Create `data/quiz_blocklist.json` with `[]`.

- [ ] **Step 5: Run tests + generate** — Run: `python -m pytest tests/test_quiz_schedule.py -q` (Expected: PASS), then `cd utils/quiz_gen && python build_schedule.py 12` (Expected: prints "Wrote N questions (12 weeks)" and the relaxed-slot count).

- [ ] **Step 6: Commit**

```bash
git add utils/quiz_gen/sample_weeks.py utils/quiz_gen/build_schedule.py data/quiz_blocklist.json data/quiz_schedule.json tests/test_quiz_schedule.py
git commit -m "feat(quiz): bake reviewable numbered quiz_schedule.json from the bank"
```

---

## Task 4: Runtime schedule loader

**Files:**
- Create: `bot/quiz/schedule.py`
- Test: `tests/test_quiz_schedule.py` (add cases)

- [ ] **Step 1: Write failing tests** — append:

```python
from bot.quiz import schedule as sched

_FIX = [
	{"id": "x1", "category": "combat", "seq": 1, "week": 1, "day": 1, "options": ["a", "b", "c", "d"], "correct_indices": [0]},
	{"id": "x2", "category": "techgaps", "seq": 2, "week": 1, "day": 2, "options": ["a", "b", "c", "d"], "correct_indices": [1, 2]},
]

def test_entry_for_seq_returns_match_or_none():
	assert sched.entry_for_seq(_FIX, 2)["id"] == "x2"
	assert sched.entry_for_seq(_FIX, 99) is None

def test_week_is_complete_true_when_all_days_posted():
	# week 1 has days {1,2} in this 2-entry fixture
	assert sched.week_is_complete(_FIX, week=1, posted_seqs={1, 2}) is True
	assert sched.week_is_complete(_FIX, week=1, posted_seqs={1}) is False
```

- [ ] **Step 2: Run to verify fail** — Run: `python -m pytest tests/test_quiz_schedule.py -k "entry_for_seq or week_is_complete" -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `bot/quiz/schedule.py`**:

```python
# -*- coding: utf-8 -*-
"""Load + query the committed data/quiz_schedule.json. Pure: the bot passes in the
set of already-posted seqs (from MySQL). Tolerant loader (returns [] on a missing /
invalid file) so the bot never crashes before the schedule is generated."""
from __future__ import annotations

import json
import os

_DEFAULT_PATH = os.path.join(
	os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "quiz_schedule.json")
_REQUIRED = ("id", "category", "prompt", "options", "correct_indices", "explanation",
			 "seq", "week", "day")


def load(path=None):
	try:
		with open(path or _DEFAULT_PATH, encoding="utf-8") as f:
			data = json.load(f)
		return [q for q in data if all(k in q for k in _REQUIRED)]
	except (OSError, ValueError):
		return []


def entry_for_seq(items, seq):
	return next((q for q in items if q["seq"] == seq), None)


def week_is_complete(items, week, posted_seqs):
	"""True iff every seq scheduled for `week` has been posted."""
	wanted = {q["seq"] for q in items if q["week"] == week}
	return bool(wanted) and wanted <= set(posted_seqs)
```

- [ ] **Step 4: Run to verify pass** — Run: `python -m pytest tests/test_quiz_schedule.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/quiz/schedule.py tests/test_quiz_schedule.py
git commit -m "feat(quiz): runtime schedule loader (seq lookup + week completion)"
```

---

## Task 5: Store — persist/read the new fields, next-seq, per-week answers

**Files:**
- Modify: `bot/quiz/store.py`

No unit test (DB-backed; conftest stubs `db`). Verified via the integration checklist (Task 12).

- [ ] **Step 1: Extend `create_post`** to store the schedule fields + `correct_indices`:

```python
async def create_post(channel_id, q, opened_at, closes_at):
	"""Insert an open post. q is a schedule entry (carries seq/week/day/correct_indices)."""
	return await db.insert("qc_quiz_posts", dict(
		channel_id=channel_id, message_id=None, question_id=q["id"], category=q["category"],
		prompt=q["prompt"], options_json=json.dumps(q["options"]),
		correct_index=q.get("correct_index"),
		correct_indices=json.dumps(q["correct_indices"]),
		explanation=q["explanation"], opened_at=opened_at, closes_at=closes_at, status="open",
		seq=q["seq"], week=q["week"], day=q["day"]))
```

- [ ] **Step 2: Add `next_seq` + `posted_seqs`**:

```python
async def next_seq(channel_id):
	"""The seq the channel should post next = (max posted seq) + 1, starting at 1."""
	rows = await db.fetchall(
		"SELECT MAX(seq) m FROM qc_quiz_posts WHERE channel_id=%s", [channel_id])
	return int((rows[0]["m"] or 0)) + 1 if rows else 1


async def posted_seqs(channel_id):
	rows = await db.fetchall(
		"SELECT seq FROM qc_quiz_posts WHERE channel_id=%s AND seq IS NOT NULL", [channel_id])
	return {r["seq"] for r in (rows or [])}
```

- [ ] **Step 3: Add multi-answer recording** (sibling to `record_answer`):

```python
async def record_answer_multi(post_id, user_id, choice_indices, is_correct, answered_at, response_ms):
	"""Record a multi-select answer once (same answered_at IS NULL TOCTOU guard as
	record_answer). Stores the chosen set as JSON in choice_indices."""
	await db.execute(
		"UPDATE qc_quiz_answers SET choice_indices=%s, is_correct=%s, answered_at=%s, response_ms=%s "
		"WHERE post_id=%s AND user_id=%s AND answered_at IS NULL",
		[json.dumps(sorted(int(i) for i in choice_indices)), (1 if is_correct else 0),
		 answered_at, response_ms, post_id, user_id])
```

- [ ] **Step 4: Add the per-week leaderboard query**:

```python
async def week_answers_by_week(channel_id, week):
	"""Answered rows for posts in a given SCHEDULE week — feeds scoring.tally. Robust to
	calendar drift (keys off the schedule week, not a rolling 7-day window)."""
	rows = await db.fetchall(
		"SELECT a.user_id, a.nick, a.is_correct FROM qc_quiz_answers a "
		"JOIN qc_quiz_posts p ON p.id=a.post_id "
		"WHERE p.channel_id=%s AND p.week=%s AND a.answered_at IS NOT NULL",
		[channel_id, week])
	return rows or []
```

- [ ] **Step 5: Verify import** — Run: `python -m pytest tests/ -q`
Expected: PASS (store still imports under the stub; existing tests unaffected).

- [ ] **Step 6: Commit**

```bash
git add bot/quiz/store.py
git commit -m "feat(quiz): store seq/week/correct_indices, next_seq, per-week answers"
```

---

## Task 6: View — numbered cards + multi-correct rendering (pure, TDD)

**Files:**
- Modify: `bot/quiz/view.py`
- Test: `tests/test_quiz_view.py`

- [ ] **Step 1: Write failing tests** — append:

```python
from bot.quiz.view import card_lines, result_lines

def test_card_lines_show_question_number_and_week_day():
	out = "\n".join(card_lines("combat", "hard", seq=17, week=3, day=3, closes_in_h=24))
	assert "#17" in out and "Week 3" in out and "Day 3" in out

def test_result_lines_render_multiple_correct_letters():
	out = "\n".join(result_lines("Q?", ["a", "b", "c", "d"], [0, 2], "because", ["Ann"]))
	assert "A, C" in out
	assert "because" in out and "Ann" in out
```

- [ ] **Step 2: Run to verify fail** — Run: `python -m pytest tests/test_quiz_view.py -k "number or multiple" -v`
Expected: FAIL (`card_lines` signature mismatch; `result_lines` takes a single int).

- [ ] **Step 3: Implement** — replace `card_lines` and `result_lines`:

```python
def card_lines(category, difficulty, seq, week, day, closes_in_h):
	return [
		f"**Daily AoE2 quiz · Week {week} · Day {day} · #{seq}**",
		f"Category: {category} · {difficulty}",
		"Tap **Reveal & start** — a private 3:00 timer starts, then lock your answer.",
		f"Closes in ~{int(closes_in_h)}h · weekly leaderboard at the end of each week.",
	]


def result_lines(prompt, options, correct_indices, explanation, winners):
	letters = letter_options(options)
	correct = ", ".join(_LETTERS[i] for i in sorted(correct_indices))
	who = ", ".join(winners) if winners else "nobody"
	return [
		f"**{prompt}**",
		f"Correct answer{'s' if len(correct_indices) > 1 else ''}: **{correct}**",
		explanation,
		f"Got it right: {who}",
	]
```

- [ ] **Step 4: Run to verify pass** — Run: `python -m pytest tests/test_quiz_view.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/quiz/view.py tests/test_quiz_view.py
git commit -m "feat(quiz): numbered cards + multi-correct result rendering"
```

---

## Task 7: Embeds — select-menu vs buttons, numbered embeds

**Files:**
- Modify: `bot/quiz/embeds.py`

No unit test (nextcord). Verified in Task 12.

- [ ] **Step 1: Add the UI-type predicate + the select view, update signatures**:

```python
def uses_select(question_type):
	"""'which civ(s) LACK ...' questions are answered by selecting ALL that apply (1+),
	so the answer count is never leaked. Everything else is single-choice."""
	return question_type in ("lack_single", "lack_both")


def card_embed(category, difficulty, seq, week, day, closes_in_h):
	return nextcord.Embed(
		title="Daily AoE2 quiz",
		description="\n".join(_v.card_lines(category, difficulty, seq, week, day, closes_in_h)),
		colour=nextcord.Colour.blurple())


def answer_view(post_id, options, question_type):
	v = nextcord.ui.View(timeout=None, auto_defer=False)   # routed via on_interaction (redeploy-safe)
	if uses_select(question_type):
		v.add_item(nextcord.ui.StringSelect(
			custom_id=f"quiz:{post_id}:msel", placeholder="Select ALL that apply, then click away",
			min_values=1, max_values=len(options),
			options=[nextcord.SelectOption(label=f"{chr(65 + i)}. {o[:90]}", value=str(i))
					 for i, o in enumerate(options)]))
	else:
		for i in range(len(options)):
			v.add_item(nextcord.ui.Button(
				style=nextcord.ButtonStyle.secondary, label=chr(65 + i),
				custom_id=f"quiz:{post_id}:ans:{i}"))
	return v


def result_embed(prompt, options, correct_indices, explanation, winners, title="Quiz result"):
	return nextcord.Embed(
		title=title,
		description="\n".join(_v.result_lines(prompt, options, correct_indices, explanation, winners)),
		colour=nextcord.Colour.green())
```

- [ ] **Step 2: Verify import** — Run: `python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add bot/quiz/embeds.py
git commit -m "feat(quiz): select-all UI for lack-questions, numbered card/result embeds"
```

---

## Task 8: Interactions — handle the select submit + multi grading

**Files:**
- Modify: `bot/quiz/interactions.py`

No unit test (nextcord). Verified in Task 12.

- [ ] **Step 1: Route the select** — in `on_quiz_interaction`, after computing `kind, post_id, choice`:

```python
		kind, post_id, choice = route
		post = await store.get_post(post_id)
		if not post:
			return await _eph(interaction, closed_notice())
		now = int(time.time())
		if kind == "reveal":
			return await _handle_reveal(interaction, post, now)
		if kind == "mselect":
			values = [int(v) for v in (interaction.data or {}).get("values", [])]
			return await _handle_answer(interaction, post, values, now, multi=True)
		return await _handle_answer(interaction, post, choice, now, multi=False)
```

- [ ] **Step 2: Generalise `_handle_answer`** for both single and multi:

```python
async def _handle_answer(interaction, post, choice, now, multi):
	row = await store.get_answer(post["id"], interaction.user.id)
	if not row:
		return await _eph(interaction, "Tap **Reveal & start** first.")
	if row.get("answered_at") is not None:
		return await _eph(interaction, already_answered_notice())
	if post["status"] != "open" or now > int(row["deadline_at"]):
		return await _eph(interaction, too_late_notice())
	response_ms = max(0, (now - int(row["revealed_at"])) * 1000)
	if multi:
		correct_set = json.loads(post["correct_indices"])
		is_correct = grade_multi(choice, correct_set)
		await store.record_answer_multi(post["id"], interaction.user.id, choice, is_correct, now, response_ms)
	else:
		is_correct = grade(choice, post["correct_index"])
		await store.record_answer(post["id"], interaction.user.id, choice, is_correct, now, response_ms)
	await _eph(interaction, "Locked in. The answer is revealed when the next quiz posts.")
```

Add the imports at the top: `from .scoring import grade, grade_multi, parse_custom_id`.

- [ ] **Step 3: Update the reveal handler** to build the answer view with `question_type` — in `_handle_reveal`, change the `answer_view(...)` call:

```python
	await interaction.response.send_message(
		embed=question_embed(post["prompt"], options, seconds_left),
		view=answer_view(post["id"], options, post["category"] and _qtype(post)),
		ephemeral=True)
```

Since `qc_quiz_posts` does not store `question_type`, derive the UI from the stored `correct_indices` instead (simpler, no schema add): replace `uses_select(question_type)` call-sites with a check on whether the post is multi. Update `answer_view` to take `multi: bool` and `_handle_reveal` to pass `multi = len(json.loads(post["correct_indices"])) > 1 or post["category"] == "techgaps"`. **Decision recorded:** techgaps always uses the select (so a single-answer "lack" question doesn't leak its cardinality); all other categories use buttons.

Concretely, change `embeds.answer_view(post_id, options, question_type)` → `answer_view(post_id, options, multi)` where `multi` decides select-vs-buttons, and in `_handle_reveal`:

```python
	multi = post["category"] == "techgaps"
	view = answer_view(post["id"], options, multi)
```

- [ ] **Step 4: Verify import** — Run: `python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/quiz/interactions.py bot/quiz/embeds.py
git commit -m "feat(quiz): select-all answering + multi grading in the interaction router"
```

---

## Task 9: Jobs — post by schedule, per-week leaderboard, multi reveal

**Files:**
- Modify: `bot/quiz/jobs.py`

No unit test. Verified in Task 12. Builds on the already-merged reveal-before-next flow.

- [ ] **Step 1: Swap the pool for the schedule** — replace the `from . import pool, scoring, store` import with `from . import schedule, scoring, store` and `_POOL = pool.load()` with `_SCHEDULE = schedule.load()`.

- [ ] **Step 2: Rewrite `_post_question` to post the next seq**:

```python
	async def _post_question(self, channel_id, open_window, now):
		"""Post the channel's next scheduled question (by seq). Claims last_post_ymd
		only after a confirmed send. Returns the post id, or None when the schedule is
		exhausted / the channel is missing."""
		seq = await store.next_seq(channel_id)
		q = schedule.entry_for_seq(_SCHEDULE, seq)
		if not q:
			log.info(f"Quiz schedule exhausted at seq {seq} — regenerate quiz_schedule.json.")
			return None
		from core.client import dc
		from . import embeds
		channel = dc.get_channel(channel_id)
		if channel is None:
			log.error(f"Quiz channel {channel_id} not found.")
			return None
		post_id = await store.create_post(channel_id, q, now, now + open_window)
		msg = await channel.send(
			embed=embeds.card_embed(q["category"], q["difficulty"], q["seq"], q["week"],
									q["day"], open_window / 3600),
			view=embeds.card_view(post_id))
		await store.set_message_id(post_id, msg.id)
		await store.upsert_config(channel_id, last_post_ymd=scoring._ymd(now))
		log.info(f"Quiz posted #{q['seq']} ({q['id']}) in channel {channel_id}.")
		return post_id
```

Update `_maybe_post_daily` and `force_post` to drop the `min_difficulty` argument (schedule order is fixed) and pass only `(channel_id, open_window, now)`.

- [ ] **Step 3: Post the week leaderboard when a week completes** — add to `_maybe_post_daily`, between the reveal and the post:

```python
	async def _maybe_post_daily(self, cfg, now):
		if not scoring.daily_due(now, _hour(cfg.get("quiz_hour"), 9), cfg.get("last_post_ymd")):
			return
		await self._reveal_previous(cfg["channel_id"])
		await self._maybe_week_leaderboard(cfg["channel_id"])
		await self._post_question(cfg["channel_id"], int(cfg.get("open_window") or 86400), now)

	async def _maybe_week_leaderboard(self, channel_id):
		"""If the highest completed schedule week has not had its leaderboard posted,
		post it now (keyed to schedule weeks, robust to calendar drift)."""
		posted = await store.posted_seqs(channel_id)
		done_weeks = [w for w in sorted({q["week"] for q in _SCHEDULE})
					  if schedule.week_is_complete(_SCHEDULE, w, posted)]
		if not done_weeks:
			return
		week = done_weeks[-1]
		cfg = await store.get_config(channel_id)
		if int((cfg or {}).get("last_leaderboard_week") or 0) >= week:
			return
		await store.upsert_config(channel_id, last_leaderboard_week=week)
		rows = await store.week_answers_by_week(channel_id, week)
		from core.client import dc
		from . import embeds
		channel = dc.get_channel(channel_id)
		if channel:
			await channel.send(embed=embeds.leaderboard_embed(scoring.tally(rows), f"Week {week}"))
```

Add a `last_leaderboard_week` (int) column to `qc_quiz_config` in `bot/quiz/__init__.py` (Task 1 follow-up; `ensure_table` ALTERs it). Remove the old calendar-based `_maybe_leaderboard` call from `_run` (replaced by `_maybe_week_leaderboard`).

- [ ] **Step 4: Make `_reveal` use `correct_indices`** — in `_reveal`, replace the `result_embed(... post["correct_index"] ...)` calls with the parsed list:

```python
		correct = json.loads(post["correct_indices"])
		...
		await msg.edit(embed=embeds.result_embed(
			post["prompt"], options, correct, post["explanation"], winners), view=None)
		...
		await channel.send(embed=embeds.result_embed(
			post["prompt"], options, correct, post["explanation"], winners, title="Yesterday's answer"))
```

- [ ] **Step 5: Verify import + tests** — Run: `python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bot/quiz/jobs.py bot/quiz/__init__.py
git commit -m "feat(quiz): post by schedule seq, per-week leaderboard, multi-answer reveal"
```

---

## Task 10: Admin commands + testing cadence

**Files:**
- Modify: `bot/commands/quiz.py`, `bot/context/slash/commands.py`, `bot/context/slash/groups.py`

No unit test. Verified in Task 12. Follow the existing `/quiz` admin subcommand pattern already in these files.

- [ ] **Step 1: `/quiz status`** — report where the channel is in the schedule:

```python
async def quiz_status(ctx):
	cfg = await store.get_config(ctx.channel.id)
	seq = await store.next_seq(ctx.channel.id)
	entry = schedule.entry_for_seq(schedule.load(), seq)
	nxt = f"#{entry['seq']} (Week {entry['week']} Day {entry['day']}, {entry['category']})" if entry else "schedule exhausted"
	await ctx.reply(f"Quiz {'ON' if cfg and cfg.get('enabled') else 'OFF'} · next: {nxt} · "
					f"last leaderboard: week {(cfg or {}).get('last_leaderboard_week') or 0}")
```

- [ ] **Step 2: `/quiz skip`** — blocklist the *next* question id and advance (records a post with `status='closed'` so `next_seq` moves past it without sending):

```python
async def quiz_skip(ctx):
	seq = await store.next_seq(ctx.channel.id)
	entry = schedule.entry_for_seq(schedule.load(), seq)
	if not entry:
		return await ctx.reply("Nothing to skip — schedule exhausted.")
	now = int(time.time())
	pid = await store.create_post(ctx.channel.id, entry, now, now)
	await store.close_post(pid)
	await ctx.reply(f"Skipped #{entry['seq']} ({entry['id']}). Add it to data/quiz_blocklist.json "
					f"and regenerate to drop it permanently.")
```

- [ ] **Step 3: `/quiz post_now` + `/quiz reveal_now`** — wrap the existing `jobs.force_post(channel_id)` and a new `jobs.reveal_now(channel_id)` that calls `_reveal_previous`. Register all four under the `/quiz` admin group in `groups.py`/`commands.py` exactly like the existing `/quiz post_now`.

- [ ] **Step 4: Testing cadence** — add a `test_interval` (int seconds) column to `qc_quiz_config`; in `_maybe_post_daily`, if `cfg.get("test_interval")` is set, gate on "`now - last_post_at >= test_interval`" instead of `daily_due` (store `last_post_at` epoch). This lets a tester fast-forward many questions in minutes. Document: `test_interval` unset → normal daily behaviour.

- [ ] **Step 5: Verify import + tests** — Run: `python -m pytest tests/ -q`; `ruff check bot/`.
Expected: PASS / clean.

- [ ] **Step 6: Commit**

```bash
git add bot/commands/quiz.py bot/context/slash/ bot/quiz/__init__.py bot/quiz/jobs.py
git commit -m "feat(quiz): admin status/skip/post_now/reveal_now + testing cadence"
```

---

## Task 11: Retire the old generator + pool

**Files:**
- Delete: `utils/quiz_gen/{build.py, db.py, templates.py}`, `data/quiz_questions.json`, `data/quiz_questions.sample.json`, `bot/quiz/pool.py`, `tests/test_quiz_templates.py`, `tests/test_quiz_pool.py`

- [ ] **Step 1: Confirm no live references** — Run: `grep -rn "quiz.*pool\|quiz_gen.templates\|quiz_questions.json" bot/ utils/ tests/`
Expected: only the files being deleted (jobs.py already imports `schedule`, not `pool`).

- [ ] **Step 2: Delete + run the suite** — Run: `python -m pytest tests/ -q`
Expected: PASS (the deleted tests are gone; everything else green).

- [ ] **Step 3: Commit**

```bash
git rm utils/quiz_gen/build.py utils/quiz_gen/db.py utils/quiz_gen/templates.py data/quiz_questions.json data/quiz_questions.sample.json bot/quiz/pool.py tests/test_quiz_templates.py tests/test_quiz_pool.py
git commit -m "chore(quiz): retire the old template generator + single-answer pool"
```

---

## Task 12: Integration verification (manual — needs MySQL + a Discord test server)

The quiz glue (`jobs`/`store`/`interactions`/`embeds`) is not unit-testable under the conftest stubs. Verify end-to-end on a throwaway test channel with `test_interval` set low (e.g. 60s) so a "day" passes in a minute.

- [ ] **Step 1:** Enable the quiz on a test channel (`/quiz` config), set `test_interval=60`. Confirm a card posts showing **Week 1 · Day 1 · #1**.
- [ ] **Step 2:** Tap **Reveal & start** → ephemeral question with the number, a 3:00 timer, and the right control (buttons for combat/stats/effects, **select-all dropdown** for a techgaps "lack" question). Lock an answer; confirm "Locked in."
- [ ] **Step 3:** With a second account, answer the same question wrong. Wait one `test_interval`; confirm the bot posts **"Yesterday's answer was …"** (with the correct letter(s), explanation, and the winner list) *before* **#2** posts.
- [ ] **Step 4:** Multi-select correctness: on a 2-answer techgaps question, pick exactly the two correct → winner; pick one of two → not a winner; pick three → not a winner.
- [ ] **Step 5:** Let a full week (7 seqs) post; confirm a **"Week 1" leaderboard** posts (ranked by correct, then answered, then user) right after Day 7's reveal and before Week 2 Day 1.
- [ ] **Step 6:** Restart the bot mid-open-question; confirm the reveal/answer controls still work (DB-driven routing) and no double-post occurs.
- [ ] **Step 7:** `/quiz skip` the next question; confirm `next_seq` advances and the question never posts. `/quiz status` reports the correct next entry.

---

## Edge Cases (handled above; collected here)

- **Schedule exhaustion** — `_post_question` logs and no-ops; regenerate `quiz_schedule.json` with more weeks. `/quiz status` shows "schedule exhausted."
- **Missed day / bot downtime** — posting is by *seq*, not calendar, so no question is skipped; `seq`/`week`/`day` labels stay stable. The week leaderboard fires on *schedule-week completion*, not a calendar weekday, so drift never splits a week.
- **Double-post crash window** — `last_post_ymd` (daily) / `last_post_at` (test cadence) claimed only after a confirmed send + `seq` from the DB; the only race is a crash between `channel.send` and the claim (one round-trip), accepted (same as today).
- **Reveal race** — a player revealing in the final seconds before the daily reveal loses the tail of their 3-min window when the question closes. Rare; accepted (same as the prior 24h-close behaviour).
- **Restart** — all state in MySQL; `on_ready` re-registers persistent views for still-open posts; ephemeral select/buttons route by `custom_id` through `on_interaction`, so they survive a redeploy.
- **Duplicate / late answer** — the `answered_at IS NULL` UPDATE guard makes the first answer win; the `deadline_at` / `status` checks reject late taps; both apply to the select path.
- **Answer-count leakage** — techgaps always use the select-all dropdown, so a single-answer "lack" question never reveals its cardinality.
- **0 winners / empty week** — reveal shows "Got it right: nobody"; an empty week tally shows "No answers this week."
- **Multiple channels** — one channel enabled at a time (`disable_all` precedes enable); `seq` is tracked per channel, so each channel runs its own copy of the schedule.
- **Question fix mid-flight** — a posted question is frozen (its text/answer are copied into `qc_quiz_posts`); blocklist + regenerate only affects *future* seqs.
- **Timezone** — all schedule predicates are UTC (the Railway process runs UTC), unchanged from today.

---

## Self-Review

- **Spec coverage:** bank-as-source ✓ (Tasks 3–4, 9); rotation + no-repeat ✓ (Task 3); multi-select ✓ (Tasks 2, 7, 8); tracking who answered right/wrong ✓ (Task 5, existing `qc_quiz_answers`); weekly leaderboard by # correct ✓ (Tasks 5, 9); referenceable #/week/day ✓ (Tasks 3, 6, 9); quality loop (blocklist) ✓ (Tasks 3, 10); reveal-before-next ✓ (already merged, extended in Task 9); testing cadence ✓ (Task 10).
- **Placeholder scan:** none — every code step is concrete.
- **Type consistency:** `result_lines`/`result_embed` take `correct_indices` (list) everywhere (Tasks 6, 7, 9); `answer_view(post_id, options, multi)` consistent (Tasks 7, 8); `grade_multi(chosen, correct)` consistent (Tasks 2, 8); schedule entries carry `seq/week/day/correct_indices` from Task 3 through Tasks 5/6/9.
- **One open follow-up:** `card_view` is reused unchanged from the current code; confirm it still imports after the `embeds` edits (it does — untouched).
</content>
