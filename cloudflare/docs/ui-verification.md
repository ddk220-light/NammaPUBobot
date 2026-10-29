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

Regression coverage also exercises reverting shared rating/tax edits after creating a queue, temporarily invalid policy values, an engine locking during a draft, and recreating a first queue with a stored engine. Saved readiness uses the latest fetched configuration while saves retain the draft's original revision. Back/Forward restores community, category and game-panel state; cancelling a community change keeps the history entry and draft. Field errors survive navigation and refresh, including nested role and rank errors. Public errors retain request IDs and access recovery links.

Import review exposes all supplied player, match and rating-history values in pages of 50 labelled cards. The 500-player test visits every page and checks final-record and Apply reachability. A successful Apply immediately replaces the preview with the server-confirmed result and counts; a failed follow-up refresh offers a separate retry and cannot expose Apply for the completed preview. Browser coverage verifies this with an unsaved configuration draft. Worker tests independently verify engine metadata after the last queue is removed, indexed validation paths, domain/revision conflicts and existing import safeguards.

Screenshots cover all five organizer destinations at phone and desktop widths, the public leaderboard and four profile sections, and import review. CI uploads screenshots, traces for failures, and the HTML report as `cloudflare-browser-evidence` for seven days.

## Isolated staging

### Console handoff follow-up (0.20.0)

The [adoption plan](console-handoff-plan.md) adds guided setup with shared drafts, feature-scoped Discord permission recovery, live queue overview cards, expandable player management, check-in progress, structured help and a revision-labelled change review. Organizer player records now include authoritative hidden status; public statistics authorization is unchanged. The full local suite passed: 882 Worker tests, 100 Chromium/WebKit browser cases and 11 converter tests, plus typechecks and both builds.

Browser fixtures cover invalid and failed guide saves, custom games, invitation return during a conflict, advanced/guide synchronization, optional permissions, two-step queue and player actions, 51-player pagination, no-community recovery, and guide reachability at phone/tablet/landscape sizes. These tests do not claim physical-device or authenticated Discord acceptance. The 0.20.0 changes have not been deployed; the live 0.19.0 verification described in PR #46 belongs to the earlier release.

### Deployed baseline

On 2026-10-28, remote `main` was `fa3647b` (0.16.0), while the existing staging Worker reported 0.18.1, Cloudflare version `df8289ac-fe02-45b5-92ee-1dd0870d4db4`. The downloaded Worker has SHA-256 `7b57a2aa807dae7a41c14042d8b6c12e6631d91fcc83e13afff1ae90529bbabd` and was retained only for read-only comparison. No matching original source commit was available. The separate [baseline reconciliation](https://github.com/ddk220-light/NammaPUBobot/blob/334c67fe0da8c06ebbcce36ed39525d961993236/cloudflare/docs/connectors/baseline.md) reconstructs those deployed behaviors; this release incorporates only that compatibility subset, without the connector extension or its schema migration.

Release 0.19.0 preserves `/test populate` and `/test checkin`, moderator authorization, atomic/idempotent population, test-result exclusions and refunds, TEST labels, retired isolated-session cleanup, and voice availability for Ping without occupant lists on queue cards. `staging-baseline.test.ts` and the voice tests cover those contracts alongside the UI regression suite. Storage remains at schema 7 with Durable Object migrations v1–v4. The build wrapper adds the source revision, dirty-tree flag and source-tree digest to `/health` and `/api/bootstrap`; deployment should use a clean committed ref. No deployment or authenticated/device acceptance is claimed by this document.

The rebuilt Worker was compared module by module with the retained deployed bundle. Remaining differences are the UI setup/capability/error contracts, authoritative rating-engine metadata, organizer login return paths, and build metadata. The test-command, match/card, economy, statistics-index and legacy-session behavior matches the deployed baseline.

Use the existing **Cloudflare foundation** workflow with `deploy_staging: true` and the reviewed branch/ref. Its deployment job depends on Worker, browser and converter checks, uses the `cloudflare-staging` environment, and serializes deployments. It updates only `community-gaming-staging`; existing secrets and Durable Object state remain in place. Do not run command-registration or secret-installation scripts for a UI release.

Verify `/health`, `/api/bootstrap`, served assets, unauthenticated organizer protection, statistics visibility and the login return path. A complete authenticated smoke check must additionally sign in through Discord, edit and save a test queue, refresh/revisit it, preview an import without applying it, and return from an invitation to recheck saved setup. Never bypass Discord authorization with the operations token.

## Device acceptance

Desktop WebKit and phone emulation do not replace real devices. Before release, use iOS Safari and Android Chrome to check:

- Native CSV/JSON file selection, visible filenames, preview invalidation and blocking errors.
- Keyboard opening, browser chrome changes, rotation, safe areas and access to the last field and Save/Apply controls.
- Pinch zoom, long community/player names, dialog close/backdrop/Escape behavior and focus restoration with an external keyboard.
- Discord invitation/login round trips, the retained location and drafts, verified saved readiness, missing permissions and pending delivery shown separately.

Record the device/OS/browser versions and staging revision with the result. A health check or fixture test alone does not complete this device and OAuth acceptance.
