# Unified data layer — working document

**Date:** 2026-07-30
**Status:** DRAFT FOR DISCUSSION. Nothing here is agreed. No implementation until it is.

## What this document is

An honest inventory of every data store the project maintains, a field-level
comparison of what the three consumers actually need, and a proposed three-layer
structure to replace the current sprawl.

It exists to be argued with. The open questions at the end are real.

---

## 1. Current state

**43 MySQL tables, 174 MB, plus a SQLite file, plus baked JSON, plus CSV seeds.**
Measured read-only against production on 2026-07-30.

No orphans — every table in the database is declared somewhere in the repo.

### 1.1 By subsystem

| group | tables | rows | size |
| --- | --- | --- | --- |
| elo / matches | `qc_matches` 3,312 · `qc_player_matches` 25,565 · `qc_players` 100 · `qc_rating_history` 27,463 · `qc_match_id_counter` 1 | 56k | 7 MB |
| config / state | `qc_configs` 1 · `pq_configs` 1 · `qc_saved_state` 1 | 3 | 48 KB |
| civs | `qc_match_civs` 16,600 · `qc_civ_reconcile` 491 | 17k | 3 MB |
| replay facts | `rs_matches` 1,126 · `rs_player_games` 8,885 · `rs_player_units` 31,046 · `rs_player_techs` 205,466 · `rs_player_buildings` 117,892 · `rs_player_events` 591,099 · `rs_player_apm` 0 · `rs_ingest` 2,479 · `rs_profiles` 89 · `rs_config` 1 | 958k | **114 MB** |
| replay labels | `rs_player_game_tags` 34,915 · `rs_player_personas` 252 | 35k | 21 MB |
| classifications | `cls_results` 32,045 · `cls_result_metrics` 111,995 · `cls_classifications` 29 · `cls_data_requirements` 128 · `cls_player_totals` 97 · `cls_match_ingest` 370 | 145k | 21 MB |
| quiz runtime | `qc_quiz_posts` 50 · `qc_quiz_answers` 424 · `qc_quiz_config` 1 | 475 | 144 KB |
| lobby | `qc_lobbies` 191 · `qc_profile_map` 0 | 191 | 64 KB |
| commentary | `bot_player_commentary` 34 | 34 | 448 KB |
| web | `web_sessions` 1 · `web_oauth_states` 0 | 1 | 32 KB |
| **dead** | `players` · `disabled_guilds` · `noadds` · `qc_phrases` · `qc_douche` · `qc_prediction_posts` · `qc_prediction_votes` | **0** | 112 KB |

### 1.2 Outside MySQL

| store | contents | written by |
| --- | --- | --- |
| `data/replay_quiz.db` (8.7 MB SQLite) | `matches` 350 · `facts` 2,778 · `units` 14,242 · `techs` 64,453 · `buildings` 37,228 · `players` 50 · `leaderboards` 1,779 · `metrics` 64 · `metric_top_games` 186 · `question_bank` 2,424 | `utils/replay_quiz/build_db.py` (manual) |
| `data/*.json` (~5.8 MB) | `quiz_bank`, `quiz_bank_player`, `question_bank`, `quiz_schedule`, `quiz_taste`, `quiz_archetypes`, `changelog` | offline generators |
| `data/*.csv` (~3.7 MB) | `player_profile_map`, `profile_resolved`, `player_civ_stats`, `civ_elo_stats`, plus Apr-2026 exports of four core tables | mixed: hand-maintained and generated |
| `data/replays/` (32 MB, gitignored) | 10 `.aoe2record` files | scratch download dir, not an archive |

### 1.3 The two facts that constrain everything

**Replay facts are unreproducible.** Of 2,479 ingest attempts, 1,353 gave up and
**1,346 of those are `unavailable:http_404`** — the replay expired upstream. The
1,126 parsed matches (34% of 3,312) are a permanent ceiling for history. Raw
replay data can never be regenerated and must never be dropped.

**There are two independent replay parsers.** `bot/replay_stats/parse.py` writes
MySQL `rs_*` live at ingest; `utils/replay_quiz/extract.py` calls
`mgz.model.parse_match` separately into SQLite, with its own reimplementation of
the eAPM bucket filter. Same source files, same schema shape, two code paths, two
stores — and the quiz is built from the smaller, staler one (350 matches vs
1,126).

**Not a problem:** `rs_player_apm` is empty because the `+4` parser bump that
emits it is new and no match has been parsed since. Expected. With no backfill,
APM aggregates simply start accumulating forward from the next parse.

---

## 2. What the three consumers actually need

The point of this section: **they are aggregates of one fact row.**

### 2.1 Field-by-field

`S` = scouting report (as redesigned), `Q` = quiz player bank, `W` = web profile
and match cards. `•` = needs it, `(•)` = would naturally want it but doesn't use
it today.

| field | S | Q | W | source today |
| --- | :-: | :-: | :-: | --- |
| **identity** | | | | |
| user_id / profile_id / identity | • | • | • | `rs_player_games`, `rs_profiles` |
| civ | • | • | • | `rs_player_games`, `qc_match_civs` |
| team, winner | • | • | • | `rs_player_games`, `qc_matches` |
| rating, deviation | | • | • | `qc_players` |
| **economy** | | | | |
| villagers | • | • | • | `rs_player_games` |
| vil_pre_feudal / castle / imperial | • | • | • | `rs_player_games` |
| farms, town centers | (•) | • | • | `rs_player_buildings` |
| **military** | | | | |
| military | • | • | • | `rs_player_games` |
| mil_pre_feudal / castle / imperial | • | • | • | `rs_player_games` |
| units by name + category | • | • | • | `rs_player_units` |
| units by age span | | • | | `rs_player_units` |
| military buildings by type | | • | | `rs_player_buildings` |
| **timing** | | | | |
| feudal_s / castle_s / imperial_s | (•) | • | • | `rs_player_games` |
| first_tc_s | | • | | `rs_player_games` |
| age_reliable | • | • | • | `rs_player_games` |
| tech click_s by tech | | • | (•) | `rs_player_techs` |
| **activity** | | | | |
| eapm (game average) | • | (•) | • | `rs_player_games` |
| peak eapm | • | (•) | • | `rs_player_apm` (derived) |
| production coverage | (•) | | • | `rs_player_events` |
| **labels** | | | | |
| strategy keys | • | (•) | • | `cls_results` |
| spawn keys | • | (•) | • | `cls_results` |
| behavioural tags | (•) | | • | `rs_player_game_tags` |
| **scores / awards** | | | | |
| army / eco / timing / recovery / impact | (•) | | • | computed, not stored |
| **medals (place 1-3 per axis)** | **•** | (•) | • | **computed at render, discarded** |
| team_top (carry) | • | | • | computed, not stored |
| persona (style × role) | (•) | | • | `rs_player_personas` |
| **match context** | | | | |
| map, duration_s, played_at | | • | • | `rs_matches` |

### 2.2 What each consumer does with it

The fields are shared; the **read patterns** differ, and that is the only real
difference between the three.

| consumer | read pattern | example |
| --- | --- | --- |
| scouting report | **rates** — per-player aggregate ÷ games played | "medals in 34% of games", "median peak 91 eAPM", "wins 71% when scout rushing" |
| quiz | **extremes** — leaderboards + per-game top-N | "who averages the most villagers", "biggest army in a single game" |
| web / cards | **per-match detail** + per-player aggregate | one card per player this match; profile page averages |

All three read the same grain. None of them needs a different fact table.

### 2.3 The unified grain

One row per **(match, player)** carrying every measure, plus long-form children
for the repeating dimensions:

```
player_game        (match, player) -> all scalar measures + scores + medals
player_game_unit   (match, player, unit)     -> counts, by age span
player_game_tech   (match, player, tech)     -> click_s, phase
player_game_bldg   (match, player, building) -> count
player_game_label  (match, player, label)    -> strategy | spawn | behavioural
player_game_apm    (match, player, minute)   -> actions
```

Scouting report reads aggregates. Quiz reads leaderboards over the same
aggregates. Web reads single matches and per-player averages. One parse, one
write, three read patterns.

---

## 3. Proposed layers

Three tiers, distinguished by **how they can be recovered if lost** — the only
property that actually matters for how carefully we treat them.

### CORE — system of record. Irreplaceable. Never dropped, never rebuilt.

Cannot be derived from anything. If this is lost, the bot cannot function and
the community's history is gone.

| table | what it is |
| --- | --- |
| `qc_matches` | the match ledger — who played whom, when, who won |
| `qc_player_matches` | per-player participation and side |
| `qc_players` | ratings, deviation, W/L/D, streak |
| `qc_rating_history` | every rating change with reason |
| `qc_match_id_counter` | match id allocation |
| `qc_configs` | **per-Discord-channel settings** — 33 config variables |
| `pq_configs` | **per-queue settings** — 29 config variables |
| `qc_saved_state` | live queue/match state across restarts |
| *(new)* `identity` | the resolved person ↔ profile ↔ in-game-name map (see §5) |

### RAW — externally sourced facts. Append-only. Never dropped.

Observations fetched from outside the bot that cannot be re-fetched later.
Distinct from CORE only in that the bot doesn't need them to run.

| table | why it is raw, not derived |
| --- | --- |
| `rs_matches`, `rs_player_games`, `rs_player_units`, `rs_player_techs`, `rs_player_buildings`, `rs_player_events`, `rs_player_apm` | replays expire upstream — 404 for 1,346 of them already |
| `qc_match_civs` | civ assignments from the aoe2companion API, whose history window is limited |
| `qc_lobbies` | lobby observations, only visible while the lobby is live |
| `rs_ingest` | ingest bookkeeping; cheap, and the record of what is permanently unavailable |

### DERIVED — everything computable from CORE + RAW. Freely dropped and rebuilt.

**This is the common layer.** Every consumer reads here, not from RAW.

| table | contents |
| --- | --- |
| `player_game` | the unified fact row of §2.3: measures + scores + **medals** + carry flag |
| `player_game_label` | one label vocabulary replacing `cls_results` **and** `rs_player_game_tags` |
| `player_rollup` | per-player aggregates: award rates, medians, strategy/spawn/unit frequencies with win-loss splits, persona |
| `player_metric_board` | leaderboards + per-game extremes for the quiz |

Rebuildable by definition, so a schema change here is a re-run, not a migration.
Medals move here from render-time: a medal is a rank against the other seven
players in the match, so it can only be computed when the whole match is in hand.

---

## 4. Table-by-table disposition

### Retire — dead, 0 rows, no consumer

`players`, `disabled_guilds`, `noadds`, `qc_phrases`, `qc_douche` — legacy
PUBobot2 features never used in this fork. `players` is read once at
`bot/queue_channel.py:484` for a `personal_expire` column nothing writes.

### Retire — consolidate into DERIVED

| table | into | note |
| --- | --- | --- |
| `cls_results` | `player_game_label` | 29 keys; `luck_baseline` fires 7,936 times on every player of every valid game, which is why the card carries a hardcoded 17-key allowlist to avoid rendering "All valid spawns (baseline)" as a strategy |
| `rs_player_game_tags` | `player_game_label` | ~25 behavioural tags at the same grain, denormalizing the same `winner`/`civ`/`team`/`played_at` |
| `cls_result_metrics` | `player_game` / evidence | 112k rows of classifier evidence |
| `cls_player_totals` | `player_rollup` | 97 rows, trivially recomputable |
| `rs_player_personas` | `player_rollup` | one field of a bigger rollup |
| `data/replay_quiz.db` | RAW + `player_metric_board` | retires the second parser entirely |

### Keep as-is

`qc_quiz_posts` / `qc_quiz_answers` / `qc_quiz_config` (runtime state, not
analysis), `web_sessions` / `web_oauth_states` (auth), `qc_civ_reconcile`
(bookkeeping), `cls_classifications` / `cls_data_requirements` (definition
registry, not data).

### Decide

`qc_prediction_posts` / `qc_prediction_votes` — 0 rows, feature never used, but
`/rank` queries it on every invocation and `finish_match` resolves it on every
report. Keep dormant or retire?

`rs_player_events` (591k rows, 62 MB) and `rs_player_techs` (205k rows, 33 MB)
are 55% of the database. Events power production coverage and the growth chart;
techs power tech-timing quiz metrics and some tags. Both are legitimately used —
but worth an explicit decision on retention windows.

`rs_player_buildings` — 118k rows, 16 MB, every building type stored, and the
match card reads exactly two: `Farm` and `Town Center`. The quiz reads five more.

---

## 5. Identity is the cross-cutting problem

There are **five** representations of "who is this person", and identity is the
join key for every table above:

| where | what | rows |
| --- | --- | --- |
| `data/player_profile_map.csv` | hand-maintained nick/user_id → profile_ids, read at runtime by `bot/civ_matcher.py` | — |
| `data/profile_resolved.csv` | generated by `utils/replay_quiz/attribution.py` | — |
| `rs_profiles` | profile_id → user_id, learned at ingest | 89 |
| `qc_profile_map` | the lobby's own mapping | **0** |
| `replay_quiz.db players` | the quiz's own copy | 50 |

Any unification that doesn't fix this will re-fragment, because each subsystem
will keep resolving identity its own way. Proposed: one CORE `identity` table as
the single resolver, seeded from the CSVs once and learned thereafter.

---

## 6. Open questions

1. **Label vocabulary.** `cls_results` (strategy/spawn/luck) and
   `rs_player_game_tags` (behavioural) merge into one table — but do they share
   one vocabulary with a `kind` discriminator, or stay separate vocabularies in
   one table? Affects every consumer's query shape.
2. **Rebuild trigger.** Is DERIVED rebuilt incrementally at ingest (like
   `rs_player_personas` today), by a scheduled full recompute, or both?
3. **Predictions.** Retire or keep dormant?
4. **Retention.** Do `rs_player_events` and `rs_player_techs` keep everything
   forever, or roll up past a window?
5. **Migration order.** Build DERIVED alongside the existing tables and cut
   consumers over one at a time, or cut over at once?
6. **Statistical floors.** The strategy × spawn × unit win/loss split is a
   ~500-cell contingency table. With 42 players averaging 203 parsed games
   (min 6, max 588), single-factor splits are comfortable, two-way is thin,
   three-way has well under one game per cell. Where do we set the floor, and
   do we surface confidence?
