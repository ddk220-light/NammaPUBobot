# Player Classification Framework

A reusable, **offline-first** system for classifying *how a player played* a given AoE2 game
from its replay, computing execution-quality metrics, and correlating them with the outcome.
**Archer rush** is the first classification; the framework is built so new ones (drush, scout
rush, fast castle, castle drop, unit-composition modes…) drop in with one new file.

- **Design spec:** [`docs/superpowers/specs/2026-06-24-classification-framework-design.md`](superpowers/specs/2026-06-24-classification-framework-design.md)
- **Implementation plan:** [`docs/superpowers/plans/2026-06-24-classification-framework.md`](superpowers/plans/2026-06-24-classification-framework.md)
- **Backlog context:** [`docs/PLAYER_ANALYSIS_BACKLOG.md`](PLAYER_ANALYSIS_BACKLOG.md) (this implements **B3 + B4**)

---

## The idea

Every classification is a structured record with **three parts**:

1. **Trigger** — a pure predicate `trigger(game, pnum) -> bool` deciding whether a player-game
   falls into the category.
2. **Factors** — a pure `factors(game, pnum) -> {metric: float|None}` of the metrics that
   characterise and *grade* the attempt.
3. **Data requirements** — a static ledger of what the trigger/factors need from replay
   extraction, each tagged `available` or `missing`. This is how a future classification that
   needs new data (e.g. building positions for "castle drop") **records the gap** reusably.

These definitions and their results live in a **reviewable database** (`cls_*` MySQL tables).
All processing runs **offline on a local machine**; results are pushed to the app's MySQL; the
bot only *reads* them for `/insights <use_case> [days] [player]` — a leaderboard of who used the
classification + winners-vs-losers aggregate facts (no quality scores — facts only). (Future: the
bot runs registered classifications automatically.)

---

## Architecture

```
 data/replays/*.aoe2record  (kept, gitignored, ~hundreds of files — NEVER deleted)
        │  utils/replay_quiz/extract.py :: extract_match()  (the vendored mgz fork)
        ▼
 normalized game dict ──► trigger(game, pnum)?  ──► factors(game, pnum)
        │                         (utils/classifications/defs/<key>.py — pure, unit-tested)
        ▼
 utils/classifications/runner.py  (CLI):  window → corpus(cache/download) → parse(cache)
        │                                  → classify → idempotent upsert
        ▼
 MySQL  cls_classifications · cls_data_requirements · cls_results · cls_result_metrics
        ▲ read
 bot/classifications/query.py (roster + winners_vs_losers + fetch_results) ──► bot/commands/insights.py
        ▲ slash
 /insights <use_case> [days] [player]
```

### Layers (each independently testable)

| Concern | File(s) | Pure? |
|---|---|---|
| Read accessors over `extract_match()` output | `utils/classifications/gamedata.py` | ✅ |
| Classification contract (`Classification`, `req()`) | `utils/classifications/contract.py` | ✅ |
| A classification's trigger + factors + ledger | `utils/classifications/defs/<key>.py` | ✅ |
| Registry of all classifications | `utils/classifications/registry.py` | ✅ |
| Factors → DB row dicts | `utils/classifications/shape.py` | ✅ |
| `cls_*` schema (raw SQL, offline) | `utils/classifications/schema.py` | — |
| Async DB I/O (aiomysql) | `utils/classifications/dbio.py` | I/O |
| Runner CLI (orchestration) | `utils/classifications/runner.py` | I/O |
| `cls_*` schema for the bot (`ensure_table`) | `bot/classifications/__init__.py` | I/O |
| Read aggregation (`roster`/`winners_vs_losers` pure, `fetch_results` DB) | `bot/classifications/query.py` | mixed |
| Slash command | `bot/commands/insights.py` | I/O |

> Indentation: `utils/` uses **4 spaces**; `bot/` uses **tabs** (`ruff.toml` `indent-style = "tab"`).

---

## Data model (`cls_*` MySQL tables)

| Table | Grain | Notes |
|---|---|---|
| `cls_classifications` | per classification | registry: `key`, `title`, `trigger_spec`, `version`, `status`, `updated_at` |
| `cls_data_requirements` | per classification × field | the available/missing ledger |
| `cls_results` | per matched player-game | `key`, `aoe2_match_id`, `player_number`, `profile_id`, `identity`, `civ`, `team`, `winner`, `played_at` (epoch). **A row exists only if the trigger fired** (presence = matched). |
| `cls_result_metrics` | per classification × player-game × metric | generic long-form (`metric`, `value`) — **new classifications add rows, never columns** |

The bot (`bot/classifications/__init__.py`) and the offline runner (`utils/classifications/schema.py`)
declare the same columns two ways; **keep them in sync**.

---

## How to add a new classification

1. Create `utils/classifications/defs/<key>.py`:
   - pure `trigger(game, pnum) -> bool`
   - pure `factors(game, pnum) -> dict[str, float|None]` (None = the factor didn't apply)
   - a module-level `CLASSIFICATION = Classification(key=..., title=..., version=1,
     trigger_spec="…", requirements=[req(...), …], trigger=trigger, factors=factors)`
   - declare each data dependency with `req(field, source=..., status="available"|"missing")`.
     If a factor needs data `extract.py` doesn't emit yet, mark it `missing` (and extend
     `utils/replay_quiz/extract.py` to emit it).
2. Append it to `utils/classifications/registry.py`.
3. Add pure unit tests in `tests/test_classifications_<key>.py` (synthetic game dicts — no DB,
   no mgz).
4. Run the runner; it auto-registers the classification + ledger and stores results. Add the new
   key to the `/insights` command's `use_case` choices (`bot/context/slash/commands.py`) to surface it.

No schema migration is needed — metrics are stored long-form.

---

## Running the offline analysis

Requires `config.cfg` (with a reachable `DB_URI`), the gitignored `.replay_scratch/` mgz fork,
and cached replays under `data/replays/`:

```bash
PYTHONPATH=.replay_scratch python -m utils.classifications.runner --days 90 [--key archer_rush] [--no-download]
```

- Lists match ids from `qc_match_civs ⨝ qc_matches` within the window (newest first).
- Ensures each replay is cached in `data/replays/` (downloads via `utils/replay_quiz/download.py`
  if missing; `--no-download` uses only what's already cached). **Replays are never deleted.**
- Parses each once; caches the JSON-serialisable extract output in
  `data/.replay_extract_cache/<id>.<EXTRACT_VERSION>.json` (bump `EXTRACT_VERSION` in
  `runner.py` to invalidate).
- Runs every registered classification per player; idempotently upserts `cls_*`
  (delete-then-insert keyed by `(key, aoe2_match_id)` — safe to re-run).

---

## Operating it in production (populating the bot's DB)

The bot **creates the empty `cls_*` tables** in the Railway MySQL at startup (`ensure_table` in
`bot/classifications/__init__.py`) and only ever **reads** them. The **offline runner fills them**,
and it must run **where the replays + mgz fork live** (locally) — those are gitignored and not in the
Railway image.

**Connecting to prod from your machine:** `mysql.railway.internal` only resolves *inside* Railway. To
write to the prod DB from a laptop, put Railway's **public** MySQL URL (`MYSQL_PUBLIC_URL`, the
`…proxy.rlwy.net:PORT` one) in `config.cfg`'s `DB_URI`, then run:

```bash
PYTHONPATH=.replay_scratch python -m utils.classifications.runner --days 400 --key archer_rush --no-download
```

`--no-download` uses only already-cached replays (fast, no aoe.ms hammering). The runner is
**idempotent** — re-running overwrites a match's rows, never duplicates. It writes the registry row +
data-requirements ledger first, then the per-player results, so even a brief run clears the
"Unknown use case" error.

**Troubleshooting:**
- `/insights` says **"Unknown use case 'archer_rush'"** → `cls_classifications` is **empty**: the
  runner hasn't run against that DB. Run it (above).
- **`1064 … near 'key))'` at deploy** (historical) → `key` is a MySQL reserved word; fixed by
  backticking `PRIMARY KEY` columns in `core/DBAdapters/mysql.py::create_table`.

**Window vs. data freshness (important):** `/insights` defaults to **385 days**. A one-time backfill
from cached replays is only as fresh as the cache (the initial corpus stopped ~Apr 2026), so a short
window can look empty even though the DB holds hundreds of games. Match the `days:` window to how far
back the populated data actually goes (or keep it current via a future live-ingest job). The DB holds
it all cheaply (~270 rows for archer rush); the window only controls the view.

---

## The archer-rush classification (`defs/archer_rush.py`)

**Trigger:** the player queued **≥1 foot Archer** (`category == "archer_line"`; **Skirmishers
excluded**) **before the Castle-age click**. A fast-castle→crossbow player clicks Castle first,
so their archers land *after* the click and score zero pre-castle archers; any archer before the
click reveals aggressive-feudal *intent*. **Rush ≠ win** — execution is what's graded.

**Factors** (declared in `factor_specs` for the `/insights` report): `archers_pre_castle`,
`feudal_s`, `castle_s`, `fletching_click_s`, `fletching_pre_castle`, `reached_castle`,
`feudal_to_castle_s`.

**Calibrated against the real corpus (350 games, 267 archer rushes):** doing an archer rush wins
≈ the baseline rate — *execution* decides. **Fletching before Castle** is the strongest single
signal (~48% win vs ~22% without); commitment (more archers) beats dabbling (1–3 archers ≈ 12%
win). Tempo factors were **tested and dropped**: archer production rate, time-to-5-archers, and
first-archer-after-Feudal are all flat between winners and losers — *when* the archers come does
not separate outcomes, only *whether* they commit (count) and tech the upgrade (Fletching). The
`/insights` report is **facts only** (roster + winners-vs-losers averages); no quality score.

---

## Testing

- Pure logic (`gamedata`, `contract`, every `defs/*`, `shape`, `roster`/`winners_vs_losers`) is
  DB/mgz-free and unit-tested in `tests/test_classifications_*.py`. CI runs `ruff check .` + `pytest tests/`.
- The runner/DB/Discord paths are I/O and validated by a real offline run + spot-checks (the mgz
  and network paths are offline-only, skipped in CI like the rest of the replay tooling).
