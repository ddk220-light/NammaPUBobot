# Active-match recovery and atomic reporting

Implementation base: `79efb2b` (the deployed version reviewed on 2026-09-22).
Scope: the two reliability fixes approved by the owner. No Railway configuration,
new service, scheduled job, polling interval, replay feature, or capacity change.

## Plan

1. Gate snapshots and match ticks until restoration finishes. Treat a failed or
   invalid durable read as a startup failure, never as an empty database. Retain
   the local-file fallback only when MySQL successfully reports no saved row.
   Keep existing canonical payload comparison and independent retry baselines.
2. Restore the saved match ID, teams, check-in state, and report state. Reserve
   the counter above restored IDs without allocating replacement IDs. Check the
   existing result tables at startup so an old snapshot cannot resurrect a
   completed game; reject inconsistent saved/results data rather than erase it.
3. On graceful or supervised shutdown, stop incoming work, cancel/drain pending
   mutations, and attempt one bounded changed-state flush before closing MySQL.
   Never save an incompletely restored application. Abrupt process/host death
   still relies on the existing 30-second snapshot, not a new durability promise.
4. Put result insertion, roster, locked rating reads, rating updates and history
   in one transaction on one connection. Preserve the rating algorithm. Use
   deterministic player/channel order to reduce deadlocks. The existing unique
   match ID and validation of the stored outcome/roster/history make retries safe.
5. Serialize reports for a live match, retain it until commit, and run teardown,
   lifecycle handlers and Discord messages after commit. Notification failure
   cannot turn a committed result into a failed report or apply ratings again.
6. Exercise startup races, failures at individual write boundaries, cancellation,
   conflicting/duplicate reports, stale snapshots and shutdown. Run the existing
   lifecycle, persistence, adapter and cost-contract tests, then the full suite.

## Self-review before implementation

| Risk | Required mitigation / validation |
| --- | --- |
| Startup tick or signal replaces saved games with empty state | Guard every save entry point, not just the periodic caller; test before/during restoration. |
| Database outage mistaken for fresh installation | Propagate durable read/parse errors; only a successful missing-row read permits file fallback. |
| Partial restore silently drops inaccessible players/channels | Fail restoration and keep snapshots disabled; leave the durable source intact. |
| Old snapshot restores a committed match | Validate its stored roster and ranked history before skipping; incomplete legacy results require repair, not automatic rerating. |
| Lost commit acknowledgement or duplicate command | Check the same match ID and content on retry; no automatic replay of individual SQL statements. |
| Two reports overwrite each other's scores or shared ratings | Per-match command serialization plus row locks inside the database transaction. |
| Discord or feature handler fails after commit | Isolate those failures; remove the match only after database success. Settlement remains after commit. |
| Shutdown snapshots a half-mutated operation | Stop acceptance, cancel/drain tasks before snapshot; bounded cleanup, cancellation rollback. |
| Pool exhaustion or idle cost regression | Use only the transaction handle inside the transaction; keep pool size, idle closure and snapshot cadence unchanged. |
| Existing admin undo/manual rating tools | Preserve their behavior; no automatic retries of admin operations. Broader atomic admin tooling is outside this change. |

Source inspection confirms both defects and the existing transaction/cost controls.
The current lifecycle test requires teardown before persistence; that assertion
must change deliberately because failed reports must remain live. Feature
settlement must still follow the committed result. Tests currently fake MySQL;
the local Docker daemon is unavailable, so actual MySQL validation will be
attempted separately and any remaining limitation recorded honestly.

## Rollout and rollback

Implement and validate locally on `codex/match-recovery-and-atomic-reporting`.
No production database writes or deployment are part of this task. Prefer no
schema changes so rollback remains a code rollback; saved JSON additions must
be optional when reading older snapshots. Before a later deployment, avoid
overlapping bot replicas (the existing deployment uses one replica).

## Validation results

Implemented locally and self-reviewed on 2026-09-22.

- `python3.11 -m pytest -q tests --tb=short`: **2,145 passed**.
- `ruff check .`: passed.
- `git diff --check`: passed.
- Ranked and unranked reports: injected failure at every write boundary;
  verified no partial rows/ratings survive and the match remains reportable.
- Lost commit acknowledgement: retry produces no additional writes or rating
  changes; a different outcome is rejected. Draws, multigame scores, shared
  rating channels, cancellation and failed Discord notifications are covered.
- Startup: blocked snapshots before/during restore; missing, invalid and failed
  reads; incomplete legacy results; stale snapshots of completed games; original
  IDs, teams and ready players; backward-compatible older JSON.
- Shutdown: pending mutations finish cancellation before the snapshot, which
  precedes database closure; repeated shutdown requests schedule one cleanup.
- Existing lifecycle, replay-pause and idle-database contracts pass. No Railway,
  Docker, dependency or job configuration changes were made. Additional database
  checks occur on startup/reporting, not through a new background polling loop.

Final review also addressed overlapping ready callbacks, reuse of initialized
channels after partial restoration, counter repair racing restoration, report
versus substitution/cancellation races, and failed unranked completion being
removed after repeated tick errors. Those completions remain in the reporting
stage for an explicit retry. No rating algorithm or map-voting behavior changed.

The SQL outcome tests execute the production reporting and AoE2 rating code
against disposable in-memory SQLite transactions, with MySQL syntax adapted.
They prove rollback/data outcomes, **not MySQL row-lock semantics**. Existing and
extended adapter tests verify connection use, commit, rollback and cancellation
with driver fakes. A live MySQL/Docker integration run could not be performed:
Docker has no running daemon and only MySQL client tools are installed locally.
This is a remaining pre-deployment check, not a claim of production validation.

Operational limits remain explicit: abrupt kills can lose changes since the
last 30-second snapshot; an unavailable database can defeat the bounded shutdown
flush; a corrupt/incomplete snapshot keeps readiness off until repaired rather
than silently discarding games. Post-commit Discord messages are best effort;
their delivery is not part of the atomic SQL transaction. Existing admin undo
and bulk-rating workflows are unchanged and should not run concurrently with
reports during the later deployment smoke test.

No production database writes, push, or deployment were performed.
