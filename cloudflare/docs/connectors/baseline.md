# Source and staging reconciliation

Verified 2026-10-28 (UTC).

The checkout was fast-forwarded from `cd9c178019b8c11b4e3d57848f6af28f3818f167`
(0.8.4) to remote `main`, `fa3647b42ea4e5c48fb80bebf730c6742d1d4d95` (0.16.0),
before connector implementation. That baseline passed all 863 tests, server/web
type checks, the web build, and the Worker dry-run build.

The existing staging deployment reported 0.18.1. Cloudflare version
`df8289ac-fe02-45b5-92ee-1dd0870d4db4` was deployed on 2026-10-28 at 18:43:15 UTC.
Its downloaded Worker had SHA-256
`7b57a2aa807dae7a41c14042d8b6c12e6631d91fcc83e13afff1ae90529bbabd`.
The download was used only as a read-only comparison artifact; it was not edited
or used as an implementation checkout. No source map or matching newer source
revision was available in the fetched branches. Consequently, this is a documented
behavioral reconciliation, not a claim to have recovered the original 0.18.1
commit. Public access to the deployed static web assets returned HTTP 403, so
those assets could not be compared independently.

Comparison of the built `main` Worker with the deployed Worker found these changes:

- `/test populate` chooses eligible real members, and `/test checkin` marks a live
  match as a test. Server managers and the queue's moderator roles authorize these
  actions. Test marking excludes results, rating changes, participation statistics,
  played selections, map-history avoidance, and story history. Existing prediction
  stakes are refunded. A finished ranked match cannot be changed into a test.
- Test labels remain visible in match/admin cards. Test results cannot be undone
  or rerated, and are excluded from normal recent-match lists.
- Retired isolated test sessions retain bounded Discord-card cleanup and suppression
  of normal record/rank jobs. This does not create a new test-session interface.
- Queue cards no longer show a list of voice-channel occupants. The existing
  permission-aware shared voice cache and explicit Ping control remain.
- Health and setup version metadata had advanced from 0.16.0 to 0.18.1.

These behaviors were restored in the TypeScript source before workflow extension.
The reconstructed modules were compiled with the project's pinned esbuild and
compared with staging module sections. Their differences were only bundler-local
identifier renaming caused by the new connector imports. The pre-existing lobby
adapter, lobby store, and lobby-domain modules were unchanged between remote main
and the deployed staging artifact.

`tests/staging-baseline.test.ts` covers moderator authorization, duplicate requests,
population atomicity, protection of closed ranked matches, refunding and exclusion
of test results, persistence across a Durable Object restart, and isolation during
retired-session cleanup. Voice-cache tests assert the staging card behavior.

Before replacing the current staging deployment, retain its version identifier and
confirm the generated build metadata includes a real source revision. The connector
rollout still requires its separate live-game acceptance gates; this comparison
does not certify any newly implemented provider.
