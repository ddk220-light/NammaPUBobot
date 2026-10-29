# Final RTS connector implementation plan

Scope agreed 2026-10-29. This plan supersedes the original ten-game rollout scope. It describes the remaining implementation and release work; it is not an acceptance record.

## 1. Final game list

| Catalog ID  | Game and provider                                     | Planned coverage and boundary                                                                                                                                                                   |
| ----------- | ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `aoe2`      | Age of Empires II: DE — Companion                     | Public waiting lobbies, ongoing matches, participants/civilizations and completed-match facts; conditional replay references. Recertify the existing workflow.                                  |
| `aoe3`      | Age of Empires III: DE — World's Edge, `age3`         | Public waiting lobbies, profiles, native match lookup and recent history. A general public running-match feed is not established. Recordings can be unavailable.                                |
| `aoe4`      | Age of Empires IV — World's Edge and AoE4 World       | Native profiles/lobbies/results; add the verified AoE4 World ongoing-match feed with its own identifiers and coverage limits. Custom/private coverage is not universal.                         |
| `aom`       | Age of Mythology: Retold — Athens, `athens`           | Public waiting lobbies, profiles, match/history facts, gods and conditional replay references. The readable AoMStats live webpage does not establish a qualifying API for a running-match feed. |
| `bar`       | Beyond All Reason — public battle/replay-summary APIs | Waiting/running battles, participants, completed facts and replay references. Live factions remain unknown unless supplied by a validated source.                                               |
| `zero_hour` | Generals: Zero Hour — GeneralsOnline                  | Credentialed API candidate. Validate supported developer access, actual permissions and production payloads before enabling. A public HTML lobby page alone does not qualify.                   |
| `wc3` (new) | Warcraft III — W3Champions                            | Public ongoing matchmaking matches and completed results. No waiting-room discovery or automatic discovery of arbitrary hosted custom/private games.                                            |

Only Warcraft III is added from the new recommendations. CoH2, CoH3, CnCNet, FAF and OpenRA are not included. Original Generals, SC2, Northgard and Tempest Rising are excluded from new connector rollout. Preserve their existing identifiers, accounts, history and any in-flight reporting; remove them from new-game onboarding and scheduled probes instead of deleting historical records.

The public-API rule applies to every target. Normal documented developer credentials can be compatible with a public API; a website, extracted game-session credential or private collector is not a substitute. Zero Hour remains conditional while legitimate API access is verified. If no qualifying access is available, exclude it from release rather than shipping a new manual-only integration. Manual reporting remains the fallback for supported games and legacy records.

## 2. Starting point and retained requirements

Continue in the isolated `codex/rts-connectors` worktree, starting from `334c67fe0da8c06ebbcce36ed39525d961993236`. The existing work already provides adapters, capability manifests, normalized errors, account trust, SQLite evidence/bindings, coordinator caching, settings, probes and release gates. Do not rebuild these as a parallel framework.

The branch's recorded verification on 2026-10-28 is 1,168 tests, both type checks, web build and Worker dry-run, plus 11 migration tests. These are prior results, not verification of this plan or the new Warcraft adapter. Re-run the baseline once when implementation begins. Fetch current `main`, review any intervening changes, and merge/reconcile in the worktree without resetting the connector work. The comparison with staging 0.18.1 was behavioral reconstruction; its original source commit was not recovered. Preserve [baseline.md](baseline.md) and source revision/tree hash in `/health`.

Retain these boundaries:

- Captains confirm results before ratings, standings or economy settlement changes. Public ranked games outside a bound bot match never create bot participation or ratings.
- Cloud APIs only. No desktop companion, graphical game collector, injected hooks or private session-token extraction.
- Replay availability and provider links only. No downloads, uploads, parsing or archival storage. The future 30-day replay retention preference does not apply here.
- Support accepted two-team modes. Unknown outcomes, unsupported modes and incomplete/ambiguous rosters use manual reporting.
- Keep the existing authoritative match writer, durable Discord outbox, duplicate/reassignment controls and additive backup/restore migrations.

## 3. Delivery sequence

### PR 1 — Close the catalog and distinguish discovery types

1. Add an explicit catalog lifecycle (`active`, `conditional`, `legacy`) so excluding a game does not break old database rows or history rendering. Enforce it on both UI selection and server creation/configuration paths; gracefully finish existing records without continuing excluded-provider polling.
2. Add `wc3`, edition/provider metadata and a versioned race catalog. Keep unknown race IDs and distinguish Random selection from a subsequently established actual race.
3. Separate waiting-lobby discovery from ongoing-match discovery in manifests, runtime checks, organizer settings and probes. Add an ongoing discovery operation and explicit supported-mode/coverage metadata. Do not mark W3Champions as having a lobby feed.
4. Extend binding/observation types to distinguish native lobby and native match references. Preserve bot match ID, platform lobby ID and provider-specific identifiers separately. W3Champions has a match reference without a pregame lobby ID; the compatibility normalizer must not invent one.
5. Store provider aliases for correlation without overwriting canonical IDs. Additive migrations and backup/restore must accept old bindings/events, defaulting their reference kind to the existing lobby behavior.
6. Scope acceptance to tested provider capabilities and modes. A successful waiting-lobby test must not enable a new ongoing feed or an untested mode through a game-wide switch alone.

Acceptance: seven target entries, four retired entries readable only for compatibility, no newly selectable excluded game, old backup restore passes, and no fabricated waiting/running capability.

### PR 2 — Warcraft III provider and account linking

Implement `src/games/wc3.ts` with separate decoding/catalog helpers as needed. Register it in the existing registry; allow only the required W3Champions HTTPS host in the bounded transport. Use the shared coordinator for caching, throttling and normalized diagnostics.

Verified public API base: `https://website-backend.w3champions.com/api`.

| Operation                        | Route                                              | Implementation rule                                                                                                                               |
| -------------------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| Ongoing list                     | `/matches/ongoing?pageSize=...&offset=...`         | Bounded pages, explicit freshness and coverage; stop at the requested page/request budget.                                                        |
| A linked player's current game   | `/matches/ongoing/{encodedBattleTag}`              | Encode `#` and other characters correctly. No current game is distinct from a failed read.                                                        |
| Completed facts/history          | `/matches?...` and validated detail/profile routes | Validate player filters and profile schema before claiming account/history support; global recent games are not automatically a player's history. |
| Exact ongoing-to-finished lookup | `/matches/by-ongoing-match-id/{originalId}`        | Primary correlation path; use the original matchmaking ID, not name/time inference.                                                               |

Maintain three distinct identifiers: original ongoing match ID, provider database record ID and FLO game ID. A match that disappears from the ongoing list becomes unresolved/pending reconciliation until exact finished lookup supplies evidence or the binding expires. Never label disappearance as a win, loss or completion.

Decode teams, BattleTags, races, map, start time, completed duration and winner. In ongoing payloads, `won: false`, zero duration and unset end time are placeholders, not a result. Label derived elapsed wall time separately from provider-reported finished duration; do not claim simulation-time semantics without verification. Preserve unknown mode/race values.

Initially accept only verified standard two-team melee modes, beginning with 1v1 and an available team ladder. Other modes, including FFA and custom-map ladders such as Direct Strike, are unsupported for automatic bot settlement until separately validated. FFA identities can be obscured. Arbitrary `CUSTOM=9` games are excluded by the provider and stay outside automatic discovery. The bot cannot create a W3Champions ladder game or force its matchmaking to reproduce scheduled teams.

Account linking starts with a declared or organizer-assigned W3Champions BattleTag. Extend the existing Battle.net OAuth flow for Warcraft: request the appropriate identity scope, obtain authenticated account subject and BattleTag, and explicitly map that identity to the W3Champions account. Remove the SC2-character-selection dependency from this path; do not call SC2 profile APIs for Warcraft. Retain stable Battle.net subject separately from the BattleTag used by W3Champions, account for rename/relinking, and never transfer another Discord user's claim silently. If a trustworthy mapping cannot be established, retain declared trust. A public profile is not ownership proof or proof of game ownership.

Replay references initially link to validated provider match pages with availability `unknown` unless metadata proves otherwise. The existence of a download route does not prove a particular replay exists. No test or probe fetches replay bytes.

Acceptance: fixtures exercise real captured schemas; a bound ongoing ID resolves to its exact finished record; unrelated ladder games cannot write statistics; ownership is optional and missing OAuth credentials do not block public reads.

### PR 3 — Complete and align the existing six providers

| Provider       | Remaining work and release evidence                                                                                                                                                                                                                                                            |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| AoE2           | Preserve the shared feed; integrate/validate ongoing coverage separately from waiting lobbies; certify roster/start/result continuity, duration and conditional replay links. Verify lifecycle and economy behavior against the reconciled staging baseline.                                   |
| AoE3           | Verify public lobby membership completeness and faction decoding. Reconcile direct match/history evidence with native IDs, roster, teams and time bounds. Preserve unknown civilizations and unavailable recordings. Do not simulate a running feed using disappearance.                       |
| AoE4           | Add the already probed AoE4 World ongoing JSON feed through its own decoder. Maintain native and AoE4 World IDs without assuming equivalence; treat enrichment failures/404s independently. Verify custom, private and delayed-result coverage.                                                |
| AoM Retold     | Validate Athens lobby-to-result continuity, roster completeness, god catalog, duration and replay-reference expiry. Use public Athens APIs; do not convert an AoMStats webpage into an undocumented integration.                                                                               |
| BAR            | Correlate battle ID, server match ID and replay hash through supported metadata. Cover repeated games in the same room, spectators, unknown live factions and abnormal endings. Absence of a replay is not evidence of a loss.                                                                 |
| GeneralsOnline | With a supported integration key, validate access scope and production schemas for discovery, lobby-to-match transition, account IDs, outcomes and replay references. Until then report access-blocked. If qualifying developer access is unavailable, leave the released roster at six games. |

The Age, BAR and Warcraft read-only work can proceed independently after PR 1. Credentialed GeneralsOnline verification must not block other providers. For any provider lacking trusted start evidence, retain explicit captain/host start confirmation and do not close betting on a disappearing room or a matching name/count.

### PR 4 — Make discovery and status understandable in Discord and the web UI

Extend the existing `/profile`, `/lobby attach`, `/lobby status`, match details and reporting controls. Add a bounded `/lobby list` view with a waiting/running filter, using the declared capabilities rather than pretending all games have both feeds. Offer the same information in the existing organizer interface.

Display up to ten actual current records with provider, waiting/running state, observed-at time, map, participant names, reported races/factions/civilizations and duration where known. Show unknown values honestly, return fewer than ten when appropriate, and label sample/page coverage without claiming it is the total population. A failed provider call is not an empty successful list. Avoid exposing privileged/private histories through a shared public cache.

For Warcraft, label the view “W3Champions matchmaking games”; attach accepts a provider match link/ID, not a nonexistent waiting-lobby invite. A community may observe a member's ranked game without recording it as a bot match. Attaching it to an existing bot match requires the exact expected participants and teams, a compatible time window, and the existing trust/confirmation rules. A late attachment cannot retroactively reopen predictions or make already-placed bets appear valid before a known start.

Organizer settings show implemented capability, credentials state, last successful normalized observation, failure/delay state and acceptance status separately. Continue to permit captain reporting during outages. Keep UI language focused on what the organizer can do, rather than internal provider field names.

### PR 5 — Tests, maintenance and release

Extend the existing reusable contract suite instead of creating a parallel test harness. Sanitize and version fixtures from the 2026-10-28/29 captures, including Warcraft ongoing, player lookup, completed facts and exact ID resolution. New profile/OAuth/replay-reference claims need their own evidence.

**Offline CI:** run adapter contracts, auth tests and the real SQLite Durable Object workflows with mocked providers. Cover malformed responses, HTTP-200 application errors, missing fields, unsafe integers, unknown factions/modes, private/unsorted histories, expired references, 401/403, 429/retry, timeouts and schema changes. Add Warcraft-specific cases for encoded/renamed BattleTags, null current game, hidden identities, placeholders, mismatched teams, duplicate results, ongoing/finished alias mapping, delayed finish and two games with the same roster. Verify no provider can settle ratings without captains; stale generations cannot affect replacement bindings; legacy data survives restore; first-time linking grants no participation; excluded catalog entries cannot be selected. Run type checks, focused browser smoke checks and standard builds once the changes pass their targeted tests.

**Live probes:** extend `scripts/provider-check*` and the separate daily workflow. Probe only the selected providers and supported operations, with explicit per-provider request/byte budgets and bounded discovery. Use public samples for metadata and configured integration credentials only where required. Reports retain `passed`, `failed`, `access-blocked` and `unsupported` plus freshness and fixed diagnostics; missing credentials or interactive sign-in requirements never pass. Retain no tokens, signed URLs, replay bytes or private history payloads. Ordinary PR CI remains independent of external uptime.

**Controlled acceptance:** on an isolated staging build with recorded `/health` identity, complete one 1v1 and one supported two-team game per automated path. Check native IDs, account mapping, actual roster/factions, start/finish, duration units, captain confirmation and persisted statistics. Exercise cancellation/remake, repeated roster, substitution/unlink, duplicate/delayed events, interrupted provider, missing/private history and restart. For Warcraft use genuine supported matchmaking games; verify custom games cannot be falsely discovered or bound. If the ladder cannot produce the bot's exact roster/teams, sign off observation-only coverage rather than automated scheduled-match association. Add real OAuth sign-ins only where credentials are configured. Record unsupported scenarios explicitly rather than counting them as passed.

**Operations:** keep shared per-provider caching/backoff, the existing AoE2 socket, approximately 30-second pregame and 120-second in-game active-binding polling, with provider-specific adjustments only when required by measured limits. On-demand browsing uses bounded shared snapshots. Stop polling at terminal state/expiry and isolate provider/capability failures. Track normalized last success, schema failures, throttles, unresolved bindings and delay. Each provider gets a short maintenance document with endpoints, auth, field mappings, mode scope, fixtures, test command, last live check, acceptance and recovery steps. Update daily probes/docs with schema changes and preserve unknown-ID fallbacks.

**Rollout:** prepare reviewable PRs on the existing branch or branches based on it; keep `main` and production unchanged during implementation. Build traceable artifacts, validate isolated staging, then enable only accepted capabilities/modes for selected communities. Preserve one authoritative writer, additive migrations, backup/restore and existing migration-spend reporting. Disable a failing connector instead of rolling back the database. No provider's credentials or failure blocks an accepted provider.

## 4. Credentials and external dependencies

| Dependency                                        | Required for                                                                             | Does it block public reads?                                                          |
| ------------------------------------------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| None                                              | Public AoE2/AoE3/AoE4/AoM/BAR/W3Champions metadata supported above                       | No credential setup needed                                                           |
| Steam OpenID configuration and HTTPS callback     | Verifying Steam accounts, followed by a validated native-account crosswalk for Age games | No; no Steam Web API key required for OpenID                                         |
| `BATTLENET_CLIENT_ID` / `BATTLENET_CLIENT_SECRET` | Warcraft Battle.net ownership sign-in                                                    | No; declared W3Champions links and public metadata remain available                  |
| BAR OAuth client registration                     | Verified BAR identity                                                                    | No; declared links and public battle reads remain available                          |
| `GENERALS_ONLINE_API_KEY` with supported access   | GeneralsOnline API verification and operation                                            | Yes, for that provider only                                                          |
| Test participants and current game clients        | Controlled 1v1/team acceptance and interactive identity sign-ins                         | Blocks acceptance of the affected capability, not deterministic implementation/tests |

Store credential values in the deployment's secret store, never chat, source, fixtures or probe reports. The implementation can be completed and tested offline without them; credential-dependent checks remain visibly blocked.

## 5. Definition of done

The selected games have honest capability/mode manifests and tested adapters; excluded games are absent from new setup while historical data remains readable. Warcraft public matchmaking tracking works without inventing custom-lobby support. GeneralsOnline either has verified supported access and acceptance, or is excluded from release under the public-API rule. Automated paths have dated controlled-game evidence tied to a source revision; remaining blocked/unsupported paths are labeled accurately. Captains retain result authority, providers fail independently, and diagnostics plus versioned fixtures make upstream changes maintainable.

Supporting sources: [existing verification](verification.md), [acceptance](acceptance.md), [operations](operations.md), [W3Champions public client](https://github.com/w3champions/website/blob/master/src/services/MatchService.ts), [match routes](https://github.com/w3champions/website-backend/blob/master/W3ChampionsStatisticService/Matches/MatchesController.cs), [custom-mode exclusion](https://github.com/w3champions/website-backend/blob/master/W3ChampionsStatisticService/Matches/StartedMatchIntoOngoingMatchesHandler.cs).
