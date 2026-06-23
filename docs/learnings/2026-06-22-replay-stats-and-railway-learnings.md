# Learnings Log — Live Replay-Stats Pipeline (AoE2 data + Railway)

- **Date:** 2026-06-22
- **Context:** Investigation + design for the live replay-stats pipeline (Phase 1 of `/player_details`).
- **Companion docs:** [spec](../superpowers/specs/2026-06-22-live-replay-stats-pipeline-design.md), [plan](../superpowers/plans/2026-06-22-live-replay-stats-pipeline.md).

This captures the non-obvious complexity discovered while designing a system that turns finished
AoE2 games into per-player in-game stats, live, on Railway. Written so a future engineer (or
future us) doesn't have to re-learn it.

---

## 1. The single most important insight: there are TWO datasets, and they don't overlap

The bot has two completely separate sources of truth, and confusing them wastes days:

| | **Live MySQL (Railway)** | **`data/replay_quiz.db` (SQLite)** |
|---|---|---|
| Freshness | Current to the last game (seconds) | Static bake; newest game **2026-05-10** |
| Contents | Match **results** only — winner, team, Elo, civ | In-game **metrics** — villagers, feudal time, units, techs, buildings |
| How updated | Bot writes it live | Offline `build_db.py` run, committed to git, shipped in the Docker image |
| Has villager/feudal/unit counts? | **No** (verified: scanned every column of every table) | **Yes** |

**Consequence:** the "latest data" you can spot-check live (e.g. matches up to yesterday) has
*zero* in-game metrics. The metrics only exist in the offline replay DB, which Railway has a
byte-identical copy of (it's in the image), so "pull it from Railway" gives you nothing newer.
To get metrics for games after the last bake, you must **re-parse replays** — there's no
shortcut. The live bot never computes villager counts; it only records who won.

---

## 2. AoE2 replay data is a multi-hop, lossy, expiring supply chain

Getting from "a game finished" to "rows of metrics" is a chain of external services, each with
its own failure mode:

```
game finished
  → aoe2companion API  (data.aoe2companion.com/api/matches/{gid})  → participant profile_ids
  → aoe.ms             (aoe.ms/replay/?gameId={gid}&profileId={pid}) → a ZIP with .aoe2record
  → mgz (sanduckhan fork) parses the .aoe2record                     → per-player metrics
  → attribution (profile_id → Discord user_id)                       → usable rows
```

Hard-won facts about this chain:

- **aoe2.net is dead** (sunset Oct 2025). The current sources are aoe2companion (match→profiles)
  and aoe.ms (the actual replay file). `replay_url` stored for humans points at aoe2insights.
- **~20–35% of replays are simply never downloadable** (aoe.ms 404/429). Coverage is *also*
  bounded by whether `civ_matcher` resolved the `aoe2_match_id` at all. So the system is
  **best-effort by nature** — design for "most games, eventually," not "every game."
- **Replays expire upstream.** A replay you don't grab and fully extract soon may be
  un-refetchable later. **This is why we must persist the raw, normalized data** (units/techs/
  buildings long-form), not just derived averages — otherwise a new metric idea later is
  impossible to backfill.
- **Near-real-time, not instant.** The `aoe2_match_id` only appears ~3–5 min after a game (once
  aoe2companion indexes it and the bot's `civ_matcher` matches it). "Parse the moment a game
  ends" is physically impossible.
- **Rate limits are real and hard.** aoe.ms returns 429s; the existing `download.py` already does
  patient exponential backoff (`[15, 30, 60, 120]` s, honoring `Retry-After`). Any bulk backfill
  must be one-at-a-time and polite.

### mgz parsing and the save-version treadmill

- Parsing needs the **`sanduckhan/aoc-mgz` fork**, pinned to a specific commit
  (`a1683d8…`), because **stock mgz can't read current AoE2 DE replays** (`save_version` 67.x).
  This fork is *not* in the bot's runtime deps today — it's offline tooling.
- **Every major AoE2 patch bumps `save_version`** (e.g. 66.6 → 67.2 → eventually 68.x), and the
  pinned fork breaks on versions newer than it knows. The repo already carries a `save67.patch`
  as evidence of this treadmill.
- **Design response:** read `save_version` *before* the full parse and gate on a
  `SUPPORTED_SAVE_VERSIONS` policy. Too-new games go to a `pending_parser_update` state (no retry
  thrashing), and a deploy that bumps the fork + a `PARSER_VERSION` constant auto-reopens the
  backlog. You get an early-warning signal ("N games stuck on save 68") telling you when to
  update the parser.
- Parsing is **CPU-bound** on 2–5 MB files. It must run **off the bot's event loop** (a
  subprocess / process pool). `extract_match()` helpfully takes a *path* and returns *plain
  dicts*, so it pickles cleanly across the process boundary.

### Identity is a four-way mapping mess

Five different identifiers refer to "a player," and nothing ties them together cleanly:

| Identifier | Scope | Where it lives |
|---|---|---|
| `player_number` | per-match slot (1–8) | the replay (what units/techs/buildings are keyed by) |
| `profile_id` | stable AoE2 account id | the replay player record; `data/profile_resolved.csv` |
| Discord `user_id` | the bot's identity | `qc_players`, `qc_match_civs`, `profile_resolved.csv` |
| `nick` / `aoe2_name` | display names | everywhere, unreliable, collide |

Gotchas that bit us (caught by adversarial review, *not* obvious from the happy path):
- The parser keys units/techs/buildings by **`player_number`, not `profile_id`** — so those
  tables must be keyed `(aoe2_match_id, player_number, …)`, with `profile_id` denormalized in
  separately at write time.
- `qc_match_civs` has the `aoe2_match_id` but **no `profile_id` column**, and its `aoe2_name` is
  **empty on the API-resolved path** (`civ_matcher` hardcodes `''`). So it can't be the
  attribution seed. The real seed is `data/profile_resolved.csv` (`profile_id → user_id`), loaded
  into a persistent `rs_profiles` map. Unmapped players (opponents, observers) are stored with
  `user_id = NULL` — expected, not an error.
- `qc_profile_map` exists but is **empty in prod (0 rows)** — a tempting-but-dead lead.

### The metric catalogue (what the quiz already extracts)

~70 metrics across 6 families, all derivable from the parsed replay: **Villagers** (4),
**Age speed** (4: feudal/castle/imperial/first-TC times), **Buildings** (6), **Military totals**
(5), **Military by type** (26: per-unit, whole-game + age-gated), **Tech timing** (19: earliest
click of curated techs). Stored as `facts` (per-player aggregates) + `units`/`techs`/`buildings`
(long-form).

---

## 3. What the bot *already* does that makes this feasible

The pleasant surprise: most of the hard plumbing exists.

- **`civ_matcher` auto-captures the AoE2 match id.** Every finished game gets an
  `aoe2_match_id` written into `qc_match_civs` (~3–5 min later). Verified live:
  **15,748 rows, 100% populated, 2,339 distinct matches**, each with `bot_match_id, user_id,
  nick, civ, team, result, at`. This is the trigger *and* the `aoe2 ↔ bot` match linkage —
  and it's **the only table that knows the `aoe2_match_id`** (`qc_matches` has no such column).
- **A working offline fetch→parse pipeline exists** (`utils/replay_quiz/download.py` +
  `extract.py` + `build_db.py`) — going live is mostly *wiring + an async refactor*, not new code.
- **The `think()` job pattern** (from `bot/quiz/jobs.py`) is the safe way to add background work:
  self-isolating (never raises into the 1-s tick), cadence-gated, one-run-at-a-time. Copy it.
- **`extract.py` can be the single source of truth** shared by the offline quiz pipeline and the
  live job — reuse it to avoid the two drifting apart.

---

## 4. How Railway actually works (verified against the docs, June 2026)

| Aspect | Reality | Implication for us |
|---|---|---|
| **Compute** | Hobby: up to **8 GB / 8 vCPU** per service. Pro: 24 GB / 24 vCPU (was 32/32, reduced ~Apr 2026). | One replay parse (~100–300 MB, seconds) is trivial. Cap to 1 concurrent parse. |
| **Ephemeral disk** | **100 GB** on paid (1 GB free), **wiped on every redeploy**. | Fine for temp `.aoe2record` files (2–5 MB) **if deleted right after parse**. Never store anything durable here. |
| **Volumes** | 0.5 GB free / **5 GB Hobby** / 1 TB Pro. DBs live on a volume. | Our metric rows (~0.2–0.3 GB/yr) fit for ~15–20 yr even on Hobby. |
| **Execution model** | Long-running services, **not serverless** — no per-request timeout. | A multi-second subprocess parse is fine; nothing will kill it mid-parse. |
| **Deploy** | **Push to `main` → auto-deploy**; the image rebuilds from git. *Don't* use the Railway CLI to poke at the running app. | Adding the mgz fork to `requirements.txt` is the cost: bigger image, slower builds. No Dockerfile change (it already `pip install`s `requirements.txt`). |
| **The DB is reachable** | `config.cfg`'s `DB_URI` points at the live MySQL via a **public proxy** (`shuttle.proxy.rlwy.net`). | Enabled read-only live spot-checks (via `utils/db_helpers.create_pool`) throughout this design — verify, don't assume. On Windows set `PYTHONUTF8=1` or embed-emoji prints crash cp1252. |

**Net Railway verdict:** the existing single service + its MySQL handle this with enormous
headroom. **No new services, no volume changes, no second database.** The only real cost is image
build time/size from the parser deps.

---

## 5. Engineering principles this drove

- **Best-effort + retries + idempotency.** Key everything by `aoe2_match_id`; a status machine
  (`done / unavailable / pending_parser_update / parse_failed / gave_up`) drives escalating
  retries (10m→1h→6h→24h, give up after 7d) and survives re-deploys without double-writing.
- **Keep raw, normalized data — not just derived metrics.** Because replays expire, this is the
  only way to keep future metrics (and the existing 70) reproducible without re-parsing.
- **Parse off the event loop** in a subprocess; reuse the proven sync download code via
  `asyncio.to_thread` rather than rewriting it to aiohttp (lower risk, equally non-blocking).
- **Durable store = MySQL** (new `rs_*` tables); ephemeral disk only for transient replay files.
- **Opt-in + admin-gated.** A feature flag keeps it dormant until ready; the outward-facing,
  rate-limited backfill fires only from `/replaystats backfill`, never automatically on deploy.

---

## 6. Process learnings (how we de-risked the design)

- **Verify against the live system, don't assume.** Read-only prod spot-checks repeatedly
  corrected mental models (e.g. that the "latest data" had no metrics; that `qc_profile_map` was
  empty; that the DB is only 9.2 MB so storage is a non-issue).
- **Adversarial multi-agent review of the spec paid for itself.** It caught **three real
  blockers** before any code: (1) long-form tables keyed by `player_number` not `profile_id`;
  (2) the attribution seed I'd named (`qc_match_civs.aoe2_name`) is empty/absent; (3) `qc_matches`
  has no `aoe2_match_id`, so the backfill source had to be the deduped `qc_match_civs`.
- **Ground estimates in measurements.** Storage projection used the offline DB's real row ratios
  × this DB's measured ~110–170 bytes/row, not guesses.

---

## 7. Open risks to watch during/after build

- **Save-version breakage** after AoE2 patches → games pile into `pending_parser_update` until
  the mgz fork is bumped. Watch `/replaystats status`.
- **`ProcessPoolExecutor` + mgz** must be validated on the deploy platform (Linux fork). The
  parity smoke test (vs `replay_quiz.db`) is the gate; fallback is a spawned subprocess + JSON.
- **External-service drift** (aoe2companion / aoe.ms changing shape or limits). The job is
  isolated and best-effort, so this degrades gracefully rather than breaking the bot.
- **`aocref` is unpinned** — pin it once the first image build resolves a version.

---

## 8. Key references

- **Services:** aoe2companion API `data.aoe2companion.com/api/matches/{gid}`; replay download
  `aoe.ms/replay/?gameId={gid}&profileId={pid}` (ZIP → `.aoe2record`); human link
  `aoe2insights.com/match/{id}/`. (aoe2.net retired Oct 2025.)
- **Parser:** `mgz @ github.com/sanduckhan/aoc-mgz` commit `a1683d8…` + `aocref`.
- **Code:** `utils/replay_quiz/{download,extract,build_db}.py`; `bot/civ_matcher.py` +
  `bot/civ_sync.py` (`qc_match_civs`); `bot/quiz/jobs.py` (think-job pattern);
  `utils/db_helpers.py` (read-only spot-checks).
- **Railway:** [plan limits](https://docs.railway.com/pricing/plans),
  [scaling](https://docs.railway.com/deployments/scaling),
  [volumes & ephemeral storage](https://docs.railway.com/reference/volumes).

---

## 9. Production rollout findings (2026-06-23)

The feature shipped (opt-in, off-by-default), was enabled, and a 90-day backfill ran. What we learned the hard way:

- **The save-version treadmill hit immediately.** Since the last replay bake (May 10, save 67.2),
  AoE2 shipped a patch bumping replays to **save 68.0**, so the **most recent ~6 weeks of games
  are unparseable** by the canonical mgz API our `extract.py` uses. The system handled it exactly
  as designed: detected the new format, **shelved** the games as `pending_parser_update` (72 of
  them), never crashed, and they'll auto-parse once the parser is updated.
- **No drop-in save-68 parser exists yet.** Tested against a real save-68 replay:
  `sanduckhan/aoc-mgz` (our pin) and `happyleavesaoc/aoc-mgz` master **both fail** at the new DE
  header (`de_string` assertion). **`AoEInsights/aoc-mgz` (package `mgz-fast`) CAN parse save-68**
  but exposes only a low-level API — **no `mgz.model`** — so adopting it means **rewriting
  `extract.py`** (a real project, not a pin bump). Decision: take the 67.x slice now, leave save-68
  shelved until a canonical `mgz.model`-compatible save-68 parser lands (then it's a one-line pin
  bump + `PARSER_VERSION` bump → auto-reparse).
- **Build-system gotcha (caught in staging).** The save-68 forks use `hatchling`+`setuptools-scm`,
  so a GitHub **tarball** install fails ("setuptools-scm was unable to detect version") — they need
  `pip install git+https://…` *and* `git` present in the build image. Our prod `sanduckhan` pin is
  the old static-version style, which is why its tarball works. Any future fork swap must account
  for this (git-install + add `git` to the Dockerfile, or `SETUPTOOLS_SCM_PRETEND_VERSION`).
- **aoe.ms rate-limits per IP.** A 2s backfill cadence triggered relentless 429s (15→30→60→120s
  in-download backoffs). Bumped `backfill.FETCH_PACING_S` to **10s**. Because it's per-IP, **local
  testing didn't contend** with the prod bot's downloads.
- **The pipeline is validated end-to-end and CORRECT.** 199 games parsed+stored, 1,566 player
  rows (97% attributed). A **parity check vs the offline `replay_quiz.db` ground truth** (the
  Task-8 test, run for real against 156 overlapping matches) found **0 mismatches** across the
  sampled players — live extraction reproduces the trusted values exactly.

**Open follow-up:** save-68 support. Watch `happyleavesaoc/aoc-mgz` for a save-68 release (then
bump the pin), or scope the `mgz-fast` migration if recent data is urgently needed.
