# Responsive organizer and statistics verification

The implementation adapts the [desktop and mobile design handoff](https://github.com/ddk220-light/NammaPUBobot/tree/56ea504f676b121be9387f609253f91e5bc8fa53/design) to the Cloudflare Worker and Community Games data model. It adds no persistence migration and does not change Railway.

## Automated checks

From `cloudflare/`:

```sh
npm ci
npm run check
python3 scripts/test-legacy-converter.py
python3 scripts/test-economy-converter.py
npx playwright install --with-deps chromium webkit
npm run test:ui
```

Worker tests exercise actual authorization, CSRF, setup/capability contracts, validation paths, revision and domain conflicts, shared game policy, import idempotency/target checks, and statistics visibility/hidden-player rules. Playwright uses deterministic API fixtures to exercise browser behavior; these fixtures are not evidence of a live Discord OAuth session.

Browser coverage includes invalid drafts, navigation and record refresh, network failures, atomic saves and concurrent revisions, explicit discard, community switching, shared ratings/tax time, rank rows, first-queue inheritance, engine locks, unavailable features, missing Discord access, invitation return, late responses, import invalidation/blocking/duplicates and a 500-player file. Both engines cover 360, 390, 430, 640, 641, 768, 900, 901 and 1440px widths, plus 844×390 landscape. Checks include touch hit areas, overflow, final-field reachability, action bars, dialog focus/dismissal, rotation with a draft, and public URL navigation.

Screenshots cover all five organizer destinations at phone and desktop widths, the public leaderboard and four profile sections, and import review. CI uploads screenshots, traces for failures, and the HTML report as `cloudflare-browser-evidence` for seven days.

## Isolated staging

On 2026-10-28, remote `main` was `fa3647b` (0.16.0), while the existing staging Worker reported 0.18.1, Cloudflare version `df8289ac-fe02-45b5-92ee-1dd0870d4db4`. That newer deployment includes behavior absent from `main`, including test-match exclusions. The separate [baseline reconciliation](https://github.com/ddk220-light/NammaPUBobot/blob/codex/rts-connectors/cloudflare/docs/connectors/baseline.md) documents the difference. This UI branch starts from `main` as requested and must be reconciled with that newer backend before replacing the shared staging Worker. No deployment or authenticated/device acceptance is claimed by this change.

Use the existing **Cloudflare foundation** workflow with `deploy_staging: true` and the reviewed branch/ref. Its deployment job depends on Worker, browser and converter checks, uses the `cloudflare-staging` environment, and serializes deployments. It updates only `community-gaming-staging`; existing secrets and Durable Object state remain in place. Do not run command-registration or secret-installation scripts for a UI release.

Verify `/health`, `/api/bootstrap`, served assets, unauthenticated organizer protection, statistics visibility and the login return path. A complete authenticated smoke check must additionally sign in through Discord, edit and save a test queue, refresh/revisit it, preview an import without applying it, and return from an invitation to recheck saved setup. Never bypass Discord authorization with the operations token.

## Device acceptance

Desktop WebKit and phone emulation do not replace real devices. Before release, use iOS Safari and Android Chrome to check:

- Native CSV/JSON file selection, visible filenames, preview invalidation and blocking errors.
- Keyboard opening, browser chrome changes, rotation, safe areas and access to the last field and Save/Apply controls.
- Pinch zoom, long community/player names, dialog close/backdrop/Escape behavior and focus restoration with an external keyboard.
- Discord invitation/login round trips, the retained location and drafts, verified saved readiness, missing permissions and pending delivery shown separately.

Record the device/OS/browser versions and staging revision with the result. A health check or fixture test alone does not complete this device and OAuth acceptance.
