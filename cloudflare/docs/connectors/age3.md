# Age of Empires III: Definitive Edition

Factory: `createAoE3Adapter` in `src/games/aoe3.ts`. Bot catalog/provider key: `aoe3`; account namespace: `worlds-edge:age3`. Host: `https://aoe-api.worldsedgelink.com`; request title: `age3`. Legacy ESO editions are outside this adapter.

Profiles, Steam resolution, public advertisements, direct matches and recent histories use the shared [World’s Edge contract](age-provider.md). The live 2026-10-28 fixture includes stable profile IDs, zero-based teams, civilization IDs and result codes. The publisher catalog omitted some civilizations present in match data; unknown IDs stay visible rather than being mapped by position. Duration is elapsed wall time only. Replay-reference lookup is implemented, but the sampled recording service returned application `NOT_FOUND`; the bot reports recording unavailable. No replay upload or parsing is included.

Authentication: anonymous metadata reads; Steam OpenID separately proves supported account ownership. No game API key is required. Tests: `npx vitest run tests/age-connectors.test.ts`. Live profile `10126818`, native match `66376255` are bounded probe defaults, not permanent availability guarantees.

Release gate: current-build controlled 1v1 and team matches, native lobby-to-result continuity, remakes, private/missing history, unknown civilization IDs and elapsed duration against actual game screens. Automatic observations remain pending those checks. Disable the affected capability on provider failure; declared links, manual lobby association and captain reporting remain available.
