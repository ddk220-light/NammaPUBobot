# Age of Mythology: Retold

Factory: `createAoMAdapter` in `src/games/aom.ts`. Existing bot catalog/provider key: `aom`; account namespace: `worlds-edge:athens`. Host: `https://athens-live-api.worldsedgelink.com`; request title: `athens`. Original and Extended Edition Mythology are outside this adapter.

The shared [World’s Edge contract](age-provider.md) provides profile and Steam resolution, public advertisements, direct native matches, recent-history reconciliation and replay-reference refresh. Actual major gods come from `civilization_id`, with a pinned 2026-10-28 publisher catalog and unknown-ID fallback. Duration is elapsed wall time; simulation duration is not guessed from timestamps. Public waiting lobbies are not a comprehensive ranked-game live feed.

Replay metadata can return multiple perspectives and an `expiryUnix` timestamp. Fresh signed links are provider references only; expired links lose their source URL and must be refreshed. They are not archived files. No replay bytes are downloaded, stored or parsed, and no 30-day storage promise is made.

Authentication: anonymous community reads and separate Steam OpenID ownership proof. Tests: `npx vitest run tests/age-connectors.test.ts`. Last public verification: **2026-10-28**; profile `1073774984`, native match `44987874`, two recording metadata records. Controlled game acceptance is still pending.

Release gate: controlled current-build 1v1/team matches, god/team/outcome agreement, lobby-result continuity, repeated rosters, private/missing histories, elapsed duration and interrupted provider connection. Disable the affected capability on failure, keep captain reporting active, and update the host/title or decoder only with a sanitized fixture and live metadata check.
