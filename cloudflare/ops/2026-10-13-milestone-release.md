# Pickup engagement release review · 13 October 2026

The approved core UX changes, M5 gold/predictions, M7 text stories and M8 maps are implemented, checked and deployed to isolated staging. M10's source distribution, independent Worker and read-only recovery rehearsal work. A fresh independent Discord installation and project billing attribution remain acceptance gates. M6 quizzes and M9 civilization modes are deferred as requested. M11 production cutover is not performed.

## Milestone outcomes

| Milestone | Implemented behavior | Verification and status |
| --- | --- | --- |
| UX | Channel defaults; fresh Join/Leave cards; reusable readiness; direct Sub me and loss reporting; captain cancellation agreement/admin cancellation; clean terminal cards; fixed 12-hour maximum and safe queue returns | Automated race/permission/expiry/restart tests plus real Discord joining, formation and immediate loss report. Core staging acceptance passed. Optional rally and game-end hooks have narrower acceptance below. |
| M5 | Optional community/game gold, personal affordable stake buttons, cumulative bets, own-team selection, spectator sides, cancel/close/refund/settlement, balances/history/privacy, optional Thursday tax with 500 floor | Ledger/payout/retry/correction/tax tests; real 50 + 50 stake clicks and cancellation, failed-check-in refund, empty/one-sided close, restart and SQL recovery. Final private-panel update protocol tested with signed interactions. |
| M7 | Optional before/after text stories, established rivalries, duos/lineups, 3+ win-streak callouts and end/extend payoffs | Pure fact/snapshot tests plus a live 3-win Killing Spree and its ending after a loss. Text only: speech/audio playback is not included in this release. |
| M8 | Optional random, captain/admin pick or player vote; plurality/approval; 1–5 maps; tie/no-vote rules; recent-map avoidance; AoE2 starter pool/custom names; vote-as-check-in | Tests cover modes, retries, stale rounds, substitution, deadlines and restart. Live map vote counted as readiness; live captain selection of Forest Pass and Desert Crossing advanced to play. Off by default. |
| M10 | Own-account configuration/credential/Discord tools, source archive, bounded legacy import converters, private checkpoints/snapshots, independent read-only restore | Clean archive installation passed; separate Worker deployed; all 47 SQL tables and all 23 saved matches match the recovered copy. Fresh Discord app credentials and billing access are pending. |

Gold is play money, with no payment or cash-out feature. Quizzes and civ controls are disabled. Features can be enabled separately per queue; queues for the same community/game share ratings and wallets. Selecting multiple maps supplies a list for one rated match, not automatic series scoring.

## UX review

- Ordinary commands use the channel default. A paused default returns a short paused response; explicit queue selection remains available. Leaving remains possible while paused.
- Roster changes coalesce briefly, post a fresh queue card and retire the older card. No-op clicks do not post another card. Generation-bound delivery/reconciliation prevents stale jobs replacing the latest card. Uncertain sends become visible blocked work instead of infinite reposts.
- Explicit readiness lasts up to 15 minutes in its queue/game. Repeated clicks do not extend the lease. Ready players retain valid check-ins through failed formation and replacements; leaving, entering play, removal and expiry clear them.
- A formed card shows teams/captains plus Cancel match and Sub me. Automatic substitution takes the next eligible waiting player and rebalances the roster. With no substitute the player remains in the match.
- Cancellation agreements are bound to the opposing current captain and proposal. Substitution/captain changes invalidate old agreements. Administrators can cancel directly.
- Losing captains report immediately. Ordinary results/manual cancellation still require manual queue re-entry. Terminal cards have no Join button.
- New matches expire no later than 12 hours from creation; a shorter configured duration is allowed. Timeout cancels without ratings, refunds affected predictions, releases active locks and preserves recoverable return intent. Returning eligibility is checked; overflow or lookup outages remain visible. The queue is held until an explicit Join resumes it. Existing active legacy matches retain their earlier semantics.

## M5 review

The wallet starts at 500 once per community/game. Imported wallets are not reseeded. Stake options use the approved 500-gold bands and shrink for poorer players. Match players back their own team; spectators choose a side. Repeated clicks add to the same bet. The private panel updates in place, shows the cumulative stake and disables cancellation when no bet exists.

All accepted money changes commit with an append-only ledger, receipts and delivery jobs. Indexed personal-bet lookup avoids loading every bettor just to render one panel. Signed component tests cover private UPDATE_MESSAGE responses, public/copy safeguards, stale closed controls and lost-response retries. The final in-place panel change was not replayed against a new live match after pausing the fixture queue; additive/cancel behavior itself was verified live earlier in the run.

Captain/admin closure freezes retained stakes and records participation. Cancelling before closure does not qualify. Open books at a result report refund instead of settling; draw, cancellation, failed check-in, one-sided pools and roster changes refund as appropriate. Frozen refunded books do not reopen silently. Closed two-sided pools use exact integer proportional payouts, with fractional remainder burned. Paid settlements remain final after a later result correction and are labeled with their original outcome.

The tax is off by default. It protects a balance of 500, checks seven-day grace/opportunity/retained participation and runs on the saved weekly schedule, without an idle polling loop. Low-balance match participation rewards refill by up to 100, capped at 500, once per player/match. Limits and invariants reject unsafe integer totals and excessive bettor counts.

## M7 and M8 review

Stories use saved game-agnostic facts, bounded history and small samples with explicit thresholds. One- and two-win streaks are omitted. Tied leaders remain visible in text. No model calls or paid AI generation are used. The staged three-win history was created as explicitly labeled test fixtures in the separate Overnight Lab game; it did not change AoE2 ratings/history.

Map rounds persist the shuffled shortlist, ballots, selected maps and policy. Old round IDs reject stale controls. Readiness and map selection coordinate so a ready roster cannot start before required maps resolve. Vote-as-check-in accepts the voting roster player; captain-only direct selection is not presented as everybody's check-in. Roster changes keep surviving eligible ballots while requiring the new roster's participation and respecting the absolute match lifetime. Invalid custom-map pools fail validation. The AoE2 pool is a 15-map starter list, not a claim of the complete game catalog or current ranked rotation.

## M10 independent installation and recovery

- Staging: [community-gaming-staging](https://community-gaming-staging.deepakdhanavel.workers.dev), version `f7034bf1-305c-49c2-acad-201306a10185`.
- Independent backend: [community-gaming-rehearsal](https://community-gaming-rehearsal.deepakdhanavel.workers.dev), version `00ec1d39-6d45-48cd-9942-064ed15111ac`.
- [Operator guide](../docs/operator-guide.md) covers own application, namespaces, setup, commands, secrets, backups and migration limits.
- [Source archive](releases/community-games-0.8.0.tar.gz) contains 89 manifest-verified source/config/example files, pinned dependencies, tests, GPL license and CI workflow. [SHA-256](releases/community-games-0.8.0.tar.gz.sha256): `38e1d1a1fcdb4b00beba76d4f1f69ccc9c1dd1770a7cafdc51e6f1e5a79c8338`.
- Archive excludes live credentials, provider storage, private backups/evidence and macOS metadata. Its staging origin/name are examples; `operator-config.mjs` generates a separate installation. Fixture generation scripts that depend on the parent Python project are excluded; committed synthetic fixtures support its tests.
- Fresh extraction at `/private/tmp/community-release-clean-kmjkrt1_/community-games-0.8.0` passed `npm ci`, complete `npm run check`, both converter suites and standalone config generation.
- SQL snapshots and manifests are private mode 0600. Provider checkpoints and snapshots were taken before live tests and after the final deployments. Final source snapshot is `ops/backups/release-final.json`; the independent copy re-export is `ops/backups/rehearsal-release-final.json`.
- Deep comparison confirms all **47 tables** and **23 saved matches** are identical. Final deployment made no SQL changes relative to the prior final snapshot. Web records show only the latest 20 matches by design.
- Recovery remains read-only: actions, settings/import writes, alarms, rewards/taxes and Discord delivery are disabled. Restore rejects populated objects, foreign communities, unsupported schemas and ledger discrepancies. It is an inspection/rehearsal copy, not live failover.

The independent Worker has an operations token but no Discord credentials. The owner must create the separate Community Games Rehearsal application and fill the prepared private `.dev.vars.rehearsal` file. Browser credential handoff rules require the owner to perform credential creation. Once supplied, install/endpoint/invite/OAuth/command/player-flow acceptance can finish without sharing the staging or production application.

Legacy converters and bounded import paths pass synthetic tests, including exact wallet/ledger and immutable final market metadata. The full current Railway database has not been imported by this release. Core history import remains bounded to 500 players/matches and 8,000 rating changes; economy import and recovery also have explicit limits. A larger real history needs a reviewed batch plan. Open/frozen legacy books and active matches must be resolved during an eventual controlled migration; no stakes are silently discarded. The existing Railway service remains the production/rollback system.

## Verification evidence

- **584 tests across 16 files**, type checking, Vite build and Wrangler dry-run passed for both the workspace and clean distribution. Both offline converters passed **11 checks**. Benign workerd teardown messages occur while deleting test Durable Objects; the suites exited successfully.
- The local load test uses **1,000 independent communities**, 20 concurrent journeys and five player actions each, with mocked Discord transport. It verifies state isolation and workload behavior; it is not a production latency, capacity or billing benchmark.
- Live acceptance used only staging app `1559284610108752068`, guild `706320010368516198`, channel **#test-bot** `1559279290577850368`.
- Real join/formation, map-readiness, personalized/additive stakes, cancellation/refund, pre/post streak and direct losing-captain report were observed in Discord and persisted in the web history.
- [Final Discord result](https://discord.com/channels/706320010368516198/1559279290577850368/1559464041846546473) and [screenshot](screenshots/overnight-final-result.png) show the two maps, Beta win, rating changes and the ended three-win streak, with no terminal action row. [Additive stake screenshot](screenshots/m5-additive-stake.png) shows the 400 balance after two 50 stakes.
- [SQL/state verification](overnight-verification.json) confirms original games, queues, matches and rating profiles unchanged. All three test wallets ended at **500**, there are **no active matches**, and the Overnight fixture queue is **paused** with its one waiting member preserved. The original Pickup default remains enabled with new modules off; settings revision is **44**.
- [Four sampled staging acknowledgements](overnight-latency.json) took **66–174 ms**. This excludes Discord client/network rendering and is not a latency SLO benchmark.
- The updated local preview uses current builders for 31 review states. Browser checks confirmed spectator side selection, two additive stakes, cumulative display and refund; it is a sample interaction review, not a live data view.

## Cost and remaining gates

Core play uses signed HTTP interactions, transactional SQLite state and one next-due alarm per community. There is no game API polling, live web subscription, voice audio or paid AI enabled by this release. Optional rally is off by default and is the exception: it starts an outbound Gateway connection on demand and stops after an hour without requests.

The owner's $50 cumulative migration warning includes staging, production, storage and backups and excludes Railway. A daily 9am local thread review is active as automation `community-games-migration-spend`, quiet while unchanged. Its actual billing collector is **not accepted**: the saved Cloudflare credential returned HTTP **403**, so project-attributed actual spend is unavailable, not zero. See [billing evidence](billing-access.json) and [budget snapshot](migration-budget.json). Read-only billing/analytics access or a reviewed attributed snapshot is needed to make the warning reliable. There is no claim that migration spend is below $50.

Remaining acceptance:

1. Fresh independent Discord application/install/OAuth and end-to-end player flow after owner credential handoff.
2. Project-attributed actual billing and a verified threshold notification path.
3. Optional voice rally has mocked presence/authority coverage but no positive live Gateway acceptance. It supports one pilot shard only; scale-out/sharding and duration cost need a separate review before broad enablement.
4. The authenticated, deduplicated game-end prompt hook is built/tested. Real game/session adapters remain later connected-game work; it never settles results itself.
5. Before M11, rehearse the actual production history within reviewed batch limits, reconcile deltas and obtain explicit cutover approval. Do not redeploy the old M4 bundle after epoch-5 feature writes; use a compatible forward repair or reviewed recovery.

No Git commit, main push, production import, global command registration or Railway cutover occurred in this run. The local source/package and two isolated Cloudflare deployments are ready for review.
