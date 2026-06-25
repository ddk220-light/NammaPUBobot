# Local-DB-First Classification Pipeline — Design

**Date:** 2026-06-25
**Status:** Approved (brainstorming) — pending implementation plan

## Goal

Build and maintain the strategy-classification dataset (the `cls_*` tables that power `/insights`
and the website Strategies tab) on a **local SQLite database**, fed by a **parallel
download → mgz-parse → classify pipeline** over the full last-365-days replay corpus. Push to the
production Railway MySQL **only on explicit go-ahead**, via a **fast, batched, verified** write.
Emit **regular progress updates** at each milestone.

## Motivation

The current runner writes **one row per SQL statement directly to Railway** over the public proxy —
~2.8k result rows + ~16k metric rows ≈ **~19,000 single-row INSERTs, each a network round-trip**
(~10–15 min per run, almost entirely latency). This couples every iteration to slow remote writes
and forces the live DB to absorb intermediate states. A local DB **decouples compute from sync**; a
**batched** sync turns the prod write into seconds; and prod stays **untouched until approved**.

## Architecture

Local-SQLite-first, two-process parallel pipeline, gated batch sync:

```
 Railway MySQL ──READ (match list)──► ingest_ledger (SQLite)
                                          │
        ┌─────────────────────────────────┴──────────────────────────┐
        ▼ (process A: Downloader)                  (process B: Ingester) ▼
   data/replays/*.aoe2record  ───filesystem───►  mgz parse (v3) → classify (17)
        (writes files only)                       → write cls_* to SQLite
                                                   → update ledger (sole DB writer)
                                          │
                            (on YOUR go-ahead) ▼
                  read local cls_* → BATCHED write → Railway MySQL → VERIFY counts
```

## Components

1. **Local SQLite `data/analysis.db`** — the working source of truth. Holds the `cls_*` tables
   (`cls_results`, `cls_result_metrics`, `cls_classifications`, `cls_data_requirements`,
   `cls_player_totals`, same columns as the MySQL schema) plus an **`ingest_ledger`**. Opened in
   WAL mode. **The Ingester is the only writer** → no lock contention.
2. **Seed** — read the 365-day match list from Railway (read-only: `qc_match_civs ⨝ qc_matches`) →
   insert every match into `ingest_ledger` as `pending` (with `played_at`). One-time, fast.
3. **Downloader (process A)** — for ledger matches with no local replay, download from aoe.ms
   (resolve profileId via aoe2companion, ~3–4 s spacing, 429 back-off) → `data/replays/`. **Writes
   replay files only, never the DB.** Resumable (skips files already on disk).
4. **Ingester (process B)** — loop: for any local replay not yet ingested, **mgz-parse** (v3
   `extract_match`, cached under `data/.replay_extract_cache/`), run the **17 classifications**
   (existing `registry` + `shape`), write the `cls_*` rows to **SQLite**, and update the ledger
   (`ingested`, or `parse_failed` + `save_version` + `error`). **Streams** — picks up files as the
   Downloader produces them. Recomputes `cls_player_totals` over all ingested matches. Sole writer.
   Terminates when the Downloader has finished **and** no un-ingested local replays remain, then
   exits (no long-running daemon).
5. **Sync (gated)** — on explicit go-ahead only: read the complete local `cls_*` → **batched** write
   to Railway (per-table clear, then multi-row `INSERT` in chunks of ~500–1000 rows) → **verify**
   (per-table row counts and distinct-match counts equal the local DB). Seconds, not minutes. This
   is the only thing that touches prod, and only when approved.
6. **Progress reporter** — milestone + periodic updates (see below), driven off the ledger + file
   counts.

## Data model (SQLite, additions)

- `cls_*` — identical columns to `utils/classifications/schema.py`.
- `ingest_ledger(aoe2_match_id PK, played_at INT, status TEXT [pending|downloaded|ingested|
  parse_failed|unavailable], save_version REAL, error TEXT, ingested_at INT)`.

## Parallelism

Downloader and Ingester are **separate OS processes** sharing only the filesystem (producer/
consumer). The Downloader races ahead fetching; the Ingester streams through the backlog and is the
sole SQLite writer. True parallelism, no shared-writer locking, both fully resumable.

## Error handling

- **mgz parse failures** — skipped, recorded in the ledger with `save_version` + `error`; reported
  as a breakdown **by save version** (answers "are older replays failing on an unsupported
  version?"). 2 known failures today (corrupt/truncated).
- **Rate-limiting** — Downloader spacing + the existing 429 exponential back-off; an optional second
  wider-spaced pass over still-`pending` matches recovers ones lost to throttling vs genuine 404.
- **Resumability** — Downloader skips replay files already on disk; Ingester skips ledger rows
  already `ingested`/`parse_failed`. Either process can crash/restart with no data loss.
- **SQLite concurrency** — WAL mode + single writer (Ingester); Downloader never opens the DB for
  write.

## Prod sync — correctness

The sync is **all-or-nothing per run**: clear + batch-insert each table, then verify. If
verification fails (counts mismatch), report and do not declare success. The live schema is
unchanged, so the bot/website read path needs no changes.

## Progress milestones (regular updates)

1. **Seed done** — N matches in the window, M already local.
2. **Download progress** — X/Y downloaded, Z unavailable (periodic).
3. **Ingest progress** — X parsed / classified, mgz-failures by version (periodic).
4. **Download complete** — final downloaded/unavailable tally.
5. **Ingest complete** — total matches in the local DB, total player-games, strategy counts.
6. **(go-ahead) Sync done + verified** — Railway now matches the local DB.

## Reuse / New

- **Reuse:** `utils/classifications/registry` (triggers/factors), `shape` (row building),
  `utils/replay_quiz/extract` (v3 parse), `utils/replay_quiz/download` (aoe.ms fetch).
- **New:** a SQLite schema + writer (a local analogue of `dbio`), the `ingest_ledger`, the
  Downloader loop, the Ingester loop, the batched Railway sync, the progress reporter.

## Out of scope / YAGNI

- No long-running daemon/file-watcher service — the two processes build the corpus and exit.
- No change to the bot/website read path — the synced schema is identical to today's.
- No automatic prod sync — always gated on explicit approval.
- Not re-architecting the existing Railway runner; this is an additive local-first path. (The
  `_ensure_replay` await-on-sync bug found during exploration is avoided here because the Downloader
  calls `download.py` directly.)

## Testing

- Pure logic (`registry`/`shape`/`extract`) is already unit-tested and unchanged.
- **New tests:** SQLite writer round-trip (write `cls_*` rows → read back equal); `ingest_ledger`
  state transitions; the batched-sync chunking (row count vs input). The download/parse I/O paths
  are offline and validated by a real run + the ledger, as with the existing replay tooling.
