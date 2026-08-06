# Quiz → public poll with gold, and the 100-gold match reward — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The daily quiz becomes a public 24-hour poll with a live named tally that pays gold at lock (50 correct / 10 played, balance-capped at 500), and the match faucet pays 100 instead of 10.

**Architecture:** The poll is bot-rendered buttons + DB state (the standing redeploy-safe pattern — global `on_interaction` router, DB-resolvable custom_ids), never Discord native polls. All money moves through `bot/predictions/gold.py` (sole-writer rule); quiz resolution follows "money first, terminal status last" with the existing `_close_due` sweep as the retry loop — no `terminal_intent` needed because a quiz has exactly one terminal branch.

**Tech Stack:** Python 3.11, nextcord 2.6.0, aiomysql; tests run pure with the conftest stubs (no real DB, no real nextcord).

**Spec:** `docs/superpowers/specs/2026-08-06-quiz-poll-gold-design.md` — the contract. Where this plan and the spec disagree, the spec governs.

## Global Constraints

- `MATCH_REWARD = 100`; `QUIZ_CORRECT_REWARD = 50`; `QUIZ_PLAYED_REWARD = 10`; `REWARD_CEILING = 500` (unchanged). Every faucet grant is `min(reward, max(0, 500 − balance))`.
- Correct pays **50 total**, not 50 + 10. One ledger row per (quiz post, user): idem key `quiz:{quiz_post_id}:{user_id}`, entry_type `quiz_correct` | `quiz_played`, `post_id` and `match_id` NULL on quiz rows.
- Only `bot/predictions/gold.py` writes `gold_ledger`/`gold_balances`. Quiz code calls it.
- The vote gate is the **clock** (`now < closes_at`) and `status == 'open'`; grading runs strictly after `closes_at`.
- Resolve order is **grade → pay → results → close**, and a payment failure raises before the close so `_close_due` retries. Announced gold totals come from the ledger, not a loop accumulator.
- A `quiz_answers` row with no choice (NULL `choice_index` AND NULL `choice_indices`) is a non-vote: excluded from tally, grading, and gold. `answers_for_post`'s `answered_at IS NOT NULL` filter already enforces this — do not weaken it.
- `quiz:{post}:reveal` stays parseable (transition converter); the card edit carries a message-id guard (see spec §2 Voting).
- All quiz Views: `timeout=None, auto_defer=False`, custom_ids as today.
- `bot/quiz/*` and `bot/predictions/*` use **tabs**. Tests use the existing conftest stubs; never import real aiomysql/nextcord in tests.
- Weekly leaderboard (`scoring.tally`, `_maybe_week_leaderboard`) and the schedule arithmetic are untouched.

## File map

| File | Change |
|---|---|
| `bot/predictions/scoring.py` | `MATCH_REWARD=100`; add `QUIZ_CORRECT_REWARD`, `QUIZ_PLAYED_REWARD`, `quiz_reward_amount` |
| `bot/predictions/gold.py` | add `grant_quiz_reward`, `quiz_paid_total` |
| `bot/predictions/view.py` | `_ENTRY_LABELS` gains quiz labels |
| `bot/predictions/__init__.py` | ledger `entry_type` comment gains the two quiz values |
| `bot/quiz/__init__.py` | `quiz_posts` gains `difficulty` column |
| `bot/quiz/scoring.py` | add `is_multi_category`; `parse_custom_id` unchanged |
| `bot/quiz/view.py` | add `poll_card_lines`, `tally_lines`, `_option_voters`; `result_lines` gains `gold_note`; old builders die in Task 9 |
| `bot/quiz/embeds.py` | add `poll_embed`, `vote_view`, `final_card_embed`; `result_embed` gains `gold_note`; old builders die in Task 9 |
| `bot/quiz/store.py` | add `record_vote`, `record_vote_multi`, `write_grade`; `create_post` writes difficulty; old answer writers die in Task 9 |
| `bot/quiz/interactions.py` | rewritten: vote flow + converter + message-id guard |
| `bot/quiz/jobs.py` | `_reveal` rewritten (grade→pay→results→close); `_post_question` sends the poll card |
| `bot/commands/quiz.py` | drop `answer_window` from config surface |
| `bot/context/slash/commands.py` | `/quiz config` description drops `answer_window` |
| Tests | `tests/test_predictions.py`, `tests/test_predictions_gold.py`, `tests/test_quiz_view.py`, new `tests/test_quiz_store_votes.py`, new `tests/test_quiz_interactions.py`, new `tests/test_quiz_jobs_resolve.py` |

---

### Task 1: Match reward 10 → 100

**Files:**
- Modify: `bot/predictions/scoring.py` (line ~15)
- Test: `tests/test_predictions.py` (lines ~96–112)

**Interfaces:**
- Produces: `MATCH_REWARD == 100`; `reward_amount(balance)` semantics unchanged otherwise. Nothing else in the codebase reads the constant directly.

- [ ] **Step 1: Update the tests to the new constant (they must fail first)**

In `tests/test_predictions.py`, find the `reward_amount` assertions (currently pinning 10) and replace them with:

```python
		assert scoring.reward_amount(0) == 100
		assert scoring.reward_amount(399) == 100
		assert scoring.reward_amount(400) == 100
		assert scoring.reward_amount(401) == 99
		assert scoring.reward_amount(480) == 20
		assert scoring.reward_amount(496) == 4
		assert scoring.reward_amount(499) == 1
		assert scoring.reward_amount(500) == 0
		assert scoring.reward_amount(620) == 0
```

Keep the surrounding test method names/structure; only the assertion values change (add the new boundary lines in the same style the file already uses — one assert per line, matching existing indentation, which is tabs).

- [ ] **Step 2: Run to verify the new pins fail**

Run: `pytest tests/test_predictions.py -q -k reward`
Expected: FAIL (e.g. `assert 10 == 100`)

- [ ] **Step 3: Change the constant**

In `bot/predictions/scoring.py`:

```python
MATCH_REWARD = 100
```

(was `MATCH_REWARD = 10`; `SEED_AMOUNT`, `REWARD_CEILING`, `reward_amount` untouched.)

- [ ] **Step 4: Run the full suite**

Run: `pytest tests/ -q`
Expected: all pass (if any other test pinned 10, update it to 100 — it is pinning the constant, not a behaviour of its own)

- [ ] **Step 5: Commit**

```bash
git add bot/predictions/scoring.py tests/test_predictions.py
git commit -m "feat(gold): the match faucet pays 100 — same 500 ceiling"
```

---

### Task 2: The quiz money rule and the multi-category helper

**Files:**
- Modify: `bot/predictions/scoring.py`, `bot/quiz/scoring.py`
- Test: `tests/test_predictions.py`, `tests/test_quiz_scoring.py`

**Interfaces:**
- Produces: `bot.predictions.scoring.quiz_reward_amount(balance, correct) -> int`; constants `QUIZ_CORRECT_REWARD = 50`, `QUIZ_PLAYED_REWARD = 10`. `bot.quiz.scoring.is_multi_category(category) -> bool`. Task 3 consumes `quiz_reward_amount`; Tasks 7–8 consume `is_multi_category`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_predictions.py` (same class/section as the `reward_amount` tests, tab-indented):

```python
	def test_quiz_reward_amount_correct_pays_50(self):
		assert scoring.quiz_reward_amount(0, True) == 50
		assert scoring.quiz_reward_amount(449, True) == 50
		assert scoring.quiz_reward_amount(450, True) == 50
		assert scoring.quiz_reward_amount(451, True) == 49
		assert scoring.quiz_reward_amount(499, True) == 1
		assert scoring.quiz_reward_amount(500, True) == 0
		assert scoring.quiz_reward_amount(700, True) == 0

	def test_quiz_reward_amount_played_pays_10(self):
		assert scoring.quiz_reward_amount(0, False) == 10
		assert scoring.quiz_reward_amount(490, False) == 10
		assert scoring.quiz_reward_amount(491, False) == 9
		assert scoring.quiz_reward_amount(499, False) == 1
		assert scoring.quiz_reward_amount(500, False) == 0
		assert scoring.quiz_reward_amount(700, False) == 0
```

Append to `tests/test_quiz_scoring.py`:

```python
def test_is_multi_category_only_techgaps():
	assert scoring.is_multi_category("techgaps") is True
	for c in ("combat", "stats", "effects", "villagers", None, ""):
		assert scoring.is_multi_category(c) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_predictions.py tests/test_quiz_scoring.py -q -k "quiz_reward or multi_category"`
Expected: FAIL with AttributeError (functions not defined)

- [ ] **Step 3: Implement**

`bot/predictions/scoring.py`, after `reward_amount`:

```python
QUIZ_CORRECT_REWARD = 50
QUIZ_PLAYED_REWARD = 10


def quiz_reward_amount(balance, correct):
	"""The quiz faucet: 50 for a correct answer, 10 for a cast vote, and the
	same ceiling as the match faucet — neither ever lifts a balance above
	REWARD_CEILING. A lifeline, not an income."""
	return min(QUIZ_CORRECT_REWARD if correct else QUIZ_PLAYED_REWARD,
			max(0, REWARD_CEILING - balance))
```

(Constant placement: put the two constants next to `MATCH_REWARD` at the top of the file, and only the function after `reward_amount` — constants group with constants.)

`bot/quiz/scoring.py`, after `grade_multi`:

```python
def is_multi_category(category):
	"""Only game 'techgaps' questions are multi-answer; every other category —
	including all player-quiz categories — is single-answer. The ONE place
	this rule lives; keep in sync if a multi-answer category is ever added."""
	return category == "techgaps"
```

- [ ] **Step 4: Run the full suite**

Run: `pytest tests/ -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add bot/predictions/scoring.py bot/quiz/scoring.py tests/test_predictions.py tests/test_quiz_scoring.py
git commit -m "feat(quiz): the quiz faucet rule — 50 correct, 10 played, one 500 ceiling"
```

---

### Task 3: `gold.grant_quiz_reward` + ledger labels

**Files:**
- Modify: `bot/predictions/gold.py`, `bot/predictions/view.py`, `bot/predictions/__init__.py`
- Test: `tests/test_predictions_gold.py`

**Interfaces:**
- Consumes: `scoring.quiz_reward_amount` (Task 2).
- Produces: `gold.grant_quiz_reward(community_id, user_id, quiz_post_id, correct, now) -> int` (gold granted; 0 when capped or already paid; raises RuntimeError when the balance row is missing — caller must `ensure_seeded` first, exactly like `grant_match_reward`). `gold.quiz_paid_total(quiz_post_id) -> int` (ledger-read total for the post's `quiz:` idem keys). Task 8 consumes both.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_predictions_gold.py`, following the file's existing fixture pattern (the fake-adapter/transaction fixtures it already uses for `grant_match_reward` — mirror the neighbouring test class structure exactly):

```python
class TestGrantQuizReward:
	async def test_correct_pays_50_with_entry_type(self, bank):
		await bank.seed(COMM, USER)                      # helper the file already has for grant_match_reward tests
		await bank.drain_to(COMM, USER, 100)             # balance 100
		got = await gold.grant_quiz_reward(COMM, USER, 77, True, NOW)
		assert got == 50
		row = bank.last_ledger_row()
		assert row["entry_type"] == "quiz_correct"
		assert row["idem_key"] == f"quiz:77:{USER}"
		assert row["post_id"] is None and row["match_id"] is None
		assert bank.balance(COMM, USER) == 150

	async def test_played_pays_10(self, bank):
		await bank.seed(COMM, USER)
		await bank.drain_to(COMM, USER, 100)
		got = await gold.grant_quiz_reward(COMM, USER, 77, False, NOW)
		assert got == 10
		assert bank.last_ledger_row()["entry_type"] == "quiz_played"

	async def test_ceiling_clamps(self, bank):
		await bank.seed(COMM, USER)                      # balance 500
		assert await gold.grant_quiz_reward(COMM, USER, 77, True, NOW) == 0
		await bank.drain_to(COMM, USER, 460)
		assert await gold.grant_quiz_reward(COMM, USER, 78, True, NOW) == 40

	async def test_idempotent_per_post_and_user(self, bank):
		await bank.seed(COMM, USER)
		await bank.drain_to(COMM, USER, 100)
		assert await gold.grant_quiz_reward(COMM, USER, 77, True, NOW) == 50
		assert await gold.grant_quiz_reward(COMM, USER, 77, True, NOW) == 0
		assert bank.balance(COMM, USER) == 150            # not 200

	async def test_missing_balance_row_raises(self, bank):
		with pytest.raises(RuntimeError):
			await gold.grant_quiz_reward(COMM, 999999, 77, True, NOW)
```

> **Adapt the helper names** (`bank`, `seed`, `drain_to`, `last_ledger_row`, `balance`, `COMM`, `USER`, `NOW`) to whatever the file's existing `grant_match_reward` tests actually use — the assertions and scenarios above are the requirement; the fixture vocabulary is the file's. The idempotency test must fail if the `INSERT IGNORE`'s rowcount stops being consulted (i.e., simulate the duplicate key and assert no balance bump).

Also append a `quiz_paid_total` test:

```python
	async def test_quiz_paid_total_sums_only_this_post(self, bank):
		await bank.seed(COMM, USER); await bank.seed(COMM, USER2)
		await bank.drain_to(COMM, USER, 0); await bank.drain_to(COMM, USER2, 0)
		await gold.grant_quiz_reward(COMM, USER, 77, True, NOW)
		await gold.grant_quiz_reward(COMM, USER2, 77, False, NOW)
		await gold.grant_quiz_reward(COMM, USER, 78, True, NOW)
		assert await gold.quiz_paid_total(77) == 60
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_predictions_gold.py -q -k Quiz`
Expected: FAIL with AttributeError

- [ ] **Step 3: Implement**

`bot/predictions/gold.py`, directly after `grant_match_reward`:

```python
async def grant_quiz_reward(community_id, user_id, quiz_post_id, correct, now):
	"""The quiz faucet: 50 for a correct answer, 10 for a cast vote, same
	ceiling as the match faucet. ONE ledger row per (quiz post, user) — the
	amount depends on correctness, so the idem key is per post+user, not per
	entry type. gold_ledger.post_id stays NULL: that column references
	prediction_posts, and a quiz post id in it would be a foreign lie — the
	quiz post id travels in the idem key. Caller must ensure_seeded first.
	Returns the gold granted (0 when capped out or already paid)."""
	async with db.transaction() as tx:
		row = await tx.fetchone(
			"SELECT balance FROM gold_balances "
			"WHERE community_id=%s AND user_id=%s FOR UPDATE",
			[community_id, user_id])
		amount = scoring.quiz_reward_amount(int(row["balance"]) if row else 0, correct)
		if not amount:
			return 0
		applied = await tx.insert("gold_ledger", dict(
			community_id=community_id, user_id=user_id,
			entry_type="quiz_correct" if correct else "quiz_played",
			amount=amount, created_at=now,
			idem_key=f"quiz:{quiz_post_id}:{user_id}"), on_duplicate="ignore")
		if not applied:
			return 0
		bumped = await tx.execute(
			"UPDATE gold_balances SET balance=balance+%s, updated_at=%s "
			"WHERE community_id=%s AND user_id=%s",
			[amount, now, community_id, user_id])
		if not bumped:
			raise RuntimeError(f"gold_balances row missing for {community_id}/{user_id} (quiz)")
		return amount


async def quiz_paid_total(quiz_post_id):
	"""What this quiz post actually paid, read back from the ledger — the
	honest figure even on a resolve retried after a partial crash, where a
	loop accumulator would count only the newly applied grants."""
	rows = await db.fetchall(
		"SELECT COALESCE(SUM(amount), 0) s FROM gold_ledger "
		"WHERE idem_key LIKE %s", [f"quiz:{quiz_post_id}:%"])
	return int(rows[0]["s"]) if rows else 0
```

`bot/predictions/view.py`, in `_ENTRY_LABELS`:

```python
	"quiz_correct": "Quiz — correct answer",
	"quiz_played": "Quiz — played",
```

`bot/predictions/__init__.py`, the ledger `entry_type` comment (line ~84):

```python
			# seed | match_reward | bet | cancel | refund | payout
			# | quiz_correct | quiz_played | admin_adjust
```

- [ ] **Step 4: Run the full suite**

Run: `pytest tests/ -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add bot/predictions/gold.py bot/predictions/view.py bot/predictions/__init__.py tests/test_predictions_gold.py
git commit -m "feat(gold): grant_quiz_reward — one idem-keyed row per voter per quiz"
```

---

### Task 4: `quiz_posts.difficulty` column

**Files:**
- Modify: `bot/quiz/__init__.py`, `bot/quiz/store.py`
- Test: `tests/test_quiz_store_votes.py` (create — it grows in Task 6)

**Interfaces:**
- Produces: `quiz_posts.difficulty` (nullable str) written by `create_post`. Task 5's renderers read `post.get("difficulty")`.

- [ ] **Step 1: Declare the column**

`bot/quiz/__init__.py`, in the `quiz_posts` columns list, after the `category` line:

```python
		dict(cname="difficulty", ctype=db.types.str, notnull=False),
```

(`ensure_table` adds missing columns to existing tables — no migration needed; old rows stay NULL and render without it.)

- [ ] **Step 2: Write it in `create_post`**

`bot/quiz/store.py`, in `create_post`'s insert dict, after `category=q["category"],`:

```python
		difficulty=q.get("difficulty"),
```

- [ ] **Step 3: Test — pin that create_post forwards difficulty**

Create `tests/test_quiz_store_votes.py` with the conftest's fake-db pattern (see how `tests/test_predictions_store.py` fakes `core.database.db` — same idiom, 4-space indent is wrong here: this file tests `bot/quiz`, use tabs like the other quiz tests):

```python
async def test_create_post_stores_difficulty(fake_db):
	q = dict(id="q1", category="combat", difficulty="medium", prompt="p",
			options=["a", "b"], correct_index=0, correct_indices=[0],
			explanation="e", seq=1, week=1, day=1, source="game")
	await store.create_post(123, q, 1000, 87400)
	row = fake_db.inserted("quiz_posts")[-1]
	assert row["difficulty"] == "medium"
	assert row["closes_at"] == 87400
```

(Adapt `fake_db.inserted` to the actual fake's API.)

- [ ] **Step 4: Run**

Run: `pytest tests/test_quiz_store_votes.py tests/ -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add bot/quiz/__init__.py bot/quiz/store.py tests/test_quiz_store_votes.py
git commit -m "feat(quiz): store difficulty on the post — the card now re-renders from the row"
```

---

### Task 5: Pure poll rendering (`bot/quiz/view.py`)

**Files:**
- Modify: `bot/quiz/view.py`
- Test: `tests/test_quiz_view.py`

**Interfaces:**
- Produces (all pure, consumed by Task 6's embeds):
  - `poll_card_lines(category, difficulty, seq, week, day, closes_in_h, prompt, options, votes, source=None) -> list[str]`
  - `tally_lines(options, votes, correct_indices=None) -> list[str]`
  - `result_lines(prompt, options, correct_indices, explanation, winners, gold_note=None)` (backwards-compatible — existing callers pass no `gold_note`)
  - `votes` rows are `quiz_answers` dicts: `nick`, `user_id`, `choice_index` (int|None), `choice_indices` (JSON str|None).
- Old builders (`card_lines`, `question_lines`, `too_late_notice`, `already_answered_notice`) stay until Task 9.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_quiz_view.py`:

```python
def test_poll_card_shows_question_and_options_but_never_the_answer():
	votes = [dict(user_id=1, nick="Ann", choice_index=0, choice_indices=None)]
	lines = view.poll_card_lines("combat", "medium", 5, 2, 3, 23.5,
			"Which unit wins?", ["Knight", "Pikeman"], votes, source="game")
	text = "\n".join(lines)
	assert "Which unit wins?" in text
	assert "A. Knight" in text and "B. Pikeman" in text
	assert "Week 2" in text and "Day 3" in text and "#5" in text
	assert "medium" in text
	assert "50" in text and "10" in text          # the gold rule is on the card
	assert "✅" not in text                        # the open card never marks the answer


def test_poll_card_without_difficulty_renders():
	lines = view.poll_card_lines("combat", None, 5, 2, 3, 23.5, "Q?", ["a", "b"], [])
	assert not any("None" in ln for ln in lines)


def test_tally_counts_and_names():
	votes = [
		dict(user_id=1, nick="Ann", choice_index=0, choice_indices=None),
		dict(user_id=2, nick="Bob", choice_index=0, choice_indices=None),
		dict(user_id=3, nick="Cy", choice_index=1, choice_indices=None),
	]
	lines = view.tally_lines(["Knight", "Pikeman"], votes)
	assert "**2** vote(s)" in lines[0] and "Ann" in lines[0] and "Bob" in lines[0]
	assert "**1** vote(s)" in lines[1] and "Cy" in lines[1]


def test_tally_multi_voter_appears_under_each_pick():
	votes = [dict(user_id=1, nick="Ann", choice_index=None, choice_indices="[0, 2]")]
	lines = view.tally_lines(["a", "b", "c"], votes)
	assert "Ann" in lines[0] and "Ann" not in lines[1] and "Ann" in lines[2]


def test_tally_caps_names_at_12():
	votes = [dict(user_id=i, nick=f"P{i}", choice_index=0, choice_indices=None)
			for i in range(15)]
	line = view.tally_lines(["a", "b"], votes)[0]
	assert "**15** vote(s)" in line
	assert "+3 more" in line
	assert "P11" in line and "P12" not in line


def test_tally_ignores_choiceless_rows():
	votes = [dict(user_id=1, nick="Ghost", choice_index=None, choice_indices=None)]
	lines = view.tally_lines(["a", "b"], votes)
	assert "Ghost" not in "\n".join(lines)
	assert "**0** vote(s)" in lines[0]


def test_tally_marks_correct_options_when_told():
	lines = view.tally_lines(["a", "b"], [], correct_indices={1})
	assert "✅" not in lines[0] and "✅" in lines[1]


def test_result_lines_gold_note_appended_only_when_given():
	base = view.result_lines("Q?", ["a", "b"], [0], "because", ["Ann"])
	with_gold = view.result_lines("Q?", ["a", "b"], [0], "because", ["Ann"],
			gold_note="🪙 60 gold paid out")
	assert "🪙 60 gold paid out" not in "\n".join(base)
	assert with_gold[-1] == "🪙 60 gold paid out"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_quiz_view.py -q`
Expected: new tests FAIL with AttributeError; old tests still pass

- [ ] **Step 3: Implement**

`bot/quiz/view.py` — add `import json` at the top (after `from __future__`), then:

```python
_NAME_CAP = 12


def _option_voters(options, votes):
	"""Voter nicks per option index, in vote order. A multi-answer voter
	appears under EACH option they picked; a row with no choice at all (an
	old-era Reveal ghost) appears nowhere."""
	per = [[] for _ in options]
	for v in votes:
		nick = v.get("nick") or str(v.get("user_id"))
		raw_multi = v.get("choice_indices")
		if raw_multi:
			for i in json.loads(raw_multi):
				if 0 <= int(i) < len(per):
					per[int(i)].append(nick)
		elif v.get("choice_index") is not None:
			i = int(v["choice_index"])
			if 0 <= i < len(per):
				per[i].append(nick)
	return per


def tally_lines(options, votes, correct_indices=None):
	"""One line per option: letter, text, count, capped names — the live
	scoreboard while the poll is open, and (with correct_indices) the final
	card's marked version."""
	per = _option_voters(options, votes)
	out = []
	for i, opt in enumerate(options):
		names = per[i]
		mark = "✅ " if (correct_indices is not None and i in correct_indices) else ""
		who = ""
		if names:
			shown = ", ".join(names[:_NAME_CAP])
			extra = f" +{len(names) - _NAME_CAP} more" if len(names) > _NAME_CAP else ""
			who = f" — {shown}{extra}"
		out.append(f"{mark}{_LETTERS[i]}. {opt} · **{len(names)}** vote(s){who}")
	return out


def poll_card_lines(category, difficulty, seq, week, day, closes_in_h, prompt, options, votes, source=None):
	"""The open-poll card: header, the question, the live tally, the rules.
	Never receives correct answers — the open card cannot leak what it does
	not know."""
	lines = [f"**Daily AoE2 quiz · Week {week} · Day {day} · #{seq}**"]
	tag = _SOURCE_TAG.get(source)
	meta = f"Category: {category}" + (f" · {difficulty}" if difficulty else "")
	lines.append(f"{tag} · {meta}" if tag else meta)
	lines += ["", f"**{prompt}**", ""]
	lines += tally_lines(options, votes)
	lines += [
		"",
		"Vote with the buttons — you can change your vote until it locks.",
		f"Locks in ~{int(closes_in_h)}h · correct pays 50 \U0001FA99, playing pays 10 \U0001FA99.",
	]
	return lines
```

And change `result_lines` to:

```python
def result_lines(prompt, options, correct_indices, explanation, winners, gold_note=None):
	correct = ", ".join(_LETTERS[i] for i in sorted(correct_indices))
	who = ", ".join(winners) if winners else "nobody"
	lines = [
		f"**{prompt}**",
		f"Correct answer{'s' if len(correct_indices) > 1 else ''}: **{correct}**",
		explanation,
		f"Got it right: {who}",
	]
	if gold_note:
		lines.append(gold_note)
	return lines
```

- [ ] **Step 4: Run the full suite**

Run: `pytest tests/ -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add bot/quiz/view.py tests/test_quiz_view.py
git commit -m "feat(quiz): the poll card renders a live named tally — pure builders"
```

---

### Task 6: Vote writes (`bot/quiz/store.py`)

**Files:**
- Modify: `bot/quiz/store.py`
- Test: `tests/test_quiz_store_votes.py`

**Interfaces:**
- Produces:
  - `record_vote(post_id, user_id, nick, choice_index, now)` — REPLACE upsert; PK `(post_id, user_id)` is the one-vote rule
  - `record_vote_multi(post_id, user_id, nick, choice_indices, now)` — same, JSON-sorted set
  - `write_grade(post_id, user_id, is_correct)` — plain UPDATE
- Tasks 7–8 consume these. `answers_for_post` is reused as-is (its `answered_at IS NOT NULL` filter is the non-vote exclusion).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_quiz_store_votes.py`:

```python
async def test_record_vote_is_a_replace_upsert(fake_db):
	await store.record_vote(9, 1, "Ann", 2, 1000)
	await store.record_vote(9, 1, "Ann", 0, 1001)          # changed her mind
	row = fake_db.row("quiz_answers", post_id=9, user_id=1)
	assert row["choice_index"] == 0
	assert row["choice_indices"] is None
	assert row["is_correct"] is None                       # graded at lock, not at press
	assert row["answered_at"] == 1001
	assert row["revealed_at"] is None and row["response_ms"] is None


async def test_record_vote_multi_stores_sorted_set(fake_db):
	await store.record_vote_multi(9, 1, "Ann", [2, 0], 1000)
	row = fake_db.row("quiz_answers", post_id=9, user_id=1)
	assert row["choice_indices"] == "[0, 2]"
	assert row["choice_index"] is None


async def test_write_grade(fake_db):
	await store.record_vote(9, 1, "Ann", 0, 1000)
	await store.write_grade(9, 1, True)
	assert fake_db.row("quiz_answers", post_id=9, user_id=1)["is_correct"] == 1
```

(Adapt the fake-db helper vocabulary; the REPLACE test must actually exercise the on_duplicate path — the fake must emulate PK replacement, as the predictions store tests' fake does for its upserts.)

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_quiz_store_votes.py -q`
Expected: FAIL with AttributeError

- [ ] **Step 3: Implement**

`bot/quiz/store.py`, in the answers section:

```python
async def record_vote(post_id, user_id, nick, choice_index, now):
	"""UPSERT the user's vote — the PK (post_id, user_id) IS the one-vote
	rule, and REPLACE makes a changed mind overwrite the row (also wiping any
	old-era timing fields, which is correct: this row now means a poll vote).
	is_correct stays NULL until the post locks; answered_at is the latest
	change, which keeps answers_for_post's cast-a-vote filter true."""
	await db.insert("quiz_answers", dict(
		post_id=post_id, user_id=user_id, nick=nick,
		revealed_at=None, deadline_at=None,
		choice_index=int(choice_index), choice_indices=None,
		is_correct=None, answered_at=now, response_ms=None),
		on_duplicate="replace")


async def record_vote_multi(post_id, user_id, nick, choice_indices, now):
	"""The multi-answer variant: the submitted set replaces the previous one
	wholesale (JSON-sorted, the grade_multi convention)."""
	await db.insert("quiz_answers", dict(
		post_id=post_id, user_id=user_id, nick=nick,
		revealed_at=None, deadline_at=None,
		choice_index=None,
		choice_indices=json.dumps(sorted(int(i) for i in choice_indices)),
		is_correct=None, answered_at=now, response_ms=None),
		on_duplicate="replace")


async def write_grade(post_id, user_id, is_correct):
	"""Lock-time grading write. Deterministic input -> safe to re-run."""
	await db.execute(
		"UPDATE quiz_answers SET is_correct=%s WHERE post_id=%s AND user_id=%s",
		[(1 if is_correct else 0), post_id, user_id])
```

- [ ] **Step 4: Run the full suite**

Run: `pytest tests/ -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add bot/quiz/store.py tests/test_quiz_store_votes.py
git commit -m "feat(quiz): votes are REPLACE-upserts; grading is a lock-time write"
```

---

### Task 7: nextcord assembly (`bot/quiz/embeds.py`)

**Files:**
- Modify: `bot/quiz/embeds.py`
- Test: `tests/test_quiz_interactions.py` (create; the embed assembly is exercised through Task 8's interaction tests and directly here via the conftest nextcord stub)

**Interfaces:**
- Consumes: Task 5's pure builders, Task 2's `is_multi_category`.
- Produces (Tasks 8–9 consume):
  - `poll_embed(post, votes)` — the living card, built from the post row; computes `closes_in_h` from `post["closes_at"] − now`
  - `vote_view(post_id, options, multi)` — A–D buttons or the multi select; `timeout=None, auto_defer=False`
  - `final_card_embed(post, votes)` — the locked card: tally with ✅ marks, no components
  - `result_embed(..., gold_note=None)` — passthrough of the new arg
- Old builders (`card_embed`, `card_view`, `question_embed`, `answer_view`) stay until Task 9.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_quiz_interactions.py`, starting with embed-shape tests (conftest's nextcord stub records Embed/View/Button/StringSelect kwargs — see `tests/test_predictions_interactions.py` for the idiom; tabs):

```python
def _post(**over):
	base = dict(id=9, channel_id=5, message_id=111, category="combat",
			difficulty="medium", prompt="Q?", options_json='["Knight", "Pikeman"]',
			correct_index=0, correct_indices="[0]", explanation="because",
			seq=5, week=2, day=3, source="game", opened_at=1000,
			closes_at=1000 + 86400, status="open")
	base.update(over)
	return base


def test_vote_view_single_answer_buttons():
	v = embeds.vote_view(9, ["Knight", "Pikeman"], multi=False)
	assert v.kwargs["timeout"] is None and v.kwargs["auto_defer"] is False
	ids = [b.kwargs["custom_id"] for b in v.items]
	assert ids == ["quiz:9:ans:0", "quiz:9:ans:1"]
	assert [b.kwargs["label"] for b in v.items] == ["A", "B"]


def test_vote_view_multi_is_a_select():
	v = embeds.vote_view(9, ["a", "b", "c"], multi=True)
	assert v.items[0].kwargs["custom_id"] == "quiz:9:msel"
	assert v.items[0].kwargs["max_values"] == 3


def test_poll_embed_reads_the_post_row():
	e = embeds.poll_embed(_post(), [])
	assert "Q?" in e.kwargs["description"]
	assert "A. Knight" in e.kwargs["description"]


def test_final_card_marks_the_correct_option():
	votes = [dict(user_id=1, nick="Ann", choice_index=0, choice_indices=None)]
	e = embeds.final_card_embed(_post(), votes)
	assert "✅ A. Knight" in e.kwargs["description"]
```

(Exact stub-introspection attributes — `kwargs`, `items` — must match what the conftest stub actually records; read the stub in `tests/conftest.py` first and use its real surface.)

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_quiz_interactions.py -q`
Expected: FAIL with AttributeError

- [ ] **Step 3: Implement**

`bot/quiz/embeds.py` — add `import json` and `import time` at the top, then:

```python
def poll_embed(post, votes):
	"""The living card, rebuilt from the post row on every render — which is
	why the row carries everything the card shows (incl. difficulty)."""
	options = json.loads(post["options_json"])
	closes_in_h = max(0, (int(post["closes_at"]) - int(time.time())) / 3600)
	return nextcord.Embed(
		title="Daily AoE2 quiz",
		description="\n".join(_v.poll_card_lines(
			post["category"], post.get("difficulty"), post["seq"], post["week"],
			post["day"], closes_in_h, post["prompt"], options, votes,
			source=post.get("source"))),
		colour=nextcord.Colour.blurple())


def vote_view(post_id, options, multi):
	# auto_defer=False is REQUIRED — clicks route through the global
	# on_interaction handler (redeploy-safe); see card_view's original note.
	v = nextcord.ui.View(timeout=None, auto_defer=False)
	if multi:
		v.add_item(nextcord.ui.StringSelect(
			custom_id=f"quiz:{post_id}:msel", placeholder="Select ALL that apply",
			min_values=1, max_values=len(options),
			options=[nextcord.SelectOption(label=f"{chr(65 + i)}. {o[:90]}", value=str(i))
					 for i, o in enumerate(options)]))
	else:
		for i in range(len(options)):
			v.add_item(nextcord.ui.Button(
				style=nextcord.ButtonStyle.secondary, label=chr(65 + i),
				custom_id=f"quiz:{post_id}:ans:{i}"))
	return v


def final_card_embed(post, votes):
	"""The card after lock: prompt + tally with the correct options marked.
	Sent with view=None — the components are stripped by the same edit."""
	options = json.loads(post["options_json"])
	correct = set(json.loads(post["correct_indices"]))
	lines = [f"**{post['prompt']}**", ""] + _v.tally_lines(options, votes, correct_indices=correct)
	return nextcord.Embed(
		title="Daily AoE2 quiz — locked",
		description="\n".join(lines),
		colour=nextcord.Colour.purple())
```

And `result_embed` gains the passthrough:

```python
def result_embed(prompt, options, correct_indices, explanation, winners, title="Quiz result", gold_note=None):
	return nextcord.Embed(
		title=title,
		description="\n".join(_v.result_lines(prompt, options, correct_indices, explanation, winners, gold_note=gold_note)),
		colour=nextcord.Colour.green())
```

- [ ] **Step 4: Run the full suite**

Run: `pytest tests/ -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add bot/quiz/embeds.py tests/test_quiz_interactions.py
git commit -m "feat(quiz): poll embed, vote view and the locked card"
```

---

### Task 8: The vote flow (`bot/quiz/interactions.py`)

**Files:**
- Rewrite: `bot/quiz/interactions.py`
- Test: `tests/test_quiz_interactions.py`

**Interfaces:**
- Consumes: `store.record_vote(_multi)`, `store.answers_for_post`, `embeds.poll_embed`/`vote_view`, `scoring.is_multi_category`/`parse_custom_id`, `view.closed_notice`.
- Produces: `on_quiz_interaction(interaction)` — same export, same registration in `bot/events.py` (untouched).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_quiz_interactions.py` a `FakeInteraction` following `tests/test_predictions_interactions.py`'s pattern (component type, `data={"custom_id": ..., "values": [...]}`, a `response` recording `send_message`/`edit_message` calls, a `message` with an `id`, a `user`). Then:

```python
async def test_vote_press_records_and_rerenders_the_card(quiz_env):
	quiz_env.posts[9] = _post()
	i = FakeInteraction(cid="quiz:9:ans:1", user_id=42, nick="Ann", message_id=111, now=2000)
	await interactions.on_quiz_interaction(i)
	assert quiz_env.votes[(9, 42)]["choice_index"] == 1
	assert i.response.edited is not None                  # the card edit IS the feedback
	assert "Ann" in i.response.edited["embed"].kwargs["description"]
	assert i.response.sent is None                        # no ephemeral on the happy path


async def test_changing_the_vote_replaces_it(quiz_env):
	quiz_env.posts[9] = _post()
	await interactions.on_quiz_interaction(FakeInteraction(cid="quiz:9:ans:1", user_id=42, nick="Ann", message_id=111))
	await interactions.on_quiz_interaction(FakeInteraction(cid="quiz:9:ans:0", user_id=42, nick="Ann", message_id=111))
	assert quiz_env.votes[(9, 42)]["choice_index"] == 0


async def test_multi_select_replaces_the_set(quiz_env):
	quiz_env.posts[9] = _post(category="techgaps", correct_indices="[0, 2]",
			options_json='["a", "b", "c"]')
	i = FakeInteraction(cid="quiz:9:msel", values=["2", "0"], user_id=42, nick="Ann", message_id=111)
	await interactions.on_quiz_interaction(i)
	assert quiz_env.votes[(9, 42)]["choice_indices"] == "[0, 2]"


async def test_press_at_or_after_closes_at_is_refused(quiz_env):
	quiz_env.posts[9] = _post(closes_at=1500)
	i = FakeInteraction(cid="quiz:9:ans:0", user_id=42, nick="Ann", message_id=111, now=1500)
	await interactions.on_quiz_interaction(i)
	assert (9, 42) not in quiz_env.votes
	assert i.response.sent is not None and "closed" in i.response.sent["content"].lower()


async def test_press_on_closed_status_is_refused(quiz_env):
	quiz_env.posts[9] = _post(status="closed")
	i = FakeInteraction(cid="quiz:9:ans:0", user_id=42, nick="Ann", message_id=111, now=2000)
	await interactions.on_quiz_interaction(i)
	assert (9, 42) not in quiz_env.votes


async def test_press_from_an_old_ephemeral_confirms_without_editing(quiz_env):
	# Old-era ephemeral answer views carry the same ans: routes but live on a
	# DIFFERENT message — the guard records the vote and answers ephemerally
	# instead of painting the card over a private message.
	quiz_env.posts[9] = _post(message_id=111)
	i = FakeInteraction(cid="quiz:9:ans:0", user_id=42, nick="Ann", message_id=999, now=2000)
	await interactions.on_quiz_interaction(i)
	assert quiz_env.votes[(9, 42)]["choice_index"] == 0
	assert i.response.edited is None
	assert i.response.sent is not None


async def test_reveal_press_converts_the_old_card(quiz_env):
	quiz_env.posts[9] = _post(message_id=111)
	i = FakeInteraction(cid="quiz:9:reveal", user_id=42, nick="Ann", message_id=111, now=2000)
	await interactions.on_quiz_interaction(i)
	assert (9, 42) not in quiz_env.votes                  # converting is not voting
	assert i.response.edited is not None                  # card now shows the poll
	assert i.response.edited["view"] is not None


async def test_foreign_custom_ids_fall_through(quiz_env):
	i = FakeInteraction(cid="bet:1:0:10", user_id=42, nick="Ann", message_id=111)
	await interactions.on_quiz_interaction(i)
	assert i.response.sent is None and i.response.edited is None
```

`quiz_env` is a fixture monkeypatching `bot.quiz.store` (posts dict, votes dict keyed `(post_id, user_id)`, `answers_for_post` returning cast votes) and freezing `time.time()` to the FakeInteraction's `now` — build it the way `test_predictions_interactions.py` builds its store fake.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_quiz_interactions.py -q`
Expected: new tests FAIL

- [ ] **Step 3: Rewrite `bot/quiz/interactions.py`**

```python
# -*- coding: utf-8 -*-
"""Global component-interaction router for the quiz poll. Registered as an
additional on_interaction listener. DB-driven: never relies on a live View, so
vote buttons keep working across a Railway redeploy. Foreign interactions fall
straight through — we only act on custom_ids starting with 'quiz:'.
Only imported at runtime (by bot.events), never during unit tests."""
import json
import time
import traceback

import nextcord

from core.console import log

from . import store
from .scoring import is_multi_category, parse_custom_id
from .view import closed_notice


async def on_quiz_interaction(interaction):
	try:
		if interaction.type != nextcord.InteractionType.component:
			return
		cid = (interaction.data or {}).get("custom_id", "")
		route = parse_custom_id(cid)
		if route is None:
			return
		kind, post_id, choice = route
		post = await store.get_post(post_id)
		if not post:
			return await _eph(interaction, closed_notice())
		now = int(time.time())
		# The gate is the CLOCK first: grading runs strictly after closes_at,
		# so refusing presses from closes_at onward leaves no press/grade race.
		if post["status"] != "open" or now >= int(post["closes_at"]):
			return await _eph(interaction, closed_notice())
		if kind == "reveal":
			# Transition-era card: the one post open at deploy still shows the
			# old Reveal button. Pressing it converts the card in place to the
			# poll format (idempotent) — it does NOT record a vote.
			return await _rerender(interaction, post)
		nick = _nick(interaction.user)
		if kind == "mselect":
			values = [int(v) for v in (interaction.data or {}).get("values", [])]
			if not values:
				return await _eph(interaction, "Pick at least one option.")
			await store.record_vote_multi(post["id"], interaction.user.id, nick, values, now)
		else:
			await store.record_vote(post["id"], interaction.user.id, nick, choice, now)
		await _rerender(interaction, post)
	except Exception as e:
		log.error(f"quiz interaction error: {e}\n{traceback.format_exc()}")
		try:
			if not interaction.response.is_done():
				await interaction.response.send_message(
					"Something went wrong — please try again.", ephemeral=True)
		except Exception:
			pass


async def _rerender(interaction, post):
	"""Answer the press by re-rendering the shared card with the fresh tally —
	the card edit IS the feedback. Old-era ephemeral answer views carry the
	same ans:/msel: routes but live on a DIFFERENT message; editing that one
	would paint the card over a private ephemeral, so those presses get a
	plain confirmation instead (the vote is already recorded either way)."""
	from . import embeds
	votes = await store.answers_for_post(post["id"])
	options = json.loads(post["options_json"])
	msg = getattr(interaction, "message", None)
	if msg is not None and post.get("message_id") and msg.id == post["message_id"]:
		return await interaction.response.edit_message(
			embed=embeds.poll_embed(post, votes),
			view=embeds.vote_view(post["id"], options, is_multi_category(post["category"])))
	return await _eph(interaction, "Vote counted — see the quiz card for the tally.")


def _nick(user):
	return getattr(user, "display_name", None) or getattr(user, "name", None) or str(user.id)


async def _eph(interaction, text):
	if not interaction.response.is_done():
		await interaction.response.send_message(text, ephemeral=True)
```

(Preserve the existing `_eph` tail behaviour if the current implementation has an else-branch — keep whatever it does today.)

- [ ] **Step 4: Run the full suite**

Run: `pytest tests/ -q`
Expected: all pass. Two racing presses need no special handling — both votes land in the DB and the later render is accurate; note this in the test file if a reviewer would look for a race test.

- [ ] **Step 5: Commit**

```bash
git add bot/quiz/interactions.py tests/test_quiz_interactions.py
git commit -m "feat(quiz): votes are public presses on the card — clock-gated, self-rendering"
```

---

### Task 9: Resolve = grade → pay → results → close (`bot/quiz/jobs.py`), and the dead code goes

**Files:**
- Modify: `bot/quiz/jobs.py`
- Delete from: `bot/quiz/store.py` (`record_reveal`, `record_answer`, `record_answer_multi`, `get_answer`), `bot/quiz/embeds.py` (`card_embed`, `card_view`, `question_embed`, `answer_view`), `bot/quiz/view.py` (`card_lines`, `question_lines`, `too_late_notice`, `already_answered_notice`)
- Modify: `bot/commands/quiz.py`, `bot/context/slash/commands.py` (drop `answer_window`)
- Test: `tests/test_quiz_jobs_resolve.py` (create), `tests/test_quiz_view.py` (drop dead-builder tests)

**Interfaces:**
- Consumes: everything above. `bot/events.py` and the slash surface are unchanged except the `/quiz config` description string.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_quiz_jobs_resolve.py`. Fake `store` (posts/votes/closed set), `community.community_for_channel`, `gold` (`ensure_seeded`, `grant_quiz_reward` recording calls, `quiz_paid_total`), and a fake channel/`dc` — the fixture idiom from `tests/test_predictions_flow.py`. Cases (each a test):

```python
async def test_resolve_grades_pays_then_closes_in_order(env):
	# 2 voters: one correct (index 0), one wrong. Assert: write_grade called for
	# both with the right verdicts; grant_quiz_reward called with correct=True
	# for the winner and correct=False for the loser; ensure_seeded before each
	# grant; close_post called LAST (env records global call order).

async def test_resolve_reruns_idempotently(env):
	# Run _reveal twice on the same post. Grades identical; grants called again
	# (the idem key no-ops live in gold, faked to return 0 on the second run);
	# close_post called each time; no error.

async def test_payment_failure_leaves_the_post_open(env):
	# grant_quiz_reward raises for one voter. _reveal must raise AFTER the loop
	# (the other voter still got paid) and close_post must NOT have been called.

async def test_no_community_grades_and_closes_without_gold(env):
	# community_for_channel -> None. write_grade called, grant never called,
	# close_post called, result embed carries no gold note.

async def test_choiceless_rows_are_not_paid(env):
	# answers_for_post returns only cast votes by contract — env includes a
	# ghost row NOT returned by the fake (documenting the filter), and a
	# multi post where choice_indices=[] grades as wrong, not crash.

async def test_gold_note_reads_the_ledger_total(env):
	# quiz_paid_total -> 60; the fresh result embed's description contains "60".

async def test_post_question_sends_the_poll_card(env):
	# _post_question sends poll_embed + vote_view (not the old card_embed) and
	# the multi flag follows is_multi_category(q["category"]).
```

Write these out fully in the file — each with concrete fake wiring, following the flow-test idiom. The order assertion (`close_post` last, and never on failure) is the one that must be mutation-checked: reorder close before pay in a scratch copy and confirm the test fails.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_quiz_jobs_resolve.py -q`
Expected: FAIL (old `_reveal` has no grading/pay)

- [ ] **Step 3: Rewrite `_reveal` and `_post_question` in `bot/quiz/jobs.py`**

```python
	async def _reveal(self, post, fresh):
		"""Resolve one poll: grade every cast vote, pay gold, edit the card
		into the final tally, optionally announce, and only THEN mark the post
		closed — money first, terminal status last. A crash (or a payment
		failure, which raises) anywhere before the close leaves the post
		'open' past closes_at, and _close_due re-enters here next tick:
		grading is deterministic, every payment idem-keyed, the card edit
		harmless to repeat."""
		import nextcord
		from core.client import dc
		from bot import community
		from bot.predictions import gold as gold_bank
		from . import embeds
		votes = await store.answers_for_post(post["id"])       # cast votes only (answered_at filter)
		options = json.loads(post["options_json"])
		correct = json.loads(post["correct_indices"])
		multi = scoring.is_multi_category(post["category"])
		# 1. Grade.
		graded = []
		for v in votes:
			if multi:
				ok = scoring.grade_multi(json.loads(v["choice_indices"] or "[]"), correct)
			else:
				ok = v.get("choice_index") is not None and scoring.grade(v["choice_index"], post["correct_index"])
			await store.write_grade(post["id"], v["user_id"], ok)
			graded.append(dict(v, is_correct=ok))
		# 2. Pay. If ANY payment fails, raise after the loop — closing past an
		# unpaid voter would put their gold beyond the retry loop forever.
		gold_note = None
		community_id = await community.community_for_channel(post["channel_id"])
		if community_id is None:
			if graded:
				log.info(f"Quiz {post['id']}: channel {post['channel_id']} has no community — no gold.")
		else:
			now = int(time.time())
			failures = 0
			for v in graded:
				try:
					await gold_bank.ensure_seeded(community_id, v["user_id"], now)
					await gold_bank.grant_quiz_reward(
						community_id, v["user_id"], post["id"], v["is_correct"], now)
				except Exception as e:
					failures += 1
					log.error(f"Quiz gold for {v['user_id']} on post {post['id']} failed: {e}")
			if failures:
				raise RuntimeError(
					f"{failures} quiz payment(s) failed on post {post['id']} — left open to retry")
			total = await gold_bank.quiz_paid_total(post["id"])
			if total:
				gold_note = f"\U0001FA99 {total} gold paid out — 50 correct, 10 played, never past 500."
		winners = [v["nick"] for v in graded if v["is_correct"]]
		# 3. Results.
		channel = dc.get_channel(post["channel_id"])
		if channel:
			if post.get("message_id"):
				try:
					msg = await channel.fetch_message(post["message_id"])
					await msg.edit(embed=embeds.final_card_embed(post, graded), view=None)
				except nextcord.NotFound:
					pass
			if fresh:
				await channel.send(embed=embeds.result_embed(
					post["prompt"], options, correct, post["explanation"], winners,
					title="Yesterday's answer", gold_note=gold_note))
		# 4. Close — last.
		await store.close_post(post["id"])
```

In `_post_question`, replace the send block:

```python
		post_id = await store.create_post(channel_id, q, now, now + open_window)
		post = await store.get_post(post_id)
		msg = await channel.send(
			embed=embeds.poll_embed(post, []),
			view=embeds.vote_view(post_id, q["options"], scoring.is_multi_category(q["category"])))
		await store.set_message_id(post_id, msg.id)
```

The two lines after the send block (`store.upsert_config(channel_id, last_post_ymd=…, last_post_at=now)` and the `log.info`) stay exactly as they are — only the embed/view construction changes.

(The old `result_embed` call in `_reveal` used the default title for the card edit — the final card is now `final_card_embed`; the fresh announcement keeps the "Yesterday's answer" title it already used.)

- [ ] **Step 4: Delete the dead code**

- `bot/quiz/store.py`: delete `record_reveal`, `record_answer`, `record_answer_multi`, `get_answer`.
- `bot/quiz/embeds.py`: delete `card_embed`, `card_view`, `question_embed`, `answer_view`.
- `bot/quiz/view.py`: delete `card_lines`, `question_lines`, `too_late_notice`, `already_answered_notice`.
- `tests/test_quiz_view.py`: delete the tests of deleted builders (`test_card_lines_*`, `test_question_lines_*`, and the notice assertions for the two deleted notices — `closed_notice` keeps its test).
- `bot/commands/quiz.py`: remove `"answer_window"` from `_INT_FIELDS`; remove `answer_window=180,` from `quiz_enable`'s upsert.
- `bot/context/slash/commands.py` line ~1102: description becomes `"quiz_hour|open_window|leaderboard_dow|leaderboard_hour|test_interval|min_difficulty"`.
- Grep-verify: `grep -rn "record_reveal\|record_answer\|get_answer\|answer_view\|question_embed\|card_embed\|card_view\|card_lines\|question_lines\|too_late_notice\|already_answered_notice\|answer_window" bot/ tests/` → the only legitimate survivors are `quiz_settings`'s `answer_window` column declaration in `bot/quiz/__init__.py` (the column stays; add a trailing comment `# dead since the poll era — column kept, never read`) and any unrelated names the pattern over-matches (e.g. `poll_card_lines` matches `card_lines` — read each hit, don't count them).

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -q && ruff check .`
Expected: all pass, ruff clean

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(quiz): resolve grades, pays and only then closes — the reveal era retires"
```

---

### Task 10: Docs — CLAUDE.md and the registry notes

**Files:**
- Modify: `CLAUDE.md` (the quiz section and the predictions section's economy sentence), `core/data_registry.py` (quiz table comments if any mention the reveal flow)

- [ ] **Step 1: Update CLAUDE.md**

In the quiz section: the quiz is now a public 24-hour poll (buttons on the card, changeable votes, live named tally, clock-gated); resolve order grade → pay → results → close with `_close_due` as the retry loop and a raise on any payment failure; gold via `gold.grant_quiz_reward` (50/10, `quiz:{post}:{user}` idem key, `post_id` NULL on quiz ledger rows); the reveal/speed era is gone (`answer_window` and `response_ms` are dead columns); `quiz:{post}:reveal` survives only as the transition converter. In the predictions section: `MATCH_REWARD` is 100.

- [ ] **Step 2: Verify and commit**

Run: `pytest tests/ -q && ruff check .`
Expected: all pass

```bash
git add CLAUDE.md core/data_registry.py
git commit -m "docs: the quiz poll era and the 100-gold faucet, recorded"
```

---

## Post-merge smoke checklist (manual, on the live test channel)

1. `/quiz post_now` (admin) — the card shows the question, options, `A`/`B`… buttons, the gold rule line.
2. Vote; card re-renders with your name. Vote differently; your name moves. Second account votes the other option; both names visible.
3. Multi day (`techgaps`): select two options; tally shows you under both.
4. On the pre-deploy open post: press the old Reveal button — the card converts to the poll.
5. `/quiz reveal_now` (admin) — card locks with ✅ marks; "Yesterday's answer" message names the winners and the gold total.
6. `/gold` on a voter — a "Quiz — correct answer" or "Quiz — played" entry.
7. Run the reconcile query — zero mismatched holders.
