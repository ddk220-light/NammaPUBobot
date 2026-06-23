# Live Replay-Stats Pipeline — Design Spec (Phase 1)

- **Date:** 2026-06-22
- **Branch:** `feature/player-details`
- **Status:** Draft for review (revised after adversarial spec review)
- **Author:** Deepak + Claude (brainstorming)

> This is **Phase 1** of the "detailed player analysis" goal. Phase 1 builds a **live,
> self-updating in-game-metrics database** from match replays. **Phase 2** (separate spec)
> builds `/player_details` and personal-baseline comparisons on top of it.

---

## 1. Problem & context

The user wants a `/player_details` command showing a player's in-game metrics over a recent
window (e.g. last 3 months) — time-to-feudal, villagers made, military made, etc. — the same
metric families the daily quiz already uses.

Investigation surfaced two **separate** datasets:

1. **Live Railway MySQL** (`qc_matches`, `qc_player_matches`, `qc_match_civs`, …) — current to
   the latest game, but contains **only match results** (winner/team/Elo/civ). It has **zero
   in-game metrics** (verified: a scan of every column in every table found no
   villager/feudal/unit/tech/building field).
2. **`data/replay_quiz.db`** (SQLite) — the **only** place in-game metrics live. It is a
   **static offline bake**, committed to git and shipped in the Docker image; its newest game
   is **2026-05-10**. It is built offline by parsing `.aoe2record` replays with the `mgz` fork.

The goal of Phase 1: make replay parsing happen **online**, keep a **durable, self-updating**
metrics store, and **backfill the last 3 months** (most-recent-first).

### Key enabling facts (verified against prod)

- The bot **already captures the AoE2 world match id for every completed game**:
  `qc_match_civs` has 15,748 rows, **100% populated with `aoe2_match_id`** (2,339 distinct),
  each carrying `bot_match_id, user_id, nick, civ, team, result, at`. Populated automatically
  by `civ_matcher` a few minutes after each game. **This is the only table that knows the
  `aoe2_match_id` ↔ `bot_match_id` link** (`qc_matches` itself has no `aoe2_match_id` column).
- A working (offline) fetch→parse pipeline already exists:
  `utils/replay_quiz/download.py` (aoe2companion API → `profile_id` → `aoe.ms` `.aoe2record`),
  `utils/replay_quiz/extract.py` (mgz parse → metrics), `utils/replay_quiz/build_db.py`.
- **Attribution** (replay `profile_id` → Discord `user_id`) runs through a **resolved map**,
  not `qc_match_civs`. `extract.py` already does this: `resolved.get(profile_id) →
  (nick, aoe2_name, source)` (extract.py:169), seeded from `data/profile_resolved.csv` /
  `data/player_profile_map.csv`. NOTE: `qc_match_civs` has **no `profile_id` column**, and its
  `aoe2_name` is empty for the API-resolved path — so it is **not** the attribution seed. It is
  still useful for the `bot_match_id` linkage and (optionally) to *learn* new profile↔user
  mappings via per-match `civ`+`team`+`user_id`. `qc_profile_map` is empty in prod (0 rows).

### Constraints / realities the design must respect

- **Near-real-time, not instant.** `aoe2_match_id` only resolves ~3–5 min after a game.
- **Best-effort coverage.** ~20–35% of replays are never downloadable from aoe.ms (404/429).
  Coverage is also bounded by `civ_matcher` success — a game with no resolved `aoe2_match_id`
  can't be located at all (and its replay couldn't be fetched anyway).
- **Replays expire upstream.** A replay not extracted soon may be un-refetchable later → we
  persist **raw normalized** data, not only derived metrics.
- **Parser fragility.** New AoE2 patches bump `save_version`; the pinned mgz fork may not parse
  them until updated (the repo already carries a `save67` patch). Must degrade gracefully.
- **CPU cost.** mgz parsing is CPU-bound on 2–5 MB files; must not block the bot event loop.

---

## 2. Goals / non-goals

**Goals (Phase 1):** online automatic ingestion of replay-derived metrics into a durable store;
self-updating within minutes, best-effort with retries; one-time ~90-day backfill, newest-first;
graceful new-patch handling; runs inside the existing bot service on Railway, no new services.

**Non-goals (Phase 1):** the `/player_details` command + baseline comparisons (Phase 2);
regenerating quiz questions from the live store (later); 100% coverage or instant ingestion;
replacing the offline quiz-bank pipeline (it stays, and **shares `extract.py`**).

---

## 3. Architecture & data flow

A new in-bot package `bot/replay_stats/`, gated by an **enable flag** (off until ready),
registered on the existing 1s `on_think` tick alongside `quiz.jobs` / `civ_reconcile`:

```
 game ends ─► civ_matcher resolves aoe2_match_id ─► qc_match_civs row (~3–5 min, already happens)
                                                          │
   NEW: replay_stats.jobs.think()  (self-isolating; one sweep at a time; every ~2–3 min)
     1. FIND   next aoe2_match_id (new from qc_match_civs, newest-first; else a due retry)
     2. FETCH  resolve profile_id (aoe2companion) → download .aoe2record (aoe.ms), async+backoff
     3. GATE   read save_version; if unsupported → pending_parser_update (stop)
     4. PARSE  extract_match(path, resolved, date_map) in a SUBPROCESS, timeout + mem ceiling
     5. ATTRIBUTE  resolve each player's profile_id → user_id via rs_profiles (NULL if unknown)
     6. STORE  rs_matches + rs_player_games + units/techs/buildings in one txn; upsert rs_profiles
     7. CLEANUP delete temp .aoe2record; set rs_ingest status
```

**Properties:** self-isolating `think()` (never raises into the tick, mirrors `QuizJobs`); one
match per sweep (bounded load, polite to aoe.ms); parsing off the event loop in a subprocess;
fully idempotent (keyed by `aoe2_match_id`); durable store in MySQL.

**Reuse, not rewrite:** `utils/replay_quiz/extract.py` is the **single source of truth** for
extraction, imported by both the offline quiz pipeline and the live job (no drift). It takes a
**file path** and returns **plain JSON-serializable dicts** (`extract.py:219-233`), so it runs
cleanly in a subprocess — to validate in rollout step 2; fall back to a spawned subprocess +
JSON pipe (or a thread, accepting GIL contention for a ~seconds parse) if `ProcessPoolExecutor`
pickling ever misbehaves. `fetch.py` reuses `download.py`'s URL logic but async (aiohttp, as
`civ_matcher` already does for aoe2companion).

---

## 4. Data model (new MySQL `rs_*` tables)

Declared via `db.ensure_table` at import (as `bot/quiz/__init__.py`). We persist **raw
normalized** data (not just derived metrics) because replays expire upstream — keeping every
current and future metric reproducible without re-parsing.

**Identity model:** within a match, `player_number` is the per-player key the parser emits for
units/techs/buildings; `profile_id` is the stable cross-match identity (on the player record).
`rs_player_games` carries **both**, so it bridges `player_number` ↔ `profile_id`. We also
**denormalize `profile_id` onto the long-form tables at store time** (we have the per-match
`player_number → profile_id` map from the player records) so Phase-2 queries can filter by
profile/user directly.

| Table | Grain | Key & columns |
|---|---|---|
| **rs_matches** | per AoE2 match | PK `aoe2_match_id`. From `extract`: `map`, `save_version`, `duration_s`, `played_at` (= extract `date`). Added by `store`: `bot_match_id?` (via `qc_match_civs`), `replay_url` (constructed aoe2insights link), `parsed_at`, `parser_version`. |
| **rs_player_games** | per player per game | PK(`aoe2_match_id`,`profile_id`). From `extract`: `player_number`, `identity`, `attribution`, `civ`, `team`, `winner`, `eapm`, `age_reliable`, `tc_relocations`, `feudal_s?`, `castle_s?`, `imperial_s?`, `first_tc_s?`, `villagers`, `vil_pre_feudal/castle/imperial`, `military`, `mil_pre_feudal/castle/imperial`. Added by `store`: `user_id?` (resolved from `rs_profiles`). Index(`aoe2_match_id`,`player_number`), (`profile_id`), (`user_id`). |
| **rs_player_units** | per player/game/unit | PK(`aoe2_match_id`,`player_number`,`unit`); `category`, `is_military`, `total`, `pre_feudal`, `pre_castle`, `pre_imperial`; denormalized `profile_id` (indexed). |
| **rs_player_techs** | per player/game/tech | PK(`aoe2_match_id`,`player_number`,`tech`); `click_s`, `phase`; denormalized `profile_id` (indexed). |
| **rs_player_buildings** | per player/game/building | PK(`aoe2_match_id`,`player_number`,`building`); `count`; denormalized `profile_id` (indexed). |
| **rs_ingest** | per AoE2 match | PK `aoe2_match_id`; `status`, `save_version?`, `parser_version?`, `attempts`, `first_seen_at`, `last_attempt_at?`, `next_attempt_at?`, `error_reason?`; index(`status`,`next_attempt_at`). |
| **rs_profiles** | per AoE2 profile | PK `profile_id`; `user_id?`, `name`, `last_seen_at`; index(`user_id`). Seeded at startup from `data/profile_resolved.csv` + `data/player_profile_map.csv`; grown as new profiles are seen. |

**Field notes:** `age_reliable` (bool) — whether age-up times were reliably detected
(`extract.py:165`: false only when a >10-min game yielded no age-up events). `profile_id` is
always present (mgz guarantees it: `extract.py:220`). Types: `aoe2_match_id` BIGINT,
`profile_id` INT, `user_id` BIGINT (the bot's existing Discord-id width), `save_version` REAL,
times/counts INT, `team`/`identity`/`civ`/`unit`/`tech`/`building`/`map`/`status` TEXT,
`winner`/`age_reliable`/`is_military` boolean.

**Retention:** keep **all** history; "3 months" is a **query-time default view** (Phase 2), not
a storage cap. Rationale: replays expire (can't re-fetch); storage is cheap. **Safety valve
(unlikely to be needed for ~15+ yr):** if the DB volume ever exceeds ~80% of quota, prune the
oldest long-form rows (`rs_player_techs/units/buildings`) by `played_at`, always keeping
`rs_matches` + `rs_player_games`.

**Storage projection (grounded):** current whole DB = 9.2 MB. From the offline DB's real row
ratios (~8 player / ~41 unit / ~184 tech / ~106 building rows per game) and this DB's measured
~110–170 bytes/row, growth ≈ **40–55 KB/game ≈ 0.2–0.3 GB/year**. Railway DB volume cap is 5 GB
(Hobby) / 1 TB (Pro) → ~15–20 yr runway on Hobby. Raw `.aoe2record` files are **not** kept.

---

## 5. Ingest job mechanics

**Module layout (`bot/replay_stats/`):** `__init__.py` (tables + `PARSER_VERSION` constant +
singleton), `fetch.py` (async download), `parse.py` (version gate + subprocess extract),
`attribute.py` (rs_profiles seeding + `profile_id → user_id`), `store.py` (idempotent rs_*
writes), `jobs.py` (`think()` loop).

**Find query (live + the basis for backfill):** `qc_match_civs` is the only source of
`aoe2_match_id`, and it has ~8 rows per match, so we **dedupe**:
```sql
SELECT mc.aoe2_match_id, MAX(m.at) AS at
FROM qc_match_civs mc JOIN qc_matches m ON m.match_id = mc.bot_match_id
WHERE mc.aoe2_match_id IS NOT NULL
  AND mc.aoe2_match_id NOT IN (SELECT aoe2_match_id FROM rs_ingest)   -- new only
GROUP BY mc.aoe2_match_id
ORDER BY at DESC                                                       -- newest first
LIMIT 1;
```
If no new match, pick the oldest **due** retry (`status IN (unavailable, parse_failed)`,
`next_attempt_at ≤ now`, `attempts < max`). One match per sweep.

**Status & retry policy (`rs_ingest.status`):**

| Outcome | Status | Retry behavior |
|---|---|---|
| Parsed & stored | `done` | terminal |
| Replay not on aoe.ms yet (404) | `unavailable` | escalating backoff 10m→1h→6h→24h; give up after 7d |
| Rate-limited (429) | *unchanged* | status not changed, `attempts` **not** incremented; only `next_attempt_at` backoff applied |
| Save version too new (patch) | `pending_parser_update` | no retries; reopened when `PARSER_VERSION` changes (below) |
| Corrupt / parse error (supported ver) | `parse_failed` | **max 3 attempts total**, then `gave_up` |

**Attribution (step 5):** for each player, `profile_id → rs_profiles.user_id`. If unknown
(non-community player, observer, unseeded profile), the `rs_player_games` row is stored with
`user_id = NULL` and `attribution = 'unmapped'` — **expected, not an error**. `rs_profiles` is
grown opportunistically; new mappings can be learned by matching the replay's `(civ, team)` to
`qc_match_civs` rows for the same `aoe2_match_id` (which carry `user_id`).

**Patch handling (made concrete):** `parse.py` defines `SUPPORTED_SAVE_VERSIONS` (an explicit
range/set reflecting the pinned sanduckhan mgz fork — base mgz handles older; the fork adds
~66.3/66.6/67.2) and a `PARSER_VERSION` string constant (bumped whenever the mgz pin or the
supported set changes). On ingest, the `save_version` is read **before** full parse; if outside
the supported set → `pending_parser_update` (record `save_version`, no thrashing). On success,
`rs_ingest.parser_version`/`rs_matches.parser_version` is stamped with `PARSER_VERSION`. Each
sweep (cheap) checks for `pending_parser_update` rows stamped with an **older** `parser_version`
than the current `PARSER_VERSION`; if found, it resets them to `unavailable` so they re-ingest.
Thus a deploy that updates the parser automatically drains the backlog.

**Concurrency/safety:** one sweep at a time (`_running` flag, as `QuizJobs`); one match per
sweep; parse in a subprocess (`ProcessPoolExecutor(max_workers=1)`, validated in rollout) with a
per-parse timeout + memory ceiling (runaway → `parse_failed`); the whole `think()` is wrapped so
it can never raise into the tick.

---

## 6. Backfill (one-time, most-recent-first)

- **Source:** the **find query above** with an added `AND m.at ≥ now − 90d` (the only source of
  `aoe2_match_id` is `qc_match_civs`; deduped by `aoe2_match_id`; `qc_matches` has no
  `aoe2_match_id` column). Yields ~150–200 matches we already have ids for.
- **Mechanism:** a **separate, resumable script** calling the *same* single-match ingest
  function in a polite, rate-limited loop (respects 429 backoff; skips `done`/`gave_up`),
  iterating in `at DESC` order so the **most recent games populate first**.
- **Run posture:** explicit, monitored kickoff (admin `/replaystats backfill <days>` or the
  script) **after** the pipeline is built and smoke-tested on a few matches — never auto-run on
  deploy. A small test batch is reviewed before the full window.

---

## 7. Railway feasibility (verified)

| Resource | Railway | Need | Verdict |
|---|---|---|---|
| RAM/CPU | 8 GB / 8 vCPU (Hobby), 24/24 (Pro) | 1 parse at a time, ~100–300 MB peak, secs | fits easily |
| Ephemeral disk | 100 GB paid (1 GB free) | temp `.aoe2record` 2–5 MB, deleted | negligible |
| DB volume | 5 GB Hobby / 1 TB Pro | ~0.2–0.3 GB/yr | ~15–20 yr on Hobby |
| Exec model | long-running service | multi-sec parse | no request timeout |

Only real cost: bot image grows (mgz fork + `aocref`) → bigger/slower builds. Pin `aocref` to a
fixed version (mgz is already pinned to a commit) and measure the image-size / build-time delta
during rollout step 4. No new services.

---

## 8. Testing

- **Unit (CI):** attribution (`profile_id → user_id`, incl. unmapped→NULL), the save-version
  gate, the retry/backoff state machine (pure decision fn), `store.py` idempotency + the
  `player_number→profile_id` denormalization.
- **Parity smoke test:** ingest a few `aoe2_match_id`s already in `replay_quiz.db`; assert live
  MySQL rows equal the offline ground truth (proves the refactor didn't change the numbers).
- mgz/network tests are offline-only (skipped in CI, like existing replay tooling). CI stays
  `ruff` + `pytest`.

## 9. Observability — admin `/replaystats` group (mirrors `/quiz`)

- `/replaystats status` — counts by status, ingested last 7d, latest parsed game, current
  `PARSER_VERSION`, and "N games pending on save `<ver>`" (signal to update mgz).
- `/replaystats backfill <days>` — admin-triggered, newest-first.
- `/replaystats reingest <match_id>` — force re-do one game.
- `/replaystats enable|disable` — feature flag.
- Per-match outcomes logged via the existing `log` channel.

## 10. Rollout order

1. Tables + `store.py` + `rs_profiles` seeding (empty rs_* tables; no behavior change).
2. Single-match ingest (`fetch`/`parse`/`attribute`/`store`) + unit tests + **parity smoke
   test** + **validate subprocess parsing**. Flag OFF.
3. `think()` job + `/replaystats` commands. Flag OFF.
4. Add mgz fork (commit-pinned) + pinned `aocref` to the bot image; measure build delta; deploy.
5. **Backfill last 90 days, newest-first; verify via `/replaystats status` + spot-check.**
6. Flip flag ON — live job keeps it current.
7. *(Phase 2, separate spec)* `/player_details` + personal-baseline comparisons.

## 11. Risks & mitigations

| Risk | Mitigation |
|---|---|
| aoe.ms / aoe2companion change or downtime | best-effort; status reflects reality; isolated from the bot |
| mgz breaks on new patch | save-version gate → `pending_parser_update`; `/replaystats status` visibility; auto-reopen on `PARSER_VERSION` bump |
| Parse OOM / hang | subprocess + timeout + memory ceiling → `parse_failed` |
| `ProcessPoolExecutor` pickling quirk | extract takes a path & returns plain dicts; validated in step 2; fallback to subprocess+JSON or thread |
| Attribution gap (empty `qc_match_civs.aoe2_name`, no `profile_id` there) | use `rs_profiles` (seeded from resolved CSVs) as the `profile_id→user_id` source; unmapped → `user_id=NULL` (not an error); learn new mappings via per-match civ+team |
| Backfill hammers external services | one-at-a-time, rate-limited, resumable, admin-triggered, newest-first |
| Image size from mgz/aocref | pin both; measure delta; build-time only |

## 12. Phase 2 preview (separate spec)

`/player_details @player` → resolve to `profile_id` (via `rs_profiles`) → query `rs_*` for the
player's last-N-days window: averages per metric category (villagers, age speed, military
totals, military-by-type, tech timing, buildings), vs the player's own all-time baseline ("are
you booming better than usual?"), plus the pending-games message when applicable. Follows the
`/rank` slash + embed pattern (`bot/commands/stats.py`, `bot/context/slash/commands.py`).
