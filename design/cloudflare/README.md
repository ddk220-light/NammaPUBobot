# Community Games — design handoff (v2, 31 Oct 2026)

Design for the **Cloudflare** app only (`cloudflare/`). Design only: no
product file was changed. The implementing agent builds it; the owner tests it.

**Live canvas (pan, zoom, press Play on any frame):**
https://claude.ai/artifact/GY8vuTZcFZYyFCShx43EuJ
(private until the owner shares it from the Share menu)

## What changed in v2

- **Theme:** light by default, dark as an option only. A calmer palette
  (warm-neutral greys, one slate-blue accent), IBM Plex Sans, smaller radii,
  sentence-case labels, no uppercase eyebrows, no glowing borders. Every
  screen was re-themed; the rules are on the `Theme.dc.html` board.
- **Public website row (new):** home, game page template, open source and
  about, plus phone versions, built for search (see `SEO-*.md`). The home page
  replaces the signed-out welcome; the console moves to `/app`.
- **Setup guide:** the game step lists all 11 catalog games with honest labels
  (lobby tracking in beta, or captains report results).
- **Review changes (new):** a plain-language summary instead of a dump of
  every saved field.
- **Review of the current build:** `IMPLEMENTATION-REVIEW.md` lists every fix
  for `codex/cloudflare-responsive-ui`. Yellow stickies on the canvas repeat
  them beside the affected screens.

## Files

| Path | What |
| --- | --- |
| `README.md` | This file |
| `IMPLEMENTATION-REVIEW.md` | **Start here.** What to fix in the current build, in order |
| `IMPLEMENTATION.md` | Tokens (light + dark), components, CSP rules, field mapping, phone rules |
| `SEO-FRONTEND.md` | Static public pages, URLs, head tags, JSON-LD, content rules, tests |
| `SEO-BACKEND.md` | Domain, real 404s, robots and sitemaps, noindex on staging, leaderboard pages, headers |
| `BACKEND-NOTES.md` | Earlier API notes (bot-presence check, record counts, delivery retry…) |
| `seo-plan.html` | The full SEO plan with reasoning and the search vocabulary |
| `design/canvas.json` | Canvas index: frame positions, titles, stickies |
| `design/*.dc.html` | One file per screen; inline styles carry exact sizes, colours and copy |

The `.dc.html` files load `./support.js`, which exists only inside the canvas,
so open the canvas link to see them rendered. Every style is inline, so the
markup can be read directly.

## Screens

**Public website** (static HTML, indexed): `Main` (home `/`), `Site-Game`
(game page template, shown for Age of Empires II), `Site-Open-Source`
(`/open-source/` and `/about/`), `Theme` (style guide, not a page).

**Setup guide** (`/app`): `Setup-Invite`, `Setup-Game` (11 games),
`Setup-Queue`, `Setup-Try`, `No-Servers`.

**Organizer console** (`/app`): `Queues`, `Queue-Editor`, `Review-Changes`,
`Matches`, `Players`, `Server-Setup`, `Statistics` (public leaderboard at
`/c/{community}/{game}/`).

**Phone, 390 px:** `M-Welcome` (public home), `M-Site-Game`, `M-Setup-Invite`,
`M-Setup-Queue`, `M-Queues`, `M-Queue-Editor`, `M-Matches`, `M-Players`,
`M-Statistics`.

## Owner decisions still open

1. Product name and domain ("Community Games" is a placeholder; it can't rank).
2. When the Cloudflare app becomes production (launch the public pages then).
3. About-page text (marked `[…]` in `Site-Open-Source.dc.html`).
4. Analytics (Cloudflare Web Analytics recommended) and AI crawlers (allow recommended).
