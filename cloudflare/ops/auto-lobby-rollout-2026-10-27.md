# Automatic lobby attachment and feature activation — 27 October 2026

## Shipped behavior

Cloudflare 0.10.0 restores Railway's `NammaNomad` name/full/player-count discovery, including a filling-lobby preview and same-player remakes before confirmed start. Declared-profile conflicts, competing lobbies/matches, unknown capacity and stale observations prevent automatic attachment. Durable search jobs last at most 90 minutes. Name subscriptions share the existing leased AoE2 feed.

Any current roster player can supply `/lobby attach reference:123456789` or a supported lobby URL, including during check-in. The active match is selected automatically unless `match` is specified. Explicit attachment pins the supplied ID, overrides an automatic binding, and wins against an in-flight discovery response. It does not require a matching lobby name or a full lobby. Captains/admins can unlink, which also disables reattachment for that match. Outsiders and cross-channel attachment are rejected. Captain reporting remains the result authority.

## Deployment and activation

- Implementation revision: `9ec335e9ed4ac2c02ebf335da039550aaf4d0c6c`.
- Worker: `community-gaming-staging` at `https://community-gaming-staging.deepakdhanavel.workers.dev`.
- Version: `58eb33bd-82fa-4587-94e2-5239fb5a2c4b`, confirmed at 100% in deployment `b6f3ea47-fcd5-460c-9937-97eb63ffad58`.
- `/health`: HTTP 200, staging, version `0.10.0`.
- The deployment source matches 57 committed source/config/web/package files in the retained SHA-256 manifest. SQL schema remains epoch 7; the added JSON settings require the new binary.
- Discord staging application `1559284610108752068`, server `706320010368516198`: 18 guild commands registered and read back. `/lobby attach` describes match-player access, with optional match ID. The application identity and deployed interaction endpoint were checked before registration.
- AoE2 game `e6d8951b-2912-4476-b268-3068149435f4`: tracking and automatic attachment enabled, name `NammaNomad`.
- Queue `a8f17cf1-b466-4c6d-90c4-1eae603274ba`, **Pickup**, in `test-bot` (`1559279290577850368`): **Civs, Stories, Gold, Predictions and Maps enabled**. Quiz remains unavailable/off. Other games and disabled test queues were not changed.
- Settings revision **68 → 69**, after an operator preview. The normal settings validator and audit record were used. The narrow OPS activation endpoint is staging-only, rejects active matches/recovery copies/stale revisions, and provides no player impersonation or arbitrary configuration mutation.

This is the existing Cloudflare staging installation. Railway's Python production runtime has not been migrated.

## Verification

`npm run check` passed **735 tests in 25 files**, server/web type checks, web build and Worker dry-run. Repository-wide Ruff passed. The 31 new tests cover selection/ambiguity, partial occupancy, any-player manual priority, override races, same-ID pinning, restart/alarm recovery, remakes after slot removals, search timeout, disabling/unlinking/cancellation, and constrained activation. An integrated fixture proves auto-attachment → provider start/end/civ evidence → betting closure → captain loss report → settlement, without automatic result entry.

Provider events and Discord requests in these tests are fixtures. They are not a human-played game. The existing runtime teardown diagnostics appeared in the full suite; the completed check exited successfully.

Live public-feed discovery evidence is retained in `auto-lobby-live-discovery.json` outside the repository. All three selected exact-name searches returned a current lobby snapshot at approximately 7, 43 and 79 seconds, with a healthy shared feed throughout two normal 35-second polling intervals. These checks used the deployed, OPS-authenticated diagnostic route without attaching public lobbies to any community match. Real human button/slash interactions and an actual AoE2 game remain acceptance checks.

## Preservation and cleanup

Backups before implementation, immediately before activation, and after activation were compared. **48 of 52 tables are unchanged row-for-row**. The only changed tables are `community` (revision), `games` (one AoE2 connector), `queues` (one Pickup module set), and `settings_audit` (one operator entry). Every other game and queue row is identical.

All **48 matches**, **47 wallets**, ratings, profile mappings, ledger/history, and the **one waiting entry** are preserved. Every wallet reconciles with its ledger. There are zero active players, deadlines and outbox jobs after activation. No live demo match, fixture identity, rating, wallet or player action was created. No temporary mutation controller or credentials were created. Diagnostic subscriptions expire after 120 seconds without renewal, with the existing 30-second cleanup alarm.

Private deployment logs, before/after backups, preservation manifests, command registration/readback, source hashes and public-feed samples remain under `/workspace/.cloud-setup/nammapubobot/aoe2-completion/`.

## Human acceptance

Use the enabled Pickup queue. Finish check-in/maps, create a public `NammaNomad` lobby, observe it filling and attach automatically when full. Alternatively, any match player can supply the ID with `/lobby attach reference:…`. Exercise `/civpick`, betting and actual buttons, start and finish AoE2, then have the losing captain report. Confirm real provider timing, civ attribution and settlement. OAuth sign-in, complete game-night reliability and actual Cloudflare cost are still human/operational acceptance items.
