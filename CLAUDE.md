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
  - (The legacy `!command` `bot/context/message/` handler was removed in Layer 5 — every prior `!cmd` has a slash equivalent, registered under its own bare name (no `namma_` or other prefix).)
- **`bot/stats/`** — Stats tracking, rating systems (Flat, Glicko2, TrueSkill)
- **`bot/civ_stats.py`** — Loads `data/player_civ_stats.csv` and `data/civ_elo_stats.csv` at import time. Provides `get_player_civs()` lookup, `pick_balanced_teams()` for randomized civ pools, and `get_today_civs()` for channel history scanning
- **`bot/web.py`** — Web dashboard server (aiohttp). Discord OAuth2 login, session management, REST API for channel/queue config CRUD, civ stats API. Serves `bot/web_page.html`
- **`bot/web_page.html`** — Self-contained SPA (inline CSS + JS). Two tabs: Civ Stats (public) and Dashboard (authenticated). Config forms auto-generated from CfgFactory variable types

### Utils (`utils/`)
Mostly standalone analysis scripts. Two subpackages are exceptions and **are** imported by the bot at runtime — `utils/replay/` (below) and `utils/classifications/registry|pipeline`:
- `replay/` — `extract.py` (replay → structured per-match records; `bot/replay_stats/parse.py` runs it in a worker process) and `download.py` (aoe2companion/aoe.ms fetch into the `data/replays/` cache; `bot/replay_stats/fetch.py` wraps it). Needs the vendored `mgz` fork (`PYTHONPATH=.replay_scratch`) only to actually parse. **Live ingest depends on both — do not move or break them.**
- `civ_analysis.py` — Async civ performance analyzer using aiohttp + aiomysql
- `analyze_matches.py` — DB match analysis tool
- `db_helpers.py` — Shared `create_pool()` and `parse_db_uri()` for utility scripts

### Quiz generation — one offline bank, one live bank (`utils/quiz_gen/`, `bot/quiz/player_bank.py`)
The daily quiz alternates two sources per week — **day 1/3/5/7 player, day 2/4/6 game** (player first, keyed on day-within-week, so week 2 also opens on player). The two sources are produced very differently:
- **Game bank** (offline, source=`game`): unit/civ questions from the `aoe2_matchup` sim DBs → `utils/quiz_gen/build_bank.py` → `data/quiz_bank.json` → `utils/quiz_gen/build_schedule.py` → `data/quiz_schedule.json`, the committed **queue** the bot draws one entry from per game day (first entry the channel has not been asked). Categories: combat, techgaps, stats, effects. Regenerate: `python build_schedule.py` from `utils/quiz_gen/`.
- **Player bank** (live, source=`player`): `bot/quiz/player_bank.py` builds "which of these four players ranks first?" from the community's `metric_boards` (stage 4, `bot/derived/boards.py`) at post time — no offline pipeline, no committed bank. A question needs ≥4 askable leaders on a board (the card's arity) and an untied answer, excludes `is_hidden` players, keys on `user_id` (names are display-only), and puts **name + Elo in the options, the metric value in the reveal**. A day that cannot produce a fair player question falls back to the game queue.
- **The calendar is arithmetic, not a file**: `bot/quiz/schedule.py`'s `slot_for_seq`/`source_for_day` derive (week, day, source) from the channel's own post counter, so a channel that started late or was paused reads its own calendar. `quiz_schedule.json` carries no seq/week/day.
- Retired in stage 5b: `utils/replay_quiz/` (the offline player pipeline), `utils/quiz_gen/convert_player_bank.py`, `utils/quiz_gen/player_sample.py`, `data/replay_quiz.db`, `data/question_bank.json`, `data/quiz_bank_player.json`. The replay parser/downloader that lived alongside them survives as `utils/replay/`.

### Command registration pattern
Slash commands are defined in `bot/context/slash/commands.py`. Each wraps a handler from `bot/commands/` via `run_slash()`, which handles interaction timing, context creation, and error formatting. Admin commands use subcommand groups defined in `bot/context/slash/groups.py`.

### Key conventions
- Bot uses tabs for indentation throughout the original codebase; newer files in `utils/` and `bot/civ_stats.py` use 4-space indentation
- Config is a `.cfg` file but is actually Python source loaded via `SourceFileLoader`
- All DB access is async through `core/database.db` (the adapter instance)
- `bot.queue_channels` is the central dict mapping `channel_id → QueueChannel`
- State is persisted to `saved_state.json` on shutdown and restored on startup
- `core/data_registry.py` is the source of truth for every table's contract: its layer (core/raw/link/derived/ops), tenancy, sole writer(s), and retention class. `tests/test_data_registry.py` enforces two-way agreement between this registry and every `ensure_table`/`FactoryTable` declaration in `bot/` and `core/`
- Deployment target is Railway (see `railway.toml`, `Dockerfile`, `start.py`)
- Web dashboard requires `WS_ENABLE=True`, `WS_ROOT_URL`, `DC_CLIENT_SECRET` env vars. OAuth2 redirect URL must be registered in Discord Developer Portal as `{WS_ROOT_URL}/auth/callback`
- `start.py` generates `config.cfg` from env vars — any new config vars need corresponding entries in its template
