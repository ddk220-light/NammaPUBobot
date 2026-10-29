# AoE2 Definitive Edition / Companion

The existing Companion integration remains the AoE2 provider. The updated observer is gated by `CONNECTOR_ACCEPTED_GAMES=aoe2` until controlled 1v1 and team acceptance is recorded. The deterministic test environment enables that gate solely for synthetic lifecycle tests; deployment configuration does not.

## Provider contract

| Operation            | Source                                                    | Mapping and limitations                                                                                                                       |
| -------------------- | --------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Public identity      | `https://data.aoe2companion.com/api/profiles/{profileId}` | Native `profileId`, display name and optional Steam64 crosswalk. Public existence does not prove account ownership.                           |
| Public lobbies       | `wss://socket.aoe2companion.com/listen?handler=lobbies`   | Existing shared feed, bounded active leases and full snapshot handling. Absence is not start evidence.                                        |
| Native match         | `/api/matches/{matchId}`                                  | Native ID, participants, teams, actual civilization, `won`, start/finish timestamps. Only coherent two-team outcomes prefill a suggestion.    |
| Recent history       | `/api/matches?profile_ids={profileId}`                    | Supporting match metadata, sorted by start. Missing history remains unknown.                                                                  |
| Recording references | Match participant `replay` flag                           | A true flag yields `https://aoe.ms/replay/?gameId=…&profileId=…`; false is unavailable and absent is unknown. No recording bytes are fetched. |

Elapsed duration is `(finished − started) / 1000` seconds. Simulation duration remains unknown; game speed is not used to invent it. Native lobby and match IDs share Companion's documented identity for this adapter; bot match UUIDs remain separate. Steam ownership is verified through OpenID, then matched to the exact Steam64 returned for a native profile. An unchecked declaration is never upgraded from name similarity.

## Findings corrected in this build

Read-only checks on 2026-10-28 established current public connectivity, but did not establish a real deployed bot lifecycle. Source inspection and implemented tests found two defects in the previous transport:

- The valid public profile sample was approximately 692 KB, exceeding the old 400 KB response cap. Profile reads now permit at most 2 MB; match reads retain the 400 KB cap.
- Cloudflare's actual `Request` implementation rejects `redirect: "error"`. The transport now uses `manual` and rejects redirect responses. A Workerd regression test validates the actual request options.

The old implementation also discarded winner/duration/replay evidence and could use provisional name associations for start-dependent behavior. The new binding records these facts, keeps name suggestions unconfirmed, and requires an exact declared roster/team match or explicit captain/organizer confirmation before start evidence affects predictions. Ratings still change only through captain reporting.

## Tests and recovery

```sh
npm run test:connectors
npx vitest run tests/lobby.test.ts tests/lobby-discovery.test.ts tests/aoe2-completion.test.ts
npm run provider:check -- --game aoe2 --output dist/aoe2-provider-check.json
```

[The dated metadata probe](evidence/2026-10-28-provider-check.json) passed profile, feed discovery, observation, match, history and replay-reference checks. A replay reference check validates metadata, not availability of replay bytes. Interactive Steam sign-in and controlled games remain pending in the [acceptance ledger](acceptance.md).

On a provider incident, set `CONNECTOR_DISABLED=aoe2` or disable the affected operation. `/ops/providers`, `/ops/aoe2/feed` and organizer connector diagnostics expose last success, failures and unresolved bindings without credentials or response bodies. Keep manual lobby facts, captain results and existing account links available. Restore through the additive backup workflow; never downgrade the database to remove a connector.

Sources: [Companion data API](https://data.aoe2companion.com/api/profiles/199325), [Companion project](https://github.com/aoe2companion/aoe2companion), [AoE short links](https://aoe.ms/), [baseline audit](baseline.md).

## Bounded live listings (2026-10-29)

Waiting-list browsing adds a short wildcard lease to the existing shared feed. Ongoing listing reads Companion's `ongoing-matches&verified=true` snapshot through the same application Durable Object, coalesces concurrent requests, and retains a short cache. It closes the capture socket on success/error within ten seconds and two MiB. This verified-player subset is not every game globally; at most 200 newest normalized observations are retained and views display ten. The decoder uses actual participant teams and reported civilizations, preserves unknown fields, rejects incomplete deltas/duplicate identities/future timestamps and excludes explicitly private or finished games. All bindings remain provisional until the normal roster/confirmation checks. Tests: `npm test -- tests/aoe2-ongoing.test.ts tests/feed-handshake.test.ts tests/lobby-discovery.test.ts`.
