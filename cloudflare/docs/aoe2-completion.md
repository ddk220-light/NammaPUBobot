# AoE2 feature completion — 0.9.0

This release implements the AoE2 gaps from the remaining-features plan. Other game adapters, automatic winner reporting, rating-model changes, quizzes and voice/audio announcements are outside this release.

## Using the features

In the organizer app, enable **Track lobby start/end (manual results)** on the AoE2 game. Enable **Civ picking**, **Stories**, and, if wanted, **Gold / Predictions** on its queue. Existing settings are preserved; deployment does not silently enable modules. Complete team formation, then a captain attaches the actual lobby with `/lobby attach reference:…`.

- **Profiles and played civilizations:** `/lobby link profile_id:…` resolves a public AoE2 profile and saves a self-declared link. Public existence is not account ownership verification. The bot also tries unique normalized/strongly similar names after actual start evidence. A full team with three independently declared profiles can identify its one remaining player. Ambiguous names, missing anchors, conflicting teams, extra guests and already-owned IDs are left alone. Inferred links never become anchors for further team guesses. Gameplay and manual results continue with missing links.
- **Corrections:** `/profile show` identifies inferred links. `/lobby link` confirms an inferred link as self-declared; `/profile unlink` rejects it and prevents that same automatic association from returning. Assignments to another Discord user still require explicit organizer reassignment. No guess overwrites an existing link.
- **Statistics:** actual civilizations come from the game provider, keyed by linked game ID and match/player, independently of picker suggestions. `/profile show` and the web player profile show civilization games, wins, losses, draws and inferred-link counts. Win rate is wins divided by completed games, including draws. Undone/aborted games, outgoing/substituted players and removed/reassigned links are excluded. Corrected evidence updates the stored game ID and side. Missing civ evidence can be enriched for up to 15 minutes after a reported result; unlinked players have no attributed civ record.
- **`/civpick minutes:3 redo:false`:** Defaults to your active match; `match:…` is an optional explicit selector. the existing 8-base/4-DLC shared pool plus unlimited Random. One exclusive non-Random pick per player, choices hidden until all finish or the 1–10-minute deadline expires. Missing choices become Random (timed out). Only the initiator or an organizer can redo, with a 30-second minimum interval. Old buttons cannot affect a new generation. Roster changes invalidate the round. Explicit choices influence the next pool for 24 hours; offered/unpicked and Random choices do not. The expired Danes/Saxons/Varangians promotion remains expired.
- **Automatic betting closure:** verified start timestamps close betting, including when first observed in a finished-game response. Full/disappeared lobbies are not game starts. Fresh pre-game occupancy is required for stake/cancel changes after attaching a lobby. Captain/admin manual close remains available. Late stake or cancellation activity at/after the start cutoff refunds the affected pool; unresolved start evidence at reporting also refunds it. Retries cannot reopen or double-settle a book. Results and ratings remain player-reported.
- **Live occupancy:** the shared AoE2 Companion WebSocket snapshot drives joined/missing linked-player lists, unlinked counts, occupied slots and capacity. Unknown capacity never means full. Disconnect/reconnect clears the snapshot. Match polling updates cards about every 30–35 seconds before start and 120–125 seconds afterward. HTTP provider Retry-After is honored independently of fresh socket occupancy.
- **Banter:** all eight Railway storyline families, thresholds, drama scores, diversity rules, phrase alternatives and win/loss payoffs: perfect/cursed pair, best/worst teammate, opposing streak, teammate streak, deadlock, personal form, trio and lineup. Each match saves its selected facts and roster; a changed roster rebuilds them, and a restart/result retry preserves them. Both teams' streak leaders and ties are shown; only a unique overall leader gets the special text announcement. History is bounded to the same game/channel, the preceding 90 days and 5,000 matches. Random ties use a stable match seed; identical text selection across Python's numeric IDs and the new UUID-based runtime is not promised.

## Storage and platform boundaries

Community schema **7**, forward-compatible with schemas 1–6 on startup. New tables:

| Table | Purpose |
| --- | --- |
| `civ_rounds` | Private picks, roster, options, generation, initiator and deadline |
| `civ_pick_history` | Explicit picks used for 24-hour pool rotation |
| `identity_rejections` | Associations explicitly rejected by users/organizers |
| `played_selections` | Actual per-match, linked-player civilization and its provenance |
| `bet_start_evidence` | Effective start cutoff, observation time and source event ID |

`profile_refs` remains the Discord ↔ game ID mapping store. Inference provenance includes method, confidence and source match; the control audit records automatic links. The existing matches, wallets, ledger, outbox and alarm remain authoritative. Civ picks are not put in the public match JSON. Corrected results are reflected by civilization-stat queries against current match state.

Schemas 4–7 can be restored to isolated read-only recovery objects. Older backups are not required to contain new schema-7 tables; complete schema-7 manifests are required. Do not deploy an epoch-6 Worker over a community that has accepted epoch-7 writes. Use a compatible forward fix or reviewed isolated recovery.

The new `AoE2LobbyFeed` Durable Object is shared per application. Connections are leased for 120 seconds and shut down after leases expire, with a 30-second alarm cadence. No permanent per-community socket or paid relay is created. Outbound sockets accrue active Durable Object time; `GET /ops/aoe2/feed` reports current-instance connection duration and lease state without extending a lease. `GET /ops/aoe2/lobbies/:id` is an OPS-authenticated public-provider diagnostic and renews a lease. This is not a measured billing-cost estimate. Feed state is ephemeral and is reconstructed from the provider after restart.

Adapters supply normalized observations, including an optional `selection`. The picker obtains a game-specific catalog through `selectionCatalog`; only AoE2 is available now. Match orchestration, persistence, predictions and stories remain shared platform behavior.

## Validation

`npm run check`: **702 tests in 23 files**, server/web type checks, web build and Worker dry-run build. Repository-wide Ruff and all 11 legacy/economy converter tests pass. Tests run actual SQLite Durable Objects with mocked external boundaries, signed Discord requests, restarts and alarms. The pre-existing test-runtime teardown/alarm diagnostics do not fail the suite.

New coverage includes exclusive simultaneous claims, interaction retries, hidden choices, unlimited Random, timeout/restart, redo authorization and stale buttons, substitutions, partial/conflicting identities, correction rejection, actual-versus-picked civs, undo exclusion, cutoff/refund ledger reconciliation, stale occupancy, Retry-After with a healthy socket, and bounded post-result enrichment. Golden fixtures generated directly from Railway cover all eight banter types and compare candidate data/scores, pre-game phrases and both outcomes across 17 histories.

Deployment and live verification are recorded in [the release evidence](../ops/aoe2-completion-2026-10-27.md). A real human-operated AoE2 start/end, real Discord button/slash clicks, and OAuth sign-in remain live acceptance checks. The user can run that game later; synthetic provider events and signed request fixtures are not presented as a real played game.
