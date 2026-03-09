# Civ Stats Web UI Design

## Purpose
Serve a simple public web page that displays `data/civ_elo_stats.csv` as a sortable table, deployed alongside the bot on Railway.

## Architecture
aiohttp web server runs in the bot's asyncio loop. Two routes:
- `GET /` — self-contained HTML page with sortable table
- `GET /api/civ-stats` — serves CSV data as JSON

## Files
| File | Change |
|------|--------|
| `bot/web.py` | New — aiohttp app, two routes, reads CSV from disk |
| `PUBobot2.py` | Start web server task alongside bot |
| `start.py` | No change needed — web server starts unconditionally |

## HTML Page
- Vanilla HTML/CSS/JS, no framework
- Table with all 11 columns from civ_elo_stats.csv
- Click column header to sort ascending/descending (toggle)
- Clean readable styling
- Fetches data from `/api/civ-stats` on page load

## Port
- Reads `PORT` env var (Railway provides this), defaults to 8080
- Binds to `0.0.0.0`

## Shutdown
Web server's AppRunner cleaned up in `think()` exit path in PUBobot2.py.

## Decisions
- Public, no auth — aggregate civ stats only
- Embedded in bot process — low overhead, shares asyncio loop
- aiohttp — already a project dependency
