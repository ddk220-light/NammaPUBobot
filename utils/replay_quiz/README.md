# Replay-quiz pipeline

Downloads this server's AoE2 DE replays, parses them, and extracts per-player
in-game stats (attributed to leaderboard identities) to drive "replay quiz"
questions (e.g. who reached Feudal slowest, who made the most army, who relocated
their TC, who made units before Feudal).

## Setup

```bash
pip install -r utils/replay_quiz/requirements.txt
```

This installs the **sanduckhan aoc-mgz fork** (= PyPI mgz + the save_version-67
fast-header fix). Stock PyPI `mgz` 1.8.51 **cannot** parse current (save_version
67.x) replays — `mgz.model.parse_match` dies in the header `players` section.
The fix is 2 lines in `mgz/fast/header.py` (the path `parse_match` actually uses);
`mgz_save67.patch` is that exact diff if you prefer to patch stock mgz.

> The local working copy used during development is a gitignored `.replay_scratch/mgz`
> (stock mgz + the patch); scripts run with `PYTHONPATH=.replay_scratch`. Installing
> the fork via requirements removes the need for that.

## Pipeline

| Step | Script | Output |
|---|---|---|
| 1. Download replays | `download.py` | `data/replays/*.aoe2record` (gitignored) + `data/replay_manifest.csv` |
| 2. Resolve player identity | `attribution.py` | `data/profile_resolved.csv` |
| 3. Extract + build stats database | `build_db.py` (uses `extract.py`) | `data/replay_quiz.db` (SQLite) |
| 4. Build the question bank | `build_questions.py` | `question_bank` table (+ `data/question_bank.json`) |
| 5. Weekly quiz (one/day) | `weekly.py` | deterministic, varied, refreshing 7-day set |
| (ad-hoc single question) | `quiz.py` | one random question |

```bash
# 1. Pull the last ~6 months (id cutoff ~Dec 2025); resumable, rate-limit-aware
python utils/replay_quiz/download.py --since-id 438000000 --space 5

# 2. Map in-replay profile_id -> leaderboard nick (seed map + elimination)
python utils/replay_quiz/attribution.py

# 3. (re)build the database — incremental: only new replays are parsed
python utils/replay_quiz/build_db.py

# 4. materialize the question bank (all valid questions) from the stats DB
python utils/replay_quiz/build_questions.py

# 5. the weekly quiz of the day (this week, or --demo for several weeks)
python utils/replay_quiz/weekly.py
python utils/replay_quiz/weekly.py --demo
```

## Quiz engine — two layers

1. **Question bank** (`build_questions.py` → `question_bank` table, ~2,400 rows). One
   row per renderable question: `top4` ("who is THE best at X") and `elo_peers`
   ("among 4 players within a ~250 Elo band, who?"), in `best` and `worst` framings,
   for every metric. Each row carries the 4 options (identity+value+Elo), the answer,
   the top-3 reference games (civ + match id), the Elo band, and a `closeness` score
   (0 = blowout … 1 = photo-finish) used to favor exciting races.
2. **Weekly generator** (`weekly.py` → `generate_week(week_ordinal)`). Deterministic
   per ISO week; picks one question per day across 7 themes (economy, age speed, army,
   tech, buildings, aggression, signature) with a fixed format mix (4 best-overall +
   3 Elo-peer). No metric or winner repeats within a week; each theme's pool rotates by
   week number so consecutive weeks barely overlap. The bot calls `generate_week()` and
   posts that day's question.

Refresh the whole thing after new downloads: `build_db.py → build_questions.py` (then
`weekly.py` automatically reflects the larger bank).

Re-run steps 3–4 any time more replays download — `build_db.py` caches per-file
extraction (keyed on path+size), so a rebuild after new downloads only parses the
new files. `extract.py` is the extraction *algorithm* (importable: `extract_match`).

## Database (`data/replay_quiz.db`)

Raw, queryable tables (all games): **`matches`**, **`facts`** (per match-player),
**`units`** / **`techs`** / **`buildings`** (long form — query any unit/tech/building).
Derived for quizzes: **`leaderboards`** (per-identity career average, ranked) and
**`metric_top_games`** (top-3 single-game performances with civ + map + match link).
**`metrics`** is the catalog (id, label, category, direction, unit) — see
`docs/replay-quiz-categories.md`.

A quiz = pick a random metric → read its leaderboard (the answer + distractors) +
`metric_top_games` (the reveal / reference games). `quiz.py` does exactly this.

## Data hygiene (built into `build_db.py`)

- **Metrics computed on the standard map only** (`Land Nomad`/`Nomad`, ~99% of
  games). Off-meta/custom maps (Yin Yang, Rage Forest…) stay in the raw tables but
  are excluded from leaderboards — they aren't comparable and injected time
  anomalies (e.g. a scripted-start map showing "Loom at 0:01").
- **Age & tech-timing metrics exclude games with no real aging** (truncated streams
  / full-tech starts: `age_reliable=False` or no Feudal click).
- **Min games to qualify** for an average leaderboard: 3 (`MIN_GAMES`).

## How it works

**Download** (`download.py`) — replays come from **aoe.ms**
(`aoe.ms/replay/?gameId=<aoe2_match_id>&profileId=<pid>`; the response is a ZIP).
aoe2.net is dead (sunset Oct 2025). A participant `profileId` is resolved from the
aoe2companion match API. aoe.ms **rate-limits hard (429)** so the downloader uses
patient exponential backoff + spacing + an incremental, resumable manifest.
Availability is **per-match (~65–80%)** — some matches 404 (replay not hosted).

**Attribution** (`attribution.py`) — each in-replay player carries a stable aoe2
`profile_id`. Mapping to a Discord/leaderboard identity uses:
1. seed `data/player_profile_map.csv` (profile_id → user_id/nick), then
2. **elimination**: in each match the replay's 8 profile_ids are the same 8 humans
   as the bot's 8 `user_id`s (`qc_player_matches` via `match_id_map`); subtract
   known links and any single leftover pair is forced.

This reached **98% of player-appearances with 0 consistency violations** on the
dev corpus. Unmapped profiles (one-off guests) fall back to their aoe2 name.

**Extraction** (`extract.py`) — from `mgz.model.parse_match`:
- age-up times from `m.uptimes` (fallback to the `RESEARCH` age-click; skipped when
  the uptime stream looks truncated),
- research timeline (`RESEARCH`, **deduped** to first-per-(player,tech) — ~28% are
  re-clicks),
- unit production (`DE_QUEUE`, **summing `amount`**, trade/transport **excluded**
  from "military"),
- most-made military unit, villager count, scouts + first-scout time,
- TC builds + **confirmed** relocations (a `DELETE` of a known TC instance followed
  by a Town Center `BUILD`),
- conditionals: scouts-before-Castle, any-military-before-Feudal.

## Reliability / caveats (important for quiz wording)

- All production/research numbers are **queue-clicks, not confirmed builds** — an
  upper bound. Fine for aggregates ("most", "fastest", "first"); don't present as
  exact unit counts.
- **Cross-patch:** verified on save_versions 66.3 / 66.6 / 67.2 (older handled by
  base mgz, 67.x by the patch). Identical payload shapes across versions.
- **TC relocation** is high-precision / **low-recall** — a TC deleted before it
  ever produces a villager (esp. on Nomad) is invisible. Report *confirmed*
  relocations, not exact totals.
- Some replays parse with **0 age-ups** (truncated stream / full-tech modes);
  `extract.py` flags `age_reliable=False` and excludes them from age leaderboards.
- 1 corpus file (`471535468`) has a corrupt header and is skipped.
