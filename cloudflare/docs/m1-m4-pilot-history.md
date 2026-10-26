# Community Games · Cloudflare pilot (M4)

Discord is where friends join, check in, receive balanced teams and report results. The web app handles organizer setup and shows saved matches and ratings. M4 adds guarded player onboarding, four rating models, optional profile references and richer statistics to the existing ranked pickup-game loop. Catalog and custom games use the same behavior. The existing Railway bot remains the production app.

## Local development

Use Node 22.12 or newer (Node 24 LTS recommended). Dependencies are pinned.

```sh
cd cloudflare
npm ci
npm run check
npm run dev
```

Open `http://localhost:8787`. Local development uses local Durable Objects, never remote staging storage. For local OAuth, copy `.dev.vars.example` to `.dev.vars` and add `http://localhost:8787/auth/callback` to the test application's redirect list. Discord interaction validation requires a public HTTPS deployment.

Tests run inside workerd with real SQLite Durable Objects and mocked outbound Discord HTTP. They cover signatures, OAuth/session security, permissions, concurrent last-slot joins and saves, tenant isolation, deadlines and results across restarts, action deduplication, outgoing-message retries, rejection of removed settings-file routes and the spending-warning calculation. Independent fixtures generated from the legacy Python bot compare 60 Elo cases and 28 captain-based plus 28 unrestricted allocations. The M3 suite also covers stale roster actions, concurrent substitutions, role restrictions, no-show replacement, timeouts, corrections after later results and deleted-card recovery. Automated tests do not replace the live acceptance check.

## Deploy your own copy

1. Create a separate application in [Discord's developer portal](https://discord.com/developers/applications), with **Staging** in its name. Create its bot and leave privileged Gateway intents disabled. Keep production credentials separate.
2. Copy `.dev.vars.example` to `.dev.vars.staging`. Fill `DISCORD_APPLICATION_ID` and `DISCORD_PUBLIC_KEY` from General Information, `DISCORD_CLIENT_SECRET` from OAuth2 and `DISCORD_BOT_TOKEN` from Bot. Generate/reset credentials yourself. Keep the ignored file private; never paste secrets into chat or commit them.
3. Run `npx wrangler login`. Set `env.staging.name` and `env.staging.vars.APP_ORIGIN` in `wrangler.jsonc` for your own account. If the workers.dev URL is unknown, deploy once, copy the returned URL into `APP_ORIGIN`, then deploy again. The checked-in URL is this pilot's staging address, not a shared backend.
4. Run `npm run deploy:staging`, then `npm run secrets:staging`. The secret helper verifies the ID, key and token belong to the same staging application and sends secrets to Wrangler through stdin.
5. Under OAuth2 Redirects in Discord, add `<APP_ORIGIN>/auth/callback`. Run `npm run connect:staging` to set and validate the interaction endpoint through Discord's official API. Alternatively, set **Interactions Endpoint URL** under General Information to `<APP_ORIGIN>/interactions`. Discord's signed validation PING must succeed before saving.
6. Open the web app, invite the bot and sign in with Discord. The invite requests View Channel, Send Messages, Embed Links and Read Message History. Setup requires Manage Server, Administrator or server ownership; check channel overrides in the app.
7. Register commands and check the exact test channel:

```sh
STAGING_GUILD_ID=YOUR_TEST_SERVER_ID npm run register:staging
STAGING_GUILD_ID=YOUR_TEST_SERVER_ID STAGING_CHANNEL_NAME=test-bot npm run doctor -- --discord
```

Only the staging application's fifteen guild commands are changed. No global commands or production credentials are used. A server can host both bots; select **Community Games Staging** when Discord shows duplicate command names.

## M2 acceptance · 20–30 minutes

For this pilot, use server `706320010368516198`, channel `test-bot`.

1. Sign in and choose the saved community. Keep the existing queue, or set it to 2 or 4 players for a shorter test. AoE2-style Elo works identically for catalog and custom games; captain-based or automatic balancing is supported.
2. In `test-bot`, choose the staging bot's `/add`. Members join themselves; an administrator can use `/add user:@member`. At capacity, the queue starts automatically. Below capacity, an admin can `/queue start` with at least two players. For an odd waiting count, the last player remains queued so teams are equal.
3. Each selected player presses **Check in**. When everyone is ready, the card confirms teams and the highest-rated captain on each team. Create and play your game room manually. The losing captain presses **Report loss** or uses `/report loss`.
4. Check `/rank`, the final card and the web app's **Match records** section (Reload). Ratings and history must agree. Have a noncaptain attempt a report and repeat the finalized report using its match ID; neither should change ratings again.
5. Rejoin with `/add`, then have an admin `/queue start skip_check_in:true`. It skips check-in only for this match. Try a draw or abort with both captains agreeing. Test a custom-game queue too: its ratings are separate from AoE2.
6. Let one check-in expire. Ready players return to the queue; absent players are removed. Eligible waiting players replace no-shows while existing readiness is retained and the replacement gets a fresh check-in window. An admin can recover a stuck check-in or match using `/match abort match:<id>`; this releases players without rating changes.

M2 supports 2–16 players and check-in deadlines of at most one hour. Random teams are also available. All four rating engines are now available in M4; established ladders cannot silently switch engines. Optional module flags remain preferences. Participation exclusion is within one community, across its games; other communities are independent. Rejoining after a result is manual. No game API or lobby monitoring is active yet.

Settings-file export and restore remain excluded. M1 setup and permission checks remain in the automated suite. M2 acceptance requires a real Discord match and the same saved result on the web.

## M3 acceptance · match control and recovery

Use only **Community Games Staging** in `#test-bot`. Administrator controls require Manage Server, Administrator or server ownership, checked again for web actions. The web has on-demand queue, match, rating and moderation controls; Reload fetches current state.

1. Try captain-based balance, unrestricted automatic balance and random teams. Highest-rated players captain both sides in every mode. Below-capacity manual starts retain equal teams; an odd leftover stays queued.
2. `/subme` toggles a replacement request. `/subfor user:@outgoing` replaces a requesting player with the caller. `/subauto` takes the next eligible member from that match's waiting queue and always applies unrestricted rating balance. The caller, a captain or an admin can replace a player; a volunteered request also allows other members to help. Active players in this community cannot be selected again.
3. During check-in, **Back out** replaces the caller when possible. Otherwise the match closes and ready teammates return ahead of waiting players. At expiry, all absent players must have eligible replacements; partial replacement never strands a match. Existing readiness is retained and each new roster gets a fresh window. Three unsuccessful replacement rounds is the limit; a failed check-in requires manual rejoin or a new manual start.
4. Admins can `/match substitute` or `/match put` to repair an unreported roster. Captains refresh, old result agreements clear and stale roster buttons are rejected. `/match report` resolves an active match; completed results must be undone before correction.
5. `/match create` records a manually organized game from two lists of server-member mentions/IDs and a winner or draw. `/match undo` subtracts only that record's original deltas from current ratings, updates counts/streaks and retains history/audit. An undone game can be reported again using current ratings. Corrections pause only while an affected player has an active match. Future economy settlements remain final under the PRD policy.
6. Disable automatic start to collect several groups, then `/queue split group_size:2 sort_by_rating:true`. Leftovers stay waiting. `/queue clear` clears waiting members without touching matches. Queue aliases and one default per channel support quick commands.
7. Optional waiting expiry, match timeout, required role and blocked role are queue settings. Zero disables a timeout. A match timeout aborts without rating changes. `/moderation noadd` temporarily restricts a member across this community; `/moderation forgive` clears it. Existing active games can finish.
8. Delete a disposable staging match card and `/match recover`. Check the recreated roster/result, web audit and unchanged ratings. Restart/redeploy staging and verify records, restrictions and deadlines survive.

Play is bounded to 2–16 players per match. Manual records permit unequal nonempty teams of up to eight each, preserving existing administrative behavior. Waiting queues hold at most 128 members when automatic start is off. Queue configuration changes affect future matches; an active match keeps its rules snapshot. No-show replacement revalidates roles when enabled, never trusts old queued membership indefinitely, and requires enough candidates for the whole absent group.

See `ops/m3-verification.json` for which scenarios were exercised live versus through signed-interaction/workerd tests. Tests involving another person's button click require that person; automation never acts as another Discord account.

## M4 acceptance · onboarding and player records

Version 0.4.0 is deployed to the separate staging application. `npm run check` passes 478 tests, both TypeScript checks and both builds; `python3 scripts/test-legacy-converter.py` passes six offline converter tests. The new suite includes 360 independent comparisons against the legacy Flat/Glicko2/TrueSkill/AoE2 calculations with rating scales, streak modifiers, draws and different team sizes. Python is used only to generate fixtures and convert legacy snapshots, never in the deployed runtime.

1. Add a custom game and a queue in `test-bot`. Choose Flat, Glicko2, TrueSkill or AoE2-style Elo and save the initial rating, deviation, scaling and streak options. Queues sharing a game must share the same rating settings. TrueSkill needs initial deviation of at least 2. Once records exist, changing the model requires a separately reviewed migration; it is not an ordinary settings edit.
2. Open **Players & onboarding → Import previews and history**. Paste CSV/manual seed rows or upload CSV/normalized JSON. Required CSV headers are `id,name,rating,deviation`; optional fields are `wins,losses,draws,streak,hidden,lastRankedAt`. Dates must be ISO 8601 with an explicit timezone. Set a distinct source identifier and select the destination game/queue. Review the counts and warnings, then apply. Bulk seeds insert new players only; they never overwrite an existing rating, counters, visibility or linked profile. `/rating seed` remains a separate, explicit organizer correction.
3. Re-preview the same bundle: **Already imported** must return the original manifest. Changing the community after preview rejects the stale apply. Full-history imports require an empty destination game, explicit source ladder/queue mappings and model, original roster/results/timestamps and rating/deviation deltas. Totals, streaks, last-played dates, history continuity and final ratings must reconcile. No imported historical Discord cards are sent automatically. Unsupported source features are listed in the preview.
4. Use the staging bot's `/rank detailed:true`, `/leaderboard page:2` or `/profile show` and compare the linked web statistics. Leaderboards page ten players and honor minimum matches, recency and hidden-player rules. Direct detail shows W/L/D, current streak, peak, recent matches and rating history. Teammate/opponent comparisons require three recorded encounters and show their sample counts. Web statistics load on demand; there is no subscription or polling.
5. `/profile link` and `/profile unlink` manage your optional HTTPS profile references for the selected game. They do not verify game API ownership or block matchmaking. An existing owner causes a clear conflict; organizers can explicitly reassign a reference with a reason and confirmation. Hide/unhide through the web or `/rating hide` and `/rating unhide`; hidden identities are masked in other players' match details and excluded from boards. Organizers and the player can inspect their own hidden record.
6. In each game's policy, try member, administrator or public statistics and the match/recency thresholds. The default is members only, 40 matches and 30 days. Public visibility is an explicit organizer choice. Private reads recheck Discord membership/permissions, and all statistics responses are `no-store` so a later privacy change is not served from a cache.
7. Optional inactivity decay is off by default. When enabled, one persisted Monday-UTC alarm applies weekly after 30 days without ranked play, respecting configured rank floors and bounding deviation at the initial setting. Never-played, recently played and active-match players are skipped; W/L/D and streak are unchanged. Missed weeks apply once. Disable it to remove the alarm.
8. Rank labels do not require extra Discord permissions. Optional cosmetic rank roles require **Manage Roles**, with the bot above those roles; optional rating prefixes require **Manage Nicknames** and the appropriate hierarchy. These permissions are intentionally absent from the basic invite. Rank roles cannot grant moderation or server-management permissions, including through channel overrides, and permissions are checked again at delivery. A failure never reverses accepted ratings. Inspect **Import, rank and inactivity diagnostics** after Reload; fix permissions/configuration and use `/rating sync` or the web sync action to retry.

Live testing used only ddk220's account and the separate Community Games Staging bot in `test-bot`. Four new manual result simulations exercised all models, alongside CSV/manual/PUBobot seed imports, a converted NammaPUBobot history fixture, stale preview rejection, repeat-import protection, profile conflict/reassignment, hide/unhide, leaderboard paging, visibility thresholds, and the optional weekly alarm. Uploaded JSON and pasted input both worked. The staging bot's missing nickname permissions produced a visible 403 without changing the result; successful role/nickname delivery is covered by mocked Discord tests, not a live permission grant. Actual 30-day decay, interruption rollback, concurrent scenarios and multi-player relationship samples are exercised in workerd tests.

Cleanup removed the temporary queues and dummy profile reference, restored fixture visibility and disabled fixture decay/nickname decoration. The five sample games and their labeled closed results remain for organizer review. The original Pickup queue, five existing ratings and thirteen earlier matches are unchanged. See `ops/m4-verification.json`, `ops/m4-deployment.json` and `ops/m4-final-ui.json` for the precise evidence and live/automated boundaries. These are synthetic import rehearsals, not production migration data.

### Offline legacy snapshots

`scripts/convert-legacy.py` reads a bounded CSV directory or PUBobot ZIP, never a database. Use the four core source tables: NammaPUBobot `player_ratings`, `matches`, `match_players`, `rating_history`, or PUBobot `qc_players`, `qc_matches`, `qc_player_matches`, `qc_rating_history`. Seed mode needs only the player table. Other CSV files are ignored. The conversion produces normalized JSON for review in the web app; it does not apply anything.

Supply a mapping JSON with `mappings: [{ladder, gameId, engine, queues}]`; `queues` maps original source queue IDs/names to saved destination queue UUIDs. NammaPUBobot also needs `ladderByChannel`, mapping original channel IDs to source ladders. Source queues sharing a ladder map to the same game; independent ladders must not be merged. JSON imports use their own mappings, not the CSV destination selectors. Review them in the preview.

```sh
python3 scripts/convert-legacy.py SNAPSHOT_DIRECTORY \
  --source nammapubobot --mapping REVIEWED_MAPPING.json \
  --mode history --source-id UNIQUE_SNAPSHOT_ID --output REVIEW_ONLY.json
```

For PUBobot, use `--source pubobot`, a ZIP or CSV directory, and an explicit `--timezone` for timestamps without offsets. A NULL winner is ambiguous in that source; classify it explicitly with `--null-result draw` or `--null-result abort` before a full-history import. Normalize ambiguous daylight-saving times before conversion. NammaPUBobot Unix timestamps are converted from seconds to milliseconds. Limits are 500 players, 500 matches, 8,000 rating-history rows, 5 MB uploads and 25 MB expanded snapshots; a larger production migration needs a reviewed batch plan in M10. Gold, predictions, quiz, drafts, game API achievements, old channel settings and external profile mappings are not migrated in M4. Operational database backups and a full production cutover remain later release work.

## Storage

- One `Community` SQLite Durable Object per Discord guild. Stable game IDs scope ratings and future gold within the community. Community ownership is checked even in internal RPC.
- Expiring `AuthStore` objects hold OAuth state and opaque sessions privately. The browser receives an HttpOnly session cookie and session-bound CSRF token. Logout deletes server-side state.
- Settings saves use one transaction and a monotonically increasing revision. A stale tab receives HTTP 409 and must reload. Unchanged settings rows are not rewritten. The audit records actor IDs, actions and revisions.
- Local and staging bind distinct resources. There is no production environment or Railway configuration change. Later production must receive its own resources/secrets.
- Schema version 4 includes the existing match, rating, participation, deadline, action, outbox, waiting-expiry, verified-member, temporary-restriction and control-audit tables plus player metadata, optional profile references, original rating/deviation details, import manifests, rank jobs and weekly record clocks while preserving existing game/queue IDs. Restart tests verify upgrade and persistence. A game with player records cannot be removed; occupied queues cannot be removed or moved. Rename referenced games instead. Established rating engines require explicit conversion; queues sharing a game must use the same engine and rating settings.
- Roster selection, results, rating changes, participation and outgoing work commit together. Persisted alarms resume check-in deadlines and finite delivery retries after restart. Unrestricted play commands acknowledge without Discord REST lookups. Role-restricted selections verify up to 32 candidates with a one-second per-request bound; failed membership checks cannot select a player. Manual records defer their private acknowledgement before resolving members.
- A shared `DiscordGate` coordinates this application's outgoing-message rate limits. Cards coalesce updates, retain message IDs and use Discord nonces plus bounded reconciliation after ambiguous sends. Discord transport is not guaranteed exactly once indefinitely; domain actions and rating effects are deduplicated independently.
- Missing channel permissions block delivery without endless retries. Fix permissions, then use `/queue show` or `/teams` to refresh the affected card. The web displays blocked deliveries. If a roster starts but its card cannot be sent, an admin can inspect the saved match ID on the web and abort it through the web repair controls or `/match abort` in Discord. `/match recover` recreates a deleted card from saved state without rerunning ratings.
- There are no settings-file download/upload controls or export/restore API handlers. Operational database backups, retention and recovery rehearsal remain M10 deployment responsibilities; they are not an organizer-facing settings feature.

## Diagnostics and spending

`npm run doctor` checks staging health/readiness. `--discord` verifies the application, redirect, endpoint, membership and channel ID without printing secrets. API errors return correlation IDs. Safe logs record paths, methods, status and elapsed time. Full invocation URL logging is disabled so callback codes are not recorded; logs are sampled at 10%. There are no idle polling loops, Gateway connections or live web updates.

Migration has not begun. Before it starts, record a dated project billing baseline and configure a delivery path that warns the owner above **US$50 cumulative migration-attributable Cloudflare spend**, including staging, production, storage and backups, excluding Railway. This is a warning, not a shutdown or permanent monthly budget. Account totals may include other projects; actual migration cost and estimated monthly running cost must remain separate.

`ops/migration-budget.example.json` and `npm run cost:check -- SNAPSHOT.json` provide a tested accounting helper. Actual amounts must come from project-attributed billing records. Null is unavailable, not zero; observations older than 48 hours are stale. The helper warns and returns exit code 2 strictly above $50. **It is not an active billing collector or scheduled notification monitor.** Connecting/testing that collector and warning delivery is required before migration and must be recorded in the release checklist.

## CI and license

`.github/workflows/cloudflare.yml` runs the same checks on changes to this app. Staging deployment is manual and requires a scoped Cloudflare token/account ID stored as GitHub environment secrets. There is no automatic production deploy.

This app remains part of NammaPUBobot under GPL-3.0-only. Preserve the repository's [LICENSE](../LICENSE) and upstream acknowledgements when distributing your copy. The Python app and its upstream history remain intact.

Rating reference methods: [Glickman’s Glicko-2 specification](https://www.glicko.net/glicko/glicko2.pdf) and the [TrueSkill reference documentation](https://trueskill.org/). Fixture generation uses the upstream Python packages and existing bot calculations for comparison. The Worker implements the two-team mathematics directly and retains legacy traversal, rounding and modifier behavior; it has no new rating-library runtime dependency.
