# Gold betting on match predictions — design

**Date:** 2026-08-05
**Status:** Approved (brainstorm with Deepak, this session)

## Summary

Replace the free 🔵/🔴 prediction votes with **pari-mutuel gold betting**. Spectators
stake a virtual currency (gold) on either team from buttons on the match card;
winners split the whole pot in proportion to their stakes. Gold is seeded once
per person (500), regenerates slowly through playing (capped so playing alone can
never push a balance above 500), and every movement of gold is a row in an
append-only ledger.

The existing prediction lifecycle (`open → freeze → resolve/void` in
`bot/predictions/`) stays; betting rides the same jobs, posts, and embeds.
Placing a bet **is** the user's prediction, so the accuracy leaderboard
continues, fed by bets (historical votes still count).

## Decisions locked during brainstorm

| Question | Decision |
|---|---|
| Betting model | Pari-mutuel (pool) — no fixed odds, no house risk |
| Who can bet | **Spectators only** — match participants cannot bet at all |
| Relation to free votes | **Betting replaces votes** — one system; bet = prediction |
| Input mechanism | **Six buttons** on the match card (🔵 10/50/100, 🔴 10/50/100) |
| Seeding | **Bulk grant at launch** + same idempotent mechanism lazily for newcomers |
| Storage | **Append-only ledger** + transactional balance cache |
| Faucet cap | Match rewards only top a balance **up toward 500**, never above |

## 1. Player-facing rules

- When a prediction post opens (teams formed), the match card carries six
  buttons: 🔵 10 / 50 / 100 and 🔴 10 / 50 / 100. Betting closes exactly when
  vote freezing happens today (`freezes_at`; `VOTE_WINDOW` unchanged).
- **Spectators only.** A press by anyone on either team roster is rejected with
  an ephemeral "players can't bet on their own match."
- **Presses are additive** (10 then 50 = 60 staked). The **first press locks the
  user's side** for that match: presses on the other side get an ephemeral
  "you're on Alpha this match." No switching, no cancels (tote rules).
- Every successful press returns an **ephemeral confirmation**: stake, side,
  both pools, and the user's new balance. Insufficient gold → ephemeral error
  with current balance.
- The embed shows both pools and the implied payout multiplier per side,
  updated on each successful bet.
- **Payout (pari-mutuel):** winners split the entire pot (winning + losing
  pool). `payout_i = floor(stake_i × total_pool / winning_pool)`. Because
  `total_pool ≥ winning_pool`, every winner receives at least their stake back.
  The flooring remainder (`total_pool − Σ payouts`) is **burned** — never
  minted back.
- **One-sided pools:** if either pool is empty at freeze, the post becomes
  `no_action` and all stakes are refunded immediately at freeze time.
- **Voids** (roster change, match cancelled, no win/loss reported, draw):
  full refund of each bettor's total stake, exactly once. The roster-change
  flow refunds the old post before re-opening a fresh one.
- **Post-match betting report** (added mid-brainstorm): when the match
  resolves, the result embed is a full report — the pot and per-side pools,
  every winner with their stake and payout (net gain shown), and every loser
  with their stake and side. Both lists capped at `MAX_NAMED_WINNERS` with a
  "+N more" overflow line, so the embed cannot blow past Discord's limits.

## 2. Economy

- **Seed:** 500 gold, once per (community, user), ever. At launch, a one-off
  bulk run seeds every user with a `player_ratings` row in each community.
  After launch, the same idempotent grant runs lazily the first time a user
  touches gold (bet press or `/gold`). Two invocations of one mechanism, not
  two mechanisms.
- **Faucet:** when a match resolves with a real win/loss, each participant
  (both teams) receives `min(10, max(0, 500 − balance))` — i.e. playing
  regenerates a depleted balance toward 500 and does nothing at or above 500.
  Balances ≥ 500 write **no ledger row** (no zero-amount rows). Voided matches
  pay nothing.
- **Consequence:** total supply is hard-bounded at 500 × seeded users. Gold
  above 500 can only be won from other bettors. There is no inflation to
  manage.

## 3. Data model

Three new tables (declared via the adapter's `ensure_table`, registered in
`core/data_registry.py`):

### `gold_ledger` — append-only, never UPDATEd, never DELETEd
| column | type | notes |
|---|---|---|
| `id` | bigint auto PK | |
| `community_id` | bigint | tenancy key |
| `user_id` | bigint | |
| `entry_type` | varchar | `seed` \| `match_reward` \| `bet` \| `refund` \| `payout` \| `admin_adjust` |
| `amount` | bigint signed | negative for `bet`; positive otherwise |
| `match_id` | bigint NULL | set for `match_reward` |
| `post_id` | bigint NULL | prediction post; set for `bet`/`refund`/`payout` |
| `created_at` | bigint | epoch seconds |
| `idem_key` | varchar NULL, UNIQUE | see below; NULL for `bet` rows (MySQL unique ignores NULLs) |

Idempotency keys make double-application impossible at the schema level:
- seed: `seed:{community_id}:{user_id}`
- match_reward: `reward:{match_id}:{user_id}`
- refund: `refund:{post_id}:{user_id}`
- payout: `payout:{post_id}:{user_id}`
- bet: `idem_key = NULL` (one row per press, many allowed)

### `gold_balances` — spendable cache, always written in the same transaction as its ledger row
| column | type | notes |
|---|---|---|
| `community_id` | bigint PK part | |
| `user_id` | bigint PK part | |
| `balance` | bigint | invariant: `balance == SUM(gold_ledger.amount)` for the pair |
| `updated_at` | bigint | |

Spending is atomic:
`UPDATE gold_balances SET balance = balance − :stake WHERE community_id=… AND user_id=… AND balance ≥ :stake`
— zero rows affected means insufficient gold; the ledger `bet` row and the
`prediction_bets` upsert commit or roll back with it.

A reconcile utility (test + callable check) asserts the invariant for every
row; any drift is a bug, and the ledger is the truth.

### `prediction_bets` — per-match aggregate the pools, payouts and leaderboard read
| column | type | notes |
|---|---|---|
| `post_id` | bigint PK part | FK-style reference to `prediction_posts.id` |
| `user_id` | bigint PK part | one row per bettor per post — enforces the side lock |
| `side` | tinyint (0/1) | fixed by first press |
| `stake` | bigint | accumulated across presses |
| `updated_at` | bigint | |

`prediction_votes` stops being written. The table and its rows stay, read-only,
so the accuracy leaderboard keeps its history.

### Registry entries
All three: `layer="core"`, `retention="forever"`. Writers: a new
`bot/predictions/gold.py` owns `gold_ledger` and `gold_balances` (the only
module that writes gold); `bot/predictions/store.py` continues to own the
post-keyed tables and gains `prediction_bets`. Tenancy: `community` for `gold_ledger`/`gold_balances`,
`channel` for `prediction_bets` (it hangs off channel-tenanted
`prediction_posts`, matching `prediction_votes`).

## 4. Prerequisite: adapter transactions

`core/DBAdapters/mysql.py` is autocommit-only. Add a `transaction()` async
context manager to the adapter: acquires one pooled connection, `BEGIN`,
yields a connection-bound handle exposing the existing helper surface
(`execute`, `insert`, `update`, …), `COMMIT` on success / `ROLLBACK` on any
exception, connection returned to the pool either way. Every multi-row gold
movement (bet press, refund sweep, payout sweep, seed) runs inside one.

This is core infrastructure, built once, properly — it is what makes
"ledger row + balance update, atomically" true.

## 5. Lifecycle integration (`bot/predictions/`)

- **open_for_match:** unchanged post creation; attach a persistent nextcord
  `View` (six buttons, `timeout=None`, stable `custom_id`s carrying post id,
  side, stake tier).
- **on_ready:** re-register views for all posts still `open`, so buttons
  survive restarts. All money state lives in the DB — `saved_state.json` is
  not involved.
- **button press:** lazy-seed if needed → roster check → side-lock check →
  one transaction: conditional balance decrement, `bet` ledger row,
  `prediction_bets` upsert → ephemeral confirm → embed edit (pools +
  multipliers).
- **freeze:** disable buttons (message edit). If either pool is empty →
  status `no_action`, refund every bettor immediately (idempotent refund
  rows), embed notes "one-sided — stakes refunded."
- **resolve:** compute payouts from `prediction_bets`, write idempotent
  `payout` ledger rows + balance updates (one transaction per bettor, so a
  crash mid-sweep resumes where it stopped), edit embed with the payout
  roll-call (existing `MAX_NAMED_WINNERS` cap), then grant the participant
  faucet (idempotent `match_reward` rows).
- **void:** idempotent refund of each bettor's total stake, embed note.
  Existing void reasons (roster change, cancellation, no result) all route
  here.
- **Crash safety:** every money movement is an idempotent insert keyed by
  `idem_key`; re-running a half-finished sweep skips rows that already exist.
  Duplicate-key on insert = already applied = skip silently.

## 6. Commands

- **`/gold`** — own balance + recent ledger lines, ephemeral. First use
  triggers lazy seeding.
- **`/gold_top`** — richest players in the community, excluding
  `player_ratings.is_hidden`, same presentation conventions as `/eapm`.
- The prediction **accuracy leaderboard** now reads the union of historical
  `prediction_votes` and new `prediction_bets` (side = vote).
- `admin_adjust` exists as a ledger entry type for manual corrections; **no
  admin command in v1** (a correction is a hand-written idempotent row).

## 7. Testing

Same pytest style as the existing suite; payout math is pure functions:

- Proportional split, flooring, burned remainder; every winner ≥ stake back.
- One-sided pool → no_action refund; empty both sides → nothing to do.
- Faucet boundary: balance 480 → +10; 496 → +4; 500 → nothing; 620 → nothing.
- Idempotency: applying resolve/void/seed/reward twice changes nothing
  (unique `idem_key` collision → skip).
- Side lock: second-side press rejected; additive same-side accumulates.
- Spectator exclusion: roster member rejected on either side.
- Ledger/balance invariant: reconcile check passes after every simulated flow.
- Registry: new tables declared + registered (existing
  `tests/test_data_registry.py` enforces the two-way set match).

## Implementation deviations

- **§5 button wiring.** The spec called for a persistent nextcord `View` with
  per-button callbacks, re-registered for every still-`open` post in
  `on_ready` so presses survive a restart. The implementation instead follows
  the existing quiz pattern: `bet_view()` builds a `ui.View(timeout=None,
  auto_defer=False)` whose buttons carry no per-View callback at all, and
  every press routes through one global `on_bet_interaction` handler hung off
  `bot/events.py`'s `on_interaction` (`bot/predictions/interactions.py`),
  keyed entirely on `custom_id` and DB state (`prediction_bets`,
  `prediction_posts.status`/`freezes_at`). There is nothing to re-register on
  `on_ready` — no in-memory View survives or needs to survive a redeploy — so
  that step of §5 does not exist in the code. Chosen for consistency with
  `bot/quiz/`'s already-proven redeploy-safe router rather than maintaining
  two different button-wiring styles in the same codebase.

## Out of scope (v1)

- Gold sinks, cosmetics, or anything to spend gold on besides betting.
- Variable/custom stake amounts (`/bet alpha 37`) — the six tiers only.
- Admin grant/adjust command.
- Cross-community gold; balances are per-community by design.
- Any change to prediction timing (`VOTE_WINDOW`, freeze rules).

---

## Amendment 1 — players back themselves, and bets can be cancelled

**Date:** 2026-08-05, after the original ten tasks shipped.
**Status:** Approved (Deepak, same session).

Two additions requested once the feature was working end to end. Both are
additive; nothing already built is thrown away.

### A. Match participants may bet — on their own team only

**Supersedes the "Who can bet: Spectators only" row in the Decisions table
above, and the "Spectators only" bullet in §1.**

- A player in the match **may** bet, but **only on their own team**. A press on
  the opposing side is refused with an ephemeral "You can only bet on
  yourself." A player is never permitted to hold a position against themselves.
- Spectators are unchanged — either side.
- Self-bets go into the **same pool**. No separate pot, no adjusted odds. A
  player backing themselves is ordinary money in the tote, and that is exactly
  the point: when the audience piles onto one team, the other team can answer
  with their own gold instead of watching the book run one-sided.
- **Incentive safety is preserved and is the reason this is acceptable at
  all:** because a participant can never take the opposing side, no participant
  can ever profit by losing. This is strictly safer than "anyone, any side",
  which was rejected during the original brainstorm for exactly that reason.

**The roster must be captured at bet time, not recomputed at report time.**
`flow._player_ids()` reads `bot.active_matches`, which is in-memory, and the
match is gone from it by the time the result is reported. So `prediction_bets`
gains an `is_player` column, written when the bet is placed. This is not an
optimisation — without it the report below is impossible to render at all.

**Trap for the implementer:** `Match.teams` has **three** entries —
`teams[0]`, `teams[1]`, and `teams[2]`, a pseudo-team named `"unpicked"` with
`idx=-1`. The own-team check must index `teams[0]` and `teams[1]` explicitly
and must never iterate `match.teams`, or unpicked players become a third side.

**Report.** The post-match betting report annotates self-bettors inline in the
winner and loser lists it already builds. Those lists are already split by
outcome, so the annotation directly answers "who backed themselves and won"
and "who backed themselves and lost" without a second pass or a separate
section.

**Accepted consequence:** participants now appear on the prediction accuracy
leaderboard, so that board begins to blend reading matches with winning them.
Judged acceptable; noted here so it is not later mistaken for a bug.

### B. Cancelling a bet

- A bettor may cancel while the post is still `open` and `now < freezes_at` —
  **the same ten-minute window, unchanged**. After the freeze there are no
  cancels, because the book is settled against locked pools.
- Cancel returns the bettor's **entire** stake for that match (every
  accumulated press), deletes the `prediction_bets` row, and thereby releases
  the side lock — after cancelling they may bet again, either side (subject to
  the own-team rule if they are playing). Partial cancel and undo-last-press
  are explicitly out of scope: they need a per-press history the schema does
  not keep, and "back out" means out.
- The affordance is a **Cancel my bet** button on the ephemeral confirmation
  that every successful press already returns. Pressing it rewrites that same
  private message into "Bet cancelled — N gold returned" and strips the button.
- **Known wart, accepted:** N presses leave N private confirmations, each
  carrying a button. After one cancel the others report "you have no bet on
  this match to cancel." Correct behaviour; tidying it would mean tracking and
  editing every prior ephemeral message, which is not worth the machinery.

**Idempotency without an idem_key — the load-bearing design decision.**
Every other credit in this system (seed, match_reward, refund, payout) is made
exactly-once by a UNIQUE `idem_key`. That mechanism **cannot** be used here: a
user may bet → cancel → bet → cancel on a single post, so
`cancel:{post_id}:{user_id}` is not unique and the second cancel would be
silently swallowed as "already applied".

Instead **the `prediction_bets` row itself is the refund token.** In one
transaction:

1. `DELETE FROM prediction_bets WHERE post_id=%s AND user_id=%s`
2. if the delete affected **0 rows** → there is nothing to refund; return
   without touching gold
3. otherwise credit `gold_balances` by the deleted row's stake and append a
   `cancel` ledger row (positive amount, `idem_key = NULL`)

The DELETE's rowcount is the exactly-once guard: a double press finds no row
and refunds nothing. This is the same discipline `place_bet` already uses for
its conditional balance decrement — the conditional write IS the guard — and
it needs no new key scheme.

**New ledger `entry_type`:** `cancel`. The set becomes
`seed | match_reward | bet | cancel | refund | payout | admin_adjust`.

### Schema delta

| table | change |
|---|---|
| `prediction_bets` | **+ `is_player`** TINYINT(1), default 0, written at press time |
| `gold_ledger` | no schema change; `entry_type` gains the value `cancel` |

`ensure_table` adds missing columns to existing tables, so the new column
needs no migration.
