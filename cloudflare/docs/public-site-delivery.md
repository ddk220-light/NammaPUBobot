# NammaMatchBot UI and public-site delivery

The public name remains Community Games. Internal package identity is NammaMatchBot; public name, repository and license are centralized in `src/identity.ts`. No Discord application or repository rename is part of this release.

## Build and routes

`npm run build:web` builds the organizer first, preserves it at `/app/`, then emits and validates 32 static public pages. The seven published-source game descriptions have 700–1,200 words each and separate setup guides. Game coverage is checked against the selectable catalog; command documentation uses the registered command tree.

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

Representative desktop/phone screenshots were captured for all organizer destinations and the public home, game, guide, features, About and Open Source pages. The website includes a labelled organizer screenshot using test fixtures. Physical iOS/Android checks and an authenticated deployed Discord setup smoke test have not been performed in this release.

Directory registrations carry the saved configuration revision. Older registrations cannot replace newer ones. Pending registration has a durable retry deadline that participates in the existing gameplay alarm schedule, so subsequent scheduling does not erase the retry.
