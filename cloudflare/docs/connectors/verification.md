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
