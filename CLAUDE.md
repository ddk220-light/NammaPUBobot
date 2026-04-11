# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

NammaPUBobot — a fork of PUBobot2, a Discord bot for organizing AoE2 pickup games. Built with Python 3.11 (Railway Dockerfile ships 3.11-slim, `ruff.toml` targets py311), nextcord (discord library), aiomysql, and MySQL.

## Running the Bot

```bash
# Install dependencies
pip3 install -r requirements.txt

# Configure: copy and fill in credentials
cp config.example.cfg config.cfg

# Run directly
python3 PUBobot2.py

# Or via Railway wrapper (generates config.cfg from env vars, then runs the bot)
python3 start.py
```

## Linting & Tests

```bash
# Lint (config lives in ruff.toml; line-length 120, tab indent)
ruff check .

# Run the pytest suite (pure-function tests for elo_sync/civ_sync parsers)
pytest tests/
```

CI runs both on every PR via `.github/workflows/ci.yml`.

## Architecture

### Boot sequence
`PUBobot2.py` is the entrypoint. It:
1. Loads `core/config.py` (imports `config.cfg` as a Python module via `SourceFileLoader`)
2. Connects to MySQL via `core/database.py` → `core/DBAdapters/mysql.py` (aiomysql pool)
3. Imports `bot/` which registers all commands and event handlers
4. Starts the asyncio event loop with a 1-second `think()` tick and the Discord client

### Core layer (`core/`)
- **`config.py`** — Loads `config.cfg` as a Python module (not INI/YAML — it's raw Python)
- **`client.py`** — `DiscordClient` subclass of `nextcord.Client`. Custom event system allowing multiple handlers per event. Command registry via `@dc.command()` decorator
- **`database.py`** — Initializes the DB adapter from `DB_URI` (only MySQL adapter exists)
- **`cfg_factory.py`** — Generic config system: `CfgFactory` manages typed variables stored in MySQL, used by both `QueueChannel` and `PickupQueue` for per-channel/per-queue settings

### Bot layer (`bot/`)
- **`bot/__init__.py`** — Global state: `queue_channels` dict, `active_queues`, `active_matches`, `waiting_reactions`
- **`bot/events.py`** — Discord event handlers: `on_ready` loads queue channels from DB, `on_think` runs match/expire/noadds ticks, `on_presence_update` removes offline/afk players
- **`bot/queue_channel.py`** — `QueueChannel` class: represents a Discord channel with pickup queues. Manages its own `CfgFactory` config and list of `PickupQueue` instances
- **`bot/queues/pickup_queue.py`** — `PickupQueue`: player queue that starts a `Match` when full
- **`bot/match/match.py`** — `Match` lifecycle: INIT → CHECK_IN → DRAFT → WAITING_REPORT. Contains `Team`, `CheckIn`, `Draft`, `Embeds` helpers
- **`bot/commands/`** — Command implementations (config, queues, matches, stats, admin, misc). Imported via `__init__.py` star imports
- **`bot/context/`** — Command context abstraction:
  - `slash/` — Slash command definitions in `commands.py`, autocomplete in `autocomplete.py`, command groups in `groups.py`
  - (The legacy `!command` `bot/context/message/` handler was removed in Layer 5 — every prior `!cmd` has a slash equivalent prefixed `namma_`.)
- **`bot/stats/`** — Stats tracking, rating systems (Flat, Glicko2, TrueSkill)
- **`bot/civ_stats.py`** — Loads `data/player_civ_stats.csv` and `data/civ_elo_stats.csv` at import time. Provides `get_player_civs()` lookup, `pick_balanced_teams()` for randomized civ pools, and `get_today_civs()` for channel history scanning
- **`bot/web.py`** — Web dashboard server (aiohttp). Discord OAuth2 login, session management, REST API for channel/queue config CRUD, civ stats API. Serves `bot/web_page.html`
- **`bot/web_page.html`** — Self-contained SPA (inline CSS + JS). Two tabs: Civ Stats (public) and Dashboard (authenticated). Config forms auto-generated from CfgFactory variable types

### Utils (`utils/`)
Standalone analysis scripts (not imported by the bot at runtime):
- `civ_analysis.py` — Async civ performance analyzer using aiohttp + aiomysql
- `analyze_matches.py` — DB match analysis tool
- `db_helpers.py` — Shared `create_pool()` and `parse_db_uri()` for utility scripts

### Command registration pattern
Slash commands are defined in `bot/context/slash/commands.py`. Each wraps a handler from `bot/commands/` via `run_slash()`, which handles interaction timing, context creation, and error formatting. Admin commands use subcommand groups defined in `bot/context/slash/groups.py`.

### Key conventions
- Bot uses tabs for indentation throughout the original codebase; newer files in `utils/` and `bot/civ_stats.py` use 4-space indentation
- Config is a `.cfg` file but is actually Python source loaded via `SourceFileLoader`
- All DB access is async through `core/database.db` (the adapter instance)
- `bot.queue_channels` is the central dict mapping `channel_id → QueueChannel`
- State is persisted to `saved_state.json` on shutdown and restored on startup
- Deployment target is Railway (see `railway.toml`, `Dockerfile`, `start.py`)
- Web dashboard requires `WS_ENABLE=True`, `WS_ROOT_URL`, `DC_CLIENT_SECRET` env vars. OAuth2 redirect URL must be registered in Discord Developer Portal as `{WS_ROOT_URL}/auth/callback`
- `start.py` generates `config.cfg` from env vars — any new config vars need corresponding entries in its template
