# Age of Empires IV

Factory: `createAoE4Adapter` in `src/games/aoe4.ts`. Bot catalog/provider key: `aoe4`; account namespace: `worlds-edge:age4`. Host: `https://aoe-api.worldsedgelink.com`; request title: `age4`.

The publisher connector is authoritative for profile, lobby and match facts under the shared [World’s Edge contract](age-provider.md). `enrichAoE4Match` is an optional display helper for AoE4 World; it only applies the returned map name after matching the native match ID. A missing/private/custom match or a World 404 leaves publisher evidence intact. This optional helper does not drive match settlement.

Custom `matchtype_id=0` remains Custom even if a separate ladder field claims ranked. Multiple teams and positive outcomes are preserved; unsupported modes remain manual. The captured ranked match has **1,630 elapsed seconds** versus **1,497 simulation seconds** (`counters.gt`); those values are never conflated. A recording reference uses the official refreshable delivery URL only when provider metadata establishes recording availability. The bot does not fetch replay bytes.

Authentication: anonymous publisher and optional World reads; Steam OpenID for Steam ownership. Other platform identities remain declared/organizer-assigned. Tests: `npx vitest run tests/age-connectors.test.ts`. Live verification: **2026-10-28**; profile `4635035`, match `257612387`, plus a custom multi-team fixture. This is connectivity evidence, not full bot acceptance.

Release gate: controlled 1v1/team games, custom and private matches, participant/team agreement, native lobby continuity, remakes and both duration semantics. On schema failure disable only that capability, retain manual play and add a sanitized fixture before changing the decoder. AoE4 World's absence alone must never remove a publisher match.
