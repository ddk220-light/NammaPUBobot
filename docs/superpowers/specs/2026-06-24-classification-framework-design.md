# Player Classification Framework — Design Spec (Phase 1)

- **Date:** 2026-06-24
- **Branch:** `feat/replay-insights-expansion`
- **Status:** Draft for review
- **Author:** Deepak + Claude (brainstorming)

> A reusable, structured framework for classifying how a player played a given game from
> their replay, and correlating those classifications with outcomes. **Archer rush is the
> first classification** — and the proof that the framework generalizes. All processing is
> **offline on the local machine**; results are **pushed to the app's MySQL** and surfaced on
> Discord via a **new slash command**. Later, the bot will run the registered classifications
> automatically. This is **Phase 3** of the player-analysis line of work (Phase 1 = live
> replay-stats pipeline; Phase 2 = `/player_details`), and implements backlog items **B3**
> (strategy/event detection) and **B4** (strategy ↔ win/loss) on a generic substrate.

---

## 1. Problem & context

We can already extract rich per-game metrics from replays (`utils/replay_quiz/extract.py` →
`rs_*` live tables / `data/replay_quiz.db`). What's missing is the **interpretation layer**:
turning raw metrics into named *strategies* ("this player went for an archer rush"), measuring
*how well* each attempt was executed, and reporting *whether it won*.

The owner wants this built **not as a one-off archer-rush script, but as a repeatable structure**
so new classifications (drush, scout rush, fast castle, castle drop, unit-composition modes…)
drop into the same shape. Each classification is defined by **three parts**:

1. **Trigger logic** — a predicate deciding whether a player-game falls into the category.
2. **Relevant factors** — the metrics that characterize and grade the attempt.
3. **Data requirements** — what the trigger/factors need from replay extraction, each marked
   *available* or *missing*, so a classification needing new data records that gap reusably.

These definitions and their results live **structured in a reviewable database**. Archer rush
is the first entry.

### Data findings that shaped this design (offline corpus, 350 games, Oct 2025 → May 2026)

A calibration pass over `data/replay_quiz.db` established the archer-rush definition empirically:

- **Trigger = "≥1 foot Archer queued before the Castle *click*."** Rationale: a fast-castle →
  crossbow player clicks Castle *first*, so their archers land *after* the click and score
  **zero** pre-castle archers; any archer before the click reveals aggressive-feudal intent.
  267 player-games (9.7% of feudal-reaching games; 33 distinct players) match.
- **Rush ≠ win.** The rush population wins ~43%, ≈ the ~44% baseline. *Execution* is what
  separates good from bad — exactly why factors + win/loss correlation matter.
- **Commitment grades the attempt.** Win rate by archers-before-castle: 1–3 archers ≈ **12%**
  (dabbled/punished), 11–20 ≈ **51%** (committed), 21+ ≈ 44%.
- **Fletching-before-Castle is a strong execution signal.** With it: **48%** win (218 games);
  without (none, or only after Castle): **22%** (49 games). Skirmishers are **excluded** — this
  is the foot-archer line only.
- **Coarse age timings do NOT separate winners from losers** (Feudal click ~10.9 min,
  Feudal→Castle ~13 min for both). The *fine* timing the owner cares about — "did archers start
  right after Feudal," production tempo, "how soon to Castle after committing 10 archers +
  Fletching" — needs **per-archer timestamps**, which `extract.py` already emits (`events`,
  one row per queue click) but the offline DB never stored. Re-parsing the **kept** replay
  corpus recovers it with no new extraction code.

### Enabling facts

- **The replay corpus is already kept.** `data/replays/` is gitignored and already holds 352
  `.aoe2record` files (~1.3 GB), untracked by git. "Keep them downloaded, don't delete" is
  satisfied by this on-disk cache; the runner adds to it and never prunes.
- **`extract.py` already emits per-queue events** with timestamps (`extract.py:147`), so all
  archer-rush factors — including fine timing — are computable today by re-parsing the corpus.
- **The local machine can reach the app's MySQL** via `DB_URI` (the `utils/` scripts already do,
  via `utils/db_helpers.py::create_pool`). The runner uses this to read the match list and to
  push results.
- **The match window is derivable.** `qc_match_civs` carries `aoe2_match_id` for completed games
  (linked to `qc_matches.at` for the timestamp), the same source the live pipeline's find query
  uses — so "last 90 days" is a query, and missing replays in that window can be downloaded with
  the existing `utils/replay_quiz/download.py`.

---

## 2. Goals / non-goals

**Goals (Phase 3):**
- A **generic classification framework**: a registry of classifications, each a pure
  `trigger(game)` + `factors(game)` module registered under a `key`, plus a recorded
  data-requirements ledger.
- **Reviewable structured storage** in MySQL (`cls_*` tables): the registry, the data
  requirements, per-player-game results, and a generic long-form factor store (new
  classifications add rows, never migrations).
- An **offline runner** (local) that fetches/caches the corpus for a date window, parses each
  replay once, runs every registered classification, and pushes results to MySQL — idempotently.
- **Archer rush** fully implemented as the first classification, with its factors and an honest
  *good-vs-bad* (execution ↔ win/loss) breakdown.
- A **new Discord slash command** that reads `cls_*` and answers: who did a classification in the
  last N days, did they win, and what separated good attempts from bad.

**Non-goals (Phase 3):**
- The bot running classifications **automatically/live** (future; the registry is designed for
  it but Phase 3 runs offline only).
- Additional classifications beyond archer rush (the framework makes them cheap, but they're
  out of scope here).
- A composite single "rush quality score" (YAGNI — start descriptive; the factors and win/loss
  splits are enough to see good vs bad).
- Replacing or modifying the live `rs_*` ingest or the quiz pipeline (untouched; we **reuse**
  `extract.py` and `download.py`).
- Storing executable trigger logic as data (the *metadata* is in the DB now; logic stays in
  code until the future live runner needs data-driven execution).

---

## 3. Architecture & data flow

Two cleanly separated sides — an **offline package** (`utils/classifications/`, local-only,
4-space indent per the `utils/` convention) and a **thin bot read path** (the new slash command):

```
 LOCAL (offline)                                          APP (Railway)
 ┌─────────────────────────────────────────────┐         ┌──────────────────────────┐
 │ utils/classifications/runner.py             │         │  MySQL  cls_* tables      │
 │  1. window = last N days                    │  read   │   cls_classifications     │
 │     match list from qc_match_civs (MySQL)───┼────────▶│   cls_data_requirements   │
 │  2. ensure each replay cached in            │         │   cls_results             │
 │     data/replays/ (download.py if missing)  │  write  │   cls_result_metrics      │
 │  3. parse once: extract_match() (mgz fork)  │────────▶│                           │
 │  4. for each registered classification:     │ upsert  └────────────┬─────────────┘
 │       trigger(game) → matched?              │                      │ read
 │       factors(game) → {metric: value}       │         ┌────────────▼─────────────┐
 │  5. push registry + requirements + results  │         │ bot: /classification cmd  │
 │     + metrics to MySQL (idempotent upsert)  │         │  → embed/chart of results │
 └─────────────────────────────────────────────┘         └──────────────────────────┘
```

**Properties:** parse-once per replay (cache keyed by `aoe2_match_id`); idempotent push (keyed by
`(classification_key, aoe2_match_id, player_number)` — re-runs overwrite, never duplicate);
the framework is closed over a **normalized game object** so triggers/factors are pure and
DB-free; the bot only ever *reads* `cls_*`.

---

## 4. Framework data model (MySQL `cls_*`)

DDL kept in one shared module (`utils/classifications/schema.py`, `CREATE TABLE IF NOT EXISTS`)
that **both** the offline runner and the bot import, so there is a single source of truth; both
ensure the tables idempotently at startup.

| Table | Grain | Key & columns |
|---|---|---|
| **cls_classifications** | per classification | PK `key` (e.g. `archer_rush`); `title`, `description`, `trigger_spec` (human-readable), `version` INT, `status` (`active`/`draft`), `updated_at`. The reviewable catalog. |
| **cls_data_requirements** | per classification × field | PK(`key`,`field`); `source` (where it comes from, e.g. `extract.events`), `status` (`available`/`missing`), `note`. The reusable "what data is still needed" ledger. |
| **cls_results** | per classification × player-game | PK(`key`,`aoe2_match_id`,`player_number`); `profile_id?`, `identity`, `civ`, `team`, `winner?` (bool), `played_at`. Index(`key`,`played_at`), (`key`,`profile_id`). |
| **cls_result_metrics** | per classification × player-game × metric | PK(`key`,`aoe2_match_id`,`player_number`,`metric`); `value` REAL (nullable), `value_text?`. Generic long-form factor store — **any new classification adds rows, never columns.** Index(`key`,`metric`). |

**Notes.** A row exists in `cls_results` **only** for player-games the trigger fired on
(presence = matched — no `matched` flag needed); we do **not** persist the non-matching majority
(recoverable from `rs_*`/replays, and they would bloat the table). `winner` is nullable (some games' result is unknown). Types mirror the `rs_*` conventions
(`aoe2_match_id` BIGINT, `profile_id` INT, booleans as small ints, times/counts in metrics as
REAL seconds/counts). Metric **names are namespaced per classification** (e.g.
`archers_pre_castle`, `first_archer_after_feudal_s`) and documented in the classification module.

---

## 5. The classification contract

Each classification is a small module under `utils/classifications/defs/<key>.py` exporting a
single registered object with **three parts** mirroring the owner's framing:

```python
# utils/classifications/defs/archer_rush.py  (illustrative)
classification(
    key="archer_rush",
    title="Archer Rush",
    version=1,
    trigger_spec="Queued >=1 foot Archer (archer line; NOT skirmisher) before the Castle-age click.",
    requirements=[                      # part 3: the data-requirements ledger
        req("foot_archer_queue_events", source="extract.events", status="available"),
        req("castle_click_s",           source="extract.players.castle_s", status="available"),
        req("feudal_click_s",           source="extract.players.feudal_s", status="available"),
        req("fletching_click_s",        source="extract.techs[Fletching]", status="available"),
        req("winner",                   source="extract.players.winner", status="available"),
    ],
    trigger=archer_rush_trigger,        # part 1: pure (game, player) -> bool
    factors=archer_rush_factors,        # part 2: pure (game, player) -> {metric: value}
)
```

- **`trigger(game, player) -> bool`** — pure; reads only the normalized game object.
- **`factors(game, player) -> dict[str, float|None]`** — pure; computes the named factors.
- **`requirements`** — declared statically; the runner writes them to `cls_data_requirements`
  so the ledger is reviewable and a *missing* requirement is visible before anyone relies on a
  classification. (Archer rush needs nothing new — it exercises the ledger with all-`available`.
  A future "castle drop / forward" classification would list `building_positions` as `missing`,
  demonstrating the gap-capture and signalling an `extract.py` extension.)

The **registry** (`utils/classifications/registry.py`) collects all `defs/*` modules. The
**normalized game object** is a thin adapter over `extract_match()`'s output dict (so triggers
never touch mgz directly and are trivially unit-testable with synthetic dicts).

---

## 6. Archer rush — the first classification

**Trigger.** Player queued **≥1 foot Archer** (`category == "archer_line"`; Skirmishers,
Cav Archers, Hand Cannoneers excluded) with `t_s < castle_click_s` (or the player never clicked
Castle). Equivalent to `units.pre_castle(archer_line) ≥ 1`.

**Factors** (the "relevant data" — all computed from existing extraction):

| Metric | Meaning | Source |
|---|---|---|
| `archers_pre_castle` | foot archers queued before Castle click | events / units.pre_castle |
| `feudal_s` | Feudal-age click time | players.feudal_s |
| `castle_s` | Castle-age click time (null if never) | players.castle_s |
| `reached_castle` | did they click Castle at all | players.castle_s ≠ null |
| `feudal_to_castle_s` | time spent in Feudal | castle_s − feudal_s |
| `first_archer_s` | timestamp of first archer queue | min(archer events t_s) |
| `first_archer_after_feudal_s` | how soon archers started after Feudal | first_archer_s − feudal_s |
| `archers_within_3min_of_feudal` | tempo: archers queued ≤180 s after Feudal | events |
| `fletching_pre_castle` | Fletching researched before Castle click | techs[Fletching] |
| `fletching_after_feudal_s` | how early Fletching came (null if not pre-castle) | fletching_s − feudal_s |
| `commit_to_castle_s` | from "committed" (10th archer **and** Fletching, whichever later) to Castle click; null if 10 archers never reached | events + techs + castle_s |
| `eapm` | effective APM (skill proxy) | players.eapm |
| `winner` | won the game | players.winner (also on `cls_results`) |

**Good-vs-bad analysis (descriptive, not a single score).** The slash command / report
aggregates matched player-games over the window and slices win rate by factor — e.g. win rate by
`archers_pre_castle` bucket (the 12% → 51% commitment curve), by `fletching_pre_castle`
(48% vs 22%), and by `first_archer_after_feudal_s` / `commit_to_castle_s` buckets (the fine-timing
question the corpus re-parse newly answers). Per-player rows show who rushed, how often, their
rush win rate, and their median execution factors.

---

## 7. Offline runner mechanics (`utils/classifications/runner.py`)

CLI: `python -m utils.classifications.runner --days 90 [--key archer_rush] [--no-download]`.

1. **Window & match list.** Query MySQL: `aoe2_match_id`, `played_at` from `qc_match_civs ⨝
   qc_matches` where `at ≥ now − days`, deduped by `aoe2_match_id`, newest-first. (Same shape as
   the live find query.)
2. **Corpus.** For each match, ensure `data/replays/<id>.aoe2record` exists; if not, download via
   `utils/replay_quiz/download.py` (polite, rate-limited, 429 backoff). **Never delete.** Replays
   that 404 upstream are skipped and logged (best-effort coverage, as the live pipeline accepts).
3. **Parse once.** `extract_match(path, resolved, date_map)` per replay; an on-disk parse cache
   (`data/.replay_extract_cache/`, already gitignored) keyed by `aoe2_match_id` + `PARSER_VERSION`
   avoids re-parsing across runs and across classifications.
4. **Classify.** Build the normalized game object; for each registered (or `--key`-selected)
   classification and each player, run `trigger`; on match, run `factors`.
5. **Push (idempotent).** Upsert `cls_classifications` + `cls_data_requirements` from the registry;
   for matched player-games, replace `cls_results` + `cls_result_metrics` rows for that
   `(key, aoe2_match_id, player_number)` in one transaction. Re-running a window overwrites, never
   duplicates.
6. **Report.** Print a summary (matches scanned, replays fetched/cached/failed, per-classification
   match counts, win-rate-by-factor table) so a run is reviewable from the terminal too.

Reuses `extract.py` and `download.py` unchanged; attribution via the existing
`data/profile_resolved.csv` (→ identity) so per-player reporting names real community players.

---

## 8. Discord surface (bot read path)

A new slash command group, e.g. **`/classification <key> [days=90] [player]`** (registered in
`bot/context/slash/commands.py` via `run_slash`, handler in `bot/commands/`), reads `cls_*` only:

- **Default (`/classification archer_rush`):** over the last `days`, an embed/chart of: number of
  rush games & distinct players, overall rush win rate vs baseline, win rate by commitment bucket
  and by Fletching-before-Castle, and a leaderboard of players who rushed most (with their rush
  win rate).
- **`player:` set:** that player's rushes in the window — per-game list (civ, archers, Fletching
  timing, time-to-Castle, win/loss) and their median execution factors.
- Autocomplete on `key` lists `cls_classifications` (so new classifications appear automatically).

Exact embed/chart layout is an implementation detail (follows the `/player_details` and `/rank`
patterns) and is left to the plan; the data contract above is what matters.

---

## 9. Testing

- **Pure unit tests (CI, no DB, no mgz):** `archer_rush_trigger` and `archer_rush_factors` over
  synthetic normalized-game dicts — boundary cases: archer exactly at vs just after the Castle
  click; never-reached-Castle; Skirmisher-only (must NOT match); Fletching before vs after Castle;
  `commit_to_castle_s` null when <10 archers. The normalized-game adapter is tested against a small
  recorded `extract_match` output fixture.
- **Schema/push idempotency (no network):** running the push twice yields identical `cls_*` rows
  (overwrite, not duplicate); long-form metrics round-trip.
- **mgz/network paths** stay offline-only (skipped in CI, like the existing replay tooling). CI
  remains `ruff` + `pytest`.

---

## 10. Rollout order

1. `cls_*` schema (`schema.py`) + the classification contract (`registry.py`, `classification()`,
   normalized-game adapter) + pure archer-rush trigger/factors **with unit tests**. No DB writes.
2. Offline runner: window → corpus(cache+download) → parse(cache) → classify → terminal report
   (still no push). Validate archer-rush counts against the calibration numbers in §1.
3. Push layer: idempotent upsert into `cls_*`; run a small window against MySQL and spot-check.
4. Bot `/classification` command (read-only) + `cls_*` ensure-table on bot startup.
5. Full 90-day run, newest-first; review via the command and a DB spot-check.

## 11. Future (designed-for, out of scope now)

- **Live auto-run:** the bot runs registered classifications as part of the `rs_*` ingest (or a
  sibling job), driven by `cls_classifications` — the offline runner and the live job share the
  same trigger/factor modules and the same `cls_*` tables.
- **More classifications:** drush, scout rush, fast castle, castle drop (needs `building_positions`
  → a documented `missing` requirement that drives an `extract.py` extension), unit-composition
  "modes," matchup-aware win rates (B4/B5).

## 12. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Corpus coverage gaps (replays 404 upstream) | best-effort; skip + log; coverage reported per run |
| Re-parse cost over 90 days | parse-once cache keyed by `aoe2_match_id`+`PARSER_VERSION`; runner is offline/unhurried |
| Local push to prod MySQL | idempotent upserts keyed by `(key, match, player)`; runner is explicit/monitored, never auto-run |
| Generic long-form metrics hard to query | per-classification metric names are fixed & documented; the command knows its own metric set |
| Trigger false-positives as classifications grow | each classification's `trigger_spec` + factors are validated against known games before `status=active` |
| `extract.py` schema drift vs cached parses | parse cache includes `PARSER_VERSION`; bump invalidates it |
