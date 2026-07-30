# Match Cards revamp — design

**Date:** 2026-07-28 · **Revised:** 2026-07-29 (eAPM placement, pre-work pipeline)
**Surfaces:** `bot/post_game.py` (Match Cards embed), `bot/replay_stats/card_scoring.py` (new)
**Status:** design agreed — active

> **Revision note (2026-07-29).** The original spec was parked until the per-player
> eAPM pipeline shipped. It has now shipped (`feat/eapm-pipeline`), and this revision
> resolves the deferred questions: average and peak eAPM go on the stats line (see
> Stats line), eAPM is display-only and enters no score, and the medal basis is
> re-confirmed as raw counts. It also records the agreed pre-work pipeline
> (see Pre-work) and the scope decision that superseded the readiness doc's Part 2:
> **strictly forward-looking — no replay archive, no backfill.** Newly played
> matches are already ingested with every table this card reads; history is used
> only to calibrate thresholds, via the existing live DB.

## Problem

The Match Cards embed reads as flat: every player looks roughly as good as every
other. Three separate causes, all in the current design:

1. **Participation tags.** `impact_tag_names_with_fallback` guarantees every player
   at least one tag, and five of the six fallbacks (`All-rounder`, `Army-leaning`,
   `Eco-leaning`, `Tempo-leaning`, `Uphill battle`) read as achievements. They
   render as the same backtick chip as `Boom carry`, so an unremarkable game
   produces a chip that looks earned.
2. **A compressed headline number.** Scores are z-scores clamped to ±2 and mapped
   `50 + 15z`, so the reachable range is 20–80, and calibration puts impact p95 at
   60. Every player on every card lands in a ~40–62 band. A bare `57` also invites
   being read as a cross-match rating, which it is not.
3. **Glyphs that fire too often.** `▲` triggers at 61, which is 0.73 standard
   deviations, not the one standard deviation the docstring claims. In an
   eight-player match roughly two players clear it per component, so across three
   components most players show at least one `▲`.

Separately, the card ignores data the ingest pipeline already stores: strategy
classifications, upgrade research, per-click production timelines, building
counts, and spawn positions.

## Goals

- Make the card discriminating: a player who did nothing notable should look like
  it, without the card lying about them.
- Surface the concrete, replay-derived facts (strategy, raw counts) that are more
  interesting than a synthetic score.
- Use data already persisted at ingest. No new parsing, no backfill.

## Non-goals

- Measuring combat effectiveness. The parser records production queue-clicks only
  — never kills, losses, resources gathered, or damage. Every signal here is
  volume, timing, or investment. No naming or framing should imply otherwise.
- Changing the `Final Tale of the Tape` embed or the `What the Civs Say` embed.
- **Touching `bot/replay_stats/scoring.py` or anything downstream of it.** See below.

## Isolation from the existing scoring pipeline

`scoring.py` is not private to the Match Cards. It has three consumers:

| Consumer | Uses it for |
|---|---|
| `bot/post_game.py:28` | Match Cards **and** Tale of the Tape |
| `bot/web.py:24` | Player profile + match stats API |
| `bot/replay_stats/player_tags.py:15` | Writes the **persisted** `rs_player_game_tags` table |

That third one is the trap. Tags are not computed on read — they are written per
player per match at ingest and read back by three web endpoints
(`bot/web.py:898`, `:1171`, `:1226`) plus the tag leaderboard. Changing the tag
vocabulary in place would leave every historical row carrying names the code no
longer emits, and `tag_leaderboard_score` (`bot/tag_leaderboard.py:15`) has a
volume term `min(tag_games / 20.0, 1.0) * 100.0`, so a newly introduced tag reads
as meaningless for everyone until twenty games accumulate, while a dropped tag's
leaderboard silently freezes.

**Decision: fork, do not migrate.** This revamp adds a new module — working name
`bot/replay_stats/card_scoring.py` — that implements everything in this spec and is
consumed *only* by `build_match_cards_embed`.

- `scoring.py` is left byte-identical.
- `player_tags.py`, `rs_player_game_tags`, the web API, the tag leaderboard and the
  Tale of the Tape narrative all keep using it and are unaffected.
- No migration, no tag backfill, no recalibration of the existing thresholds.
- `_tag_word()` (`bot/post_game.py:268`) hardcodes the old payload tag names for the
  Tale of the Tape; because that embed stays on `scoring.py`, it needs no change.

`card_scoring.py` follows the same discipline as `scoring.py`: pure functions, no
DB access, no `core` imports, so it stays unit-testable under the CI import shim.

**Accepted consequence:** the three correctness issues below are fixed in
`card_scoring.py` only. They remain live in `scoring.py`, and therefore in the web
profile, the stored tags and the Tale of the Tape. This is a deliberate deferral to
avoid destabilising a surface owned by another contributor — not a judgement that
they don't matter. They should be revisited as their own piece of work.

## Removed

| Removed | Reason |
|---|---|
| Displayed impact score | Compressed into a ~22-point band; reads as an absolute rating it isn't |
| Rank (`#2 of 8`) | Considered and rejected — redundant with sort order |
| `▲` / `▼` / `·` glyphs | Three-state read that didn't distinguish anyone |
| `⏱` clock and the whole timing component | Age-up speed judged not to matter much; see Scoring |
| `High impact` tag | Says nothing the carry crown doesn't |
| `Army pressure`, `Boom carry`, `Eco carry`, `Timing edge` | Now restated by the medals |
| All five participation fallbacks | Primary cause of the flatness |

`Partial replay` is **not** removed but is reclassified from a tag to a data-quality
marker (see Error handling).

## Scoring model

Two components, unchanged in structure, with upgrades folded in:

```
ECO_MIX   = villagers 0.55, vil_pre_castle 0.45, + economic-tech term
ARMY_MIX  = military 0.55, mil_pre_imperial 0.25, mil_pre_castle 0.20, + military-tech term
IMPACT    = army ~0.58, eco ~0.42        (renormalised from 0.45 / 0.32)
```

- **Economic techs**: Wheelbarrow, Hand Cart, Horse Collar, Heavy Plow, Crop
  Rotation, Double-Bit Axe, Bow Saw, Two-Man Saw, Gold Mining, Gold Shaft Mining,
  Stone Mining, Stone Shaft Mining.
- **Military techs**: Forging / Iron Casting / Blast Furnace, the armour lines,
  Fletching / Bodkin Arrow / Bracer, and unit line upgrades.

Read from `rs_player_techs`. The count of relevant techs researched is z-scored
like any other column and enters its component's mix.

The z-score machinery is unchanged: clamp to ±2, map `50 + 15z`. Component and
impact scores remain **internal only** — they drive sort order, the carry crown,
and tag thresholds, and are never displayed.

`TIMING_MIX` is deleted. `feudal_s`, `castle_s` and `imperial_s` no longer feed any
score.

These constants live in `card_scoring.py` as its own `ECO_MIX` / `ARMY_MIX` /
`IMPACT_WEIGHTS`. The identically-named constants in `scoring.py` are untouched.

### Calibration is required, not optional

`card_scoring.py` needs **its own** threshold set, derived from its own component
distributions — it cannot inherit `scoring.py`'s `TH` values, which are percentile
anchors for a three-component mix without tech terms.

Thresholds must be derived against the live history (1,061 matches / 8,371
player-games) before ship. `utils/tag_calibration.py` currently imports
`bot/replay_stats/scoring.py` **by path** (`utils/tag_calibration.py:34`), so it
needs a parameter or variant to point at `card_scoring.py` instead. Because the
existing module is untouched, this calibration is additive — it cannot change how
often any existing tag fires on the surfaces this revamp doesn't own.

## Pre-work: coverage, calibration, preview

Three steps run against the **live Railway MySQL** before the implementation plan
is finalized. Access is **read-only and SELECT-only** — results are exported to
files committed to the repo; nothing ever writes to the live DB. This constraint
is part of the design, not an operational detail.

1. **Strategy coverage measurement.** One query answering: what share of
   player-games match at least one of the 17 classifications in `cls_results`?
   This is the checkpoint that can invalidate the headline design. If coverage is
   low, the preview step decides whether the headline slot needs rethinking —
   either way the card never renders a placeholder.
2. **Calibration.** `utils/tag_calibration.py` gains a parameter to target
   `card_scoring.py`. Thresholds are derived from the live history; the
   calibration **input snapshot** (per-player-game component inputs) is committed
   to the repo so the derived thresholds are reproducible offline. The resulting
   `TH` constants land in `card_scoring.py` with their provenance (snapshot file,
   date, sample size) noted alongside.
3. **Preview.** A small script renders the exact card text for ~5 recent
   post-eAPM-deploy matches from live data — real strategy, spawn and eAPM, no
   stubs. The preview is reviewed by a human before build; this spec is amended
   if the design does not survive contact with real matches.

There is no replay archive and no backfill. The forward ingest pipeline already
writes every table this card reads; nothing here adds parsing, storage, or
schema.

## Medals

Two medal types, ranked **across all players in the match** (not per team):

| Medal | Ranked by | Award |
|---|---|---|
| `⚔⚔⚔` / `⚔⚔` / `⚔` | `military` (raw count) | 1st / 2nd / 3rd |
| `🌾🌾🌾` / `🌾🌾` / `🌾` | `villagers` (raw count) | 1st / 2nd / 3rd |

Everyone outside the top three gets no medal for that axis. In a 4v4 that means
most players carry zero or one medal, which is the intended discrimination.

Medals rank on **raw counts**, not component scores. The medal answers "who made
the most", which is a plain fact; the component score answers "who contributed
most overall" and includes upgrade investment. Keeping them distinct is deliberate.
Re-confirmed 2026-07-29 with eAPM data available: activity measures something
other than production volume and does not change the medal basis.

Ties break deterministically: raw count, then the other component's raw count,
then nick ascending — so re-renders of the same match never reorder.

## Tags

Tags now describe **shape**, which medals cannot express. Three survive:

| Tag | Condition (thresholds pending calibration) |
|---|---|
| `Low-eco pressure` | army high **and** eco clearly sacrificed |
| `Recovery` | genuinely weak early eco **and** the recovery actually landed |
| `Constant production` | production coverage above the calibrated bar |

Allocation changes from per-player to **per-team**:

1. Compute component scores for every player.
2. For each tag, collect the players on a team who clear its absolute threshold.
3. Award to the **highest scorer among them**.
4. If nobody on the team clears the threshold, **nobody gets the tag**.

A single player may hold several tags — a dominant game should show that, and their
teammates showing nothing is the honest result.

Note the deliberate scope mix: **medals are match-wide, tags are team-scoped.**
Medals answer "who did the most, overall"; tags answer "what was the shape of this
player's game relative to their team".

### Constant production

Measured from `rs_player_events`, which stores one row per production click with a
timestamp. Definition: split the game into **two-minute buckets** from the first
click to match end, and measure the share of buckets containing at least one
production click.

Bucketing was chosen over gap-based measures because production clicks are
**batched** — one click can queue five villagers via the `amount` field, covering
roughly two minutes. Gap-based metrics would read efficient batch-queuing as
idleness, and correcting for that needs per-unit train times which are not stored.
A batch click still marks its bucket, so bucketing is immune to the problem.

Known limitation: clicks with a null timestamp are dropped from the timeline
(`utils/replay_quiz/extract.py:130`) while still counting toward the totals, so the
timeline is slightly sparser than the raw counts imply. Calibration should confirm
this does not systematically bias the metric.

## Strategy label

Each player's headline label comes from `cls_results`, written on every ingest by
`bot/replay_stats/classification_sync.py`. Seventeen classifications are registered:
`archer_rush`, `scout_rush`, `maa_rush`, `knight_rush`, `crossbow_rush`,
`cav_archer_rush`, `camel_rush`, `ram_push`, `forward_castle`, `safe_castle`,
`late_knight`, `late_crossbow`, `late_cav_archer`, `late_camel`, `late_unique`,
`late_ram`, `boom_to_imp`, plus the luck family.

If multiple classifications fire for one player, show exactly one: the
earliest-phase match (rush before castle before late). No label when none fire.

## Stats line

Raw counts, given real space since they are more concrete than any derived score:

```
84 vils · 46 military · 14 farms · 3 TC · 62 eAPM (pk 89)
```

`villagers` and `military` come from `rs_player_games`; farms and TCs from
`rs_player_buildings` (counts only — that table stores no timestamps).

### eAPM (added in the 2026-07-29 revision)

- **Average** reads from `rs_player_games.eapm` — the parity-preserving stored
  value. It must **never** be recomputed from `apm_query.apm_series`: that
  function's `mean_active` divides by the last active minute, not whole game
  minutes, and was renamed specifically so this card would not mistake it for
  the average.
- **Peak** is the maximum bucket from `rs_player_apm`. That table is
  forward-only, so matches ingested before the eAPM deploy have no rows — the
  `(pk N)` suffix is omitted silently, per the no-placeholder rule. The seam
  closes on its own as forward-only matches accumulate.
- **Early-eliminated players** show their stored average as-is. mgz divides
  every player's actions by full game duration, which deflates a player who
  died at minute 15 — but their low eAPM sits next to their low counts and
  reads coherently as a short game. Inventing a survival-adjusted denominator
  would break parity with the stored value.
- eAPM is **display-only**. It enters no component mix, no medal ranking and no
  tag threshold. Whether activity deserves a scored role is revisited after
  live use.

## Spawn context

From `cls_result_metrics`, which `utils/classifications/defs/luck.py` already
populates with `ally_dist`, `enemy_dist` and `nearest_player_dist` via
`gamedata.spawn_proximity`. Rendered as a short phrase — "spawned alone",
"spawned next to enemy", "spawned with team" — against calibrated distance bands.

**Availability is gated.** `is_valid_luck_game` requires a Nomad map, exactly 6 or 8
players, and a balanced recorded result. The community plays NammaNomad so this
usually passes, but 2v2s, odd player counts, non-Nomad maps and unresolved results
produce no spawn metrics. The card shows the phrase when present and omits it
silently when absent — it must never render a placeholder or a guess.

## Card layout

```
🟩 Alpha · W

👑 Deepak — Franks · Castle drop
   ⚔⚔⚔ 🌾🌾 · `Low-eco pressure`
   84 vils · 46 military · 14 farms · 3 TC · 62 eAPM (pk 89) · spawned alone

• Sathish — Mongols · Scout rush
   ⚔ · `Constant production`
   61 vils · 52 military · 8 farms · 2 TC · 48 eAPM (pk 71)

• Ravi — Britons · Archer rush
   71 vils · 38 military · 11 farms · 2 TC · 55 eAPM

• Kumar — Mayans · ⚠ partial replay data
```

Three lines per player, roughly 110 characters each with the eAPM term included.
A four-player team lands near 460 characters, comfortably inside Discord's
1024-character field cap. The existing hard truncation at `[:1024]` stays as a
backstop but should no longer be reachable; if it ever triggers, drop the stats
line before dropping a player.

`👑` still marks the highest impact score on each team. Sort order within a team
remains `carry_sort_key` on the internal impact score.

## Data sources

All reads. No new parsing, no schema change, no backfill.

| Signal | Table | Availability |
|---|---|---|
| Villagers, military, age splits | `rs_player_games` | Every ingested match |
| Strategy | `cls_results` | Every ingested match |
| Upgrade research | `rs_player_techs` | Every ingested match |
| Production timeline | `rs_player_events` | Every ingested match |
| Farms, TCs | `rs_player_buildings` | Every ingested match |
| Spawn distances | `cls_result_metrics` | Nomad, 6/8 players, balanced result only |
| Average eAPM | `rs_player_games.eapm` | Every ingested match |
| Peak eAPM | `rs_player_apm` | Matches ingested after the eAPM deploy only |

## Correctness fixes in scope

All four apply to `card_scoring.py` and the Match Cards path **only**. `scoring.py`
keeps its current behaviour, so the web profile, the stored tags and the Tale of the
Tape are bit-for-bit unchanged. Fixes 1–3 therefore remain live on those surfaces;
see "Accepted consequence" above.

1. **Honour `age_reliable`.** The card path does not currently SELECT it, so age
   splits flagged unreliable by the parser still score. The web profile correctly
   excludes them (`bot/replay_stats/query.py:82`), so the two surfaces disagree
   about the same player. Timing is gone, but `mil_pre_castle` and
   `mil_pre_imperial` inside `ARMY_MIX` both depend on age clicks, so this stays
   in scope. When `age_reliable` is 0, drop those terms for that player and
   renormalise their army mix rather than scoring junk.

2. **Stop treating missing data as average.** `_z` returns `0.0` for a `None`
   value, which is indistinguishable from "exactly match average". A partially
   parsed replay therefore produces a confident-looking mid score from absent
   data. Missing values must be excluded from the mix and the remaining weights
   renormalised.

3. **Pre-existing: never-reached-Imperial inflation.** `utils/replay_quiz/extract.py:15`
   documents that "before age X when never reached X counts the whole game", so a
   player who never clicks Imperial has `mil_pre_imperial == military`. Inside
   `ARMY_MIX` this inflates them on the 0.25 term relative to teammates who did
   reach Imperial — the player who stayed in Castle Age scores as though all their
   army were early. This already affects live scores and matters more now that army
   carries a larger share of impact. Fix by excluding the pre-Imperial term for
   players with no reliable Imperial click, consistent with fix 1.

4. **Hoist the roster fetch.** `build_match_cards_embed` and
   `build_match_analysis_embed` each call `_analysis_rows()` and `_team_names()`
   independently, and always post together — so the same queries run twice per
   post. This design adds several more reads (`cls_results`, `rs_player_techs`,
   `rs_player_events`, `cls_result_metrics`, `rs_player_apm`), which duplication
   would double as well. Fetch once in `post_match_analysis` and pass the rows to
   both builders.

## Error handling

- **No production data** (`villagers + military == 0`): render
  `⚠ partial replay data` in place of medals, tags and the stats line. Ranking a
  player last and showing them bare would read as "played badly" when the truth is
  "not measured". They are excluded from medal ranking entirely.
- **No strategy match**: omit the label. No placeholder.
- **No spawn metrics**: omit the phrase. No placeholder.
- **No peak eAPM rows** (pre-deploy match): omit the `(pk N)` suffix. **No stored
  average eAPM**: omit the eAPM term entirely. No placeholder in either case.
- **Any failure in the new queries** must degrade to the card without that signal,
  never drop the post. `post_match_analysis` already wraps everything in a
  try/except and returns False; per-signal fetches get the same treatment
  individually so one missing table cannot blank the whole card.

## Testing

`card_scoring.py` stays pure, DB-free and free of `core` imports (the same
discipline as `scoring.py`), so everything below is unit-testable in the existing
suite (547 tests, `tests/conftest.py` stubs the heavy imports):

- **Medal allocation**: top-three across the match, ties, fewer than three players
  with data, players excluded for missing data.
- **Tag allocation**: nobody qualifies, exactly one qualifies, several qualify with
  a clear winner, ties, one player sweeping multiple tags, a team where every
  player is below the bar.
- **Component mixes**: tech terms included, `age_reliable == 0` renormalisation,
  `None` values excluded rather than scored as average, never-reached-Imperial
  exclusion.
- **Bucketed coverage**: steady production, one long gap, batch-queued production,
  a timeline sparser than the totals.
- **`REQUIRED_COLUMNS`** must be updated and its existing guard test extended —
  `tests/test_replay_scoring.py` enforces that callers with explicit SELECT lists
  include every column the mixes read.

Rendering (`_player_card_line`, `_team_card_fields`) is tested for character budget
against a 4v4 with maximum-length nicknames and civs, and for the stats line in all
three eAPM states: average + peak, average only (no `rs_player_apm` rows), and no
eAPM at all.

## Risks

- **Calibration drift.** New component distributions mean every threshold moves. If
  calibration is skipped or rushed, tags will fire at unintended rates and the
  flatness could return in a new form.
- **Strategy label coverage is unknown.** No measurement yet of what share of
  players match at least one classification per match. If coverage is low, most
  cards will show no headline label and the card gets sparse. Now scheduled as
  pre-work step 1, before the implementation plan is finalized.
- **Medal scope is match-wide while tags are team-scoped.** Deliberate, but it is a
  mixed frame that may confuse readers. Worth revisiting after a few live matches.
