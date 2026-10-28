# Zero Hour / GeneralsOnline

Adapter `zero_hour`, provider `generalsonline`, account namespace `generalsonline:user`. This is distinct from existing `generals` records; original Generals and other networks stay manual. Automatic release status: **credentials and controlled-game acceptance required**.

## Access and endpoints

Set `GENERALS_ONLINE_API_KEY` to an operator-authorized read-only integration key. It is sent as `X-Api-Key` to the fixed provider host only. Never collect a player's game-session token. Without a key, protected operations return `authentication_required` before issuing a request.

Base: `https://api.playgenerals.online/env/prod/contract/1`.

| Operation                | Contract                                                                                                                                                |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Discovery/observation    | `GET /Lobbies`, response `lobbies[]`; a partner key may pass policy but lack Monitor/client context, so validate actual permissions                     |
| Exact match lookup       | `GET /MatchHistory/{startingMatchID}`, response `matches.matches[]`; source returns at most 100 IDs and the adapter selects only the exact requested ID |
| Replay references        | Match `members[].metadata[]` with `file_type=1`; public Strata match page is the source link                                                            |
| Account lookup/ownership | Unsupported: no supported third-party native-account crosswalk established                                                                              |
| Player history           | Unsupported: do not invent a history-by-player route or crawl all history                                                                               |

The source also publishes highest-ID `/MatchHistory` and `/MatchHistory/since/{date}`, which active-match polling does not need. `/Monitoring/BasicStats` is an activity counter and does not establish access to lobbies, players or results.

## Binding and evidence

A numeric attachment is a native lobby ID. A `https://strata.gamereplays.org/zh/match/{id}` link or `match:{id}` is an explicitly supplied match. Bot match ID, `LobbyID` and `MatchID` stay distinct, with integers preserved as strings. The live lobby supplies an explicit `MatchID`; once pinned it stays fixed even when the room is reused. The bot store owns binding-generation and roster checks. Missing publication returns `delayed`.

Live `State` values setup=0, in-game=1 and complete=2 can establish phase with unknown timestamps. `TimeCreated` is not game start. Human slots use `SlotState=5`; `Side=1` is an observer. Native user IDs are not assumed to be Steam or Discord IDs.

Completed fields are `is_finished`, `time_started`, `time_finished`, `map_name`, and `members`. Source-derived fixtures use UTC timestamps. Timezone-free production timestamps fail semantic validation until the operator confirms the contract; server-local time is not silently treated as UTC. Elapsed duration comes from valid start/finish timestamps. Per-member `duration` units are unverified and omitted.

Outcomes need two teams, consistent win/loss flags, no desync and no AI members. Missing outcomes, all-lost reports, unassigned teams and unsupported modes remain unknown. Provider winner provenance may include server fallback rules; captains confirm every result. Army numbers are retained as `selectionId` with `Faction {id}` fallback until a production patch-specific mapping is validated.

Replay metadata yields `https://strata.gamereplays.org/zh/match/{id}` references, with native perspective account IDs. The bot does not reconstruct bucket URLs, fetch binaries or read anti-cheat screenshots. Missing files mean unknown/unavailable. Historical patch notes mention delay and retention limits; no current retention guarantee is assumed.

## Verification and recovery

Last public verification: **2026-10-28**. `Monitoring/BasicStats` returned200, `Lobbies`401, and `MatchHistory/1` a missing-key validation error. Authenticated routes are **access-blocked**, not passed.

The v1 fixture is explicitly **source-derived synthetic data**, based on [Services revision85dfbc7ac6f9e336f5d18939f3cd1eb20663b6ea](https://github.com/GeneralsOnlineDevelopmentTeam/Services/tree/85dfbc7ac6f9e336f5d18939f3cd1eb20663b6ea), not an authenticated production capture. Contracts: [history controller](https://github.com/GeneralsOnlineDevelopmentTeam/Services/blob/main/GenOnlineService/Controllers/MatchUpdate/MatchUpdateController.cs), [lobby controller](https://github.com/GeneralsOnlineDevelopmentTeam/Services/blob/main/GenOnlineService/Controllers/Lobbies/LobbiesController.cs), [lobby model](https://github.com/GeneralsOnlineDevelopmentTeam/Services/blob/main/GenOnlineService/LobbyManager.cs), [history model](https://github.com/GeneralsOnlineDevelopmentTeam/Services/blob/main/GenOnlineService/Database/Database.MatchHistory.cs).

```sh
cd cloudflare
npm exec vitest run tests/bar-generals-connectors.test.ts
```

The shared live provider-check command uses credentials only when configured and requires an explicit production match ID. Diagnostics must not retain keys or raw private histories.

Recovery: disable the failing capability, preserve manual reporting, inspect normalized status and confirm key permissions. Add sanitized authorized production fixtures before changing decoding. Before enabling observers, record 1v1/team acceptance, lobby-to-match continuity/rematches, key revocation, unknown army IDs, timezone/duration semantics, cancellation/remake, delayed publication and desync behavior.
