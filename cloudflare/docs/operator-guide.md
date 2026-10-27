# Independent operator deployment and recovery

For access to the existing Cloudflare test deployment, use the consolidated
[agent access setup](agent-access-setup.md) for credential names, Codex network
secrets, Worker secrets, Discord history permissions and verification steps.

## Deployment boundary

One installation owns a Cloudflare account, a Worker and its Durable Object namespaces, a Discord application and an interaction endpoint. Community/game identifiers scope ratings and gold. Do not point two applications or Workers at shared active state. The default application has no game API requirement. No PostgreSQL, Neon, Redis or paid AI service is needed for this edition.

The distribution includes source, pinned dependencies, SQLite migrations, command definitions, configuration generator, secret installer, diagnostics and bounded import/recovery tools. It does not include credentials, private backups, another community's data or local workerd storage.

## Organizer console

- **Queues:** create and edit queue rules. Saved queues start collapsed; expand one to change its settings.
- **Matches:** review recent games, filter by queue/status, and open an individual record for administrative actions.
- **Players:** inspect ratings and gold, filter by game/name, manage record policies, or preview an import. Use Load more players to reach records beyond the first page. Imports require a separate reviewed Apply action.
- **Setup:** add games and check the bot's access to configured Discord channels.

Switching views preserves unsaved inputs. Refresh intentionally reloads the saved state; stale saves are rejected. Views and filters do not poll for updates. Start a Discord cancellation with `/report abort`; match cards have no Cancel match button, and older ones direct the user to the command. Captain cancellation/draw requests appear in their own messages for the opposing captain to agree. An administrator can cancel immediately using the command. Completed check-in reminders are removed.

## Discord administrator menu

Use `/admin` to find the Setup, Queue, Match and Player command categories. Discord's command explorer suggests full command paths; the base `/admin` alone is not an executable command. All categories require Manage Server or Administrator permission, checked by both command visibility and the handler.

| Category | Actions |
|---|---|
| `/admin setup` | `status`, `web` |
| `/admin queue` | `add`, `remove`, `start`, `clear`, `split`, `rally` |
| `/admin match` | `cancel`, `report`, `undo`, `recover`, `substitute`, `put`, `record`, `maps-redo`, `close-betting` |
| `/admin player` | `seed`, `hide`, `unhide`, `sync`, `restrict`, `unrestrict` |

Examples: `/admin queue start skip_check_in:true`, `/admin player seed user:@player rating:1200`, `/admin match cancel match:MATCH_ID`. Match IDs are available in the web organizer. Recording a game with `/admin match record` immediately writes a completed ranked result; starting a new queued game uses `/admin queue start`.

Player and captain commands are unchanged. The previous administrator command paths are no longer registered in the slash menu. The handler accepts already-issued older interactions during Discord client refresh; their permission checks remain in force. Queue settings and bulk imports remain in the web organizer opened by `/admin setup web`.

## First installation

1. Install Node 22.12+ and run `npm ci` and `npm run check` in the release directory. Run the two Python converter test scripts if you will import legacy history.
2. Create your own Cloudflare account or use an existing one. Run `npx wrangler login`. Review the account/plan yourself; this guide does not enroll you in a paid plan.
3. Create your own Discord application and bot. Record its application ID, public key, OAuth client secret and bot token in a private local environment file. Privileged intents are unnecessary for the default slash/button flows; reading ordinary channel conversation additionally requires Message Content Intent as described in the [agent access setup](agent-access-setup.md#enable-message-content-intent-to-read-the-conversation). Generate a 32-byte random hexadecimal `OPS_TOKEN` locally; it protects operator backups/imports. `CONNECTOR_TOKEN` is optional and should exist only when an adapter is used. Never put these files in Git or chat.
4. Generate a unique configuration:

   ```sh
   node scripts/operator-config.mjs community-games-YOURNAME https://YOUR_WORKER.YOUR_SUBDOMAIN.workers.dev
   npm run build:web
   npx wrangler deploy --config wrangler.operator.json
   ```

   If the final URL differs, update `vars.APP_ORIGIN` in the standalone file and deploy again. The generated configuration uses separate namespaces and starts as staging. It contains no pilot account ID or secret.
5. Run `node scripts/install-operator-secrets.mjs wrangler.operator.json PRIVATE_ENV_FILE`. The script verifies that the bot token, app ID and public key match before passing secrets through stdin. Only recognized credential names are installed. Do not upload unrelated environment variables.
6. In Discord, add exactly `APP_ORIGIN/auth/callback` as the OAuth redirect, and set the signed interaction endpoint to `APP_ORIGIN/interactions`. Discord's validation PING must succeed. Use a guild command registration for a disposable server first.
7. Invite the bot with `bot applications.commands` scopes and permissions 84992. Check channel overrides. Open the web app, sign in, add a catalog/custom game and save a queue. Owner/Administrator/Manage Server access controls web setup; ordinary members cannot manage it.
8. Test a complete Discord join → formation → check-in → result and compare the saved web history. A health response, bot invitation or command discovery alone is not acceptance.
9. Enable gold, stories and maps one at a time, and test their complete flow. Leave quizzes and civ modules off; they are deferred in 0.8. The web view is on demand; Refresh fetches new state.
10. Only after acceptance should you change APP_ENV to production, register the desired global commands and invite real communities. Production cutover of the existing Railway community needs separate approval and the migration checklist below.

Use the standalone helper with your explicit private file and configuration:

```sh
node scripts/operator-discord.mjs connect wrangler.operator.json PRIVATE_ENV_FILE
node scripts/operator-discord.mjs register wrangler.operator.json PRIVATE_ENV_FILE TEST_GUILD_ID
node scripts/operator-discord.mjs verify wrangler.operator.json PRIVATE_ENV_FILE
```

Use `global` in place of a guild ID only when intentionally releasing commands for every invited community. The helper verifies app identity, OAuth redirect and signed endpoint; it never reads a Discord user token.

The checked-in `register:staging`/`connect:staging` scripts are pilot-specific safeguards. For your own installation use the standalone command/endpoint helper described below. They must never register another app's command namespace.

## Configuration and capacity

Queues in one channel have exactly one default; explicit names still select other queues. A paused default is not silently bypassed. Queues of the same game share ratings/economy settings. Participation locks are community-wide, not global across communities. Map pools are 1–100 names; Discord menus show at most 25 saved choices. Match selections are 1–5 maps. Prediction pots allow at most 1,000 bettors per book; gold uses safe integer balances and exact integer payout arithmetic.

New games without a saved player policy use a one-match leaderboard minimum and 30-day recency. Saved custom thresholds remain in effect. Profiles are created on queue entry, and administrators can seed ratings later outside an active match.

The optional voice rally opens one shared outbound Gateway connection for the installation, never one per guild. It currently refuses deployments needing multiple shards; add reviewed sharding/session-start limits before enabling it at that scale. The connection does not hibernate and has duration cost. Keep rally disabled when unused. No game connector is necessary for matchmaking; the generic game-end hook only prompts a captain and cannot settle results automatically.

## Backups and recovery

Use a private shell environment with `OPS_TOKEN` loaded from your ignored operations file. The key belongs in the environment, never command arguments or URLs. Backups include all community SQL tables (settings, matches, ratings, wallet/ledger, map/story snapshots, participation, deadlines, outbox and audit); they exclude provider-private SQLite internals and OAuth sessions. Files and checksum manifests are mode 0600.

```sh
node scripts/operational-backup.mjs checkpoint HTTPS_ORIGIN GUILD_ID PRIVATE_CHECKPOINT.json
node scripts/operational-backup.mjs backup HTTPS_ORIGIN GUILD_ID PRIVATE_BACKUP.json
node scripts/operational-backup.mjs restore HTTPS_REHEARSAL_ORIGIN GUILD_ID PRIVATE_BACKUP.json RECOVERY_COPY
node scripts/operational-backup.mjs inspect HTTPS_REHEARSAL_ORIGIN GUILD_ID PRIVATE_INSPECT.json RECOVERY_COPY
```

Store a dated checkpoint and SQL backup before upgrades/imports and after each completed migration stage. Maintain at least two private copies outside the Worker account; do not publish these with a source release. Cloudflare SQLite PITR provides provider-level recent recovery; the explicit SQL snapshot remains an independent inspectable copy. Rehearsal restore is bounded to 25 MB, 50,000 rows per table and supported schema epochs; a larger dataset needs a reviewed paged backup/batch plan.

Restore rejects a populated object and foreign community ID, verifies known tables/columns and checks every wallet against its ledger. The checksum CLI detects file damage before transmission. The restored copy is read-only: no settings/import/results/actions, alarms, rewards/tax or Discord delivery. It preserves source app identity for inspection. Do not remove this guard to make a recovered bot live; that needs a separate reviewed activation/cutover process with app/message remapping and exactly one writer.

For a failed deployment, first pause new admissions, inspect request IDs/blocked work, and take a backup. Epoch 6 adds lobby bindings and shared scheduling to epoch 5's additive tables and match semantics. Do not redeploy an epoch-5 or older bundle against data that has accepted epoch-6 writes; prefer a compatible forward repair. Rewinding data after paid settlements or newer games can lose accepted actions; reconcile those changes and obtain cutover approval first. The existing Railway production bot remains the rollback service during this pilot.

## Legacy migration rehearsal

Take a consistent read-only SQL export. Stop/resolve active matches and open/frozen prediction books before the eventual production copy; do not infer their state or drop stakes. Review explicit source-channel → community/game/queue mappings. Test on an empty destination game/copy before any production import.

Use `convert-legacy.py` for ratings/matches/history, with its explicit mapping file and preview in the organizer app. It retains the M4 bounds (500 players/matches, 8,000 rating changes). Use `convert-economy.py` for complete original gold balances and ledger, plus optional final prediction metadata:

```sh
python3 scripts/convert-economy.py CSV_DIRECTORY --community SOURCE_COMMUNITY_ID \
  --source-id UNIQUE_SNAPSHOT_ID --channels LEGACY_CHANNEL_ID,OTHER_CHANNEL_ID \
  --output PRIVATE_ECONOMY.json
node scripts/import-economy.mjs HTTPS_ORIGIN GUILD_ID DESTINATION_GAME_UUID PRIVATE_ECONOMY.json preview
node scripts/import-economy.mjs HTTPS_ORIGIN GUILD_ID DESTINATION_GAME_UUID PRIVATE_ECONOMY.json apply
```

The converter reads files only. It validates balances against the complete original ledger, running balances, final book states, source sides and original payout/refund amounts. Unix seconds become milliseconds. It does not manufacture an opening adjustment for missing history. Final prediction metadata is imported separately from live books; the already-copied ledger is never paid again. Wallet import requires an empty destination game; source ID/digest binds repeat imports and prevents reseeding.

Legacy gold was community-wide. The new product has community/game wallets. Map the existing balance to exactly one chosen game; do not copy it into every game. Other games start their own new wallets when enabled. Historical settings, Discord card IDs and external profile associations require deliberate mapping/review, not blind settings restore. Quiz data is explicitly excluded until refreshed.

Reconcile source and destination counts, rating totals, exact gold balances/ledger, immutable prediction outcomes and privacy rules. Compare after a Worker restart and a recovery-copy restore. Keep fixture results distinct from real history. M10 rehearsals do not authorize production import or DNS/Discord endpoint switching.

## Monitoring and low cost

Use `operator-discord.mjs verify CONFIG PRIVATE_ENV_FILE` for an independent installation's health and application identity. The pilot's `doctor` reads its staging configuration. API failures expose request IDs; safe logs omit credentials, OAuth callback codes and request bodies. Logs are sampled at 10%. Missing Discord permissions block deliveries; fix them and refresh/recover the current card. Ambiguous sends become visible blocked work after bounded reconciliation instead of unlimited duplicate posts.

Record a dated project billing baseline. The owner requested a warning strictly above $50 cumulative migration-attributable Cloudflare spend, including staging, production, storage and backups, excluding Railway. Account totals may include unrelated projects. Use resource-attributed actual billing when available; estimated usage cost must be labeled separately. `cost-report.mjs` detects unavailable/stale observations, does not interpret null as zero, and returns exit 2 for the threshold warning. Do not automatically interrupt accepted games at that threshold.

The current saved Wrangler credential may not grant the billing API. If denied, the active collector/notification acceptance gate remains unresolved until an operator supplies read-only billing/analytics access or a reviewed billing snapshot. Do not claim monitoring is active merely because the arithmetic helper exists.

## Acceptance checklist

- [ ] Own account/config/resources and own Discord application verified; no shared active writer.
- [ ] OAuth, bot invitation, signed PING, commands and organizer/member permissions tested.
- [ ] Discord action → persistent domain state → Discord card → web record verified.
- [ ] Both catalog and custom games, multiple queues/shared game rating scope tested.
- [ ] Gold off/on, additive stakes, cancel/close/refund/settle, tax floor/grace and final correction policy tested.
- [ ] Map modes, ballot retry, vote-as-check-in, roster change and restart tested.
- [ ] Stories omit streaks below three; before/after snapshots verified.
- [ ] Backup checksum, independent read-only restore, ledger reconciliation and no outgoing delivery verified.
- [ ] Real delivery latency and project-attributed billing warning path accepted.
- [ ] Production freeze/delta/reconciliation/cutover/rollback approval complete before switching the old bot.

Reference: [Cloudflare SQLite storage and PITR](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/), [Durable Object alarms](https://developers.cloudflare.com/durable-objects/api/alarms/), [Discord interactions](https://docs.discord.com/developers/interactions/receiving-and-responding), [Gateway](https://docs.discord.com/developers/events/gateway), and [Durable Object pricing](https://developers.cloudflare.com/durable-objects/platform/pricing/).

## Optional game lobby connectors

Lobby support defaults off per game. See [the lobby journey, capability matrix and extension contract](lobby-integration-handoff.md) before enabling it. Only the AoE2 HTTP adapter is implemented; live occupancy remains unavailable. Local/hosted egress must permit HTTPS to `data.aoe2companion.com`. Profile existence checks do not prove account ownership.

The first lobby upgrade uses schema epoch 6 and a versioned, binding-checked external completion hook; update any old hook caller before testing. Internal polling needs no hook token. Use isolated staging to validate an actual game's start/end, restart and manual report journey before release. Do not register commands, deploy or migrate production as a side effect of running local tests.
