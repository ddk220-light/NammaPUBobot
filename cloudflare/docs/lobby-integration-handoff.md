# Lobby integration: implementation and acceptance

27 October 2026 UTC. The first remaining-feature chunk implements the HTTP/manual lobby journey and is deployed to the existing Cloudflare test installation. The test guild has 17 commands, including `/lobby`. Three live Discord match/economy scenarios passed with simulated player actions; the temporary demo controller was then removed. Railway remains production and was not changed. An actual AoE2 start/end lifecycle remains an open acceptance gate. See the [deployment and demo evidence](../ops/live-demo-2026-10-27.md).

## Player and organizer journey

Enable **Track lobby start/end (manual results)** for a supported catalog game under Games in the organizer app. It defaults off. Other catalog and custom games continue working manually.

- Join a queue so the game has a player profile, then use `/lobby link profile_id:…`. The provider lookup verifies public profile existence only; the mapping remains explicitly self-declared. An organizer can assign another player using `user:…`. Existing profile references and explicit organizer reassignment retain their conflict checks. Remove ambiguous extra provider mappings before attaching.
- After check-in and map selection complete, a captain or organizer uses `/lobby attach reference:…`, with `match:…` when needed. Supported AoE2 references include a numeric game ID, `aoe2de://0/ID`, and the recognized HTTPS match/lobby URLs. The generated HTTPS join landing link appears on the match card and in `/lobby status`.
- `/lobby status` shows saved evidence, freshness, mapping coverage and manual recovery. `/lobby unlink` stops tracking. Repeating attachment to the same active binding retrieves it; unlink/relink, replacement lobby and roster/profile edits require a fresh binding.
- Positive start evidence is displayed. It does **not** close predictions in phase 1. Positive completion evidence prompts the losing captain only when every declared profile and both teams match. Players still report manually; provider observations never award ratings or gold.

## Capabilities and transport

| Capability | AoE2 | AoE4 / Generals / other catalog games | Custom games |
| --- | --- | --- | --- |
| Queues, matches, ratings, optional gold/stories/maps | Existing support | Existing support | Existing support |
| Public profile existence lookup | HTTP | No adapter yet | Manual |
| Explicit lobby attachment and HTTPS join link | Implemented | Manual | Manual |
| Positive start/end evidence | HTTP match endpoint; live acceptance pending | No adapter yet | Manual |
| Live lobby occupancy | Unavailable | Unavailable | Unavailable |
| Automatic winner or betting closure | Not implemented in this phase | Manual | Manual |

The adapter uses bounded, TLS-verified reads from `data.aoe2companion.com`, six-second timeouts and a 400 KB response ceiling. HTTP 404/publication delay, malformed results and outages never imply start or cancellation. Retry-After is respected; exponential failure backoff is capped at 15 minutes, with longer provider-requested delays honored. Successful checks target 30–35 seconds before start and 120–125 seconds after start. Only attached active matches are polled, at most four per alarm batch, and tracking stops at the match lifetime limit. These intervals are implementation settings, not measured provider latency or cost.

The former WebSocket snapshot experiment was removed from the runtime adapter. Live occupancy has not been validated economically or end to end. A future occupancy capability needs a separate transport/completeness/cost decision; no always-on per-community socket or paid relay is provisioned.

## Extending the platform

`src/games/types.ts` is the provider boundary. An adapter parses references, validates profiles, normalizes observations and supplies safe join URLs. It never writes ratings, wallets, database state or Discord messages. Register a future adapter in `src/games/registry.ts` and advertise only implemented capabilities in `src/games/catalog.ts`; catalog selection vocabulary distinguishes civilizations, factions and gods without implementing another picker here.

`src/domain/lobby.ts` validates normalized evidence. `src/storage/lobby-store.ts` owns the generic lifecycle: bindings, declared-profile snapshots, roster generations, evidence freshness, retry leases and shared deadlines. Game APIs are called outside transactions; acceptance rechecks the current community, game, match, adapter, external ID, binding and roster/profile snapshot inside a transaction. Settings disablement, substitutions, profile reassignment, unlink/relink, cancellation and lifetime expiry invalidate pending work. Only the latest observation and event ID are retained on the match; prompt state prevents duplicate delivery intents.

The existing community scheduler owns the single Durable Object alarm. Lobby jobs share it with check-in, lifetime, maps, economy and delivery jobs. The existing outbox handles prompt delivery; stale prompt generations are retired before sending and after delayed receipts. Match cards use saved state. No betting or rating operation is invoked by lobby evidence.

The optional authenticated `/connectors/game-ended` hook now requires the `lobbyEventSchema` version-1 envelope: `communityId`, `gameId`, `matchId`, `bindingId`, `rosterVersion`, `adapterId`, `eventId`, and a complete normalized `observation`. The old unbound five-field request is rejected. Internal polling uses the same acceptance boundary without needing connector credentials.

Civilization/faction picking and banter parity remain phases 3 and 4. They should reuse the shared match/round lifecycle and obtain game-specific catalogs or presentation from capabilities/adapters. This change does not claim those additional features are implemented for other games.

## Storage compatibility

Schema epoch **6** is a forward upgrade from epoch 5. Binding state lives in the existing match JSON and deadlines table; profile references and outbox intents remain in their existing tables. Epoch 4/5 backups remain readable; epoch 6 backups include the saved bindings and leases. Restore remains read-only, with alarms, provider polling and Discord delivery disabled. Do not roll back to the epoch-5 Worker after accepting epoch-6 writes; use a compatible forward fix or a reviewed isolated recovery.

## Validation and remaining gate

Run from `cloudflare/` with Node 22.12+:

```sh
npm ci
npm run check
python3 scripts/test-legacy-converter.py
python3 scripts/test-economy-converter.py
```

Validation passed: **658 tests across 20 files** (51 new lobby/command cases), server/web type checks, web build, Worker dry-run build, and 6 legacy plus 5 economy converter tests. The runtime still emits the checkpoint’s teardown/alarm diagnostic warnings; the runner completed with all tests passed and exit status zero.

The focused tests use real SQLite Durable Objects and mocked network boundaries. They cover eight-player identity/team matching, malformed identities/times/URLs, duplicate and stale evidence, unavailable/404/429 responses, long provider backoff, shared alarms, restart and read-only restore, and cancellation/substitution/relink/settings/profile changes during a fetch. Signed interaction tests verify deferred acknowledgement and explicit match/caller routing. The full suite also exercises the existing economy, ratings, maps, tenant isolation and delivery machinery.

Cloudflare, Discord and operator access are configured and verified. The earlier egress restriction is resolved: a public AoE2 Companion profile lookup returned HTTP 200 with the expected identity. That read does not establish in-game start/end timing or profile ownership. The deployed demo verified real SQLite state, Discord delivery/read-back, refunds, result idempotency, substitution/cancellation and a real check-in timeout. All 47 wallets reconcile with their ledgers; the original game data and waiting player are unchanged. Release preparation resolved the 89 Python Ruff findings in six Cloudflare converter/fixture scripts. Repository-wide Ruff and all 11 converter tests now pass; regenerated legacy fixtures have identical parsed content.

Before accepting the real-lobby integration, use the existing test application to demonstrate an actual eight-player game: link reviewed profiles, attach a lobby, verify the join link, observe start, restart the Worker while tracking, observe completion and deliver exactly one report prompt. Confirm bets remain manual and ratings unchanged until a captain reports. Measure provider publication lag separately from bot delivery latency and attributed active/idle cost. Exercise wrong IDs, missing profiles, provider outages and replacement rosters. Human Discord button/slash interactions and OAuth sign-in also need live acceptance; signed request and UI handler tests alone do not certify those journeys. Live occupancy remains explicitly unavailable. Keep this acceptance gate open until those results are recorded.
