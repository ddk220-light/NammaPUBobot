# World’s Edge connector maintenance

The AoE3 DE, AoE4 and AoM Retold adapters share bounded JSON transport and a response envelope decoder in `src/games/worlds-edge.ts`. Title configuration lives in `aoe3.ts`, `aoe4.ts` and `aom.ts`; recorded publisher definitions are in `age-catalogs.ts`. Catalog version: **2026-10-28**. Existing bot catalog IDs `aoe3`, `aoe4` and `aom` are preserved.

## Endpoints and authentication

| Operation                   | Community endpoint                  | Parameters beyond title              |
| --------------------------- | ----------------------------------- | ------------------------------------ |
| Profile                     | `leaderboard/getPersonalStat`       | `profile_ids=[id]`                   |
| Steam-to-profile resolution | `leaderboard/getPersonalStat`       | `profile_names=["/steam/SteamID64"]` |
| Public advertisements       | `advertisement/findAdvertisements`  | None                                 |
| Direct match                | `leaderboard/getMatchHistory`       | `matchIDs=[id]`                      |
| Recent history              | `leaderboard/getRecentMatchHistory` | `profile_ids=[id]`                   |
| Replay references           | `leaderboard/getReplayFiles`        | `matchIDs=[id]`                      |

All are read-only GETs under `/community/`. Profile and match reads require no application credential in the checked publisher service. Ownership verification is a separate Steam OpenID flow: resolve only the authenticated Steam identity to the native profile. Public profile existence, a player name, and a manually declared link do not prove ownership. Xbox and other platform links remain declared or organizer-assigned.

The shared transport is injected through `AdapterRuntime.request`; production should use the provider coordinator. Requests have a fixed allowlisted host, bounded response size, timeout, no redirects and lossless integer decoding. Profile cache lifetime is five minutes; advertisements/matches/history use 30 seconds. Replay metadata uses no cache so a user request can refresh an expiring URL. History polling only serves active bindings; it is not a crawler or historical backfill service.

## Evidence and decoding rules

- `id` from an advertisement is `nativeLobbyId`; a completed record's `id` is `nativeMatchId`; Steam's `steamlobbyid` is `platformLobbyId`. Decimal strings preserve large identifiers. They are never interchangeable with the bot match UUID.
- The compatibility `externalId` identifies the current bound lobby during observation. Direct match lookup leaves `nativeLobbyId` unset; observation sets the association only after correlation checks.
- `profile_id` uses the title-specific `worlds-edge:<title>` namespace. `civilization_id` is preserved independently of its display label. Unknown catalog IDs are displayed as `Civilization <id> (unknown)` or `God <id> (unknown)`.
- Publisher teams are zero-based. The normalized `we:0`, `we:1`, … strings avoid confusing a real team 0 with the older AoE2 unassigned-team marker.
- Known result codes are `resulttype=1` win and `0` loss. Other, absent or contradictory member/report codes remain unknown. Report and membership collections sometimes renumber identical team partitions; equivalent partitions are accepted, while contradictory grouping becomes unknown rather than contaminating a result. Multiple teams and unusual custom outcomes are retained as facts; the bot's two-team agreement check controls whether a report can be suggested.
- `startgametime` and `completiontime` are Unix seconds, converted to timestamp milliseconds. Their difference is explicitly elapsed seconds. Only AoE4's consistent per-player `counters.gt` is exposed as simulation seconds; mixed or missing counters remain unknown.
- Only advertisement `state=0` is presently decoded as open/full. A disappeared lobby never means a start or finish. Unsupported/new states produce a diagnosable invalid response until a fixture-backed decoder change is made.
- `result.code` is checked even on HTTP 200. Code 2 means not found; documented message shapes distinguish authentication/private-history errors and throttling. Unknown application errors are unavailable, not successful empty arrays. Missing arrays and mismatched IDs are invalid responses.
- Replay metadata with `datatype=0` describes a recording; other data types are not exposed as recordings. Expired links have no `sourceUrl`. AoE4 uses the provider's refreshable delivery endpoint. AoM's signed URLs are returned only after a fresh metadata request. There is no replay-byte fetch, upload, parser or archive.

## Lobby-to-match continuity

The observer first checks a currently advertised lobby. After it disappears, it requires a binding timestamp and at least two exact expected native profile IDs. It checks a known native match ID (or the lobby's numeric ID as a candidate), validates participant equality and bot-match creation time, then reconciles recent histories for at most two anchor profiles if necessary. Exact native IDs permit explicit attachment after a game has started; history-only candidates must additionally fit the tighter attachment-time window. A previously observed open lobby also excludes results that started before that observation (allowing one second for publisher timestamp precision). A history association is accepted only when exactly one candidate fits. Histories are sorted by start time, never assumed to be newest first. An absent/private history, incomplete profile set, earlier game, competing same-roster rematch or conflicting native match stays unresolved. The bot remains responsible for matching these native participants to both Discord teams and requiring captain confirmation before ratings change.

This is conservative candidate correlation, not proof that every lobby ID is a publisher match ID. Automatic observations remain pending real-game acceptance. Repeated games with the same roster and native lobby-to-result transitions are explicit release gates.

## Tests, verification and recovery

Run `npx vitest run tests/age-connectors.test.ts` from `cloudflare/`. The same contract cases run against all three title fixtures; additional cases cover AoE4 elapsed/simulation duration, custom multi-team games, optional-provider 404s, expired replay references and unavailable AoE3 recordings. Fixtures in `tests/fixtures/connectors/age-*-v1.json` are trimmed from public 2026-10-28 captures. Player names, Steam identities and replay URLs were replaced; unrelated options and counters were removed.

The separate provider-check command/workflow performs bounded live metadata reads. Public connectivity was checked **2026-10-28**; known match-ID lookup succeeded for AoE3 `66376255`, AoE4 `257612387` and AoM `44987874`. Those checks establish feasibility, not acceptance of a bot-controlled game. No controlled 1v1/team lifecycle is certified by these captures.

When the publisher changes a schema: disable only the affected provider/capability, keep manual play/reporting active, capture a small sanitized response, add a failing contract case, then update its decoder/catalog. Do not convert failures into empty lists, guess new result codes, reuse numeric catalogs between titles, or refresh the catalog at every community poll. After updating a catalog or decoder, repeat fixtures, direct-match checks and the affected real-game acceptance scenarios before re-enabling automation.

Sources: [LibreMatch community API reference](https://github.com/librematch/wiki/tree/main/src/rlink/community), [publisher domain migration](https://forums.ageofempires.com/t/age-of-empires-definitive-edition-update-100-2-31845-0/245735), [AoE4 World API](https://aoe4world.com/api/), [AoM collector host/title reference](https://github.com/erin-fitzpatric/aom-lambda/blob/ee51b41da4a60fcfe066ca5894e7e09849d3e6a5/extract-leaderboard/app.mjs).
