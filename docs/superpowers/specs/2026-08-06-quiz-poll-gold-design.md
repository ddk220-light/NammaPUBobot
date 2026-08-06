# Quiz → public poll, with gold — and the 100-gold match reward

**Date:** 2026-08-06
**Status:** Approved in conversation (Deepak); this document is the contract.

Two changes, one economy. The match faucet pays 100 instead of 10, and the
daily quiz stops being a private timed test and becomes a public 24-hour poll
that pays gold — 50 for a correct answer, 10 for showing up — into the same
balance the betting feature spends.

## Decisions

| Question | Decision |
|---|---|
| Match reward per ranked match | **100** (was 10), same 500 balance ceiling |
| Quiz mechanics | Public poll: question + options visible to all immediately |
| Vote privacy | None — live tally with voter names on the card, changeable votes |
| Window | 24 hours (`open_window`, already defaulted to 86400) |
| Speed component | Dead. No reveal step, no per-user deadline, no `response_ms` |
| Correct answer pays | **50 total** (not 50 + 10) |
| Played-but-wrong pays | **10** |
| Cap | Same balance ceiling as matches: `min(reward, max(0, 500 − balance))` |
| Poll widget | Bot-rendered buttons + DB, **not** Discord native polls (nextcord 2.6.0 predates the polls API; vote state must live where gold can transact against it) |
| Weekly leaderboard | Survives unchanged — `scoring.tally` already ranks on correct count, never used speed |
| `quiz_answers` table | **Reused**, not replaced — the recorded fact ("user answered post, correctly or not") is unchanged; timing columns go NULL on new rows |

## 1. Match reward: 10 → 100

`bot/predictions/scoring.py`: `MATCH_REWARD = 100`. `reward_amount()` is
otherwise untouched — `min(100, max(0, 500 − balance))`. Still one idempotent
ledger row per (match, player) keyed `reward:{match_id}:{user_id}`; a player at
450 gets 50, at 500+ gets nothing. Tests that pin the constant update with it.

## 2. The quiz lifecycle, rebuilt

### Posting (unchanged cadence, new card)

The daily tick posts at `quiz_hour` exactly as today (`_maybe_post_daily` →
`_reveal_previous` → `_maybe_week_leaderboard` → `_post_question`). The card
changes: the **question and options are on it from the start**, options are
buttons (multi-answer `techgaps` questions get a select menu instead), and the
card shows a live tally. No Reveal button, no "private 3:00 timer" copy.

Card content while open:
- Header line (week/day/seq/category/difficulty/source tag — as today)
- The prompt and lettered options
- Per-option: vote count and voter names (display capped at 12 names per
  option, then "+K more")
- "Vote with the buttons — you can change your vote until it locks."
- Closes-in time and the gold rule: "Correct pays 50 🪙, playing pays 10 🪙."

### Voting

- `custom_id`s: `quiz:{post_id}:ans:{index}` (button) and
  `quiz:{post_id}:msel` (multi select) — the **same routes that exist today**.
  `quiz:{post_id}:reveal` disappears from the view but **stays in
  `parse_custom_id`, repurposed as the transition converter**: the one post
  open at deploy time still shows the old Reveal button, and pressing it
  re-renders that card in place into the poll format (idempotent, and the
  only way anyone can vote on it).
- **Message-id guard on the card edit**: old-era ephemeral answer views carry
  these same `ans:`/`msel:` routes but live on a different message — blindly
  `edit_message` would paint the shared card over someone's private
  ephemeral. A press whose `interaction.message.id` is not the post's
  `message_id` records the vote and answers with a plain ephemeral
  confirmation instead of the card edit.
- A press while `status == 'open'` **and** `now < closes_at` UPSERTs the
  user's vote row: PK `(post_id, user_id)` means one vote per user; pressing a
  different option **replaces** it. `answered_at` = time of the latest change.
  `is_correct` stays NULL until lock. `revealed_at`/`deadline_at`/
  `response_ms` stay NULL forever on new rows.
- The handler answers the press with `interaction.response.edit_message` on
  the card itself — the shared card re-renders with the new tally, which IS
  the feedback. No ephemeral confirmation. Two racing presses both land in
  the DB; the later render wins and is accurate; a stale render self-heals on
  the next press.
- A press at/after `closes_at` gets the ephemeral closed notice. **The gate
  is the clock, not the status flag** — grading runs strictly after
  `closes_at`, so there is no press/grade race window.
- Multi-answer: the select submission replaces the user's whole set
  (`choice_indices`, JSON-sorted as today). Grading stays exact-set
  (`grade_multi`). The tally counts a user under each option they picked.

### Lock → grade → pay → results

`_reveal(post, fresh)` is rewritten to resolve a poll. In order:

1. **Grade**: for every `quiz_answers` row of this post **that carries a
   choice**, compute `is_correct` (`grade`/`grade_multi` against the stored
   key) and write it. A row with NULL `choice_index` and NULL
   `choice_indices` is a **non-vote** — excluded from grading, the tally,
   and gold. (Such rows exist: the old flow's `record_reveal` created a row
   on Reveal, before any answer; the post open at deploy time will have
   them.) Deterministic — re-running produces identical rows.
2. **Pay**: resolve `community.community_for_channel(post.channel_id)`. If
   None: skip payment entirely, log, and omit gold lines from the results.
   Otherwise, per voter: `gold.ensure_seeded(...)` (a voter may never have
   touched gold), then `gold.grant_quiz_reward(...)` — see §3. Every payment
   is idem-keyed, so re-running pays nobody twice. **If any payment errors,
   the resolve raises after finishing the loop** — the post stays `open`
   and `_close_due` retries next tick, where the already-paid voters
   idem-key to no-ops. Closing past an unpaid voter would put their gold
   beyond the retry loop forever, the same debt-beyond-the-sweep mistake
   the betting spec forbids. The announced gold total is read back from the
   ledger (`SUM` over this post's `quiz:` idem keys), not accumulated in the
   loop, so a retry-after-partial-crash still announces the true figure.
3. **Results**: edit the original card — final tally, correct answer marked,
   components stripped. When `fresh` (the daily path), also send the
   "Yesterday's answer" message: correct answer, explanation, who got it
   right, and the gold summary ("N players earned gold — 50 🪙 correct /
   10 🪙 played").
4. **Close**: `store.close_post` flips `status='closed'` **last**.

Crash-safety needs no new machinery and no `terminal_intent`: a quiz post has
exactly **one** terminal branch. If the process dies anywhere in 1–3, the post
is still `open` past its `closes_at`, and the existing `_close_due` sweep
(which runs every tick regardless of enabled/schedule) re-enters `_reveal`
next tick: grading is deterministic, payments are idempotent, the card edit is
harmless to repeat. Money moves before the status flips — the same "money
first, terminal status last" rule the betting book follows.

`reveal_now` (admin `/quiz` subcommand) keeps working — it now locks, grades,
and pays immediately. `force_post` unchanged.

### What dies

- The Reveal button and `_handle_reveal`; `record_reveal`; the reveal route
  in `parse_custom_id`.
- The per-user 180-second deadline and every notice about it
  (`too_late_notice`, the "3:00 timer" card copy).
- `already_answered_notice` — changing your vote is the point now.
- `quiz_settings.answer_window` — the column stays (ensure_table never
  drops), the code stops reading it, `/quiz config` stops offering it.
- `response_ms` — recorded by nothing; column stays, always NULL on new rows.

### What survives untouched

- The two question banks and all schedule arithmetic
  (`slot_for_seq`/`source_for_day`, player-day fallback to the game queue).
- The weekly leaderboard: `scoring.tally` (correct desc, answered asc) and
  `_maybe_week_leaderboard`, verbatim.
- `daily_due`/`leaderboard_due`, `/quiz enable|disable|config|status`,
  `/quiz_leaderboard`.
- `closes_at = opened_at + open_window` already is the 24-hour window.
- `quiz_posts` gains ONE column: **`difficulty`** (nullable str). The card
  now re-renders from the post row on every vote, and difficulty was the
  only displayed field not stored (it rode in from the bank entry at post
  time). `create_post` writes it; old rows stay NULL and render without it.

## 3. Gold integration

All money moves through `bot/predictions/gold.py` — the sole-writer rule from
the betting spec holds; quiz code calls it, never touches the tables.

New pure rule in `bot/predictions/scoring.py`:

```
QUIZ_CORRECT_REWARD = 50
QUIZ_PLAYED_REWARD = 10

def quiz_reward_amount(balance, correct):
    return min(QUIZ_CORRECT_REWARD if correct else QUIZ_PLAYED_REWARD,
               max(0, REWARD_CEILING - balance))
```

New function in `gold.py`, mirroring `grant_match_reward` exactly:

```
async def grant_quiz_reward(community_id, user_id, quiz_post_id, correct, now):
    # one transaction: balance FOR UPDATE → quiz_reward_amount →
    # INSERT IGNORE gold_ledger (idem_key f"quiz:{quiz_post_id}:{user_id}",
    # entry_type "quiz_correct" | "quiz_played", post_id=NULL) →
    # conditional balance bump. Returns gold granted (0 if capped or already paid).
```

- **One ledger row per voter per quiz** — the amount depends on correctness,
  so the idem key is per (quiz post, user), not per entry type. A re-run
  after a crash hits the key and pays nothing.
- `gold_ledger.post_id` stays **NULL** on quiz rows — that column references
  prediction posts, and a quiz post id in it would be a foreign lie. The quiz
  post id travels in the idem key, which is auditable.
- `entry_type` set becomes: `seed | match_reward | bet | cancel | refund |
  payout | quiz_correct | quiz_played | admin_adjust`. `/gold` history labels
  (`view._ENTRY_LABELS`): "Quiz — correct answer" / "Quiz — played".
- The registry (`core/data_registry.py`) notes gain the new entry types; no
  new tables, so `tests/test_data_registry.py` is unaffected.

Economic note, stated so it is never mistaken for a bug: with the shared
balance ceiling, a player sitting at 500+ earns **nothing** from quiz or
matches until betting losses pull them under — the faucets are lifelines, not
income, and that is the design.

## 4. Copy and rendering (`bot/quiz/view.py`, `embeds.py`)

- `card_lines` → the open-poll card described in §2 (pure, testable).
- New `tally_lines(options, votes)` — per-option counts + capped names;
  used both live and (with the correct answer marked) in the final card.
- `result_lines` gains the gold summary and the voter breakdown.
- `card_view(post_id, options, multi)` — option buttons labeled **A–D**
  (the card body carries the full option text; letters dodge the 80-char
  button limit), or the select for multi; `timeout=None, auto_defer=False`,
  custom_ids as in §2 — the standing redeploy-safe pattern.
- Vote-count copy uses "vote(s)"; names joined with ", ".

## 5. Testing

Same standard as the betting branch — pure functions and fakes, mutation-aware:

- `quiz_reward_amount`: 50/10 split, ceiling clamp at every boundary
  (0, 450, 490, 499, 500, 501).
- `grant_quiz_reward`: idem-key no-op on second call, correct entry types,
  post_id NULL, balance bump matches ledger row (the reconcile invariant).
- Vote flow: UPSERT replaces a vote; multi replaces the set; press at
  `closes_at` refused; press on a closed post refused; the card edit is the
  interaction response.
- Resolve: grade→pay→close ordering pinned (a crash between pay and close
  leaves the post resumable; re-run pays nobody twice); no-community skips
  payment but still grades and closes.
- `parse_custom_id`: reveal route gone, ans/msel intact.
- Weekly tally: unchanged behaviour pinned across the transition (old rows
  with timing fields + new rows without, one board).

## Out of scope

- Alt-account/farming resistance (same posture as betting).
- Retroactive gold for quiz answers given before this ships.
- A nextcord upgrade / Discord native polls.
- Partial-credit grading for multi-answer questions (exact-set stays).
- Tidying old still-open posts at deploy time beyond what `_close_due`
  already does — they will grade and pay under the new rules, which is
  acceptable and idempotent.
