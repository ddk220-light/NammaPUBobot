# Connector release acceptance

Last reviewed: 2026-10-29 UTC. Automated tests and public connectivity probes establish implementation behavior and feasibility. They do not establish that the bot follows a real game from lobby to captain-confirmed result. No controlled live games or interactive provider sign-ins were completed in this implementation session.

## Gate status

| Game                       | Current evidence                                                           | Controlled 1v1                        | Controlled team game                  | Automatic observation release                                          |
| -------------------------- | -------------------------------------------------------------------------- | ------------------------------------- | ------------------------------------- | ---------------------------------------------------------------------- |
| AoE2 DE                    | Existing integration, offline lifecycle tests, public metadata/feed checks | Pending recertification               | Pending recertification               | Gated pending recertification; preserve links and use manual reporting |
| AoE3 DE                    | Offline decoder contracts and public API probes                            | Pending                               | Pending                               | Disabled until accepted                                                |
| AoE4                       | Offline decoder contracts and public API probes                            | Pending                               | Pending                               | Disabled until accepted                                                |
| AoM Retold                 | Offline decoder contracts and public API probes                            | Pending                               | Pending                               | Disabled until accepted                                                |
| Beyond All Reason          | Offline decoder contracts and public API probes                            | Pending                               | Pending                               | Disabled until accepted                                                |
| Zero Hour / GeneralsOnline | Source-derived adapter contract tests                                      | Blocked on authorized integration key | Blocked on authorized integration key | Credentials and acceptance both required                               |
| Warcraft III / W3Champions | Offline adapter/OAuth/lifecycle contracts and anonymous JSON reads         | Pending supported matchmaking 1v1     | Pending supported 4v4                 | Observation gated per mode; arbitrary custom games excluded            |

Original Generals, SC2, Northgard and Tempest Rising are excluded from new rollout; their legacy data and manual-report compatibility checks do not constitute new connector release acceptance.

Public probe results are recorded separately with their exact timestamp and status. An inaccessible operation is not signed off. The absence of a replay reference can be a valid metadata result, but a provider error or malformed payload cannot be treated as an empty successful match history.

## Required scenarios

For each API-backed observer, test one supported two-player 1v1 and one two-team game on the current game build, recording:

1. Link first-time participants before their first match, including at least one declared account. Confirm this does not create participation, awards or ratings. Run an ownership sign-in where supported; check duplicate claims, reassignment and revoked credentials.
2. Attach the intended lobby. Compare native lobby, native match, platform lobby and bot match IDs independently. Confirm exact participant IDs and team mapping. A matching name/count without verified roster or explicit captain/host confirmation must remain provisional and must not close betting.
3. Start and finish the game. Compare actual factions/civilizations/gods, map, build, timestamps and duration with the game screens. Label elapsed wall time and simulation duration separately. Check unknown identifiers render without inventing a name.
4. Check provider outcomes can only prefill a result when the entire roster, teams and outcome agree. Ratings, standings and economy remain unchanged until the existing captain reporting/confirmation workflow completes. Verify the persisted record and statistics afterward.
5. Request a replay reference if offered. Verify availability and expiry behavior without downloading or uploading the replay through the bot. Requesting expired metadata should refresh the link or report it unavailable.
6. Cancel/remake, substitute/unlink a player, repeat a game with the same roster, restart the bot's Durable Object, deliver duplicate/delayed observations and interrupt the provider connection. A replaced binding must reject old evidence; the Discord outbox must not duplicate authoritative actions.
7. Exercise private/missing history, unsupported mode, and a provider throttle/outage. Manual reporting must remain available. Verify another community cannot read or alter the binding.

Additional title gates:

- **Age games:** establish native lobby-to-result continuity on real public and private/custom games; verify zero-based provider teams, unknown civilization/god IDs, and duration units. An AoE4 World 404 does not refute a World's Edge match.
- **BAR:** repeat games in the same room, confirm battle/server-match/replay-hash correlation, and inspect abnormal endings and unknown ally teams.
- **GeneralsOnline:** verify the authorized credential's actual lobby permissions, production payloads, native account mapping, lobby-to-match transition and replay-reference availability. Do not use individual game session tokens as integration credentials.
- **Legacy SC2 compatibility only:** choose owned profiles across all supported regions/realms, include an unavailable region, and show the limited history coverage explicitly. History does not establish the full game roster or a lobby lifecycle.
- **Legacy Original Generals, Northgard and Tempest compatibility only:** compare every manual field against current game screens, including a cancellation and captain dispute. Verify external recording references are links only.

- **Warcraft III:** test real supported W3Champions matchmaking, exact original-ID-to-finished lookup, distinct ongoing/finished database aliases, both positive normalized teams, race/Random semantics and stable Battle.net ownership. Verify custom/FFA/unsupported modes cannot be discovered as scheduled matches. Matchmaking may not reproduce a scheduled roster; accept observation-only coverage if exact scheduled teams cannot be established. Confirm native-game reuse after a completed bot match is rejected.

## Evidence record and enablement

Add one reviewed record per game/build beneath this directory. Use this template; keep private player histories, tokens, signed URLs and private account details out of the repository.

```text
Game/edition and provider:
Test date (UTC), operator, isolated staging community:
Worker /health sourceRevision, sourceDirty, sourceTreeHash, version:
Game build and connector catalog version:
1v1: sanitized native IDs, roster/team match, start/finish, duration, captain result:
Team game: same evidence:
Cancellation/remake and same-roster repeat:
Substitution/unlink/stale event and restart:
Private/missing history and interrupted connection:
Provider credentials/permissions tested (names only, no values):
Replay-reference availability/expiry (no signed URL):
Manual fallback and persisted statistics verified:
Known unsupported modes or remaining limitations:
Acceptance decision and reviewer:
```

New ongoing feeds and Warcraft observations also require `CONNECTOR_ACCEPTED_CAPABILITIES` for the accepted operation/mode; public read-only listing does not. Enable only the accepted catalog IDs in `CONNECTOR_ACCEPTED_GAMES`, then enable observation in the relevant community's game settings. Acceptance does not bypass credentials or per-provider/capability disable switches. A new source revision, upstream schema change or game patch that affects identity, continuity or result semantics requires renewed affected scenarios. If evidence is missing, keep the automatic observer gated and use the manual workflow.
