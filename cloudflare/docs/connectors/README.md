# RTS connectors

**Current scope (2026-10-29):** the [final implementation plan](implementation-plan.md) selects AoE2 DE, AoE3 DE, AoE4, AoM Retold, BAR, Zero Hour/GeneralsOnline (access-gated) and Warcraft III/W3Champions. It supersedes the original ten-game rollout. Original Generals, SC2, Northgard and Tempest Rising retain historical compatibility but are excluded from new rollout. The table below describes implemented capabilities; automatic workflows still require acceptance.

The connector layer supplies account and game evidence to the existing bot workflow. Captains still confirm results before ratings or economy settlement change. Every catalog game can use manual account declarations, lobby references and reporting, even when a provider is disabled, unavailable or requires credentials.

This release is cloud-only. It retains provider replay references and availability; it does not upload, download, parse or archive recordings. A temporary provider URL is not a stored replay. The proposed 30-day retention policy belongs to a later storage project.

## Supported editions and boundaries

| Catalog ID  | Edition/provider                   | Implemented public evidence                                                                                                     | Release boundary                                                                                          | Maintenance                          |
| ----------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| `aoe2`      | AoE2 DE / Companion                | Shared waiting-lobby feed; bounded verified-player ongoing feed; native facts, civilizations and conditional replay links       | Recertify automatic lifecycle; ongoing listing is a bounded subset                                        | [AoE2](aoe2.md)                      |
| `aoe3`      | AoE3 DE / World's Edge `age3`      | Profiles, waiting lobbies, direct match lookup and recent history                                                               | No verified general public ongoing feed; unknown civilization IDs retained                                | [AoE3](age3.md)                      |
| `aoe4`      | AoE4 / World's Edge and AoE4 World | Native profiles/lobbies/results plus bounded AoE4 World ranked 1v1 ongoing feed                                                 | Distinct provider IDs; validate custom/private coverage and exact continuity                              | [AoE4](age4.md)                      |
| `aom`       | AoM Retold / Athens `athens`       | Profiles, waiting lobbies, match/history facts, gods and replay references                                                      | No general ongoing API established; earlier editions excluded                                             | [AoM](age-mythology.md)              |
| `bar`       | Beyond All Reason                  | Public waiting/running battles and replay summaries                                                                             | Battle/server-match/replay IDs stay separate; unknown live factions stay unknown                          | [BAR](bar.md)                        |
| `zero_hour` | Zero Hour / GeneralsOnline         | Credentialed adapter and source-derived contracts                                                                               | Supported integration key, permissions and real-game acceptance required; otherwise excluded from release | [GeneralsOnline](generals-online.md) |
| `wc3`       | Warcraft III / W3Champions         | Profiles, ongoing standard 1v1/4v4, bounded player history, exact finished-match reconciliation and optional Battle.net linking | No arbitrary custom/private or waiting lobbies; automation requires capability/mode acceptance            | [Warcraft III](wc3.md)               |

Original Generals, SC2, Northgard and Tempest Rising are legacy catalog entries for stored accounts, history and in-flight reporting. They cannot be newly selected through setup, their automatic discovery/observation is disabled, and daily probes omit them. Legacy account/history paths remain covered by compatibility tests.

Public `/lobby list phase:waiting|running` and the organizer's live-list buttons return up to ten actual records from bounded snapshots. They honor provider and capability switches independently of automatic-observation enablement. Browsing does not bind matches or change ratings. Unsupported feeds and unavailable providers are shown explicitly.

A manifest describes provider support separately from configured credentials, operational switches and real-game acceptance. An `available` operation in the manifest does not certify a newly implemented automatic workflow. See [acceptance](acceptance.md) before enabling one.

## Design and maintenance entry points

- [`src/games/types.ts`](../../src/games/types.ts): capabilities, normalized provider failures, lossless string identifiers, evidence and replay reference contracts. Bot match, native lobby, native match and platform lobby IDs have separate fields.
- [`src/games/registry.ts`](../../src/games/registry.ts) and [`catalog.ts`](../../src/games/catalog.ts): one adapter registry and versioned catalog. Existing game identifiers retain their meaning; Zero Hour has a separate entry.
- [`src/games/transport.ts`](../../src/games/transport.ts): fixed allowed provider hosts, bounded JSON reads, timeouts, lossless wide integers and normalized HTTP failures. Title-specific decoders detect application errors inside successful HTTP responses.
- [`src/storage/provider-coordinator.ts`](../../src/storage/provider-coordinator.ts): shared provider throttling, short-lived cache and diagnostics. AoE2 retains its existing shared feed, rather than opening permanent sockets per community.
- [`src/domain/lobby.ts`](../../src/domain/lobby.ts) and [`src/storage/lobby-store.ts`](../../src/storage/lobby-store.ts): compatible observation events, binding generations, roster checks and captain-confirmed evidence. These decide whether evidence can affect an active bot match.
- [`src/auth/game-accounts.ts`](../../src/auth/game-accounts.ts): provider ownership proofs. A successful public lookup remains different from ownership verification; declared/organizer-assigned links retain their trust state.
- [`tests/fixtures/connectors`](../../tests/fixtures/connectors): small, sanitized and versioned provider examples. A decoder change needs a representative fixture and a failure case, preserving unknown IDs and missing fields.

Do not let adapter code write ratings, wallet balances or Discord messages. Keep authoritative settlement and the durable Discord outbox in the existing workflow. Ambiguous rosters, unsupported modes and inconclusive outcomes must remain manual.

## Check and release

From `cloudflare/`:

```sh
npm ci
npm run test:connectors   # focused offline adapter/auth/probe contracts
npm run check             # type checks, all offline tests and dry-run builds
npm run provider:check -- --output dist/provider-check.json
npm run build:metadata
```

The live provider command is separate from ordinary tests. It makes bounded read-only metadata requests and produces only safe status diagnostics. Missing integration credentials are `access-blocked`, never a successful test. It neither tests account ownership interactively nor replaces real-game acceptance. [Verification](verification.md) records the completed implementation checks. [Operations](operations.md) explains credentials, sample IDs, switches and recovery.

The source baseline was reconciled with deployed staging 0.18.1 before workflow changes; see the [baseline evidence](baseline.md). Build and deployment scripts inject the actual Git revision, dirty flag and source-tree hash into `/health`. The new package version is 0.19.0; a version number alone is insufficient to identify the running code.

Roll out each wave to isolated staging and selected communities only after its acceptance record is complete. Use additive migrations and maintain the current backup/restore path. If a provider breaks, disable its capability and keep manual reporting available; do not roll back an additive database to an incompatible version. Existing migration-spend reporting remains in force; no replay infrastructure or paid parsing service is introduced.
