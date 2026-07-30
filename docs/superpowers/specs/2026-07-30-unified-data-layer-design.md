# Unified data architecture — design

**Date:** 2026-07-30
**Status:** DRAFT v3 — decisions folded in; naming + retention added.

## Decisions taken so far

- **One shared bot instance** serves every community. Multi-tenancy lives in the
  schema, not in packaging.
- **A community = one Discord server.** Player stats, identity, quiz, rollups
  and replay links are community-scoped. Queues, ratings and channel settings
  stay per-channel, exactly as PUBobot always did.
- **No backfill.** New derived layers populate forward as matches happen.
- The web dashboard's *viewing* layer may break during migration; it gets
  rethought from a community point of view afterwards. Core must not break.
- Goal restated: **only the data spaces we need, each with one dedicated writer,
  everything else removed.**
- **Labels: one namespace** with a `kind` column (strategy / spawn / behaviour).
- **Commentary: retired.** `bot_player_commentary` and its read path go; the
  scouting report keeps the persona line + generated read only.
- **Alt ratings: retired.** `/leaderboard_alternate`, `bot/alt_ratings.py`,
  `utils/compute_alt_ratings.py`, `data/alt_ratings.csv`. (The `namma_`-prefixed
  test commands are already gone from the code; the CLAUDE.md line claiming they
  exist is stale and gets fixed in the rename stage.)
- **Retention: aggressive, per-community** (§7). This community is the flagship
  and retains everything; partner communities keep only the durable spine +
  derived.
- **Community defaults: deferred.** Assume every feature enabled for now.
- **Everything gets renamed** (§8): the `qc_`/`rs_`/`cls_` patchwork is replaced
  by one plain-English naming scheme owned by this product.

---

## 1. Every feature, and the data behind it

The complete surface — commands, background jobs, and posts. `KEEP` = data
design unchanged (maybe re-keyed), `FOLD` = absorbed into the unified layers,
`RETIRE` = removed.

### 1.1 Core game loop

| feature | data it reads/writes | disposition |
| --- | --- | --- |
| queues, add/remove/pick, team draft | `qc_configs`, `pq_configs`, in-memory + `qc_saved_state` | KEEP |
| ratings, /report, leaderboard, decay | `qc_players`, `qc_matches`, `qc_player_matches`, `qc_rating_history`, `qc_match_id_counter` | KEEP |
| /expire (personal auto-remove) | legacy `players` table — **live**, written by `bot/commands/misc.py:86` | KEEP (rename `qc_user_prefs` in stage 6) |
| /noadds (queue bans) | `noadds` — live moderation, empty only because unused lately | KEEP |
| custom add-phrases | `qc_phrases` — live admin feature | KEEP |
| douche leaderboard | `qc_douche` — live admin feature | KEEP |
| elo_sync (parses legacy Pubobot result messages) | writes core tables | KEEP (writer of core) |
| disabled_guilds | declared, never read or written in this fork | **RETIRE** |

### 1.2 Match-adjacent features

| feature | data | disposition |
| --- | --- | --- |
| Tale of the Tape tease + payoff | reads core only (`qc_matches`, `qc_player_matches`) | KEEP — already clean |
| audience predictions | `qc_prediction_posts`, `qc_prediction_votes` — channel-scoped runtime state. Empty because the feature is new, not because it is dead. Votes live on Discord reactions until frozen, so the tables are the durable record | KEEP as-is |
| match cards + APM chart | `rs_*` raw + render-time scoring | FOLD reads onto derived-global |
| civ tracking + civ stats | `qc_match_civs` (raw), CSV seeds, `qc_civ_reconcile` | KEEP raw; FOLD stats into derived-community; retire CSVs |
| lobby watching / captain results | `qc_lobbies`, `qc_profile_map` | KEEP; `qc_profile_map` becomes part of identity (§4) |

### 1.3 Player-analysis features

| feature | data | disposition |
| --- | --- | --- |
| /rank scouting report | snapshot re-derived per request from `rs_*`/`cls_*` + `rs_player_personas` + `bot_player_commentary` | FOLD onto derived |
| /insights | `cls_*` | FOLD onto derived |
| /player_details | `rs_player_events` | KEEP (reads raw directly — per-match detail, not aggregate) |
| personas | `rs_player_personas`, refreshed at ingest | **RETIRE** (decided §2.5) |
| commentary | `bot_player_commentary`, generated on a laptop with an LLM | **RETIRE** (decided) |
| /leaderboard_alternate | `data/alt_ratings.csv`, baked by laptop script | **RETIRE** (decided) |

### 1.4 Content features

| feature | data | disposition |
| --- | --- | --- |
| daily quiz runtime | `qc_quiz_posts` / `qc_quiz_answers` / `qc_quiz_config` — already channel-scoped | KEEP |
| quiz content | `data/quiz_schedule.json` global bake + `replay_quiz.db` + second parser | game bank KEEP (global content); player bank + schedule FOLD to per-community jobs; SQLite + second parser RETIRE |
| /changelog | `data/changelog.json`, baked at build | KEEP (build artifact, not data) |

### 1.5 Web

The dashboard **owns only its auth tables** (`web_sessions`, `web_oauth_states`)
— everything else it shows is read from core/raw/derived, so there is no
redundant web-owned data to remove. The redundancy is in *how* it reads:
`player_overview_snapshot` re-derives impact profiles with six queries per page
view. Target: the web reads the same derived tables as every other consumer and
derives nothing itself. Its viewing layer is allowed to break during stages 3–5
and is redesigned community-first afterwards.

---

## 2. What the three heavy consumers need (unchanged from v1)

Scouting report, quiz and web consume the same per-(match, player) grain and
differ only in read pattern — rates vs extremes vs per-match detail. Field
matrix in appendix A. This is the argument for a single derived fact table.

### 2.5 The new scouting report is the standard — old derivations retire

The redesigned scouting report defines what per-player analysis *is*:

- **medal rates** — how often top-3 in military / villagers, per game played
- **APM** — median game eAPM, median peak eAPM
- **strategy / spawn / unit preferences** — what they do most, each with its
  win-loss split at declared sample floors

The pre-existing derivation stack is **not** carried into the new schema:

| old construct | fate |
| --- | --- |
| component scores (army/eco/timing/recovery, impact 0-100) | render-time code for the match cards only — never stored |
| behavioural tags ("Low-eco pressure", "Tech greedy", "Naked FC", …) as stored rows | not stored; the cards keep computing their descriptive/team tags at render from the match's own rows (verified: they never read the stored table) |
| personas (style × role, "Boomer · Board Topper") | retired with their store, backfill and calibration utils — their only surviving consumer was the old scouting report line this replaces |
| carry flag / team_top rate | not stored; the cards' crown stays render-time |
| stored `rs_player_game_tags` | dropped — its consumers were the web (being rethought) and persona tag rates (retired) |

What IS stored per player-game (`game_stats`) is exactly what the rollup needs
and what the retention sweep would otherwise destroy: medal places, peak eAPM,
average eAPM, the game's top units — captured at ingest while the full detail
still exists. The match cards read the stored medal places too, so medals are
computed once, in one writer, instead of twice.

---

## 3. Target architecture

### 3.1 Entities

**`community`** — one row per Discord server running the bot. Channels attach
to a community. Everything in §1.3–1.4 keys on `community_id`. Migration seeds
community #1 from the current server.

**Global vs community truth.** A parsed replay is a fact about the game;
"our match #1234 is that replay" is a fact about a community. The schema keeps
them apart:

```
                    GLOBAL                         PER COMMUNITY
core      —                                qc_* queues/ratings/config (per channel)
                                           community, community_channel
raw       replay facts (rs_*)              qc_match_civs*, qc_lobbies
          identity truth (profile<->user)
links     —                                match<->replay link, nick attribution
derived   player_game (scores, medals,     player_rollup, metric boards,
          labels — per parsed match)       civ stats, personas, quiz player bank
```

*`qc_match_civs` is written per bot-match today and stays community-keyed; it is
raw by recoverability (API history expires).

### 3.2 The layers by recovery contract

| layer | contract |
| --- | --- |
| **CORE** | irreplaceable, never rebuilt: match ledger, ratings, configs, feature state (quiz posts, predictions, lobbies, noadds, phrases, douche, saved state, user prefs) |
| **RAW** | append-only, never dropped: replay facts (1,346 replays already 404 upstream — unreproducible), civ observations, ingest bookkeeping |
| **LINKS** | small, precious: community match ↔ aoe2 match; community nick attribution |
| **DERIVED** | dropped and rebuilt freely; **the only layer feature code reads for analysis** |

`rs_matches.bot_match_id` (one nullable slot = one community per replay) is
replaced by a link table keyed `(community_id, bot_match_id) -> aoe2_match_id`.

### 3.3 Derived, concretely

**Derived-global** — computed once per parsed match, community-independent:

```
game_stats          (replay_match_id, player) -> medal places, avg eAPM,
                    peak eAPM, top units — the per-game facts the rollup
                    needs, captured before any retention sweep (§2.5)
game_labels         (replay_match_id, player, label) -> kind = strategy |
                    spawn; one namespace replacing cls_results. Behavioural
                    tags are NOT stored (render-time only, §2.5)
```

**Derived-community** — aggregates within one community:

```
player_rollups      (community_id, user_id) -> games, medal rates,
                    APM medians, strategy/spawn/unit frequencies with
                    win-loss splits
metric_boards       (community_id, metric) -> leaderboard + top games
                    (feeds quiz player bank and web)
civ_stats           (community_id, civ) -> games, winrate
```

One writer each: ingest writes derived-global; a per-community refresh job
writes derived-community. Nothing else writes derived, nothing reads raw for
analysis.

### 3.4 Identity

Global truth + community attribution, replacing five stores
(`player_profile_map.csv`, `profile_resolved.csv`, `rs_profiles`,
`qc_profile_map`, `replay_quiz.db players`):

```
identity            (profile_id) -> user_id, confidence, learned_from
identity_alias      (community_id, user_id) -> nick, aoe2_names
```

`qc_profile_map` already exists as the intended self-healing CSV replacement
(`bot/lobby/__init__.py` docstring) — the design completes that intent rather
than inventing a sixth store. Seeded from the CSVs once; CSVs then retired.

---

## 4. Dedicated writers (the "one script per space" rule)

| space | sole writer |
| --- | --- |
| core match/rating tables | `bot/stats` (as today) + elo_sync |
| raw replay facts | `bot/replay_stats/store.write_match` (as today) |
| raw civs | `bot/civ_matcher` + `bot/civ_sync` (as today) |
| links | match report hook (bot match side) + ingest (replay side) |
| derived-global | ingest, immediately after raw write |
| derived-community | one refresh job per community, triggered by ingest + nightly |
| identity | lobby watcher + ingest learning + one admin command for corrections |
| quiz player bank + schedule | per-community bot job reading `player_metric_board` |

Laptop pipelines eliminated: `replay_quiz` parser + SQLite, quiz bank/schedule
baking, identity CSV curation, civ stats CSVs, persona calibration (already at
target), alt-ratings bake (pending §6).

---

## 5. Stages

Each stage deploys alone and nothing depends on a later stage.

1. **Community entity + core renames + easy retirements.** `communities`,
   `community_channels`, the `match_replays` link table, the §8.1 core renames,
   and the already-decided removals that touch nothing structural: commentary,
   alt ratings, `disabled_guilds`, the stale CLAUDE.md line. Seed this server as
   community #1 with `retention='full'`. `rs_matches.bot_match_id` kept in
   place until stage 5, then dropped.
2. **Identity.** `identities` + `identity_aliases`, seeded from CSVs +
   `rs_profiles`; all readers cut over; CSVs retired.
3. **Derived-global.** `game_stats` + `game_labels` (one namespace, `kind`
   column) written at ingest. Populates forward only. Raw-layer renames ride
   this stage.
4. **Derived-community + retention.** `player_rollups`, `metric_boards`,
   `civ_stats`, the per-community refresh job, and the retention sweeper (§7).
5. **Consumers cut over**, one per deploy: scouting report → quiz (player bank
   job per community; delete `utils/replay_quiz` parser + SQLite) → match cards
   → web APIs. Old read paths deleted as each lands.
6. **Final retirements.** `cls_*` tables, `rs_player_game_tags`,
   `rs_player_personas`, `rs_profiles`, `qc_profile_map` (absorbed), CSV seeds,
   remaining old names.

Stage 3 forward-only means the scouting report and quiz start thin and grow.
That is accepted (decided above: no backfill) — but note the option exists to
re-run derived-global over the 1,126 already-parsed raw matches at any time,
because derived is rebuildable by construction. That is a *rebuild*, not a
backfill of missing raw data, and costs one script run if we ever want history.

---

## 6. Open decisions

1. **Naming scheme sign-off** (§8): the mapping is proposed, not yet approved.
2. **Community defaults**: deferred by decision — everything enabled until the
   partner-onboarding work begins.

## 7. Retention

**Policy is per-community**: `communities.retention` = `full` | `lean`.
This community is `full` (the flagship and test bed — keeps everything).
Partner communities default to `lean`.

**Kept forever, every community** — the durable spine:

- CORE, LINKS, identity — always.
- `replay_matches` + the scalar `replay_players` row (~5 KB/match): the
  measures every aggregate is built from.
- The label rows and both derived tiers.

**Swept for `lean` communities** — the bulky per-match children, after a
retention window (default 30 days):

- `replay_events` (~55 KB/match), `replay_techs` (~30 KB/match),
  `replay_buildings`, `replay_units`, `replay_apm`.

The sweeper only ever deletes rows whose match has already been consumed by
derived-global **and** the community rollup, and never touches a `full`
community. At 50 lean communities this is ~27 MB/month kept instead of
~550 MB/month.

**The honest consequence.** Derived is "freely dropped and rebuilt" only while
the raw beneath it exists. For a lean community, once the sweep passes, the
rollup's unit/timing aggregates are *incrementally maintained, not rebuildable*
— the derived row becomes the only copy. Two feature limits follow, both
accepted: `/player_details` and the APM chart work only within the window for
lean communities (both post right after ingest, so in practice this costs
nothing), and a future "recompute all rollups" applies to lean communities only
from their window forward.

## 8. Renaming — making the schema this product's own

`qc_` is PUBobot2's prefix for QueueChannel; `rs_` was bolted on for replay
stats, `cls_` for the classifier, `pq_` for pickup queues. Four generations of
patchwork. The scheme below replaces all of it with **plain-English domain
names** — no cryptic prefixes to decode. The layer contract (core / raw /
derived) is not encoded in table names; it is declared in one place, a
`data_registry` module listing every table's layer, sole writer, and retention
class. Names say *what it is*; the registry says *how it is treated*.

### 8.1 Table mapping

| today | becomes | note |
| --- | --- | --- |
| `qc_matches` | `matches` | col `at` → `reported_at` |
| `qc_player_matches` | `match_players` | fixes the swapped words |
| `qc_players` | `player_ratings` | it is the per-channel rating row |
| `qc_rating_history` | `rating_history` | |
| `qc_match_id_counter` | `match_counter` | |
| `qc_configs` | `channel_settings` | |
| `pq_configs` | `queue_settings` | |
| `qc_saved_state` | `bot_state` | |
| `players` | `player_prefs` | the /expire store, finally named honestly |
| `noadds` | `queue_bans` | |
| `qc_phrases` | `player_phrases` | |
| `qc_douche` | `douche_log` | the name IS the brand — keep it |
| *(new)* | `communities`, `community_channels` | |
| `rs_matches` | `replay_matches` | |
| `rs_player_games` | `replay_players` | |
| `rs_player_units` / `_techs` / `_buildings` / `_events` / `_apm` | `replay_units` / `replay_techs` / `replay_buildings` / `replay_events` / `replay_apm` | |
| `rs_ingest` | `replay_ingest` | |
| `rs_config` | folded into `communities` / registry | |
| `qc_match_civs` | `civ_picks` | |
| `qc_civ_reconcile` | `civ_reconcile` | |
| `qc_lobbies` | `lobbies` | |
| *(new link)* | `match_replays` | replaces `rs_matches.bot_match_id` |
| `rs_profiles` + `qc_profile_map` + CSVs | `identities`, `identity_aliases` | §3.4 |
| `cls_results` | `game_labels` | one namespace, `kind` = strategy \| spawn |
| `cls_result_metrics` | evidence json on `game_labels` | |
| `rs_player_game_tags` | **dropped** | behavioural tags become render-time only (§2.5) |
| `rs_player_personas` | **dropped** | personas retired (§2.5) |
| *(new derived)* | `game_stats`, `player_rollups`, `metric_boards`, `civ_stats` | |
| `qc_quiz_posts` / `_answers` / `_config` | `quiz_posts` / `quiz_answers` / `quiz_settings` | |
| `qc_prediction_posts` / `_votes` | `prediction_posts` / `prediction_votes` | |
| `web_sessions` / `web_oauth_states` | unchanged | already clean |
| `disabled_guilds`, `cls_classifications`\*, `cls_data_requirements`\*, `cls_player_totals`, `cls_match_ingest`, `bot_player_commentary` | **dropped** | \*definitions move into the classifier code, which is where they are edited anyway |

### 8.2 Column conventions

- keys: `community_id`, `channel_id`, `match_id` (bot match),
  `replay_match_id` (today's `aoe2_match_id`), `user_id` (Discord),
  `profile_id` (AoE2), `player_number` (seat in the replay)
- `bot_match_id` disappears — with the link table there is no ambiguity left
  for it to resolve
- timestamps are `*_at` epoch ints, always named for the event
  (`reported_at`, `parsed_at`, `computed_at`)
- the `on_dublicate` typo in the DB adapter is fixed in passing

### 8.3 Mechanics

`RENAME TABLE` in MySQL is atomic and instant. Each stage renames the tables it
touches **in the same deploy** that moves their `ensure_table` declarations and
readers — never a separate big-bang rename, which would leave a window where
ensure_table recreates the old names. Core renames land in stage 1 (they gate
everything else); the rest ride their stage.

---

## Appendix A — consumer field matrix

(unchanged from v1; abbreviated)

All three consumers read the per-(match, player) grain: identity, civ, team,
winner; villagers/military totals + per-age splits; units by name/category;
buildings; age timings + reliability; eAPM average + peak; strategy/spawn
labels; medals; match map/duration/date. Scouting report additionally needs
per-player *rates* (medal rate, win-rate by strategy/spawn/unit at declared
sample floors); quiz needs *leaderboards and single-game extremes*; web needs
*per-match detail rows*. None needs a field the others lack — only different
aggregations of the same row.
