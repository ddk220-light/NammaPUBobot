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
bot only *reads* them for `/classification`. (Future: the bot runs registered classifications
automatically.)

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
 bot/classifications/query.py (summarize + fetch_games) ──► bot/commands/classification.py
        ▲ slash
 /classification <key> [days] [player]
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
| Read aggregation (`summarize` pure, `fetch_games` DB) | `bot/classifications/query.py` | mixed |
| Slash command | `bot/commands/classification.py` | I/O |

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
4. Run the runner; it auto-registers the classification + ledger and stores results. The
   `/classification <key>` command works immediately (autocomplete-free; pass the key).

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

## The archer-rush classification (`defs/archer_rush.py`)

**Trigger:** the player queued **≥1 foot Archer** (`category == "archer_line"`; **Skirmishers
excluded**) **before the Castle-age click**. A fast-castle→crossbow player clicks Castle first,
so their archers land *after* the click and score zero pre-castle archers; any archer before the
click reveals aggressive-feudal *intent*. **Rush ≠ win** — execution is what's graded.

**Factors:** `archers_pre_castle`, `feudal_s`, `castle_s`, `reached_castle`,
`feudal_to_castle_s`, `first_archer_s`, `first_archer_after_feudal_s`,
`archers_within_3min_of_feudal`, `fletching_pre_castle`, `fletching_after_feudal_s`,
`commit_to_castle_s` (= Castle click − max(time of 10th archer, Fletching click); **None unless
≥10 archers AND Fletching before Castle**), `eapm`.

**Calibrated against the real corpus:** doing an archer rush wins ≈ the baseline rate —
*execution* decides. **Fletching before Castle** is the strongest single signal (~48% win vs
~22% without); commitment (more archers) beats dabbling (1–3 archers ≈ 12% win). Note:
`W_SECONDS = 180` ("within 3 min of Feudal") is empirically a touch tight — real first-archer
timing clusters ~180–196s after Feudal — so `archers_within_3min_of_feudal` often reads 0;
bumping `W_SECONDS` to ~240 is a reasonable future tweak (left at 3 min per the owner's choice).

---

## Testing

- Pure logic (`gamedata`, `contract`, every `defs/*`, `shape`, `summarize`) is DB/mgz-free and
  unit-tested in `tests/test_classifications_*.py`. CI runs `ruff check .` + `pytest tests/`.
- The runner/DB/Discord paths are I/O and validated by a real offline run + spot-checks (the mgz
  and network paths are offline-only, skipped in CI like the rest of the replay tooling).
