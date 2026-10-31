# Review v3 — web app on `main` (31 Oct 2026)

Reviewed `origin/main` at `4efa411` ("Merge organizer UI refinements and
public search pages", #49). Design only: no code was changed. Design canvas:
https://claude.ai/artifact/GY8vuTZcFZYyFCShx43EuJ (private until shared).
Green stickies marked "v3" on the canvas repeat the items below next to the
affected screens. Two new boards: **Copy guide** (public row) and **Match
actions** (console row).

## How it was checked

- `npm run check`: type checks, **1,358 unit tests pass**, 32 public pages
  generated and validated, worker dry build passes.
- Playwright, Chromium: **62 of 62 pass**.
- Screenshots of the public site, every console view, the full setup guide and
  the review dialog at 1440 px and 390 px, using the repo's own fixtures, plus
  a dark-scheme check. WebKit, real phones and a deployed Discord run were not
  part of this review.

## Done well since v2

- Light theme by default; IBM Plex self-hosted; no gold eyebrows, blurple
  buttons or diamond logo. The console looks calm and human-made.
- Review changes is a plain summary ("2 changes … Create queue · pickups · 8
  players · captain teams"), with every saved field behind a link.
- The setup guide replaces the Setup page while it runs, uses game cards, shows
  a neutral not-invited state and says `/add`.
- Public site: 32 static pages (7 games with setup guides, features, guides
  including generated commands, About, Open source, Privacy, Terms, Changelog),
  console at `/app/`, real 404, canonical links, Open Graph, JSON-LD,
  per-environment robots and sitemaps, noindex until launch, opt-in search
  publishing for leaderboards with revocation. All of this matches
  `SEO-FRONTEND.md` / `SEO-BACKEND.md`.

## Needs work

### Public website (affects search and sign-ups) — high

1. **No "Add to Discord" anywhere on the public pages.** The home and game pages
   have only "Get started" and "Open dashboard"
   (`src/public-site.ts` header template; `site/content.mjs`). Add a worker route
   such as `/invite?ref=<page>` that redirects to the bot invite URL (static
   pages can't know it at build time), and link it from the header, the hero,
   each game page's top and end, and the at-a-glance box. Count `ref`.
2. **Home page doesn't target the searches.** Title and H1 are "Free,
   open-source Discord tools for your gaming community" — no "bot", "pickup" or
   game names. Use the copy on the Copy guide board. Remove the breadcrumb from
   the home page (the template in `src/public-site.ts` adds
   `Home / {title}` to every page, including `/`).
3. **Game pages read like internal notes.** Long paragraphs about "provider
   observations", "Beta label never guarantees…", "Configuration required and
   Awaiting activation mean different things". Rewrite in the order and voice
   of the Copy guide board and `Site-Game.dc.html`: one-line answer, Add to
   Discord, five-step game night, a screenshot of the Discord cards, what's
   specific to the game, setup steps, up to four limits, up to five questions.
   Move the three sections repeated on all seven pages ("Queues that fit your
   community", "A dashboard for organizers", "Free software and community
   control") to Features / Open source. Fix "How **a** Age of Empires II night
   works" → "How an AoE2 night works" (`site/content.mjs`).
4. **The home-page screenshot shows the console with "0 waiting / 8 capacity"
   and "Start with 0 now".** Replace it with the Discord queue card and teams
   card (as in `Main.dc.html`), or a console shot with a full queue.
5. **Home game cards all say the same thing** ("community queues and
   captain-confirmed results"). Give each one line that differs (civ records,
   W3Champions profiles, lobby tracking beta).
6. **Four games have no page** (StarCraft II, C&C Generals, Northgard, Tempest
   Rising). If they stay selectable as legacy or custom games, give them short
   pages that say "captains report results"; they are low-competition searches.
   If they're intentionally unsupported, leave them out and say so in the
   changelog.

### Console — medium

7. **"Start with 0 now" is always enabled** (`web/main.ts:825`). Disable below 2
   waiting with the label "Need 2 players to start"; hide Clear queue when
   nobody is waiting; wording "5 of 8 waiting", not "0 waiting / 8 capacity".
   See the Match actions board, right column.
8. **Recording a result still goes through a generic form.** Matches →
   Administrator actions → "Match action: Record result" → "Who won?" select,
   with free-text "Player ID" and "Replacement ID" fields for roster fixes.
   Replace with the Match actions board: "Who won?" choice on the live match
   with a one-sentence preview; separate Replace / Move / Cancel forms; people
   picked by name from the roster and a member search; match and record IDs only
   under "Technical details". Replacing after the start keeps everyone else on
   their team.
9. **Setup-guide game cards repeat two lines of status on every card**
   ("Manual reporting · Beta connections: Awaiting activation") and game names
   wrap mid-card. One short label per card: "Lobby tracking · beta" or
   "Captains report results"; let the card grow so names don't wrap
   (`web/guide.ts`).
10. **"Unavailable channel for <queue>"** still appears in the access table
    when no channel name has been cached (`web/guide.ts:60`,
    `web/main.ts:807`). Show the queue name and "channel not checked yet".
11. **Phone keeps a menu button** at 390 px instead of the bottom tab bar
    (Queues, Matches, Players, Setup). `responsive.css` defines `--tabbar`; use
    it at phone widths for the console.
12. **Small polish:** the community picker draws a heavy dark outline at rest,
    which looks focused; labels "Players", "Team formation", "Queue aliases"
    should read "Players per match", "How teams are made", "Nicknames"
    (`web/change-review.ts:18,24`, `web/settings.ts`, `web/guide.ts`).

### Not checked here (from `docs/public-site-delivery.md`)

- Real iOS Safari and Android Chrome checks.
- An authenticated setup on the deployed app with a real Discord server.
- Launch decisions still open: product name and domain, About text, contact,
  analytics. Indexing correctly stays off until then.

## Discord bot (separate analysis)

The Discord flow review (match pings, captain reporting, mid-game substitutes,
command clutter) is in `discord-journeys.html` in this package.
