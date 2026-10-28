# Community Games organizer console — design handoff

Design for the **Cloudflare** web app only (`cloudflare/web/`: `index.html`,
`main.ts`, `onboarding.ts`, `stats.ts`, `style.css`). Nothing in the Railway
dashboard (`nammaoe2bot/web/`) is covered here.

This package is design only. No product file was changed. The implementing
agent builds it; the owner tests it.

**Live canvas (pan, zoom, press Play on any frame):**
https://claude.ai/artifact/GY8vuTZcFZYyFCShx43EuJ

The canvas is private until its owner shares it from the page's Share menu.

## What is in this zip

| Path | What |
| --- | --- |
| `README.md` | This file: screen index and reading order |
| `IMPLEMENTATION.md` | Tokens, components, behaviour, CSP rules, field mapping |
| `BACKEND-NOTES.md` | API gaps and checks the backend agent should look at |
| `design/canvas.json` | Canvas index: frame positions and titles |
| `design/*.dc.html` | One file per screen. Plain HTML with inline styles: read them for exact sizes, colours and copy |

The `.dc.html` files load `./support.js`, which only exists inside the
canvas, so they don't render on their own. Open the canvas link to see
them, or read the markup directly — every style is inline.

## Screens

Desktop frames are 1440 px wide and fluid (they are pages, not fixed
mock-ups). Phone frames are 390 px wide.

### Desktop — getting started
| File | Screen | Replaces in today's app |
| --- | --- | --- |
| `Main.dc.html` | Welcome, signed out | The bare signed-out card in `main.ts` `render()` |
| `Setup-Invite.dc.html` | Setup guide step 2: invite the bot, with a real **Check again** | The red "Invite the bot…" error and the endless "Loading settings…" |
| `Setup-Game.dc.html` | Step 3: pick a game (catalog cards + custom), AoE II lobby options | `setupSection()` add-game form |
| `Setup-Queue.dc.html` | Step 4: first queue — name, channel, size, team method — with a live Discord preview | The first `queueSection()` form |
| `Setup-Try.dc.html` | Step 5: try `/add` in Discord. **A note, not a checkbox** — the page can't see Discord | New |
| `No-Servers.dc.html` | Signed in but manages no server | The empty server list |

### Desktop — organizer console
| File | Screen |
| --- | --- |
| `Queues.dc.html` | Queue cards: status, waiting bar, players, quick actions; "Happening now"; player command cheat-sheet |
| `Queue-Editor.dc.html` | Queue settings in 8 categories with left nav, plain-language help, info tips, unsaved-change bar, danger zone |
| `Matches.dc.html` | Live matches first, check-in progress, review-then-confirm result recording, finished table, waiting-player controls, delivery problems, activity log |
| `Players.dc.html` | Ratings table with inline manage row, leaderboard visibility and ranks, rank-role permission warning, import preview, gold summary |
| `Server-Setup.dc.html` | Bot status, games, per-channel Discord permission matrix with fix steps, who can sign in, public page link, version |
| `Statistics.dc.html` | Public leaderboard + player detail (rating, peak, win rate, streak, history chart, civs, teammates, recent matches) |

### Phone
| File | Screen |
| --- | --- |
| `M-Welcome.dc.html` | Welcome |
| `M-Setup-Invite.dc.html` | Setup guide, invite step, actions pinned to the bottom |
| `M-Setup-Queue.dc.html` | Setup guide, first queue |
| `M-Queues.dc.html` | Queues with bottom tab bar |
| `M-Queue-Editor.dc.html` | Queue settings: category chips, info tip as a bottom sheet, save bar |
| `M-Matches.dc.html` | Matches with result recording |
| `M-Players.dc.html` | Players as an expandable list |
| `M-Statistics.dc.html` | Public statistics |

Phone Setup reuses the desktop `Server-Setup` layout (its sections already
stack); the tab bar links to it.

## Reading order for the implementer
1. `IMPLEMENTATION.md` §1–3 (tokens, CSP, components).
2. The setup guide frames, then `IMPLEMENTATION.md` §4.
3. `Queue-Editor.dc.html` with the field table in `IMPLEMENTATION.md` §5.
4. The remaining console screens, then phone (§7).
5. Hand `BACKEND-NOTES.md` to whoever owns `cloudflare/src/`.
