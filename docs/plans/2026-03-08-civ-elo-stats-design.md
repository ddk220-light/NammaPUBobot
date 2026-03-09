# Civ Elo Stats CSV Generator

## Purpose
Generate `data/civ_elo_stats.csv` — aggregate civ performance broken down by player elo and team elo brackets.

## Script
`utils/civ_elo_stats.py` — standalone script, reads `match_civ_details.csv` + rating history (DB or CSV fallback).

## Data Flow

```
match_civ_details.csv          qc_rating_history (DB or CSV)
  (bot_match_id, nick,    +     (match_id, user_id,
   civ, result, team)           rating_before)
        |                              |
        |    qc_players (DB or CSV)    |
        |      (user_id -> nick)       |
        +----------+-------------------+
                   v
         Join on (match_id, nick->user_id)
                   v
         For each row: compute
           - player_elo = rating_before
           - team_avg_elo = avg(rating_before) for all
             players on same team in that match
                   v
         Aggregate by civ across brackets
                   v
         data/civ_elo_stats.csv
```

## Output CSV Columns

| Column | Description |
|--------|-------------|
| `civ` | Civilization name |
| `games` | Total games played |
| `winrate` | Overall win rate (0.00-1.00) |
| `games_player_elo_above_1000` | Games where player elo >= 1000 |
| `winrate_player_elo_above_1000` | Win rate in those games |
| `games_player_elo_below_1000` | Games where player elo < 1000 |
| `winrate_player_elo_below_1000` | Win rate in those games |
| `games_team_elo_above_1000` | Games where player's team avg elo >= 1000 |
| `winrate_team_elo_above_1000` | Win rate in those games |
| `games_team_elo_below_1000` | Games where player's team avg elo < 1000 |
| `winrate_team_elo_below_1000` | Win rate in those games |

Sorted alphabetically by civ. Winrates two decimal places. Zero-game brackets show 0 games / 0.00 winrate.

## Joining Strategy

1. Build `nick -> user_id` map from `qc_players` (DB or CSV)
2. For each row in `match_civ_details.csv`, look up `user_id` via nick
3. Look up `rating_before` from `qc_rating_history` via `(match_id, user_id)`
4. Compute team avg elo = average of `rating_before` for all players on same team in same match
5. Rows where rating lookup fails: included in overall stats, excluded from elo-bracket columns

## CLI

```
python3 utils/civ_elo_stats.py [--csv] [--threshold 1000]
```

- `--csv`: Use CSV files instead of DB
- `--threshold`: Elo cutoff (default 1000)
- DB connection via `db_helpers.py` (same pattern as other utils)

## Decisions
- Elo source: `rating_before` from `qc_rating_history` (rating at time of match)
- Team elo: player's own team average
- Zero-game brackets: show 0 games / 0.00 winrate
- Approach: read CSV + DB lookup, aggregate in-memory (Approach 1)
