# Design: `/player_civ_stats` Slash Command

## Overview
Discord slash command that shows a player's top 5 best and worst civilizations with win rates, sourced from pre-generated `player_civ_stats.csv`.

## Components

### 1. CSV Data Loader (`bot/civ_stats.py`)
- Module-level function to load and parse `data/player_civ_stats.csv`
- Data structure: `{nick: [{civ, wins, losses, games, winrate}, ...]}`
- Loaded once on import, with reload helper
- Filters to civs with >= 3 games, sorts by winrate for best/worst

### 2. Slash Command (`bot/context/slash/commands.py`)
- `/player_civ_stats` with `player: Member` parameter (Discord built-in member autocomplete)
- Handles interaction directly (no `run_slash`) — not queue-channel-specific
- Calls civ_stats module, formats as embed, responds

### 3. Embed Output Format
```
Civ Stats for fenrir05

Best Civs
1. Burgundians — 80.0% (4W / 1L, 5 games)
2. Berbers — 75.0% (3W / 1L, 4 games)
...

Worst Civs
1. Gurjaras — 0.0% (0W / 3L, 3 games)
2. Aztecs — 33.3% (1W / 2L, 3 games)
...

12 civs with 3+ games
```

### 4. Edge Cases
- **Player not in CSV**: "No civ stats found for {player}."
- **Fewer than 5 qualifying civs**: show available with note "X civs with 3+ games"
- **Overlap**: if <= 5 total qualifying civs, show in best only, skip worst

### 5. Nick Matching
CSV uses bot `nick` field. Match Discord member's `display_name.lower()` against CSV nicks (lowercased). The `player: Member` type gives Discord's built-in member autocomplete.

## Files to Touch
| File | Change |
|------|--------|
| `bot/civ_stats.py` | **New** — CSV loader + data lookup |
| `bot/context/slash/commands.py` | Add `/player_civ_stats` command |

## Decisions
- **Data source**: Pre-generated CSV (fast, simple)
- **Autocomplete**: Discord guild members (consistent with other commands)
- **Min games threshold**: 3 games
- **Max civs shown**: 5 best + 5 worst
