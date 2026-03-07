# Civ Analysis Script Improvements (Approach 2)

## Goal

Improve `utils/civ_analysis.py` to be faster, require fewer manual steps, and produce more reliable match mappings. Keep it as an offline script for now; lay groundwork for future bot integration.

## Changes

### 1. Parallel async API fetching

Replace `urllib.request` (blocking, sequential) with `aiohttp` + `asyncio.gather()`.

- Bounded concurrency via `asyncio.Semaphore(5)` to avoid hammering the API.
- Keep 0.2s delay between requests within each player's pagination, but fetch multiple players simultaneously.
- Expected speedup: ~5x (from ~40s to ~8s for 20 players).
- `aiohttp` added to requirements (dev dependency, not needed by the bot itself).

### 2. Read match data directly from MySQL

Eliminate the need for manual CSV exports of `qc_matches`, `qc_player_matches`, and `qc_players`.

Connect to MySQL using the same `config.cfg` DB_URI the bot uses (pattern already exists in `analyze_matches.py`).

Field mapping from current CSV to DB:
- `qc_matches.at`: CSV has datetime string, DB has unix timestamp. Convert with `datetime.fromtimestamp()`.
- `qc_matches.winner_team` (CSV) = `qc_matches.winner` (DB). Same semantics (0/1/NULL).
- All other fields match directly.

Keep `player_profile_map.csv` as the profile ID source (no DB table yet).
Keep CSV *outputs* (`match_civ_details.csv`, `player_civ_stats.csv`, `match_id_map.csv`) for sharing/analysis.

Add `--csv` flag to fall back to CSV input mode for environments without DB access.

### 3. Delete `cross_reference_matches.py`

Superseded by `civ_analysis.py`. Contains hardcoded match data and duplicated logic. Remove it.

### 4. Smarter time-window matching

Replace the rigid 30-120 min window with a wider 0-180 min window, weighted by time proximity.

New scoring formula:
```
time_penalty = time_diff_min / 180  (0.0 to 1.0)
player_score = overlap_count / total_bot_players  (0.0 to 1.0)
combined_score = player_score * (1 - 0.3 * time_penalty)
```

Require `combined_score >= 0.4` (currently requires `player_score >= 0.5`).
This handles short games and timezone edge cases while still preferring close time matches.

### 5. Clean up `analyze_matches.py`

Minor: share the DB connection helper between `analyze_matches.py` and `civ_analysis.py` to avoid duplicating the URI parsing logic.

## What stays the same

- `player_profile_map.csv` remains the source for profile IDs (manual edits).
- Output CSVs continue to be written to `data/`.
- `match_id_map.csv` caching mechanism unchanged.
- Script remains a standalone CLI tool, not part of the bot runtime.

## Future evolution (Approach 1)

When ready to integrate into the bot:
- Move profile map to a `player_profiles` DB table.
- Add background task to map matches after they finish.
- Add `/civstats` slash command.
- This design keeps the matching logic clean and reusable for that transition.
