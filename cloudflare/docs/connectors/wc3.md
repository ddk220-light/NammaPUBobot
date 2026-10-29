# Warcraft III / W3Champions

Adapter: `src/games/wc3.ts`. Race/mode catalog: `src/games/wc3-catalog.ts`, version `2026-10-29.1`.

This connector reads public W3Champions JSON metadata. It implements ongoing **matchmaking games**, not waiting lobbies. Initially it accepts melee **1v1 (`1`) and 4v4 (`4`)** only. Arbitrary hosted/private custom games (`CUSTOM=9`) are excluded from the provider's ongoing records. FFA and custom-map ladders such as Direct Strike are unsupported by this adapter, even when the public service lists them. Other melee enum values are held back until their schemas/modes are separately validated. Captains retain result authority and controlled-game acceptance remains pending.

## Routes and limits

All requests use the bounded shared JSON transport/coordinator and the exact HTTPS host `website-backend.w3champions.com`.

| Operation                             | GET route under `/api`                                         | Bound/cache                                                                |
| ------------------------------------- | -------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Public profile                        | `/players/{encodedBattleTag}`                                  | 1 request; 5 minutes                                                       |
| Ongoing discovery                     | `/matches/ongoing?pageSize=50&offset=...`                      | At most 2 pages / 100 source rows; 30 seconds                              |
| Current match for a bound participant | `/matches/ongoing/{encodedBattleTag}`                          | 1 request; 30 seconds                                                      |
| Exact finished lookup                 | `/matches/by-ongoing-match-id/{originalId}`                    | 1 request; 2 minutes                                                       |
| Finished provider-record lookup       | `/matches/{recordId}`                                          | 1 request; 2 minutes                                                       |
| Player history                        | `/matches/search?playerId=...&season=...&pageSize=20&offset=0` | Profile plus 1 history request; latest participated season only; 2 minutes |

Every response is limited to 1.5 MB. Ongoing discovery is a bounded sample, not a claim of complete platform coverage. Unsupported modes are filtered after validating the collection envelope; failed reads remain failures, including a failure on the second page. History validates each supported match contains the requested BattleTag and sorts by start time. It never substitutes global recent games for player history.

No credentials are needed for these reads. The ownership flow is separate: a public profile establishes existence, not Discord ownership, Warcraft ownership or an authenticated Battle.net subject. The adapter uses namespace `w3champions:battletag`, preserves the full case-sensitive BattleTag string and never sets `platformId`. Canonical redirects/renamed accounts are not silently followed or transferred. The organizer can relink a changed identity through the normal account flow.

The service is a community backend without a verified general request quota/SLA. Use shared caching and error backoff. API errors, throttling and malformed payloads do not become empty successful lists. No private API tokens or game-client sessions are used.

## References and normalized facts

- A raw original matchmaking ID is canonical, e.g. `DW1g4cYZw4`. `nativeMatchId` always uses this namespace.
- A 24-hex database record ID or `https://w3champions.com/match/{recordId}` becomes `record:{recordId}`. It must resolve through that record or a proven previous association before following a different identifier.
- `referenceKind` is `match`. **No `nativeLobbyId` or join URI is invented.** `externalId` preserves the actual bound reference, including a record alias when applicable.
- `providerIds` preserves `originalMatchId`, current `recordId`, phase-specific `ongoingRecordId` / `finishedRecordId`, and FLO identifiers when present. Ongoing and finished Mongo record IDs can differ for the same game. Previous verified phase-specific aliases survive reconciliation.
- Ongoing rows provide start time, map, roster and races. Their zero duration, unset end time and `won:false` fields are placeholders. All live outcomes remain `unknown`; the adapter emits no live duration. A UI may derive elapsed wall time from `startedAt` and label it accordingly.
- Completed duration is the provider's reported seconds, checked against start/end timestamps with a two-second rounding tolerance. It is exposed as elapsed duration, never assumed to be simulation duration.
- Teams normalize to `1` and `2`, as the shared roster contract reserves team `0` for unresolved participants. Exact participant membership and previous team groupings must remain consistent; reversing the provider's team-array order is allowed.
- A completed result needs exactly one winning team and consistent player/team booleans. Missing, contradictory or both-false outcomes stay unknown; both-false is not interpreted as a draw.
- Race IDs use the provider's `Race` enum: Random `0`, Human `1`, Orc `2`, Night Elf `4`, Undead `8`. Unknown IDs remain visible. For Random, `selectionId` remains `0`; an established `rndRace` appears explicitly in the label, e.g. `Random (Undead)`.

When a match disappears, the adapter uses the **exact original-ID finished lookup**. Another current game, including a rematch with the identical roster, cannot replace it. Missing completion stays `delayed`; no history/name/time heuristic marks a game finished. Binding checks reject incompatible participants, games predating the bot match's allowed window, conflicting native IDs and team changes.

Replay references use only an already validated finished match's provider page. Availability is `unknown`: the existence of a replay route does not prove the file exists. Neither the adapter nor its tests download replay bytes, generate replays or call `/api/replays`.

## Verification and fixtures

Last live schema check: **2026-10-29 UTC**. Anonymous reads verified profiles, ongoing list/player lookup, completed 1v1/4v4 metadata, player-filtered history, and exact original-ID-to-finished lookup. One captured 1v1 appears in both its actual ongoing and finished responses with the same original ID and different database record IDs. Captured 4v4 responses contain two teams of four and completed Random-race resolutions.

Sanitized fixtures: `tests/fixtures/connectors/wc3/v1/contracts.json`. Source schemas, field shapes and match IDs are retained; BattleTags/names are replaced consistently, and country/location/Twitch fields are removed or nulled. Fixtures contain public metadata, not credentials or replay files. Counterexample/rematch scenarios are constructed explicitly in tests.

Focused test command:

```sh
npx vitest run tests/wc3-connectors.test.ts
```

Tests cover bounded discovery, application/transport errors, unsupported modes, roster/time/ID validation, shared two-team binding compatibility, alias correlation, repeated rosters, ongoing placeholders, contradictory winners, encoded/renamed accounts, player-filtered history and replay-page-only references. Controlled real Discord match acceptance and production rollout are **not** implied by these tests.

If the provider schema changes, disable the affected capability, preserve captain reporting, capture one bounded public metadata response, update the decoder and versioned fixture, and rerun the focused suite plus the shared connector workflow tests. Do not add a name/time matching fallback or infer a winner from a missing row.

## Primary references

- [Public frontend API calls](https://github.com/w3champions/website/blob/master/src/services/MatchService.ts).
- [Match routes and exact original-ID lookup](https://github.com/w3champions/website-backend/blob/master/W3ChampionsStatisticService/Matches/MatchesController.cs).
- [Profile route](https://github.com/w3champions/website-backend/blob/master/W3ChampionsStatisticService/PlayerProfiles/PlayersController.cs).
- [Custom-game exclusion](https://github.com/w3champions/website-backend/blob/master/W3ChampionsStatisticService/Matches/StartedMatchIntoOngoingMatchesHandler.cs).
- [Mode enum](https://github.com/w3champions/website-backend/blob/master/W3C.Contracts/Matchmaking/GameMode.cs) and [race enum](https://github.com/w3champions/website-backend/blob/master/W3C.Contracts/GameObjects/Race.cs).
- [FFA identity and provisional-MMR obfuscation](https://github.com/w3champions/website-backend/blob/master/W3ChampionsStatisticService/Matches/PlayersObfuscator.cs).
- [Provider match/player page routes](https://github.com/w3champions/website/blob/master/src/router/index.ts).
