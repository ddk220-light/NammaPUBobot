# AoE2 Unit Quiz — Design Spec

_Status: approved design, 2026-06-17. Implementation pending._

## 1. Goal

A recurring trivia game in Discord built on real AoE2:DE unit data. The bot posts
one quiz per day to a configured channel. A player taps **Reveal & start**, gets a
private (ephemeral) multiple-choice question with a personal 3-minute timer, and
locks in one answer. Each correct answer is worth 1 point. Once a week the bot posts
a leaderboard of who answered the most questions correctly.

Questions must be *interesting* — synergy/odd-one-out facts ("the only siege unit
with no melee armor"), not trivial cost lookups — and every correct answer must be
**provably correct**, computed from the unit database rather than guessed.

## 2. Non-goals / guardrails (do-no-harm)

This feature is **strictly additive and opt-in**, mirroring the lobby work:

- It lives in a new isolated `bot/quiz/` package plus an offline generator under
  `utils/quiz_gen/`. No existing match / civ / rating / lobby / reconcile flow is
  modified. With the quiz disabled (the default), the bot behaves byte-for-byte as
  it does today.
- The bot **never** reads the `aoe2_matchup` repo or its SQLite DBs at runtime. The
  only runtime inputs are the committed question pool JSON and MySQL.
- New MySQL tables only (`qc_quiz_*`), created via the existing `ensure_table`
  pattern at import. No schema change to any existing table.

## 3. Data source

The committed golden SQLite DBs in the sibling repo `D:\AI\aoe2_matchup\data\golden\`:

- `aoe2_units.db`
  - `units` (112) — canonical unit list: `slug`, `display_name`, `age_id`,
    `unit_type` (standard/naval/unique), `civ_id` (NULL = generic).
  - `unit_stats` (5,936) — per-civ stats: `hp`, `attack`, `melee_armor`,
    `pierce_armor`, costs, `unit_category` (military/siege/trash), and the two JSON
    maps that drive synergy questions:
    - `armors_json` — `{armor_class_id: armor_value}` the unit **belongs to**.
    - `attacks_json` — `{armor_class_id: bonus_damage}` the unit **deals bonus to**.
  - `armor_classes` (40) — id → name (e.g. 27 = Spearmen, 30 = Camels, 35 = Heroes
    & Kings). See `reference/armor-classes.md`.
- `aoe2_reference.db`
  - `ref_units` (972) — per-civ base **and** fully-upgraded stats (`base_*`,
    `final_*`), enabling "after all upgrades…" questions.
  - `ref_special_effects` (1,278) — named mechanics per unit (`property_name` ∈
    {bleed_dps, trample_radius, hp_regen, charge_*, pass_through_*, …}).
- `derived_data.db`
  - `advisor_recommendations` (5,618) — best counters per civ/opponent.
  - `battle_scores` (51,367) — unit power rankings.

## 4. Architecture — two decoupled halves

### 4.1 Offline generator (`utils/quiz_gen/`)

A standalone script (run by a human, like the other `utils/` analysis tools) that:

1. Opens the golden SQLite DBs read-only.
2. Runs the template engine to produce candidate questions.
3. **Verifies** each candidate's correct answer against the DB.
4. Writes a reviewable pool to `data/quiz_questions.json`, committed to this repo.

Each pool entry:

```json
{
  "id": "siege-no-melee-armor-0007",
  "category": "armor",
  "difficulty": "medium",
  "prompt": "Which siege unit has no melee armor at all?",
  "options": ["Battering Ram", "Mangonel", "Scorpion", "Siege Tower"],
  "correct_index": 2,
  "explanation": "The Scorpion's armors_json has no Base Melee (class 4) entry…",
  "source": "aoe2_units.unit_stats armors_json, unit_category='siege'"
}
```

Regeneration is a deliberate PR — a bad auto-generated question is caught in review,
never posted live. The pool can grow over time without touching the bot.

### 4.2 Template engine (DB-grounded)

A library of question *types*, each a query that yields a provably-correct answer
plus plausible **same-family** distractors (so the wrong options aren't obviously
wrong). Seed types:

| Type | Example | Source |
|------|---------|--------|
| Only-one / odd-one-out | "Which siege unit has no melee armor?" | `unit_stats.armors_json` |
| Bonus damage | "Which of these does bonus damage vs Camels?" | `unit_stats.attacks_json` |
| Superlative | "Fully upgraded, which has the highest pierce armor?" | `ref_units.final_*` |
| Mechanic owner | "Which is the only unit with bleed damage?" | `ref_special_effects` |
| Counter | "What's the best counter to X?" | `advisor_recommendations` |

Every candidate is re-verified against the DB before being written. Each question
carries `category` + `difficulty` so the daily picker rotates variety. Generic
(civ-agnostic) units are preferred for cross-civ facts; civ-specific questions are
allowed where the fact is inherently civ-bound (unique units).

### 4.3 Bot runtime (`bot/quiz/`)

A new isolated package consuming only the pool JSON + MySQL:

- `pool.py` — load + validate `data/quiz_questions.json` at import; pick an unused
  question (varying category) for the next daily post.
- `jobs.py` — `QuizJobs.think(frame_time)`, cadence-gated exactly like `StatsJobs`
  / `LobbyJobs`: (a) post the daily quiz at the configured hour, (b) close expired
  quizzes and edit in the answer, (c) post the weekly leaderboard on the configured
  day/hour. Bulletproof try/except so a failure never breaks the global tick.
- `view.py` — pure helpers for rendering the quiz card embed, the ephemeral
  question, and the leaderboard table.
- `interactions.py` — interaction routing + grading (see §6).
- `store.py` — async MySQL access (posts, answers, weekly tally).

## 5. Discord flow

1. **Daily post** — at `quiz_hour`, the bot posts a quiz **card** (category teaser +
   `Reveal & start` button; the answer is not shown) to `quiz_channel`.
2. **Reveal** — a player taps the button and receives an **ephemeral** message: the
   full question, four answer buttons, and a personal deadline of
   `now + quiz_answer_window` (default 180 s). The deadline is stored so the timer is
   authoritative server-side, not client trust.
3. **Answer** — the player taps one option. Recorded with `is_correct` and
   `response_ms`. One answer per person, no changes; late or duplicate taps get an
   ephemeral notice and are not recorded.
4. **Close** — the quiz stays revealable until `closes_at` (default: the next daily
   quiz, ~24 h). At close the bot edits the card to show the correct answer, the
   explanation, and a short roll of who got it right.

## 6. Restart safety

The bot redeploys on Railway, so no in-memory-only state:

- All quiz state (post, `question_id`, `correct_index`, options, per-user reveal
  deadlines, answers) lives in MySQL.
- Buttons route by `custom_id`: `quiz:{post_id}:reveal` and
  `quiz:{post_id}:ans:{i}`. Grading parses the `custom_id` and reads the DB — no
  reliance on a live `View` object.
- On `on_ready`, a persistent `View` is re-registered for each still-open post, so
  buttons keep working across a redeploy (same philosophy as the lobby watcher's
  rehydrate).
- The daily-post job claims the day's slot in the DB before sending (the
  `LobbyJobs` `_inflight` claim pattern) so a restart mid-tick can't double-post.

## 7. Data model (MySQL, `qc_` prefix, `ensure_table` at import)

- `qc_quiz_posts` — `id` (auto), `channel_id`, `message_id`, `question_id`,
  `category`, `correct_index`, `options_json`, `opened_at`, `closes_at`,
  `status` (open/closed), `explanation`.
- `qc_quiz_answers` — composite PK `(post_id, user_id)`; `nick`, `revealed_at`,
  `deadline_at` (= `revealed_at + quiz_answer_window`, the authoritative
  server-side window), `choice_index`, `is_correct`, `answered_at`, `response_ms`.
  A row is **created at reveal** (deadline set; `choice_index`/`is_correct`/
  `answered_at`/`response_ms` NULL) and **updated at answer**. The composite PK
  enforces one reveal+answer per person; a revealed-but-unanswered row is a valid
  state (player opened it, never locked in).
- `qc_quiz_config` — one row per channel: `channel_id` (PK), `enabled`,
  `quiz_hour`, `answer_window`, `open_window`, `leaderboard_dow`,
  `leaderboard_hour`, `min_difficulty`, `last_post_ymd` (date of the last daily
  post, to avoid double-posting), `last_leaderboard_ymd`. Self-contained to the
  quiz package — see §8.
- The weekly leaderboard is a `GROUP BY user_id` over `qc_quiz_answers` filtered to
  the trailing ISO week + channel — no extra table.

## 8. Scheduling, scoring, config

- Daily post and weekly leaderboard both ride the existing 1-second `think()` tick
  via `QuizJobs`, cadence-gated like `StatsJobs`.
- Scoring: **1 point per correct answer** within the player's window. The weekly
  leaderboard ranks by correct count (with answered count + accuracy shown).
- Config lives in the dedicated **`qc_quiz_config`** table (§7), one row per
  channel, managed entirely inside `bot/quiz/` — the lobby precedent (`qc_lobbies`
  has its own tables and never touched the core config). This keeps
  `queue_channel.py` and its CfgFactory **byte-for-byte untouched** (strongest
  do-no-harm). Admins configure via a `/quiz` admin subcommand group. Fields,
  with defaults applied in code: `enabled` (False), `quiz_hour`, `answer_window`
  (180 s), `open_window` (~24 h), `leaderboard_dow` + `leaderboard_hour`,
  `min_difficulty`. The quiz posts to the channel the row is keyed to.
- No `start.py` / `config.cfg` template changes are required — all quiz config is
  DB-resident, not environment-driven.

## 9. Commands

- `/quiz_leaderboard` — show the current week's standings on demand (public).
- Admin `/quiz_post` (subcommand group) — fire an extra quiz immediately, for
  testing and one-offs.
- Admin `/quiz_stats` (optional) — pool size, how many asked, last post.

## 10. Error handling & edge cases

- Empty / exhausted pool → skip posting + log; never raise inside the tick.
- Interaction after close → ephemeral "this quiz has closed."
- Duplicate / late answer → ephemeral notice, not recorded.
- Restart mid-quiz → re-registered views + DB state + claim guard; no double-post.
- Generator is offline and idempotent; pool regen is reviewable in a PR.

## 11. Testing

Pure-function pytest coverage in `tests/`, matching the existing style (conftest
stubs; no DB / nextcord at test time):

- Template engine: each generated question's `correct_index` matches the DB fact;
  distractors are same-family and distinct.
- Grading: correct/incorrect classification, one-answer enforcement, late-answer
  rejection.
- Weekly bucketing: ISO-week boundaries, per-user correct counts.
- Pool loader: schema validation, dedup, category rotation.

The generator is validated against the golden DBs offline; the bot is tested against
a tiny committed fixture pool.

## 12. Open items deferred to implementation

- Exact distractor-selection heuristic per template type (tuned during generation).
- Final default for `quiz_open_window` (24 h vs shorter) — config-driven, easy to
  change.
- Whether `/quiz_post` lives in the existing admin subcommand group or its own.
