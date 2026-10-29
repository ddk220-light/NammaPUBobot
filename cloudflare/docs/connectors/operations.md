# Connector operations

Applies to the 0.19.0 connector foundation. Last updated 2026-10-29 UTC. Use [acceptance.md](acceptance.md) to decide whether a new automatic observer may be enabled; a public API check alone does not grant that acceptance.

## Configuration and failure isolation

| Setting                                                       | Purpose                                                                                                                                                                                                                                                                                                |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Per-community game connector settings                         | Enable an available observer for that game; configure optional public-lobby name discovery. Manual reporting remains available.                                                                                                                                                                        |
| `CONNECTOR_ACCEPTED_GAMES`                                    | Comma-separated catalog IDs with reviewed controlled-game acceptance. Every observer, including the updated AoE2 integration, remains gated unless listed. Existing account links and manual reporting remain available.                                                                               |
| `CONNECTOR_ACCEPTED_CAPABILITIES`                             | Additional acceptance for new ongoing feeds and WC3 observation modes. Examples: `aoe4.ongoingDiscovery:rm_1v1`, `wc3.lobbyObservation:1`, `wc3.lobbyObservation:4`. Game-wide approval alone cannot enable these paths. Use an unqualified operation only after all its supported modes are accepted. |
| `CONNECTOR_DISABLED`                                          | Comma-separated catalog IDs, provider names, or `catalogId.operation` entries. Examples: `aoe3`, `worlds-edge`, `aoe4world`, `bar.replayReferences`, `aoe2.lobbyDiscovery`. The manifest's operation names are used verbatim.                                                                          |
| `GENERALS_ONLINE_API_KEY`                                     | Supported read-only integration key for GeneralsOnline. Verify actual lobby permissions with the provider. Never substitute a player's game-session token.                                                                                                                                             |
| `BATTLENET_CLIENT_ID`, `BATTLENET_CLIENT_SECRET`              | Registered Battle.net app for ownership linking and public API service grants. Configure the bot's HTTPS callback.                                                                                                                                                                                     |
| `BAR_CLIENT_ID` and the registration's required client secret | Registered BAR OAuth application; unavailable registration leaves declared links working. Follow [account setup](accounts.md).                                                                                                                                                                         |
| `CONNECTOR_TOKEN`                                             | Existing authenticated observation ingestion, separate from provider credentials. Keep it server-side; incoming evidence still passes community, binding and roster generation checks.                                                                                                                 |
| `OPS_TOKEN`                                                   | Protected operational diagnostics. It is not a provider credential or account-ownership proof.                                                                                                                                                                                                         |

Steam OpenID does not require a Steam Web API key. It verifies control of the Steam identity, not ownership of the game or participation in a particular match. Declared and organizer-assigned links must retain their trust state, including after a successful public profile lookup.

`GET /api/communities/:guild/connectors` requires organizer authorization and reports each game's capability availability, last successful observation, failures, unresolved bindings and observation delay. `GET /ops/providers` requires `OPS_TOKEN` and returns compact per-host coordinator diagnostics: `lastSuccessAt`, `lastAttemptAt`, `lastFailure`, `failures`, `throttled`, `invalidResponses` and `retryAt`. Existing protected AoE2 feed diagnostics remain available. Do not infer a healthy match workflow from `/health` alone.

The shared provider coordinator bounds network reads, deduplicates/cache-shares suitable public requests, honors provider retry instructions and records failure states. Credentialed/private responses and replay metadata are not cached as public data. AoE2's existing shared lobby feed remains the only permanent feed for that application and closes when its tracked leases expire. Community orchestration polls active bindings at approximately 30 seconds before start and 120 seconds during play, with bounded retries and existing match expiry. Unknown, invalid or missing evidence never proves a start, finish or winner.

## Deterministic checks and daily live probes

Ordinary pull-request checks run `npm run check`: server/web type checks, all offline tests (including adapter contracts and SQLite Durable Object workflows), the web build and a Worker dry-run build. `npm run test:connectors` selects connector/account/probe tests for development. Tests mock providers; builds do not depend on live game APIs.

Run read-only checks separately, from `cloudflare/`:

```sh
npm run provider:check -- --output dist/provider-check.json
npm run provider:check -- --game aoe3,aom --output dist/age-provider-check.json
```

The command uses the actual adapter decoders and checks normalized response semantics, not just HTTP status. It performs at most 12 provider requests per game, bounds each JSON body to at most 2 MB, and opens at most one AoE2 WebSocket for no more than 10 seconds. It closes the feed after reading a bounded snapshot. No replay URL is fetched, even when a reference advertises a recording. On Node versions supporting `--use-env-proxy`, the wrapper opts into the configured HTTP proxy without changing the environment's network policy.

Reports contain only game, operation, status, fixed diagnostic code, timestamp and request counts. They never include tokens, signed URLs, raw provider responses or private player histories. The exit status is nonzero when an operation fails; a successful process may still contain `access-blocked` and `unsupported` rows, which must be reported separately.

| Probe status     | Meaning                                                                                                                                                                                   |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `passed`         | A read returned valid normalized metadata. A valid empty public feed or unavailable-recording response may pass, without claiming a match or recording exists.                            |
| `failed`         | Transport/schema/application error, invalid normalized evidence, exhausted request budget, or a previously selected sample no longer found. Investigate before changing a fixture.        |
| `access-blocked` | Missing/rejected integration credential, missing sample ID, no current public lobby to observe, or an interactive ownership check the command cannot perform. This is not a passing test. |
| `unsupported`    | The game/provider has no supported operation. Manual workflow coverage belongs to offline and real-game checks.                                                                           |

Interactive Steam, Battle.net and BAR ownership flows always remain `access-blocked` in this command. Their offline callback tests and real sign-in acceptance are separate. SC2 profile/history probes use `BATTLE_NET_ACCESS_TOKEN` if explicitly supplied, or obtain an in-memory service token using configured `BATTLENET_CLIENT_ID`/`BATTLENET_CLIENT_SECRET`. A service token never establishes user ownership. No access or refresh token is written to the report.

The dedicated [provider workflow](../../../.github/workflows/connector-probes.yml) runs daily at 07:17 UTC and can be dispatched manually. It has read-only repository permissions and uploads the compact diagnostic JSON for 14 days. It does not deploy, publish replay files, edit community data or post Discord messages. The 14-day diagnostic retention is unrelated to the future replay-storage preference.

Configure public test identifiers with repository/environment variables when the research samples expire:

```text
CONNECTOR_PROBE_AOE2_PROFILE_ID / CONNECTOR_PROBE_AOE2_MATCH_ID
CONNECTOR_PROBE_AOE3_PROFILE_ID / CONNECTOR_PROBE_AOE3_MATCH_ID
CONNECTOR_PROBE_AOE4_PROFILE_ID / CONNECTOR_PROBE_AOE4_MATCH_ID
CONNECTOR_PROBE_AOM_PROFILE_ID / CONNECTOR_PROBE_AOM_MATCH_ID
CONNECTOR_PROBE_BAR_PROFILE_ID / CONNECTOR_PROBE_BAR_MATCH_ID
CONNECTOR_PROBE_ZERO_HOUR_MATCH_ID
CONNECTOR_PROBE_WC3_PROFILE_ID / CONNECTOR_PROBE_WC3_MATCH_ID
```

WC3 profile samples are full BattleTags; match samples are original matchmaking IDs or explicit `record:<database ID>` references. SC2 remains legacy and is omitted from default/scheduled checks. BAR's match sample is the replay hash, not the live battle ID or numeric server match ID; absent an explicit profile sample, its probe selects one current public participant instead of downloading the entire account directory. GeneralsOnline requires an authorized key and a real native match ID; a successful public monitoring endpoint is not a substitute. Keep credential values in secrets, not repository variables or command arguments.

## Last live verification

The implemented read-only command completed on **2026-10-28 at 20:00 UTC**: **30 passed, 0 failed, 14 access-blocked and 26 unsupported**. The [complete redacted per-operation report](evidence/2026-10-28-provider-check.json) contains the exact timestamps and request counts.

| Game                       | Passed | Access-blocked | Unsupported |
| -------------------------- | -----: | -------------: | ----------: |
| AoE2 DE                    |      6 |              1 |           0 |
| AoE3 DE                    |      6 |              1 |           0 |
| AoE4                       |      6 |              1 |           0 |
| AoM Retold                 |      6 |              1 |           0 |
| Beyond All Reason          |      6 |              1 |           0 |
| Zero Hour / GeneralsOnline |      0 |              4 |           3 |
| StarCraft II               |      0 |              3 |           4 |
| Original Generals          |      0 |              0 |           7 |
| Northgard                  |      0 |              1 |           6 |
| Tempest Rising             |      0 |              1 |           6 |

Blocked checks were interactive ownership verification or missing credentialed integration access. No provider-authenticated sign-in or controlled game was run. The public checks exercised profile, discovery, observation, match, history and replay-reference metadata for each of the five available public providers. They establish current connectivity and decoder behavior; the separate real-game gates remain pending.

## AoE2 Companion

Endpoints: `https://data.aoe2companion.com/api/profiles/{id}`, `/matches/{id}` and `/matches?profile_ids={id}`. Public lobby occupancy comes from `wss://socket.aoe2companion.com/listen?handler=lobbies`. The HTTP match endpoint can return 404 for an open lobby; disappearance or 404 must not advance the game lifecycle. No provider API key is currently required. Requests send the bot's identifying User-Agent.

Stable native profile/match IDs remain strings. Team/player data supplies civilization and optional `won` outcome evidence. ISO start/finish timestamps give elapsed seconds, not necessarily simulation time. Replay flags produce conditional `aoe.ms` provider references; they are not archived files. Declared/native identity and Steam ownership remain separate trust facts.

Live checks on 2026-10-28 exercised profile `199325`, match `517004874`, recent history, conditional replay metadata and the bounded lobby feed. The initial implemented profile probe exposed a real response-limit issue: this active public profile was about 692 KB, exceeding the previous 400 KB cap. The profile operation now has a bounded 2 MB allowance; match responses retain their smaller limit. This is a useful reason to retain operation-specific limits rather than unbounded JSON reads.

Test command: `npm run test:connectors`, plus the existing `lobby`, `lobby-discovery`, `aoe2-completion` and interaction tests within `npm test`. Live command: `npm run provider:check -- --game aoe2`. Verify controlled lobby-to-result continuity, provisional bindings, actual civilizations/duration and captain-confirmed statistics before claiming renewed certification.

## Recovering a provider

1. Check `/health` source metadata and the affected game's connector status. Separate a missing credential, acceptance gate, disabled capability, provider throttle and schema failure. A fresh HTTP success may still have a decoding error; inspect compact diagnostic codes.
2. Disable only the affected provider/capability when needed. Keep manual lobby attachment and captain reporting available. Do not reinterpret existing IDs or reassign accounts to work around a provider outage.
3. For throttling, honor `retryAt`/`Retry-After` and reduce discovery/request pressure. For expired credentials, fix registration/permissions or reauthorize; never copy an end-user game token into global configuration.
4. For schema changes, capture one minimal public example through an authorized read, remove identifiers not needed by the test, remove signatures/tokens and private history, and add a dated/versioned fixture. Preserve oversized integers before decoding. Add the relevant failure case before updating the title-specific decoder. Never turn a provider error into a successful empty response.
5. Run the affected offline contract and workflow tests, then bounded live checks. If identity, lobby continuity, result or duration semantics changed, repeat the affected real-game acceptance scenarios before enabling the observer.
6. If the incident crossed a binding replacement, inspect audit history and stale-event rejection. The existing match writer and Discord outbox remain authoritative. Use additive recovery and backup/restore procedures; do not deploy an older Worker against an incompatible database schema.

## Source identity and deployment

Use `npm run build:worker`, `npm run deploy:staging` or `npm run dev`, which wrap Wrangler through `scripts/build-metadata.mjs`. The wrapper injects Git `HEAD`, whether the workspace is dirty, and a deterministic SHA-256 of the Cloudflare source tree. `npm run build:metadata` prints the same identity without building or deploying. Direct raw Wrangler runs display `sourceRevision: unbuilt`; they must not be presented as a traceable release.

`/health` and bootstrap metadata expose `version`, `sourceRevision`, `sourceDirty` and `sourceTreeHash`. Use clean reviewed commits for releases, and compare these values after deployment. No timestamp is injected into the source hash, so rebuilding an unchanged tree is reproducible. The [baseline reconciliation](baseline.md) distinguishes recovered staging behavior from an unavailable original 0.18.1 source commit. Preserve that distinction in release notes.

Deploy accepted waves to isolated staging, validate the complete workflow there, then select communities. Provider configuration or credentials must not block unrelated games. Keep normal migration-spend checks and current cost-reporting policy; this foundation adds no paid parser or replay storage service.

## Read-only browsing and new acceptance boundaries

`/lobby list` and `GET /api/communities/:guild/connectors/:game/games?phase=waiting|running` read bounded public snapshots. The web route requires the normal Discord session and organizer authorization. Discord resolves configured queues only within the interaction channel. Public views strip replay references and private rooms; ten bounded embeds fit Discord's message budget. Organizer views retain the full returned roster. Each entry carries provider/source and observation time; sampled results are not the global population.

Public listing can operate before controlled-game acceptance or while automatic observations are off. Global provider/host and per-capability switches still apply. `connector.enabled` controls automatic observation; a public view never starts a match, closes predictions or settles results. Warcraft observations always enforce mode acceptance even when created by a listing-capable runtime.

The final default probe list is AoE2, AoE3, AoE4, AoM, BAR, Zero Hour and WC3. Public repeated reads may share one anonymous snapshot within a probe run; credentialed responses are not shared. The per-game request budget remains 12 actual provider requests. AoE2 ongoing snapshots use at most ten seconds and two MB, close the socket after capture and share a short cache inside the application Durable Object. Waiting browsing adds a bounded lease to the existing shared feed.

For Warcraft, use `CONNECTOR_ACCEPTED_GAMES=wc3` plus accepted observation modes such as `CONNECTOR_ACCEPTED_CAPABILITIES=wc3.lobbyObservation:1`. Add `wc3.ongoingDiscovery:1` only for accepted automatic discovery; public browsing does not require it. Only standard 1v1 and 4v4 are implemented. A stable authenticated Battle.net subject cannot be claimed by another Discord user by renaming the BattleTag. Same-owner renames require explicit unlink and re-verification.

Reusing a finished native game for a second bot match is rejected even if the provider's record alias differs. Current incoming evidence still requires binding generation, roster version, supported mode and time consistency; captain result confirmation remains authoritative.
