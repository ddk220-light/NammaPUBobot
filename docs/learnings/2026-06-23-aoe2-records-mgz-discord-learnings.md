# Learnings Log — AoE2 records, mgz, and Discord

- **Date:** 2026-06-23
- **Context:** Building `/player_details` (Phase 2 of the replay-stats work) — the build-timeline
  chart command — plus resolving the save-68 backlog by parsing locally and pushing to prod.
- **Companion doc:** [2026-06-22 replay-stats + Railway learnings](2026-06-22-replay-stats-and-railway-learnings.md)
  (the data supply chain, the two-datasets insight, and Railway specifics live there — not repeated here).

This focuses on the three things the owner asked to capture: **AoE2 records**, **mgz**, and
**Discord (nextcord)**. Written so the next person doesn't re-learn it the hard way.

---

## 1. AoE2 records (`.aoe2record`)

### 1.1 What a record is, and the one number that gates everything
A `.aoe2record` is a compressed **header** (lobby, players, civs, map, settings) followed by a
**body** of timestamped player **actions** (RESEARCH, BUILD, age-up, DELETE, …). The single most
important field is **`save_version`** — a float that bumps every AoE2 DE patch (we've seen
`66.6`, `67.2`, `68.0`). The header layout is version-gated (fields appear/move at specific save
versions), so the parser must know how to read each version. **The body format is effectively
stable across these versions** — the action stream parses the same at 66.6 and 68.0. That
asymmetry is why a "new patch" usually only needs header tweaks, not a rewrite.

### 1.2 What you can actually extract (and what it really means)
From the body we derive per-player, per-game metrics (`utils/replay_quiz/extract.py`):
- **Age-up click times** — when the player *researched* Feudal/Castle/Imperial (the RESEARCH
  action timestamp), not when the uptime completed. This is the meaningful "how fast did they
  commit" number.
- **Villager / military counts**, split by phase (`pre_feudal`, `pre_castle`, `pre_imperial`,
  total) — counted from QUEUE/train actions.
- **Tech click times** — earliest RESEARCH timestamp per tech (Loom, Wheelbarrow, Bloodlines…).
- **Buildings** — BUILD-action counts (TCs, Barracks, Castles…).

**Gotcha — timestamps are *order* times, not *completion* times.** `first_tc_s` is the time of the
first Town Center **build order** (`min` of the TC BUILD actions), so on Nomad it reads ~`0:35`
(villagers are still walking to the spot) — that is correct, not a bug. Any "fastest X" metric is
"earliest *clicked*," which is exactly what you want for build-order analysis but will confuse you
if you expect wall-clock completion.

### 1.3 `age_reliable` — trust your timing data conditionally
Some games (full-tech lobbies, truncated/abandoned records) have no usable uptime data. The
extractor sets an **`age_reliable`** flag; all age-up and tech-timing metrics must be gated on it
(`age_reliable=1 AND feudal_s IS NOT NULL`) or you'll average in garbage. We carry this gate
through every layer — extract → `rs_*` schema → the `/player_details` queries.

### 1.4 Normalize the map before comparing players
Times are only comparable on a fixed map (Nomad has no starting TC, so *everything* is minutes
later than Arabia). The quiz and `/player_details` both restrict to `STANDARD_MAPS`
(`Land Nomad`, `Nomad`). Without this, off-meta maps inject time anomalies that make leaderboards
meaningless.

### 1.5 The supply chain expires
Replays are fetched live from `aoe.ms` (per-IP rate-limited, 429s) and only stay available for a
window. Practical pacing that avoids the 429 spiral: **~10s between fetches** (2s hammered it into
15–120s backoffs). Full chain + failure modes: see the companion doc §2.

---

## 2. mgz (the parser) — forks, internals, and install traps

`mgz` is the Python AoE2 replay parser. There are **three forks** and they are NOT interchangeable:

| Fork / package | `mgz.model`? | Save ceiling | Use |
|---|---|---|---|
| `happyleavesaoc/aoc-mgz` (canonical) | yes | ~66.6 | genuinely fails on 68 |
| **`sanduckhan/aoc-mgz` (our pin, `a1683d8`)** | **yes** | nominally 67.2 | **parses 68 fine** (see 2.2) |
| `AoEInsights/aoc-mgz` (pkg `mgz-fast`) | **no** | 67.2 | low-level only, no high-level model |

- **`mgz.model.parse_match`** is the high-level API we depend on (gives players/actions/uptimes as
  objects). `mgz-fast` deliberately drops `mgz.model` — it's a fast low-level reader, so it is NOT
  a drop-in even though it imports as `mgz`. Check `import mgz.model` to tell a real fork from
  mgz-fast.

### 2.1 The version "gate" is in *our* policy, not in mgz
`mgz/fast/header.py:parse()` only rejects unknown **game_version** enums (`USERPATCH15/DE/HD`) —
**there is no `save_version` ceiling** in the parser. Save 68 is a DE replay and reuses the
`save >= 66.6` header branches, so it parses. The thing that shelved 68 games was *our own*
`bot/replay_stats/policy.py` gate (`MAX_SUPPORTED_SAVE`), set too conservatively.

### 2.2 The save-68 lesson (verified empirically)
We first concluded a fork/rewrite was needed — **wrong**, because we tested `happyleavesaoc`
master (66.6-max, which does fail on 68) instead of the `sanduckhan` fork we actually ship. The
sanduckhan pin parses 68 cleanly (verified 7/7, then 72/72 real games). **The fix was one line**
(`MAX_SUPPORTED_SAVE` → `68.99`) + a `PARSER_VERSION` bump to auto-reopen shelved games.
**Rule: gate by what the parser *empirically* handles, not by a README version number.** When a
new save NN piles up, run one such replay through `extract.py` with the pinned fork; if it parses,
bump the gate + `PARSER_VERSION`, redeploy.

### 2.3 Install + runtime traps
- **Forks build with hatchling + setuptools-scm.** A **tarball** install works
  (`mgz @ https://…/archive/<sha>.tar.gz`, our `requirements.txt`), but a **git** install needs
  `git` in the image *and* a real ref; `setuptools-scm` errors with "unable to detect version" on
  a bare archive. Prefer the pinned-tarball form.
- **Package vs import name:** `mgz-fast` installs as `mgz-fast` but imports as `mgz` — a
  `mgz @ git+…AoEInsights…` line silently gives you the wrong parser. Pin by repo+sha, then verify
  `mgz.model` exists at runtime.
- **`ProcessPoolExecutor` is fork-vs-spawn sensitive.** The live ingest runs `extract_match` in a
  one-worker pool (`bot/replay_stats/parse.py`) so the event loop never blocks. On Linux/Railway
  (`fork`) the worker inherits the already-imported, DB-connected parent — fine. On Windows
  (`spawn`) the worker re-imports `bot.replay_stats`, which runs `ensure_table` against a
  not-yet-connected `db` and crashes. **For a local one-off, call `extract_match` in-process**
  (skip the pool) — same output, no spawn headache.

---

## 3. Discord / nextcord — slash commands, embeds, files

### 3.1 Command wiring (this codebase's pattern)
- Slash commands are declared in `bot/context/slash/commands.py` with
  `@dc.slash_command(name=…, **guild_kwargs)` (root) or `@group.subcommand(…)`, each delegating to
  `run_slash(bot.commands.<handler>, interaction=…, **opts)`.
- `run_slash` builds a `SlashContext` and calls `await handler(ctx, **kwargs)`. Handlers live in
  `bot/commands/` and are exported via `__all__` + star-import in `bot/commands/__init__.py`.
- **Lazy-import heavy deps inside the handler** (matplotlib, the query layer) so the commands
  module loads cheaply at boot.

### 3.2 The 3-second ack rule (defer!)
Discord kills an interaction that isn't acknowledged within ~3s. Any handler that does real work
(multi-table queries + a matplotlib render comfortably exceeds 3s) must
`await interaction.response.defer()` first, then reply via the followup. The `ctx.reply()` wrapper
already routes to `send_message` vs `followup.send` based on `interaction.response.is_done()`.

### 3.3 Embeds vs images — and the limits that bite
- **Embeds** are great for compact, structured data but have hard caps: **1024 chars/field, 25
  fields, ~6000 chars total.** Monospace alignment needs a ```` ``` ```` code block inside the
  field value. We initially shipped a 6-field stat card this way — then the owner found it "too
  much," and we moved to an **image**. Lesson: a long stat dump is better as a chart than as a
  wall of embed fields.
- **Image attachments:** render with the **OO `Figure` API**, not `pyplot` — pyplot keeps global
  state that's unsafe under the async bot. Pattern (mirrors `bot/player_profile.py`):
  `matplotlib.use("Agg")` → `Figure(...)` → `fig.savefig(io.BytesIO(), format="png", dpi=…)` →
  `File(fp=buf, filename="…png")` → `ctx.reply(file=…)`. Keep the matplotlib import lazy (CI has
  no matplotlib; importing it at module load would break the test collection).
- **Unicode in matplotlib:** the default DejaVu font has `·`, `→`, accents — but **no emoji**
  (they render as tofu boxes). Use plain text + colors for section headers in a chart.

### 3.4 Resolving a player
`ctx.get_member(player)` turns a mention / user-picker / name into a Discord `Member` with `.id`
(the Discord `user_id`). The bridge to AoE2 data is `rs_profiles` / `rs_player_games.user_id`
(seeded from `data/profile_resolved.csv`, grown as replays are parsed). So:
Discord user → `user_id` → `profile_id`(s) → `rs_*` rows (one user can have multiple profiles —
handle the set, not a single id). Unlinked players legitimately have no stats; say so clearly.

### 3.5 Command registration is invisible until connect — don't panic-debug
`@dc.slash_command` rewrites the function into a `SlashApplicationCommand`, but
`dc.get_application_commands()` returns **empty until the client connects and syncs** (commands
sync on `on_ready`). So an import-only boot check shows **0 commands for *every* command**,
including known-good ones — that's not a failure. To verify registration offline, check the
**decorated object's type** (`type(sc._player_details).__name__ == "SlashApplicationCommand"`),
not the client's command list. Guild-scoped commands (our `guild_kwargs`) sync near-instantly
after restart; global ones can take up to an hour.

### 3.6 Testing bot code without booting the bot
`bot/__init__.py` pulls in nextcord, matplotlib, web, and every subsystem's `ensure_table`. To
unit-test a leaf module, the conftest pre-registers a **`bot` package shim** (`sys.modules['bot']`
with an explicit `__path__`) so `from bot.x.y import z` skips the heavy `__init__` but still
resolves submodules. Keep pure logic (aggregation, bucketing) in **DB-free, matplotlib-free**
functions so they're testable under that shim — and keep the rendering/DB in separate modules.

---

## 4. Cross-cutting gotchas worth remembering

- **aiomysql on Windows needs the Selector loop.** The default Proactor event loop flakes on
  connect with `WinError 121` ("semaphore timeout") even though raw TCP to the host works. Set
  `asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())` for any local script
  hitting the DB.
- **The core MySQL adapter drops the URI port.** `core/DBAdapters/mysql.py:connect()` parses
  `dbPort` but never passes `port=` to `aiomysql.create_pool`, so it always hits 3306. Fine for
  Railway's internal network; **wrong for the public proxy** (port 10509+). Local scripts must
  build the pool themselves with the real port. (Flagged as a one-line fix.)
- **Local-parse → push-to-prod is a valid recovery pattern.** To backfill a parser-gate change
  fast, parse locally in a venv pinned to the *exact prod parser* (so rows are byte-parity with
  the rest), reuse the real `store.write_match` logic, and write straight to the prod DB via the
  public proxy — then let the online instance handle only *new* games. 72/72 save-68 games filled
  this way with zero parity drift.
- **Don't over-show.** The arc of `/player_details` was: rich embed → "too much" → single chart.
  When a player asks to "see their stats," a focused visual beats an exhaustive dump.
```
