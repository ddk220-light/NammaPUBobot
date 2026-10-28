# RTS connectors

The connector layer supplies account and game evidence to the existing bot workflow. Captains still confirm results before ratings or economy settlement change. Every catalog game can use manual account declarations, lobby references and reporting, even when a provider is disabled, unavailable or requires credentials.

This release is cloud-only. It retains provider replay references and availability; it does not upload, download, parse or archive recordings. A temporary provider URL is not a stored replay. The proposed 30-day retention policy belongs to a later storage project.

## Supported editions and boundaries

| Catalog ID       | Edition/provider                              | Automated evidence                                                                                   | Manual or gated boundary                                                                                                | Maintenance                          |
| ---------------- | --------------------------------------------- | ---------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| `aoe2`           | AoE2 Definitive Edition / Companion           | Existing shared lobby feed, native profiles, match facts, elapsed duration, conditional replay links | Updated observer gated pending controlled-game recertification; captain reporting                                       | [AoE2](aoe2.md)                      |
| `aoe3`           | AoE3 Definitive Edition / World's Edge `age3` | Profiles, public lobbies, direct match lookup, recent history, recording availability                | New observers require acceptance; unknown civilization IDs retained; sampled recordings unavailable                     | [AoE3](age3.md)                      |
| `aoe4`           | AoE4 / World's Edge `age4`                    | Profiles, public lobbies, native match/history facts; optional AoE4 World enrichment                 | Custom/private games may be incomplete; enrichment 404 does not invalidate the native match                             | [AoE4](age4.md)                      |
| `aom`            | AoM Retold / Athens `athens`                  | Profiles, public lobbies, match/history facts, gods, temporary replay references                     | Earlier editions excluded; new observers require acceptance                                                             | [AoM](age-mythology.md)              |
| `bar`            | Beyond All Reason                             | Public battles, bounded account discovery and replay summaries                                       | Separate battle ID, server match ID and replay hash; OAuth needs registration; rematches require unambiguous continuity | [BAR](bar.md)                        |
| `zero_hour`      | Zero Hour / GeneralsOnline                    | Credentialed lobby and match metadata adapter                                                        | Integration key, actual permissions and controlled-game acceptance required; no assumed identity crosswalk              | [GeneralsOnline](generals-online.md) |
| `generals`       | Original Generals / named network             | None                                                                                                 | Network-specific declared identity, host lobby reference, declared facts and captain results                            | [Manual games](manual-games.md)      |
| `sc2`            | StarCraft II / Battle.net                     | Verified regional character linking; official history as supporting evidence                         | OAuth registration required; no general lobby feed, complete roster, duration or replay retrieval                       | [SC2](sc2.md)                        |
| `northgard`      | Northgard / Steam PC                          | Steam identity ownership verification                                                                | Lobby ID, clans, teams, duration and result are supplied by participants                                                | [Manual games](manual-games.md)      |
| `tempest_rising` | Tempest Rising / Steam PC                     | Steam identity ownership verification                                                                | Lobby/invite, factions, teams, duration and result are supplied by participants                                         | [Manual games](manual-games.md)      |

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
