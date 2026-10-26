# Cloudflare app and AoE2 lobby work in progress

26 October 2026. This checkpoint was requested before finishing lobby integration, for continuing development from another checkout. The existing Railway bot remains production. No production migration, deployment or Discord command registration is part of this checkpoint.

## What is included

The `cloudflare/` directory contains the previously local Cloudflare app: Discord interactions, organizer web app, community SQLite Durable Objects, queues/check-in/teams, results and ratings, optional gold predictions, text stories, maps, and operator tooling. Its README and operator guide describe the existing behavior. The root Cloudflare workflow checks feature-branch pushes; deployment requires a separate manual workflow dispatch. The independent workflow under `cloudflare/.github/` is for copying the app into its own repository.

The [remaining feature plan](../ops/remaining-features-plan.md) orders lobby integration, automatic betting closure, civ picking, banter parity, a later rating decision, and migration rehearsal. Quizzes and OpenSkill conversion remain deferred.

Only the following lobby foundation has been drafted:

| File                    | Current scope                                                                                                 |
| ----------------------- | ------------------------------------------------------------------------------------------------------------- |
| `src/games/types.ts`    | Provider-neutral identity, observation, error and adapter contracts                                           |
| `src/games/catalog.ts`  | Proposed capabilities for AoE2, AoE3, AoE4, AoM and Generals; not connected to the existing settings catalog  |
| `src/games/registry.ts` | Registry containing only the draft AoE2 adapter                                                               |
| `src/games/aoe2.ts`     | Profile lookup, lobby-reference parsing, match evidence parsing, and a bounded live-lobby snapshot experiment |

There is no saved lobby binding, attachment command, organizer setting, join/status card or active-game poll scheduler yet. Nothing imports this adapter into the Worker execution path. Its capability metadata is a draft, not an enabled feature. Automatic prediction closure, civ picking and new banter behavior have not been implemented in this checkpoint. Other catalog games have no real connector yet.

## Run from a fresh checkout

Use Node 22.12+ and Python 3. From the repository root:

```sh
cd cloudflare
npm ci
npm run check
python3 scripts/test-legacy-converter.py
python3 scripts/test-economy-converter.py
npm run dev
```

`check` performs type checks, workerd tests, the web build and a Worker dry run. It does not deploy. The local suite uses synthetic fixtures and mocked Discord transport. Local development uses local Durable Object storage. Actual OAuth/Discord usage needs a separately configured application and credentials; follow [the operator guide](operator-guide.md). Credentials and community data are deliberately absent from Git.

## Provider spike: evidence and limits

Read-only public requests on October 26 established that AoE2 Companion's profile endpoint and finished-match endpoint responded, while the candidate `/api/lobbies` path returned 404. An open lobby also returned 404 from the match endpoint, so that response cannot prove a game started or ended.

The public lobby WebSocket returned an initial event snapshot, including lobby and slot records. Filtering by one match ID returned only that lobby's initial records. The draft experiments with a short connection that closes after obtaining a snapshot; this is not a deployed or cost-validated polling design. The provider is an unofficial dependency without an assumed availability guarantee.

Raw provider captures and production history stay outside version control. These probes do not establish a complete real-game lifecycle. The Cloudflare outbound WebSocket transport, retry/error classification, partial-frame completeness and cleanup need focused tests and live staging verification. In particular, a timeout or missing lobby must never imply start/cancellation, and socket cleanup must not leave a promise unresolved.

## Resume implementation here

1. Test the adapter contracts and parsers with sanitized synthetic fixtures: invalid/duplicate identities, partial snapshots, 404/publication delay, rate limits, timeouts, malformed payloads and timestamp consistency. Validate the Cloudflare WebSocket transport before selecting it. Measure active polling latency and cost; no permanent per-community socket or new paid relay is assumed.
2. Reconcile the draft catalog with `src/domain/config.ts`. Keep the AoE2 connector optional; unsupported games retain manual operation. A successful public profile lookup proves existence, not account ownership.
3. Persist explicit lobby bindings, roster/profile snapshots, generation, evidence freshness, retries and next due work in the community object. Reuse `profile_refs` conflict controls. Extend backup/restore and schema compatibility deliberately.
4. Add captain/admin attach, unlink and status flows, organizer configuration and a safe join link. Acknowledge Discord interactions before external reads. Resolve ambiguous active matches explicitly.
5. Fetch outside transactions, then recheck community/match identity and the current binding/roster generation before applying evidence. Include lobby polling in the existing shared alarm scheduler; stop on unlink, terminal state or maximum lifetime. Do not replace the object's one alarm independently.
6. Use positive start/end evidence. Full/disappeared lobbies and the generic match state `playing` are not proof of an in-game start. Phase 1 may prompt the losing captain to report; it must not assign a winner, update ratings or close bets automatically.
7. Cover cancellation/substitution during a fetch, relinking, duplicate events, provider failures, restart recovery and tenant isolation. Then demonstrate an actual AoE2 lobby through joins, start and finish in isolated staging. Fixture tests alone do not complete this feature.

Keep generic matchmaking, ratings and wallets authoritative in the community Durable Object. Adapters supply game evidence; they do not write ratings, settle predictions or send Discord messages directly. Future AoE4/Generals connectors should implement the same boundary, with honest capability differences.

## Validation of this checkpoint

The selected source files were copied to a temporary directory without credentials, provider captures or local storage, using the existing installed dependency directory. Validation passed on October 26:

- `npm run check`: server/web type checks; 607 tests across 18 files; web build; Worker dry-run build.
- Python converter suites: 6 legacy-import tests and 5 economy-import tests.
- Staged whitespace checks and credential-pattern scans, including an exact-value comparison against the local app secrets, without printing them.

The workerd test runtime emitted `deleteAllDurableObjects()` teardown warnings; Vitest reported all 607 tests passed and exited successfully. No fresh dependency installation or real Discord/AoE2 lifecycle test was performed for this checkpoint. The draft adapter has no dedicated tests yet. Passing the app's existing suite is a build/regression checkpoint, not certification of the unfinished lobby adapter or a production migration.
