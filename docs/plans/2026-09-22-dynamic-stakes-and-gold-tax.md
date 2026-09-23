# Dynamic match stakes and weekly gold tax

Status: approved for implementation and production deployment. Implementation complete; deployment validation in progress.

## Scope and confirmed choices

The quiz and match betting features share the gold bank. These changes affect match betting and that shared balance; quiz rewards remain unchanged.

The user confirmed:

- A bet must remain placed until betting closes to count as participation. Placing and manually cancelling a bet does not qualify.
- Below 250 gold, stake buttons should shrink to affordable amounts.

Keep the existing seed, quiz/match rewards, payout formula, cancellation ability, own-team restriction, and community isolation. Add no Railway service, replica, paid dependency, or frequent database polling.

## 1. Calculate personal stake options

Use the player's current available gold, `B`, and an upper-inclusive 500-gold band:

```python
band_ceiling = 500 * max(1, (B + 499) // 500)
normal_options = (band_ceiling // 10, band_ceiling // 4, band_ceiling // 2)
```

Exactly 500 stays in the 500 band; 501 enters the 1,000 band. This is a proposed boundary convention, resolving the initial “less than” wording while keeping the example of 500 gold giving 50 / 125 / 250.

| Available gold | Buttons |
| --- | --- |
| 250–500 | 50 / 125 / 250 |
| 501–1,000 | 100 / 250 / 500 |
| 1,001–1,500 | 150 / 375 / 750 |
| 1,501–2,000 | 200 / 500 / 1,000 |
| 9,501–10,000 | 1,000 / 2,500 / 5,000 |

For low balances, shrink the reference amount just enough that the largest option is affordable:

```python
if B < 10:
    return ()
reference = min(band_ceiling, 2 * B)
options = tuple(sorted({
    min(B, max(10, reference // divisor))
    for divisor in (10, 4, 2)
}))
```

Examples: 100 gold gives 20 / 50 / 100; 50 gives 10 / 25 / 50; 20 gives 10 / 20; 10 gives one 10-gold button. Keep the existing 10-gold minimum, round down to whole gold, and remove duplicate choices. Below 10, explain that quiz/match rewards can replenish the balance. Three distinct affordable choices are not possible for every small balance.

No stored band table, per-player tier, or scheduled tier recalculation is needed. Use one pure function for rendering and validation.

### Personal buttons and safe placement

1. The public match card offers a team choice. A click opens a private response with that player's balance and actual stake amounts.
2. Bind the chooser to the initiating user and post; resolve the community from the post, never from client-provided data.
3. On a stake click, lock the post and wallet, recalculate valid options, and recheck funds and betting status. Keep the existing roster/own-team checks.
4. Charge exactly the displayed amount only if it is still valid. Otherwise refresh the choices with no debit. Never silently substitute a larger amount after a reward, payout, tax, or other bet changes the wallet.
5. Use the existing ledger's unique `idem_key` for one successful placement per chooser: `bet:{community}:{user}:{chooser_id}`. The chooser ID can come from the opening Discord interaction. Check an already-committed key before treating a retry as a stale quote. A deliberate additional bet opens a fresh chooser.
6. Keep restart-safe component routing. Old public `bet:...` buttons open the new chooser; they must not continue accepting a rich player's legacy 10-gold stake. There is no need to rewrite historical Discord messages.

These are limits per placement. Existing same-side top-ups remain possible, with options recalculated from the remaining balance. There is no new aggregate per-match limit or custom amount input.

## 2. Apply a weekly inactivity tax

### Proposed rules

- Scope: existing gold holders in an explicitly enabled community, across all of its match betting channels. Quiz participation alone does not count.
- Schedule: Thursday in `Asia/Kolkata`, matching the community's existing dashboard convention. Proposed time: the Thursday daily quiz posting time, to share an existing database wake. Read the actual configuration during implementation; the code's default is 09:00 UTC / 14:30 IST. Persist the chosen tax time so later quiz configuration changes do not silently move the tax boundary.
- Each scheduled cutoff `T` uses the fixed interval `[T - 7 days, T)`. A bet qualifies in the week **betting closes**, not when the result arrives. An open bet at Thursday's cutoff can qualify in the next week when it closes; it does not receive an early exemption.
- A retained stake on a closed book qualifies whether it wins or loses. Automatic refunds for a one-sided pot or a bot-voided match also qualify: the player did not manually withdraw. A manual cancellation before closure does not qualify.
- An eligible inactive holder loses `min(available_balance // 10, max(0, available_balance - 500))`, computed under the wallet lock when the assessment runs. Balances at or below 500 are exempt, and tax never reduces a higher balance below 500 (updated September 23). The ledger records the negative amount as `inactivity_tax`; it is not added to a betting pot.
- Tax is rounded down to whole gold and capped to preserve the 500-gold floor. For example, 10,000 idle gold becomes 6,561 after four consecutive weekly assessments, assuming no other movements.
- Proposed fairness safeguards: at least seven days of grace after both feature activation and the holder's initial seed; skip a community's assessment if no betting opportunity existed during that week. An opportunity is a positive-duration open betting window overlapping the week, not merely a post created within it.
- After an outage, assess only the latest due week, never stack several missed weeks of surprise deductions. Keep its scheduled activity window; use the available balance at actual assessment time. Record completion even when nobody owes tax.

The timezone, closure-based week assignment, grace period, quiet-week exemption, and outage behavior are recommendations accepted with the approval to implement this plan.

### Existing records are enough for participation

Closed `prediction_bets` rows are retained; manual cancellation deletes them while the book is open. The post's `freezes_at` records its actual closure and remains stable. Query distinct retained bettors on posts closing inside the weekly window, joined to their community channels. This handles automatic refunds without mistaking a cancelled placement's permanent ledger entry for participation.

Do not reuse `store.bet_activity_by_user()` for tax eligibility: that function intentionally counts even subsequently cancelled placements for the existing leaderboard. Leave that leaderboard behavior alone.

No per-player “last bet” cache or additional write on every placement is needed. Validate the weekly query with `EXPLAIN`; add a targeted index only if the plan needs it. Avoid one activity query per holder and unbounded Python reads of ledger history.

### Minimal durable tax state

- One small policy row per enabled community: activation time, schedule, enabled state. Keep it in a betting-owned table rather than adding fields to the privacy policy setter, which currently replaces its entire row.
- One run row per community and scheduled cutoff, uniquely keyed by those values. It records completion and aggregate counts/amounts, including skipped weeks.
- One ordinary ledger entry per nonzero tax deduction, with a unique key such as `tax:{community}:{user}:{cutoff}`. Reuse the wallet and append-only ledger; no second balance system.

For the current small community, use one short transaction per community/week:

1. Atomically create/claim and exclusively lock the run row. A completed run exits immediately. Use the established safe upsert pattern, not a missing-row `SELECT FOR UPDATE` as a mutex.
2. Lock existing community wallet rows in deterministic user order, then read the qualification/grace data using a consistent assessment snapshot. Do not lock prediction posts after locking wallets; placements already lock posts before wallets.
3. Calculate deductions, insert their ledger rows, and update the corresponding wallets. Mark the run completed in the same transaction.
4. Commit everything together. A crash before commit changes nothing; a lost acknowledgement or overlapping deployment can retry without a second charge.

Keep all gold movements in `gold.py`. No Discord calls inside the transaction. Exercise concurrency against the existing reward, refund, placement, and cancellation lock orders in real MySQL; use bounded whole-transaction retry for a deadlock, with the same run ID. If measured duration makes the community-wide transaction unsuitable, revise to resumable batches before release rather than silently introducing partial assessments.

### Scheduling and presentation

Extend the existing betting scheduler with an independent cached tax deadline. The scheduler's next wake is the earlier of its existing recovery/freeze deadline and the tax deadline. A match opening must not reset the tax deadline or rerun its database query.

Load policy/run state during startup recovery, calculate future deadlines in memory, and touch the database for tax only when due. Use bounded retry/backoff after failure, not the active-betting 15-second cadence. Keep tax failures isolated from match recovery and settlement. Align the Thursday batch with the daily quiz wake where configuration permits.

Show “Weekly inactivity tax” in the existing personal gold history and document the rule in betting help. Use one aggregate operational log per run; no per-player DMs, extra announcements, or leaderboard refresh loop.

## Design review: changes and remaining limits

| Finding | Decision |
| --- | --- |
| Public stake buttons cannot vary by viewer. | Choose a team, then show personal amounts privately. |
| Low gold could leave a player unable to participate. | Shrink options; retain the 10-gold minimum and deduplicate tiny choices. |
| A cancelled placement remains in the ledger. | Use retained stakes at closure, as confirmed by the user. |
| A payout or another click can change the wallet after display. | Revalidate the exact displayed amount under lock; make each chooser single-use. |
| Restarts and overlapping deployments can repeat Thursday work. | Persist a unique weekly run and debit ledger/wallet atomically. |
| A new periodic query could undo Railway idle savings. | Cache the deadline; one weekly batch on the existing process, aligned with an existing wake. |

Two product limits should remain visible before implementation:

1. **Band percentages are not percentages of the exact balance.** At 501 gold, the largest option is 500. This follows the requested band formula. If a strict “never more than half my wallet per click” limit is intended, use percentages of actual balance instead; that is a different rule. Repeated top-ups can also commit more than one button's maximum.
2. **The proposed tax covers available gold, not stakes already in open pots.** An inactive player could park gold in an open bet over the Thursday cutoff and cancel later, reducing that week's taxable balance without earning an exemption. Requiring bets to survive closure prevents the simple cancel-for-exemption loophole, but does not eliminate this separate tax-base loophole. Recommendation for a small first release: document this limitation rather than silently changing cancellation or adding tax debts. If preventing this maneuver is essential, revise the plan to include escrow/deferred-tax accounting and review settlement/refund behavior before implementing the tax. Do not describe this draft as impossible to evade.

One qualifying bet exempts the entire week. This encourages participation and scales the buttons for wealthier players; it does not guarantee larger pots or eliminate all hoarding. Automatic refunds can qualify without the bettor ultimately risking a loss, intentionally.

No additional Railway capacity is needed. Formula calculation adds negligible CPU, but the private chooser adds a balance read and the weekly batch performs database work and ledger writes. A literally unchanged metered bill cannot be guaranteed; avoiding new services and extra idle polling is the cost constraint this design can enforce.

## Implementation sequence and acceptance checks

1. **Pure rules:** add dynamic stake and tax/date helpers to `features/betting/scoring.py` or a small pure policy module. Cover 0/9/10, low-balance deduplication, 249/250, 499/500/501, 999/1,000/1,001, large integer balances, integer tax rounding, and exact weekly boundaries.
2. **Personal chooser:** update `embeds.py`, `interactions.py`, `gold.py`, and help in `view.py`. Test old cards, user/community binding, malformed IDs, stale amounts, duplicate delivery, simultaneous bets, deliberate top-ups, insufficient funds, own-team restrictions, closure races, and a committed bet followed by a failed Discord response.
3. **Weekly assessment:** declare the policy/run tables in betting `__init__.py`; add a focused tax module for scheduling/eligibility and bank methods in `gold.py`. Test retained vs manually cancelled bets, automatic refunds, all community channels, two-community isolation, open bets spanning Thursday, fresh holders, no-op weeks, and downtime catch-up.
4. **Real transaction checks:** extend the disposable MySQL test harness with the betting schema. Inject a failure after each tax write, retry a lost commit acknowledgement, run two assessors concurrently, and race tax with placement/cancellation/reward/payout. Check nonnegative wallets and equality with ledger sums after every outcome.
5. **Cost and regression checks:** prove no tax database calls before the cached deadline, no extra tax calls when a match arms the scheduler, bounded failures, and no change to ordinary idle cadence. Run targeted betting/quiz tests, the full suite, `ruff check .`, MySQL integration checks, and a Docker build before deployment.
6. **Rollout:** deploy additive schema with tax disabled; enable the intended community with a persisted activation date and a full seven-day grace. Validate one assessment in a read-only preview before enabling deductions. Disabling tax stops future runs; any correction uses an idempotent compensating ledger credit, never deletion of financial history. Existing stakes keep their original amounts and payout behavior.

## Validation performed for this plan

Inspected the actual staking, cancellation, ledger, schema, scheduler, quiz reward, and leaderboard paths. A standalone Python check (without importing the application or connecting to a database) verified the proposed formula for every balance from 0 through 100,000, 17 explicit expected examples/boundaries, two large integer balances, tax bounds, and a seven-day Thursday window. All checks passed.

These are design/arithmetic checks, not implementation or production validation. Concurrency, query plans, the actual configured quiz hour, and the open-stake tax limitation still need the treatment described above.

## Implementation notes

The tax scheduler loads its policy at startup and on due assessments. With no
enabled policy it performs no further tax queries; enabling a policy requires
a bot restart. The operational command `python -m scripts.gold_tax --community ID`
previews eligibility without changing gold; `--enable` saves the policy and its
activation date. A separate `--disable` stops future deductions, including when
a running bot still has an old cached deadline.

Stake interactions acknowledge Discord before database access so a sleeping
MySQL service can wake without the interaction timing out.
