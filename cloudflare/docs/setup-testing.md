# Setup tests — 0.17.0

Use `/test` in a configured queue channel to rehearse queue population, balancing, check-in, map selection and results. Server members with **Administrator**, **Manage Server**, or a configured **Bot moderator role** for that queue can run these commands. Permissions are checked on every request against live queue settings. A Discord role name alone grants nothing. Commands are registered without a default Discord permission restriction so custom moderators can discover them; the server-side permission check still rejects ordinary members.

## Quick rehearsal

1. `/test populate` randomly selects **eight real human server members** for a separate test queue. It does not notify them or enter them into the live queue. Optional `count:2` through `count:16` lets you test smaller or uneven teams.
2. `/test start` starts the test using the queue’s saved team method, capacity, check-in timer and map settings. Population never automatically starts a match. If you populate more than queue capacity, the remainder stays in the test queue.
3. `/test checkin` checks in the entire test roster. When map votes count as check-in, it casts simulated votes for everyone instead, through the normal voting handler.
4. `/test maps` completes an open map round by simulating participant votes or a captain’s direct selection. Use this when voting is separate from check-in. Random maps are already selected automatically. With maps disabled there is nothing to select.
5. `/test result result:alpha` finishes the test with Alpha winning. `beta`, `draw`, and `abort` are also available. Wins/draws require completed check-in and maps; abort is available earlier.
6. `/test reset` erases temporary test data and removes its Discord cards. `/test status` shows progress, check-in counts, map state, expiry, and pending/blocked message delivery.

Every command accepts an optional `queue` name, alias or ID. The configured default is used when unambiguous. There is one test session per community/queue; moderators share it. Reset before repopulating or starting another test. Tests automatically expire after **one hour**, even across Worker restarts. The configured check-in timer is real: a missed deadline can fail the test just as a normal match does.

Test cards are public and clearly labelled **TEST**. Their ordinary live-match buttons are removed: operate them through `/test` commands. Successful updates do not leave redundant private receipts; status, reset acknowledgement and errors are private. Test cards still demonstrate check-in, teams, map choices, the result, and the fresh queue below the match. They are temporary demo messages, not permanent match logs.

## Isolation and limits

Test sessions use a separate Durable Object named `test:<guild>:<queue>`, never the guild object used by real play, the website, `/admin`, statistics or match history. The session uses the actual matchmaking, readiness, map and result handlers. The test result path explicitly skips rating calculation and rating writes. It also skips record indexing, rank delivery and match audit writes. Real profiles, links, queue places, active matches, ratings, win rates, wallets, betting and history remain unchanged, including when selected people are already playing a real match.

The copied configuration disables automatic starts, voice previews/pings, periodic queue bumps, admission-role filters, readiness reuse, external lobby integration, civilization picking, banter, gold, predictions and rank/nickname changes. Only map selection remains enabled if the source queue enables it. Test player ratings use the queue’s defaults rather than modifying or importing real profiles. This is a setup rehearsal, not an external AoE2 lobby or betting simulator. Actual client buttons and real game start/end detection still require human testing.

Member sampling uses up to 1,000 members returned by Discord, excludes bots and selects distinct humans. If Discord denies listing because Server Members intent is unavailable, it samples known player IDs and recent channel authors, verifying current membership individually (up to 32 candidates). Sampling is therefore from available members, not guaranteed uniform across very large servers. If too few humans can be verified, population fails without adding anyone. It does not enable privileged intents or request more credentials.

## Delivery and cleanup

Public cards use the existing durable outbox and shared Discord rate limiter. Test messages never set allowed user/role mentions and never attach live queue/match controls. Lost create receipts are identified by nonce or a footer containing the unique test session ID; live queue controls cannot be mistaken for test receipts.

Reset/expiry first stops the session, then removes temporary gameplay data. Message receipts are retained only while cleanup needs them; deletion retries respect Discord limits, tolerate already-deleted messages, and survive restarts. Ambiguous receipts are searched in bounded channel-history pages. If access is denied or receipt recovery is exhausted, status reports blocked cleanup and a new test is refused. Restore access and run `/test reset` to retry. Live queue cards are never deleted by test cleanup. Minimal command-ID receipts are bounded and retained to prevent duplicate interaction delivery from replaying actions; they contain no player, result or match details. No database migration or new binding is required.

The feature adds no listener or idle polling. Cost comes from requested test actions, their existing check-in/map alarms, and a single expiry/cleanup cycle. Normal production queue operations are unchanged.

Deploy the Worker and run `npm run register:staging` to register the new guild command for the staging application. This does not change the legacy Railway bot or global command registration.
