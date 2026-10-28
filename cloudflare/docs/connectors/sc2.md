# StarCraft II connector

Connector: `sc2`; edition: `StarCraft II`; provider/profile key: `battle-net`; account namespace: `sc2-character`. Catalog and contract versions are kept in `src/games/catalog.ts`. Last documentation/discovery verification: **2026-10-28**. Authenticated real-game acceptance is pending.

## Supported surface

The cloud connector supports profile lookup, Battle.net ownership verification and limited official match history as supporting evidence. It has **no lobby discovery or lobby observer**, no native match lookup and no replay-reference API. Host-supplied lobby names/invitations, races, teams, duration and captain-confirmed results remain available through the common manual workflow. No desktop client API, replay parser, upload or storage is installed.

Character identity is `region:realm:profile`, for example `2:1:3141896`. Regions are 1=US, 2=EU, 3=KR/TW; realm remains a separate 1/2 component. Profile IDs are strings and large values are decoded losslessly. The parser also accepts `2-S2-1-3141896` and supported official profile URLs. A BattleTag or display name is never an account key. China-region access is outside this connector's configured provider surface.

## Endpoints and authentication

For hosts `https://us.api.blizzard.com`, `https://eu.api.blizzard.com`, `https://kr.api.blizzard.com`:

| Operation                   | Endpoint                                                                    | Important mapping                                                                          |
| --------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Own characters, after OAuth | `/sc2/player/{accountId}?locale=en_US`                                      | `regionId`, `realmId`, `profileId`, `name`; account ID comes from authenticated userinfo   |
| Profile                     | `/sc2/profile/{regionId}/{realmId}/{profileId}?locale=en_US`                | `summary.id`, `summary.realm`, `summary.displayName` must agree with requested identity    |
| Supporting history          | `/sc2/legacy/profile/{regionId}/{realmId}/{profileId}/matches?locale=en_US` | `date` is a timestamp, `map`, `type`, `decision`; sort by date, preserve unknown decisions |

Requests carry bearer authorization in a header. Fixed host/route construction prevents arbitrary URLs from account input. Responses are bounded, and HTTP errors or application-level errors inside HTTP 200 remain failures, distinguishable from a successful empty history.

See [account setup](accounts.md) for client registration and callback configuration. `AdapterRuntime.battleNetAccessToken` supplies a short-lived API bearer token. Public lookups may use a client-credentials grant, while ownership verification requires the user authorization flow and selection from that user's returned characters.

## Why history cannot drive match lifecycle

Legacy history does not establish a native lobby/match ID, complete roster, team assignment, actual races, elapsed duration or reliable custom-game coverage. The adapter therefore emits `SupportingMatchEvidence` through `supportingHistory`, not `LobbyObservation`. No synthetic match ID, duration, start/end transition or replay URL is invented. An empty/private/delayed result cannot cancel a bot match or prove a result.

SC2 history must never be routed to automatic betting closure, match start, result prefill or rating updates. The common captain reporting workflow remains authoritative. Same-map, same-time or same-name matches are insufficient to join histories to bot matches.

## Verification and maintenance

```sh
npm test -- tests/sc2-connectors.test.ts tests/game-accounts.test.ts
```

Fixtures under `tests/fixtures/connectors/sc2/v1` are explicitly labeled documentation-derived synthetic examples, not authenticated live captures. Tests cover region separation, large IDs, wrong-identity payloads, unknown outcomes, unsorted/empty/private histories, missing or revoked authorization, HTTP 200 errors, partial regional account enumeration and unsupported operations making no network calls.

Use the separate provider-check command with `CONNECTOR_PROBE_SC2_PROFILE=region:realm:profile` and configured integration credentials. Missing credentials are access-blocked, never passed. Public profile/history probes do not replace interactive OAuth or real-game acceptance.

For acceptance, verify owned characters in several regions, select the intended realm/profile, create a first-time bot user without match participation, complete the manual 1v1 and team-game flow against actual game screens, and compare legacy history without treating omissions as failures of the manual match. Record date, build and limitations. Keep automatic lobby observation unsupported even when these checks pass.

On an upstream schema or auth failure, disable the affected capability and keep manual reporting available. Update versioned fixtures and run contract checks before changing mappings. Quota guidance currently states 36,000 requests/hour and 100 requests/second; shared caching/backoff should remain far below these limits.

Sources: [official SC2 API reference](https://community.developer.battle.net/documentation/starcraft-2/community-apis), [Blizzard OAuth sample](https://github.com/Blizzard/oauth-client-sample/tree/main/java-webmvc), [published OAuth discovery](https://oauth.battle.net/.well-known/openid-configuration), [getting started and quota guidance](https://community.developer.battle.net/documentation/guides/getting-started).
