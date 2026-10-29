# Beyond All Reason

Implemented provider: BAR Live Services (`bar`, account namespace `bar:user`). Automatic observation release status: **acceptance pending**. The connector reads JSON metadata; it never requests `.sdfz` files.

## Operations and identifiers

| Operation                        | Endpoint / behavior                                                                                                    |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Lobby discovery and observation  | `GET https://api.bar-rts.com/battles`, shared 30-second cache, 2 MB response ceiling                                   |
| Account lookup                   | Numeric `userId` in bounded live roster; offline/missing users return `delayed`, not `not_found`                       |
| Match lookup / replay references | `GET /replays/{32-character-replay-hash}`, 1 MB ceiling                                                                |
| Recent history                   | `GET /replays?page=1&limit=5&players={current-name}` plus at most five detail reads; numeric IDs verify each candidate |
| Ownership                        | BAR OAuth authorization code + PKCE after client registration; public lookup is not ownership proof                    |

The native lobby ID is `battleId`; native match ID is `hostSettings.server_match_id`; the replay reference's match ID is the replay hash. They are never substituted for each other. The public feed has no guaranteed battle-to-server-match crosswalk. Attach a numeric battle ID for live status, or explicitly attach a provider replay page/hash for completed facts. A host/captain must select the final replay when the room cannot be correlated. Same-roster and same-room rematches cannot be resolved from names/counts.

History yields candidates, not bindings. Names missing from current rosters, renamed players, games beyond the five-result window, and missing/private history may require a manually supplied replay-page link. The full `/cached-users` directory was 13.55 MB / 243,045 entries on 2026-10-28; the bot intentionally does not download it per account lookup or poll. A targeted API or separately maintained bounded index can improve offline lookup later.

## Field mapping and limitations

- Live `running` emits v2 `phase=started` with unknown `startedAt`. `gameTime` is not converted into an invented timestamp. Live `teamId` is an engine/player ID, not a proven ally-team assignment, so normalized teams remain unknown. Provisional observations cannot independently close betting.
- Spectators, bot hosts, players who joined after start, and stale game clients without a current lobby slot are excluded. This bot supports at most 16 players and two teams; other modes remain manual.
- Completed metadata uses `AllyTeams[].allyTeamId`, `Players[].userId` and `faction`. Unknown faction names survive unchanged. Engine/player IDs are not confused with alliance IDs.
- Results require exactly two teams, one winner, normal completion and no bots. Abnormal endings, missing winners and unsupported modes retain unknown outcomes. Captains still confirm results.
- `durationMs / 1000` is **simulation seconds**. `fullDurationMs / 1000` is **wall-clock seconds**, including the complete recording interval. Upstream [demo-parser](https://github.com/beyond-all-reason/demo-parser/blob/cac2b22b1c7fbd9c75ed4c45450637e4b3286e0b/src/demo-parser.ts) derives these from `gameDuration` and `header.wallclockTime`. Completion is recording start plus that wall-clock interval, not the precise simulation victory instant. Missing durations stay unknown.
- Replay references point to `https://bar-rts.com/replays/{hash}`. Availability reflects provider metadata, never a bot archive. Files older than one year are marked unknown because the provider warns they may expire. No download probe is performed.
- A disappearing/idle room does not prove completion. Without a pinned replay reference, return `delayed` rather than assigning a likely same-roster replay.

## Verification and maintenance

Last public verification: **2026-10-28**, `/battles`, `/replays`, `/replays/4842e26a56a1bef5da914e8b4ae29c9a`, replay landing page and OAuth metadata. This is connectivity evidence, not real-game acceptance. The v1 fixture projects the public response with player IDs/names replaced and IPs/countries/unneeded settings/spectators omitted.

```sh
cd cloudflare
npm exec vitest run tests/bar-generals-connectors.test.ts
```

The shared provider-check command performs separate bounded live probes. Use a replay hash for match/reference checks and a currently discovered player for profile lookup. OAuth registration and controlled game acceptance remain external prerequisites.

Recovery: disable the affected capability, keep manual lobby association and captain reporting, inspect compact schema/throttle diagnostics, and reproduce with a sanitized fixture. Schema changes must retain all three identifiers and both duration definitions. Do not broaden matching to names/counts to recover a failing provider.

Before enabling automation, record public/private 1v1 and team matches, same-room same-roster rematch, abnormal ending, cancellation/remake, provider interruption, account rename and replay/server-match correlation. Preserve payload version and acceptance date. Public probes do not replace this acceptance.

Sources: [BAR API](https://github.com/beyond-all-reason/bar-db), [replay UI](https://github.com/beyond-all-reason/bar-live-services/blob/main/pages/replays/_gameId.vue), [OAuth userinfo](https://github.com/beyond-all-reason/teiserver/blob/main/lib/teiserver_web/controllers/o_auth/userinfo_controller.ex).
