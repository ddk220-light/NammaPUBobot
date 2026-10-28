# Community Games · Cloudflare 0.8

**Lobby integration, 27 October 2026 UTC:** the first backlog item is deployed to the Cloudflare test installation: optional game connectors, profile linking, explicit lobby attachment, durable start/end evidence and manual report prompts. Three live Discord match/economy demos passed with simulated player actions. Actual AoE2 lobby acceptance remains pending, and live occupancy is unavailable. See [deployment evidence](ops/live-demo-2026-10-27.md), [implementation and acceptance](docs/lobby-integration-handoff.md), and the [remaining feature plan](ops/remaining-features-plan.md).

Friends join queues, check in, receive teams and report results in Discord. Organizers configure communities and games in the web app. Each community has its own transactional SQLite Durable Object; queues for the same game share ratings and gold. Catalog and custom games use the same matchmaking behavior.

Player profiles are created automatically on joining a queue. Administrators can seed or adjust their ratings later. By default, players appear on the leaderboard after one completed match, with a match in the last 30 days; organizers can configure these thresholds.

This release completes the pickup UX update, optional gold predictions (M5), match stories (M7), optional map selection (M8), and operator deployment/recovery tooling (M10). Daily quizzes and civilization drafts are deferred. The existing Railway bot remains the production service.

## Run locally

Use Node 22.12+ and Python 3 for the offline migration checks.

```sh
npm ci
npm run check
python3 scripts/test-legacy-converter.py
python3 scripts/test-economy-converter.py
npm run dev
```

Local development uses local SQLite, never remote staging storage. Copy `.dev.vars.example` to `.dev.vars` for local Discord OAuth; register `http://localhost:8787/auth/callback` on your test application. Interactions require public HTTPS. Dependencies are pinned.

## What players use

- Join/Leave on the latest queue card, or `/add` and `/remove`. A channel always has a default queue; an explicit queue name selects another. Paused defaults stay paused. Roster changes edit the existing card when it is among the last five messages; otherwise they publish a fresh card and retire the previous one.
- Enabled queues also check every 15 minutes and move a buried card to the bottom. Disable **Keep queue card near the bottom** in the queue's web settings to stop periodic checks. See [queue visibility behavior and the 50-community cost estimate](docs/queue-cards.md).
- Check in once: explicit readiness remains valid for up to 15 minutes in that queue while an incomplete formation is repaired. Leaving, playing, expiry or replacement clears readiness. Manual `/manage queue start skip_check_in:true` remains organizer-only.
- Teams use captain balance, unrestricted balance or random assignment; captains are the highest-rated member on each side. The formed card has **Sub me**, which takes the next eligible waiting player and rebalances. With no replacement, the player remains in the match.
- The losing captain uses `/report loss`; a trusted game-end adapter can show the same direct Report loss button. Start a cancellation with `/report abort`; old Cancel match buttons cannot initiate one. Cancellation and draws need both captains' agreement on a separate request card; an admin's command cancels immediately. Terminal cards have no rejoin controls; joining again is manual.
- New matches have a maximum lifetime of 12 hours, measured from creation. A timeout cancels without ratings, refunds predictions, checks returning members' eligibility and holds the queue until someone explicitly resumes it. Settings can shorten the lifetime. Legacy active matches retain their original semantics.

## Administrator commands

`/admin` opens a public queue-status and match-history panel with action buttons. Anyone can open it and start matches, including uneven teams; `/queue start` does the same. Clearing and result overrides require Administrator, Manage Server, or a bot moderator role selected by name in the website. Captains can report their own team’s loss. See the [admin panel guide](docs/admin-panel.md).

Detailed administrator commands are grouped under `/manage` with Setup, Queue, Match and Player categories. Discord suggests the full paths as you type. These commands retain their Manage Server or Administrator requirement.

- `/manage setup`: `status`, `web`.
- `/manage queue`: `add`, `remove`, `start`, `clear`, `split`, `rally`.
- `/manage match`: `cancel`, `report`, `undo`, `recover`, `substitute`, `put`, `record`, `maps-redo`, `close-betting`.
- `/manage player`: `seed`, `hide`, `unhide`, `sync`, `restrict`, `unrestrict`.

`cancel` immediately cancels an unfinished match; `record` records an already-played game. `restrict` temporarily blocks queue entry, and `unrestrict` removes that block. Optional maps, predictions and rally still require their feature configuration. Match administration uses an explicit match ID from the web organizer. Queue creation and configuration remain in the web app opened by `/manage setup web`.

Everyday player/captain commands and card buttons keep their existing names, including `/add`, `/remove`, `/report loss`, `/report abort`, `/maps redo` and `/predictions close`.

## Optional engagement

**Gold and predictions:** enable both on a queue. Each community/game wallet starts with 500 play-money gold. Match players automatically back their own team; spectators choose a side. Personal buttons reflect the current balance and repeated clicks add stakes. Open bets can be cancelled. Captains/admins close betting manually. Reporting while betting is open refunds the pot. Closed two-sided pots pay integer proportional winnings; rounding is burned. Draws, cancellations, failed check-in and roster changes refund affected books. Correcting a paid result changes ratings/history while the labeled original settlement stays final.

The optional Thursday tax is off by default, protects 500 gold, grants initial seven-day grace and requires a betting opportunity. A positive bet kept until betting closes counts as participation; cancelled bets do not. UTC scheduling is configurable. Match participation rewards refill low balances by up to 100 gold, never beyond 500.

**Stories:** short game-agnostic pre/post-match text for 3+ win streaks, established rivalries, successful duos and repeated lineups. Saved story facts make post-match payoffs reproducible. One- and two-win streaks are omitted. No audio or paid AI service is required.

**Maps:** off by default. AoE2 gets a starter pool; other games use organizer-entered names. Choose random, captain/admin pick, or player vote; select 1–5 maps, plurality/approval voting, tie/no-vote policy, duration and recent-map avoidance. A chosen list belongs to one rated match; per-map series scoring is not included. Voting can count as check-in. Saved rounds survive restarts, and roster changes invalidate old controls. `/maps show` and `/maps redo` are available.

**Voice presence:** optional queue previews show up to five recent public voice participants who are neither queued nor playing, refreshed every five minutes without notification pings. The queue **Ping** button sends a separate invitation to eligible public voice participants, with a server-wide five-minute cooldown. `/rally` uses the same shared voice-only Gateway adapter to invite eligible members with bounded mentions/cooldown. Neither feature processes audio. The connection stops ten minutes after the last preview refresh, or an hour after the last rally request, whichever is later. Outbound sockets have duration cost and do not hibernate; the adapter supports one shard only. See [voice previews and cost](docs/voice-preview.md).

## Deploy your own copy

For agent access to the existing test installation, see the
[credential and permission setup](docs/agent-access-setup.md), including reading
Discord conversation history and the distinction between Codex network secrets
and deployed Worker secrets.

Follow [the operator guide](docs/operator-guide.md). Generate a standalone configuration for your account and use your own Discord application, secrets and storage namespaces. No hosted service or existing bot token is shared. Guild commands can be registered for testing before global registration.

```sh
node scripts/operator-config.mjs YOUR_WORKER_NAME https://YOUR_PUBLIC_ORIGIN
npm run build:web
npx wrangler deploy --config wrangler.operator.json
node scripts/install-operator-secrets.mjs wrangler.operator.json PRIVATE_ENV_FILE
```

Only the operator creates credentials or accepts provider terms. Base bot permissions are View Channel, Send Messages, Embed Links and Read Message History. Privileged Gateway intents are unnecessary for the default HTTP/button flows. Optional rank role/nickname decoration requires additional permissions separately.

## Durability and recovery

Settings revisions reject stale saves. Actions, ratings, bets, payouts, map ballots, deadlines and outgoing work commit together. A shared Discord rate gate coordinates requests. Domain effects are idempotent; Discord delivery uses nonces, bounded reconciliation and visible blocked states when an outcome remains uncertain.

Schema epoch 6 adds lobby bindings and shared scheduling to epoch 5. Do not redeploy an epoch-5 or older bundle after accepting epoch-6 writes. Use a compatible forward fix, or a reviewed snapshot recovery with one active writer. See [recovery and migration](docs/operator-guide.md#backups-and-recovery).

Operational backup/restore is private, bearer-protected and separate from community settings. Restore accepts only an empty nonproduction object and leaves it read-only, with alarms and Discord delivery disabled. It is a rehearsal/inspection tool, not an automatic failover switch. OAuth sessions are excluded; users sign in again. Backup files contain private community data and must remain private. There are no organizer-facing settings export/restore controls.

## Verification and cost

The workerd suite uses real SQLite Durable Objects and mocked Discord transport. It covers queue races, readiness, substitutions, stale controls, expiry, restart recovery, money invariants, map votes, story snapshots and tenant isolation. The 1,000-community local rehearsal checks isolation; its timing is not production latency or billing evidence. Live Discord/web acceptance is recorded separately under `ops/`.

Game API polling is off by default and runs only for attached active games with a supported connector enabled. No paid AI generation, voice audio or always-live game socket is started. `/lobby link`, `/lobby attach`, `/lobby status` and `/lobby unlink` use the shared game journey. `/connectors/game-ended` is an optional authenticated, binding-checked prompt hook; it never records a result or closes betting.

`npm run cost:check -- BILLING_SNAPSHOT.json` validates the requested $50 cumulative migration warning. It needs dated project-attributed billing data, including storage/backups and staging/production, excluding Railway. Null means unavailable. Connecting the collector/notification path remains a release gate if account access is unavailable; the helper alone is not active monitoring. Never infer actual cost from a load test or included allowances.

## License and previous pilot evidence

This project retains behavior and reference fixtures derived from [PUBobot2](https://github.com/Leshaka/PUBobot2) by Leshaka. Copyright (C) 2020 Leshaka and contributors to this fork.

GPL-3.0-only; preserve [LICENSE](LICENSE) and upstream acknowledgements when distributing. The Python production app is unchanged. Historical M1–M4 acceptance notes are in [the pilot archive](docs/m1-m4-pilot-history.md); those describe earlier UI behavior, not this release.
