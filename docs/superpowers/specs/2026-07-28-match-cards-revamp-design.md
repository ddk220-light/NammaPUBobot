# Match Cards revamp — design

**Date:** 2026-07-28
**Surfaces:** `bot/post_game.py` (Match Cards embed), `bot/replay_stats/scoring.py`
**Status:** design agreed — **parked, sequenced second**

> **Sequencing.** This spec is deliberately held until the per-player eAPM pipeline
> ships. APM measures activity the production data cannot see — unit movement,
> micro, fighting — and adding it afterwards would mean designing this card twice.
> The APM work is a parser + schema change (`EXTRACT_VERSION` bump, new table,
> backfill decision) and gets its own spec; this one is then revised to place
> average and peak eAPM on the card and to reconsider which signals still earn
> their space once activity data exists.
>
> Everything below is agreed and should survive that revision, with the medal and
> tag sets the most likely to change.

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
- Changing the `Final Tale of the Tape` embed, the `What the Civs Say` embed, or
  the web dashboard's use of `scoring.py`.

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

### Calibration is required, not optional

The current `TH` values are percentile anchors derived from the existing component
distributions. Folding techs into army and eco changes those distributions, and
dropping timing changes the impact mix. **Every threshold must be re-derived**
against the live history (1,061 matches / 8,371 player-games) using
`utils/tag_calibration.py` before ship. Shipping the old thresholds against new
distributions would silently change how often every tag fires.

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
84 vils · 46 military · 14 farms · 3 TC
```

`villagers` and `military` come from `rs_player_games`; farms and TCs from
`rs_player_buildings` (counts only — that table stores no timestamps).

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
   84 vils · 46 military · 14 farms · 3 TC · spawned alone

• Sathish — Mongols · Scout rush
   ⚔ · `Constant production`
   61 vils · 52 military · 8 farms · 2 TC

• Ravi — Britons · Archer rush
   71 vils · 38 military · 11 farms · 2 TC

• Kumar — Mayans · ⚠ partial replay data
```

Three lines per player, roughly 110 characters each. A four-player team lands near
440 characters, comfortably inside Discord's 1024-character field cap. The existing
hard truncation at `[:1024]` stays as a backstop but should no longer be reachable;
if it ever triggers, drop the stats line before dropping a player.

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

## Correctness fixes in scope

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
   post. This design adds four more queries, which would double from 8 to 16.
   Fetch once in `post_match_analysis` and pass the rows to both builders.

## Error handling

- **No production data** (`villagers + military == 0`): render
  `⚠ partial replay data` in place of medals, tags and the stats line. Ranking a
  player last and showing them bare would read as "played badly" when the truth is
  "not measured". They are excluded from medal ranking entirely.
- **No strategy match**: omit the label. No placeholder.
- **No spawn metrics**: omit the phrase. No placeholder.
- **Any failure in the new queries** must degrade to the card without that signal,
  never drop the post. `post_match_analysis` already wraps everything in a
  try/except and returns False; per-signal fetches get the same treatment
  individually so one missing table cannot blank the whole card.

## Testing

`scoring.py` stays pure, DB-free and free of `core` imports, so everything below is
unit-testable in the existing suite (547 tests, `tests/conftest.py` stubs the heavy
imports):

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
against a 4v4 with maximum-length nicknames and civs.

## Risks

- **Calibration drift.** New component distributions mean every threshold moves. If
  calibration is skipped or rushed, tags will fire at unintended rates and the
  flatness could return in a new form.
- **Strategy label coverage is unknown.** No measurement yet of what share of
  players match at least one classification per match. If coverage is low, most
  cards will show no headline label and the card gets sparse. Measure before ship.
- **Medal scope is match-wide while tags are team-scoped.** Deliberate, but it is a
  mixed frame that may confuse readers. Worth revisiting after a few live matches.
