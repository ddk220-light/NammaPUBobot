# NammaMatchBot UI and public-site delivery

The public name is RTS Pickup. Internal package identity is NammaMatchBot; public name, repository and license are centralized in `src/identity.ts`, and site copy reads the name from there rather than hard-coding it.

## Build and routes

`npm run build:web` builds the organizer first, preserves it at `/app/`, then emits and validates 31 static public pages plus 10 moved-page notices. Game coverage is checked against the selectable catalog; command documentation uses the registered command tree.

The Worker runs before assets so environment indexing policy applies to both. `/stats` and `/link` remain supported. Old root organizer queries redirect to `/app/` preserving query state. Unknown public routes use the real 404 asset. Vite preview supplies equivalent app entry routing for browser fixtures.

Public indexing is **off by default**. Before launch, confirm the public name, canonical domain, About details, contact and hosted-service offer. Build with `PUBLIC_SITE_ORIGIN=https://approved-domain` and `PUBLIC_SITE_INDEXING=true`, and set the Worker `PUBLIC_SITE_INDEXING` variable to `true` only in production. Staging always receives noindex. The public origin must agree with APP_ORIGIN. Analytics and third-party directory submissions are not enabled.

## Publishing and migrations

Community schema 9 adds `site_metadata`, included in operational backups and isolated recovery. It stores public presentation, stable opaque route identifiers, last-known channel names and pending directory registration. Wrangler migration v6 adds `PublicDirectory`; main also contains the earlier provider migration v5.

Search publishing defaults off. It requires public statistics and a public community display name. It is saved atomically with configuration. A directory entry is only a discovery hint: every page and sitemap generation checks saved community policy. Dynamic pages use no-store. Revocation returns 404 immediately at the service; search-engine copies can persist temporarily.

Registration is attempted after successful save. Failure retains a durable pending flag and schedules a retry. It does not report a successful configuration save as failed. Public/game route identifiers remain stable when names change. Directory data can be reconstructed by saving the community configuration; authoritative settings and route identifiers are backed up in the community object.

Current channel discovery and cached names are distinct. Cached names are displayed as unverified and do not grant permissions.

## Verification and remaining launch work

Run `npm run check`, `npm run test:ui`, and both converter test scripts. Public build checks validate metadata, headings, canonicals, JSON-LD, internal links, font files and authored game coverage.

Before deploying current main to the existing Cloudflare app, perform an operational backup and verify connector acceptance gates. This release introduces storage changes and is not a UI-only backport. Keep staging noindex. Check the first AoE2 setup and optional connections in the actual deployed environment before announcing hosted support for each provider.

Real iOS Safari and Android Chrome verification requires physical devices: native file picker, soft keyboard, safe areas, pinch zoom and returning from Discord. Browser emulation is not evidence that those checks were performed. Search Console/Bing verification, domain configuration and external announcements remain launch work after the public identity is confirmed.

## Implementation verification

Local verification passed: 1,357 Worker tests, 124 Chromium/WebKit cases, 11 converter cases, type checks, public-page build validation and Worker dry build. A focused 101-case regression run covers the final publishing retry/revision guard and gameplay scheduling changes. Anonymous requests against a real local Worker verified public HTML, `/app/`, `/stats`, `/link`, robots, sitemap, asset noindex and a true 404.

Representative desktop/phone screenshots were captured for all organizer destinations and the public home, game, guide, features, About and Open Source pages. Physical iOS/Android checks and an authenticated deployed Discord setup smoke test have not been performed in this release.

Directory registrations carry the saved configuration revision. Older registrations cannot replace newer ones. Pending registration has a durable retry deadline that participates in the existing gameplay alarm schedule, so subsequent scheduling does not erase the retry.

## UI gap fixes (0.22.2)

Public marketing pages offer a runtime `/invite` action using the configured Discord application and base channel permissions. It accepts no destination override and adds no analytics. The home page names Discord pickup games, and each of the seven game pages has authored game-specific examples, a practical five-step workflow, accurate limitations and separate setup links. Search publishing and public-by-link statistics remain distinct. Indexing stays off on the existing staging deployment.

Queues disable Start below two waiting players and when paused; empty queues omit Clear. Manual starts retain the existing uneven-team policy and explain it. Advanced match repairs use separate reviewed forms and named roster/server-member choices. Member search is authenticated, requires fresh Manage Server authority, excludes bots, and keeps loaded choices on failure. Final actions still resolve members and validate the saved match version on the server. Stale search responses cannot update a different community, query or replaced form.

Automatic substitutions rebalance during check-in and preserve team membership once playing, matching explicit replacements. Captain ordering may refresh within each team. Initial render no longer focuses the community picker without a prior focused control; keyboard focus restoration remains in place.

This release adds no storage migration or connector activation. Real iOS/Android hardware checks and an authenticated organizer journey on the live deployment remain outside browser-fixture coverage.

## Public site for server owners (S3)

Content lives in `site/content.mjs` (pages), `site/game-copy.mjs` (what is specific to each game), `site/examples.mjs` (example bot cards) and `site/commands.mjs` (command grouping). `scripts/build-site.mjs` assembles them; `src/public-site.ts` is the shell.

- **One setup guide.** `/guides/getting-started/` holds the five steps. The seven `/games/*/setup/` pages, `/features/queues-and-teams/`, `/guides/import-ratings/` and `/guides/switch-from-pubobot/` are folded into other pages; each old URL keeps a short noindex page with a canonical link and an immediate meta refresh, is listed under `moved` in `site-manifest.json`, and is left out of the sitemap. A worker 308 for those paths would be cleaner and can replace the notices later.
- **Feature pages.** Eleven pages under `/features/`, each short and with a "What it can't do yet" list. Beta: AoE2 lobby tracking and other game tracking, predictions (opt-in).
- **Example cards.** `site/examples.mjs` renders what the bot posts (queue, check-in, teams, result, substitute, map vote, predictions, civ picks, withdrawn result) in the site palette, captioned "Example". Wording follows `src/discord/cards.ts`; names and ratings are illustrative. The match card shows a "Report result" button, which the bot unit is adding.
- **Console screenshots.** `web/public/screenshots/console-*.png` come from the local preview with the e2e mock fixtures (`mockApp`), environment badge hidden, demo names. Regenerate them with a throwaway spec like the one in the S3 hand-off; do not commit it under `e2e/`, because it writes into `web/public/`.
- **Command reference.** Grouped Players → Captains → Organizers by a small map in `site/commands.mjs`. A command the map does not know is placed by its permissions and description (admin permissions → Organizers, "Captain…" → Captains, otherwise Players), never dropped.
- **Checks.** `scripts/check-site.mjs` now also checks title (≤62) and description (50–160) length, heading order, breadcrumb JSON-LD against the visible trail, images (exists, width, height, alt), same-page anchors, moved pages, the sitemap and internal wording on public pages. `tests/public-site.test.ts` crawls the generated pages through the assets binding.
