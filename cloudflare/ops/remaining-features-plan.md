# Community Games remaining features backlog and implementation plan

**Implementation update, 27 October 2026:** phases 1–4 are implemented in Cloudflare 0.9.0, including optional inferred profile links and played-civilization statistics. See [current behavior and validation](../docs/aoe2-completion.md) and [deployment evidence](aoe2-completion-2026-10-27.md). The original plan below is retained for acceptance criteria; its older “planned/unavailable” statuses are historical. Actual human-operated game and Discord interaction acceptance remain open. Rating changes and additional game adapters remain deferred.

Status: 27 October 2026 UTC. Phase 1's HTTP/manual implementation, with game-neutral orchestration and an AoE2 adapter, is deployed to the existing Cloudflare test installation with schema 6 and `/lobby` registration. Three live Discord match/economy scenarios passed with simulated player actions; see [deployment evidence](live-demo-2026-10-27.md) and [remaining acceptance](../docs/lobby-integration-handoff.md). Actual AoE2 lifecycle and human interaction acceptance remain open; live occupancy is unavailable. Phases 2–6 remain planned. Railway production has not been changed or migrated.

The next priorities are AoE2 lobby integration, automatic betting closure, civilization picking, and match banter. Rating changes follow these features as a separate decision. OpenSkill is removed from the immediate implementation scope; the current TrueSkill model and historical trial remain available for that later decision. “Civilization picking” is the working interpretation of the requested picking feature.

## Backlog and sequence

| Order | Feature | Existing foundation | Work remaining | User acceptance checkpoint |
| --- | --- | --- | --- | --- |
| 1 | AoE2 lobby integration | Legacy Python lobby/profile adapters; Cloudflare has a generic game-ended hook | Profile linking, explicit lobby attachment, join link, live occupancy where supported, start/end evidence, recovery and manual fallback | Attach a real test lobby, watch joins, start a game, restart staging, and receive a result-report prompt |
| 2 | Automatic betting closure | Optional gold/predictions, manual captain/admin close, transactional ledger, refund and settlement paths | Consume verified start events; close atomically; handle delayed evidence, retries, outages and late betting | An eight-player game closes betting once and rejects old stake buttons; normal payouts and failure refunds reconcile |
| 3 | Civilization picking | Legacy shared-pool picker; Cloudflare civ module flag is disabled | Port the existing picker, catalogs, exclusive claims, private choices, timeout, redo and recent-pick rules | Eight players complete a round, including a collision, Random choices, a timeout and a redo |
| 4 | Match banter and streaks | Cloudflare already implements optional before/after text, rivalries, partnerships and 3+ streaks | Review timing and feature parity; handle final rosters, substitutions, duplicated events and corrected results | Review short before-match and after-match examples for wins, losses, ties and limited history |
| 5 | Rating and ranking changes | Current engines, shared game ratings, seeding, ranks and statistics; completed historical replay | Agree on what should change, validate any candidate in isolation, then prepare an explicit conversion if selected | Review changed standings and matchmaking examples; retain current TrueSkill unless a change is accepted |
| 6 | Complete journey and migration rehearsal | Independent Worker and backup/restore tools exist | Test all features together; scale import beyond pilot limits; rehearse real data safely and prepare rollback | Review a complete game night and a restored staging copy; make a separate migration decision afterward |

These are the next delivery chunks, not a renumbering of the earlier M1–M11 roadmap. Civilization picking fills the deferred civ gap; banter extends the existing M7 implementation. Map selection remains available. Quizzes stay deferred because their content needs refreshing. Advanced civ drafts/bans, voice playback and additional game connectors are not part of these chunks.

## Shared design

Keep queues, matches, ratings, wallets, picking rounds, deadlines and durable Discord deliveries authoritative inside the existing community SQLite Durable Object. Ratings and gold remain separate per community/game and shared between that game's queues. Add game-specific adapters behind the generic game journey; unsupported games retain manual lobby and result flows.

External game observations are evidence, not direct commands to award ratings or gold. Normalize them into versioned events carrying community, game, internal match ID, external match ID, roster/link generation, provider event identity, observed time, and provider occurrence time where available. Authenticate the event source, validate the binding, deduplicate it, and recheck current match state before changing anything.

Perform network reads outside database transactions. Apply accepted state changes, idempotency receipts, ledger changes and delivery intents together in one local transaction. The Discord card is a view of saved state; delivery failure must not repeat a monetary or rating operation. Render the newest saved generation, not an obsolete payload from a delayed job.

Add due work to the existing persisted scheduler. Each Durable Object has one alarm, so lobby work and civ deadlines must share the current next-due-job mechanism. Alarm callbacks can be delivered more than once; every action must tolerate retries. [Cloudflare alarm documentation](https://developers.cloudflare.com/durable-objects/api/alarms/).

Each optional feature needs organizer settings, useful disabled/unavailable states, concise Discord messages, audit entries, bounded retries, and a manual recovery path. Freeze relevant queue settings on the match/round so edits during play do not change its rules.

## 1 AoE2 lobby integration

### Player and organizer journey

1. An organizer enables the AoE2 connector for an AoE2 game. Generic queues continue working without it.
2. A player links their Discord identity to an AoE2 profile, or an administrator assigns a reviewed mapping. Label self-declared mappings honestly; looking up a public profile does not prove ownership. Resolve conflicting assignments rather than matching solely by display name.
3. After teams form, a captain/admin attaches the in-game lobby by supported URL or game ID. Resolve the default active match in the channel, with explicit selection when needed. Explicit attachment is the first release; do not depend on reading another bot's messages or arbitrary channel chatter.
4. Show the join link, supported lobby details, and joined/missing players when the provider can establish them. Distinguish open, full, starting, started, finished, unavailable and stale. Lobby occupancy is not proof of game start.
5. A verified completion prompts the losing captain to report, using the existing direct report flow. Automatically recording a winner is a separate capability and stays disabled in this first release.

### Build work

- Port the pure parsing and identity-matching behavior from `nammaoe2bot/features/lobby/`; verify current provider response shapes and allowed usage before selecting transport.
- Start with a short provider spike: establish reliable lobby lookup, roster freshness, start timestamp, end signal and result/team identity. The existing legacy match-details adapter uses the path-form AoE2 Companion endpoint; its old behavior is not a guarantee of current provider support.
- Record a capability matrix. If an endpoint cannot supply live occupancy, show the supported join-link/start/end features and label occupancy unavailable; fixtures alone cannot close the real-lobby acceptance gate.
- Persist lobby bindings, identity mappings, evidence timestamps, last successful observation, retry state, generation and next due check. Keep bounded recent evidence, not an unbounded copy of every provider response.
- Poll only attached active games. Provisional target: about 15–30 seconds during lobby preparation, slower completion checks after start, with jitter, backoff and provider rate limits. Measure actual event latency before accepting this target.
- Stop subscriptions/checks on unlink, cancellation, verified terminal state or the existing maximum lifetime. A lost lobby/listing entry is not a start or cancellation event. A generic `playing` match state currently means formation is complete and must not substitute for a verified in-game start.
- Reuse the generic game-ended hook after strengthening its event binding. Prevent cross-community use, stale roster generations, and replacement-lobby events affecting the current game.

### Transport and cost gate

Prefer bounded HTTP reads where they supply the required evidence. If live occupancy requires an outbound WebSocket, use an optional shared provider connector while there are active subscriptions, with reconnect/reconciliation and no polling when idle. Do not place a separate permanently connected client in every community object. Outbound WebSockets do not hibernate, so their duration cost must be measured rather than assumed negligible. [Cloudflare WebSocket documentation](https://developers.cloudflare.com/durable-objects/best-practices/websockets/).

The provider spike decides whether such a connector is necessary and where it can run economically. No paid relay or always-on service is assumed or provisioned by this plan. If that cost is unsuitable, ship the validated HTTP/manual subset and keep live occupancy explicitly pending.

### Acceptance

Test correct/wrong lobby IDs, missing and conflicting profiles, changing occupancy, lobby full without start, lobby disappearance, API 404/publication delay, 429, timeout, provider outage, stale and duplicate events, unlink/relink, replacement players, cancellation during a fetch, and restart recovery. A live test must demonstrate start/end evidence from an actual AoE2 game. Measure provider lag separately from the bot's processing/delivery latency.

## 2 Automatic betting closure

### Behavior

For an enabled, linked AoE2 match, verified in-game start closes betting automatically. A full lobby, completed check-in, map vote, or civ round does not itself prove start. Captain/admin manual closure remains available, including for games without a connector.

Extend the existing freeze/settlement machinery. Record the effective cutoff, the time it was observed, the closure reason and event ID. Duplicate observations of the same closure are successful no-ops. Closure and the final competing stake action are serialized; old buttons cannot add to or cancel a frozen bet.

Provider delay is a correctness issue as well as a latency issue. Preserve timestamped stake/cancel actions. If trusted start evidence arrives late and any betting activity occurred on or after the effective cutoff, conservatively refund the whole affected book before settlement rather than pay out a contaminated pool. This catches cancellations as well as new stakes. If the source cannot establish a trustworthy cutoff, retain manual-close/refund semantics instead of pretending automatic closure is safe. Result processing must reconcile available start evidence before settlement; unresolved automatic-mode cutoff evidence at reporting refunds the book. Already paid settlements remain final.

During loss of the fresh evidence required by automatic mode, pause new/changed stakes and explain how to close manually. Resume only when the adapter can establish that the linked lobby is still pre-game; a frozen/refunded/settled book never reopens because an older event arrives. This freshness policy is enabled only for a connector with a validated live status capability, not inferred from an absent match-details response.

### Preserved rules

- Gold is optional play money; retain dynamic personal stakes, repeated additions and own-team selection for match players.
- Keep open-at-report refunds, cancellation/draw refunds, one-sided refunds, roster-change protection and the existing tax participation rule.
- A stale generation cannot qualify participation or settle a replacement book. Record qualifying participation once at valid closure.
- Substitution after closure refunds according to the existing policy. Unlinking a lobby never silently reopens betting.

### Acceptance

Race a stake and cancellation against closure; deliver start twice, late and out of order; restart after persistence but before Discord delivery; report a loss before start evidence arrives; test empty/one-sided pools, disabled modules and substitutions. Verify every wallet against its ledger and ensure retries cannot double-pay, double-refund or double-count participation. Review the linked and manual fallback flows in `test-bot`.

## 3 Civilization picking

Port the current experience first. Keep it optional and independent of both betting and the lobby connector.

- One shared offer of 12 distinct civs: eight base-game choices and four paid-DLC choices, mixed together in the UI, plus unlimited Random.
- Preserve the existing catalog classifications initially, then validate the catalog against current multiplayer availability before release. Do not add a new ownership lookup; players choose a civ they own.
- The former Danes/Saxons/Varangians bonus window ended October 23, 2026 at 00:00 Asia/Kolkata according to the legacy configuration. Do not restart it during migration. These choices follow the normal catalog rules afterward. A future promotional window would require an explicit dated configuration.
- One successful claim per roster player and one claimant per non-Random civ, enforced transactionally. A rejected collision does not consume a choice. Keep assignments private until the round ends.
- Finish when all choose or the saved deadline passes; missing choices become `Random (timed out)`. Replace expired countdown wording with the terminal summary.
- Repeating the command retrieves the existing round. Explicit redo creates a new generation and invalidates old controls without extending deadlines accidentally.
- Preserve the rolling 24-hour explicit-pick cooldown and the existing shortage fallback within the base/DLC groups. Random and offered-but-unpicked civs do not enter the cooldown.
- A roster change invalidates the current round and requires an explicit redo for the new roster. Match completion/cancellation closes it. Suggestions never become assertions about the civ actually played.

Persist options, catalog version, roster generation, choices, deadline and terminal state in the community database. Schedule timeout through the shared alarm. Map voting remains a separate feature; civ picking does not implicitly mark everyone checked in or freeze betting.

Acceptance covers eight-player simultaneous picks, a collision, several Random choices, limited DLC access, exact timeout boundaries, reopen/redo, stale buttons, replacement players, restart, cooldown shortages, and the expired promotional window. Organizer enable/disable and saved settings must also work.

## 4 Match banter and streaks

The existing `stories.ts` already provides before/after messages. Complete the integration and parity review instead of adding a second story system.

Show the pre-match story on the formed-team experience using the finalized roster and captains, after relevant team drafting. Show the post-match story with the accepted result. Check current generation timing: facts are currently created during match creation, so captain drafting, substitutions and rebalancing must not leave claims about an obsolete lineup.

Preserve 3+ win-streak thresholds, tied leaders in text, the full streak announcement wording, established rivalries, and useful partnership facts. Retain bounded recent-history queries and minimum samples. Keep messages short and factual; lack of qualifying history is a normal outcome, not a reason to invent a story. This remains game-agnostic, optional text without paid model calls.

Snapshot the facts used for the pre-match message so post-match wording can resolve that story consistently. Rebuild or invalidate facts when the roster changes before play. Duplicate connector events must not post the story twice. Reporting, corrections and withdrawal must keep cards/history coherent without rewriting paid prediction settlements. Cancellation/timeout must not emit a victory celebration.

Acceptance includes no qualifying history, streaks of one/two/three, tied leaders, streak ended/extended, a rivalry reversal, a substituted captain, draw, cancellation, repeated report, result correction and a failed Discord edit followed by recovery. Review the actual wording both in the local flow preview and in staging Discord.

## 5 Rating and ranking decision

Keep the current production TrueSkill model as the migration baseline. The October 26 historical trial compared 629 matches and did not establish improved probability scoring from standard OpenSkill. No OpenSkill package is added to the application in this feature phase, and the analysis artifacts are retained.

After the other journeys are accepted, define the desired change explicitly: rating calculation, balancing policy, uncertainty handling, visible rank thresholds, or some combination. Ranking display changes need not imply changing the underlying model.

For a proposed engine change, evaluate fixed parameters on an untouched period and test how balanced teams would change. Review initial seeding, learning speed for new players, inactivity policy, point display and uncertainty floors. Keep replay/shadow results separate from authoritative ratings. Freeze and compare the actual conversion proposal, including per-player deltas, before any adoption.

All queues sharing one community/game must retain compatible rating settings. Never switch an established game by merely changing a dropdown. Any accepted conversion needs its own dated baseline, versioned history, current-match boundary, repeat-safe operation, explicit approval and tested rollback. Gold settlement history is not part of rating conversion.

## 6 Full journey and later migration rehearsal

Run the full journey in a separate staging game in server `706320010368516198`, channel `test-bot` (`1559279290577850368`), using Community Games Staging application `1559284610108752068`. Verify application/server/channel identity before every live test. Use existing fixture controls only within their valid scope; expired temporary simulators must not be silently re-enabled.

Run these acceptance sessions after individual milestones pass:

1. Eight players join, automatically form teams, finish readiness/maps if enabled, pick civs, attach a lobby, place additive bets, start the game, close betting automatically, report a loss, settle once and see the result story.
2. One player misses check-in; preserve valid readiness and replace from the queue. With no replacement, return to the supported waiting flow. Rebalance/invalidate affected picks and predictions safely.
3. A player substitutes or a captain requests cancellation during lobby preparation. Confirm the current command-only cancellation flow and opposing-captain agreement/admin permissions.
4. Disconnect the provider and restart the Worker during play. Recover evidence and deadlines; preserve manual reporting and safe refunds.
5. Deliver late start evidence, repeated end events and a corrected result. Verify no double rating update, settlement, message or tax participation.
6. Repeat a normal game with lobby, predictions, civs and stories disabled. Verify generic/custom games and independently enabled modules still work.

The existing pilot importer is bounded to 500 matches and 8,000 rating-history rows. The October 26 source snapshot contains 3,725 matches and 30,922 rating-history rows, so importing production requires a reviewed resumable batching/validation path rather than merely increasing upload limits. Earlier rating-chain inconsistencies also need an explicit archival/import policy. Preserve original facts and identifiers; do not silently repair, discard, or replay corrupted history into new ratings. One possible policy is a verified current rating baseline plus separately retained historical records with their provenance, but the exact import representation must be validated before adoption.

Rehearse current ratings, records, profile mappings, wallets/ledgers, prediction history, civ history needed for cooldowns, settings, and backup/restore in an isolated destination. Keep in-flight matches out of the first cutover by draining them on Railway. Verify row counts, foreign references, original outcomes, balances, settlement finality and representative player histories. Maintain one authoritative writer at eventual cutover; retaining Railway for recovery does not mean running both bots against the same live journey.

The completion artifact is a reviewed migration checklist with a dated source backup, validated destination, command/application routing steps, acceptance results and a feasible rollback. Rolling back after new Cloudflare matches requires reconciling those new writes; restoring an old snapshot alone is not a lossless rollback. Production migration remains a later separately approved action.

## Architecture review and release gates

| Risk | Resolution required before acceptance |
| --- | --- |
| Lobby disappears without starting | Require positive start evidence; never infer start from full/absent |
| Provider publishes start late | Preserve effective/observed times and stake actions; refund contaminated books before settlement |
| Connector retries or duplicate alarms | Durable event IDs, generation checks and atomic operations |
| Wrong match or community bound | Explicit attachment, scoped identity and external-ID validation; reject ambiguous mapping |
| Substitution makes facts/picks/bets stale | Version roster changes and invalidate dependent state transactionally |
| Slow outbound calls block interactions | Fetch outside transactions, acknowledge promptly and revalidate state afterward |
| Live WebSocket costs defeat low idle cost | Gate transport on measured active-time cost; share only where safe; stop when unused |
| Banter and civ code already exists elsewhere | Port documented behavior and reuse Cloudflare stories; do not create competing flows |
| Engine conversion surprises players | Keep it out of this phase; review explicit deltas and conversion separately |
| Full import exceeds pilot limits or contains gaps | Batch, reconcile, preserve provenance and rehearse before a cutover decision |
| “Tested” means fixtures only | Require real AoE2 evidence and real staging Discord interaction before integration acceptance |

For each phase: review the design, implement locally, run focused state/race/restart tests plus required repository checks, deploy only to the isolated staging target when implementation is authorized, then demonstrate the acceptance journey before advancing. Record code changes, migration/version compatibility, actual test coverage, latency and resource use.

Keep low idle cost: no paid AI for banter, no polling for unattached games, bounded retention and indexed lookups, and no second SQL server. Review attributed usage/cost manually at milestone checkpoints. The earlier spending automation remains stopped. If reliable cumulative migration-attributable actual spending crosses the owner's $50 warning target, flag it; unavailable billing data is not zero. Do not provision a new paid service or automatically stop accepted games.

## Source references

- [Current staged feature release](2026-10-13-milestone-release.md): M5 predictions, M7 stories, M8 maps and M10 recovery foundations; later UX fixes supersede its older cancellation-button description.
- [Legacy civ-pick behavior](../../docs/plans/2026-09-09-civ-pick.md) and [dated DLC trial](../../docs/plans/2026-09-22-civ-pick-dlc-trial.md).
- Current source: `src/domain/stories.ts`, `src/storage/play-store.ts`, `src/storage/economy-store.ts`, `src/domain/records.ts`, `src/worker.ts`, and `nammaoe2bot/features/lobby/` in the parent project.
- October 26 local rating trial: `rating-backtest-2026-10-26/report.md` in the task workspace. Direct read snapshot; production rating engine verified as TrueSkill.
