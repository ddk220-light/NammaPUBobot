# Implementation verification

Verified on 2026-10-28 UTC in the `codex/rts-connectors` worktree. The source baseline and the limits of the staging comparison are recorded in [baseline.md](baseline.md).

| Check                                      | Result                                                                                            |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| Baseline before implementation             | 863 tests, both type checks, web build and Worker dry-run passed                                  |
| Final `npm run check`                      | 1,168 tests across 43 files passed; server/web type checks, web build and Worker dry-run passed   |
| Legacy migration converter                 | 6 tests passed                                                                                    |
| Economy migration converter                | 5 tests passed                                                                                    |
| Browser smoke check                        | Mocked organizer settings, capability switches, member account-link page and mobile layout passed |
| Final read-only provider probes, 20:00 UTC | 30 passed, 0 failed, 14 access-blocked, 26 unsupported                                            |

The offline checks execute the real SQLite Durable Objects with mocked providers. They cover account trust and old backups, first-time linking without rating participation, all ten manual workflows, stale and duplicate events, substitution/unlink, cancellation/rematches, restart, community isolation, captain reporting, prediction settlement, missing timestamps, native identifier continuity, expired replay references, capability switches, and authentication callbacks. Provider fixtures preserve wide IDs and unknown factions; failures remain distinct from successful empty responses.

The browser smoke script is `scripts/connector-ui-smoke.mjs`. Run it against a local Vite server with `CONNECTOR_UI_BASE_URL` set to that server's origin. Its API and authentication requests are mocked; it does not sign in to a provider or modify a real community.

The [live probe report](evidence/2026-10-28-provider-check.json) records each operation separately. The public checks cover AoE2, AoE3 DE, AoE4, AoM Retold and BAR metadata. Missing GeneralsOnline/Battle.net credentials and interactive ownership checks are access-blocked, not passing tests. No replay bytes are retrieved.

Controlled 1v1/team games and actual provider sign-ins were not performed. All automatic observers, including updated AoE2, remain gated pending the [acceptance scenarios](acceptance.md). SC2 and manual-only games have no fabricated lobby feed. This branch does not deploy or register Discord commands; the deployment workflow requires an explicit dispatch after its checks pass.

## Final scope and Warcraft implementation — 2026-10-29

The branch now selects seven targets: AoE2 DE, AoE3 DE, AoE4, AoM Retold, BAR, access-gated Zero Hour and Warcraft III/W3Champions. Excluded catalog entries remain readable for legacy accounts/history but cannot be newly configured. Public browsing is independent of automatic observation approval and honors provider/capability switches.

| Check                              | Result                                                                                                                                                      |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Final `npm run check`              | **1,332 tests across 50 files passed**, both server/web type checks, web build and Worker dry-run build                                                     |
| Migration converters               | **6 legacy and 5 economy tests passed**                                                                                                                     |
| Browser smoke                      | Mocked member Steam sign-in, WC3 running view, final new-game selection, credentials diagnostics, independent settings persistence and mobile layout passed |
| Current read-only provider command | **39 passed, 0 failed, 10 access-blocked, 7 unsupported**                                                                                                   |

The fresh [per-operation live report](evidence/2026-10-29-provider-check.json) was checked at 2026-10-29T00:26:43Z. Missing GeneralsOnline access and interactive identity sign-ins are blocked rather than passing. AoE3/AoM general running discovery and WC3 waiting discovery remain unsupported. Repeated anonymous public reads share a bounded snapshot within the command; credentialed responses are not shared. No replay bytes were fetched.

Warcraft fixtures preserve distinct original, ongoing-record, finished-record and FLO identities. The SQLite tests exercise supported 1v1 ongoing-to-finished reconciliation, team reordering, start/betting evidence, captain-only rating changes, duplicate/delayed events, all original/record alias-reuse combinations after completion, mode/roster rejection and first-time linking without participation. Battle.net callback tests preserve stable subject separately from BattleTag and require explicit unlink before rename. Mode-scoped acceptance cannot be bypassed by a listing-capable runtime. Retired game settings/account metadata survive editing and backup/restore.

AoE4 adds a bounded ranked 1v1 feed and exact prefixed World references with roster/team/start continuity checks; no publisher-ID equivalence is assumed. AoE2 keeps its shared waiting feed and adds coalesced, cached, short-lived verified-player ongoing snapshots. Provider failures remain distinct from empty success. Public list payloads omit replay references/private rooms; Discord cards fit its aggregate embed budget.

An older workflow test used separate clock reads for observation and finish, occasionally producing a finish later than its observation by one millisecond. It now captures one timestamp per fixture; the focused regressions and full suite passed. No production validation was weakened.

Controlled game acceptance, actual registered-provider sign-ins, deployment and Discord command registration remain outstanding. Automatic observation stays gated; this verification does not label W3Champions custom/private games supported or approve Zero Hour without qualifying API access.
