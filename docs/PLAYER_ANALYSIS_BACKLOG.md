# Player-Analysis Feature Backlog

Living backlog for the replay-stats / `/player_details` line of work. Captured 2026-06-23 from
the owner. Each item lists the **goal** (the owner's intent), why it's valuable, a rough
**approach**, what it **touches**, **dependencies**, and **open questions** to resolve before
building. Sized roughly: S / M / L / XL.

**What exists today (the foundation these build on):**
- Live replay-stats pipeline → `rs_*` MySQL tables (per-player-game metrics: age-up clicks,
  villager/military counts per phase, tech click times, units, buildings). See
  [replay-stats learnings](learnings/2026-06-22-replay-stats-and-railway-learnings.md).
- `/player_details` → a build-timeline chart (villager/military bars across the 4 phases with
  eco/military upgrades annotated). See [AoE2/mgz/Discord learnings](learnings/2026-06-23-aoe2-records-mgz-discord-learnings.md).

**Suggested sequencing:** B1 (data quality) → B3 (event/strategy detection — the analysis engine)
→ B2 (post-match card, a basic version can ship after B1) → B4 & B5 (analytics on top of B3).
B3 is the keystone: B4 and B5 can't really exist without it.

> Note on terminology: the owner said "sieve" — that's **civ** (civilization) throughout.

---

## B1 — Granular timeline + statistical reliability  *(size: L)*

**Goal.** Make the chart *more granular* (more data points per game, finer than the current 4
phases), and *improve accuracy*: only average over games where the thing actually happened, and
show a **confidence interval / reliability indicator** so a value isn't trusted off a single data
point — tell the user whether a number is reliable.

**Value.** A single-game average is misleading. Users need to see "feudal 11:28 across **28**
games, tight spread" vs "imperial 39:09 from **2** games, huge spread" and trust them differently.

**Approach.**
- *Granularity:* emit a **time series** per game rather than only phase snapshots — e.g. villager
  & military count sampled at fixed intervals (every 1–2 min) or at every meaningful event, so the
  chart can draw a real growth curve instead of 4 bars. Requires `extract.py` to walk the action
  stream and emit samples.
- *Accuracy / reliability:* for every metric compute **n, mean, spread (std or IQR), and a CI**
  (e.g. mean ± 1.96·std/√n). Already we skip games where a tech/age didn't happen (good); formalize
  this everywhere. Render reliability on the chart — **error bars** or a shaded CI band, plus the
  **n** on each point — and optionally hide or grey out metrics below an `n` threshold.

**Touches.** `extract.py` (time-series sampling) · `rs_*` schema (likely a new per-game time-series
table) · `query.py` (n / std / CI) · `chart.py` (error bars / CI shading / n labels).

**Open questions.** Granularity unit — per-minute, or per-event? How many points before it's
noise? Reliability threshold (min n to show a metric)? How to render CI without clutter (error
bars vs band vs an n-badge)?

---

## B2 — Dynamic post-match analysis card  *(size: M)*

**Goal.** When a match completes, **automatically post** a replay-analysis statistic/card for that
game — give players the analysis right after they finish, without running a command.

**Value.** Immediate, contextual payoff at the moment of highest interest (game just ended).

**Approach.** The bot already knows when a match ends (`qc_matches`) and the ingest already parses
each game's replay once it lands on aoe.ms. When a match's `rs_*` rows are written, post a
per-match summary to the match channel/thread: per-player feudal/castle times, villager & military
counts, civ, winner highlight, maybe a mini build-timeline. Ship a **basic version after B1**
(just the parsed metrics), then enrich with strategy labels once **B3** lands.

**Touches.** Match-completion hook (`events.py` / the ingest's "match done parsing" point) · a
single-match card/chart builder.

**Dependencies.** None hard; richer version wants **B3**.

**Open questions.** The replay appears on aoe.ms minutes *after* the game — is a delayed post (not
instant) acceptable? Where does it post (match channel vs a thread)? Opt-in per channel? Avoid
double-posting on re-ingest.

---

## B3 — Strategy & in-game event detection  *(size: L–XL, keystone)*

**Goal.** Detect *what happened* and *what the player was going for*: castle drops, "douche"
(early militia/men-at-arms all-in), feudal rushes, castle rushes / fast castle — and identify the
player's intended **unit composition / "unit mode"** (their main army type).

**Value.** Turns raw counts into meaningful strategic labels — the bridge from "data" to
"insight," and the prerequisite for B4 and B5.

**Approach (heuristics — each needs design + validation against known games).**
- **Fast Castle:** early castle click + minimal pre-castle military.
- **Drush** (dark-age militia rush): militia-line produced in dark/early feudal.
- **Feudal rush / "flush":** mass archers / skirms / scouts produced in feudal, low eco lean.
- **Castle rush:** aggressive timing into castle age with knights/crossbows/unique units.
- **Castle drop:** an early — and ideally *forward* — Castle BUILD. **Forward detection likely
  needs building map positions**, which we may not currently extract; verify what positional data
  mgz exposes before promising this.
- **Unit mode:** the dominant unit category by production share (we already store per-category
  counts) → label the main composition (e.g. "Knights", "Archers", "Xbow + Pike").

**Touches.** `extract.py` (possibly positions / tighter time windows) · a new strategy-classifier
module (heuristics over per-game metrics + action stream) · `rs_*` (store detected labels per
player-game so they're queryable).

**Dependencies.** Benefits from **B1**'s richer time series.

**Open questions.** Precise, validated heuristic per strategy (false positives are easy here).
**Confirm the term "PC douche"** — is it pre-castle douche / TC-area militia all-in / something
else? Do we have positional data for "drops" and "forward" plays?

---

## B4 — Strategy ↔ win/loss correlation  *(size: M, needs B3)*

**Goal.** Put it together: for each game, categorize the player's strategy (from B3) **and** the
outcome (win/loss — we already have `winner`), and report *what strategy they went for and whether
it won*.

**Value.** Actionable — "which strategies actually work for this player."

**Approach.** Aggregate win rate per (player, strategy) and overall: e.g. "ddk goes Fast Castle
Knights in 60% of games and wins 55%; when they drush, 30%." Surface in `/player_details` or a new
view.

**Touches.** Aggregation over (strategy, winner) · a presentation surface.

**Dependencies.** **B3** (strategy labels). Reliability from **B1** matters (small per-strategy
samples).

**Open questions.** How to present (per-player table? matchup-aware?). Minimum games per strategy
before showing a win rate.

---

## B5 — Civ + unit effectiveness  *(size: L, needs B3)*

**Goal.** Interpret **what units work best in a given game for a given civ**, and connect it to
**which players win most with which civs and what about their play drives those wins**.

**Value.** Civ-specific meta insight — the "why" behind winning, per civ, per player.

**Approach.** Cross-tabulate **civ × unit-composition × outcome** (win rate); per-player civ win
rates (we have civ + winner already); then correlate the *characteristics of winning games* per
civ (timings, unit mix, upgrades) — e.g. "with Mongols, players win more when they mass Mangudai by
~25 min." Data/analysis heavy; small per-(civ,player) samples make **B1**'s reliability work
essential to avoid noise.

**Touches.** Aggregation over civ × units × outcome · a civ-insights presentation surface.

**Dependencies.** **B3** (unit mode / composition labels), **B1** (reliability for thin samples).

**Open questions.** Per-player vs global civ insight (or both)? How to phrase causal-sounding
claims responsibly (correlation, not proof)? Sample-size floors per civ.

---

## Cross-cutting themes

- **Data quality first (B1).** Every analytic item degrades into noise without enough games and
  honest reliability — do B1 before leaning on per-strategy / per-civ slices.
- **B3 is the engine.** B4 and B5 are aggregations over B3's labels; B2 gets much richer once B3
  exists. Invest in validating B3's heuristics against games whose strategy we already know.
- **Keep pure logic testable.** Continue the pattern: DB-free, matplotlib-free classification /
  aggregation functions (unit-tested) with thin DB + rendering layers around them.
