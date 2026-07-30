# Unified data architecture — design

**Date:** 2026-07-30
**Status:** DRAFT v2 — feature-complete audit + community model. Discussion open.

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
| personas | `rs_player_personas`, refreshed at ingest | FOLD into community rollup |
| commentary | `bot_player_commentary`, generated on a laptop with an LLM | DECIDE (§6) |
| /leaderboard_alternate | `data/alt_ratings.csv`, baked by laptop script | DECIDE (§6) |

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
player_game         (aoe2_match_id, player) -> all scalar measures,
                    component scores, medals (rank vs the 8 in THIS game),
                    carry flag
player_game_label   (aoe2_match_id, player, label) -> one vocabulary
                    replacing cls_results AND rs_player_game_tags
```

**Derived-community** — aggregates within one community:

```
player_rollup       (community_id, user_id) -> games, medal rates,
                    APM medians, strategy/spawn/unit frequencies with
                    win-loss splits, persona
player_metric_board (community_id, metric) -> leaderboard + top games
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

1. **Community entity + links.** `community`, `community_channel`, the
   match↔replay link table. Seed community #1. `rs_matches.bot_match_id`
   kept in place until stage 5, then dropped.
2. **Identity.** `identity` + `identity_alias`, seeded from CSVs +
   `rs_profiles`; all readers cut over; CSVs retired.
3. **Derived-global.** `player_game` + `player_game_label` written at ingest.
   One label vocabulary decision executed here (§6). Populates forward only.
4. **Derived-community.** `player_rollup`, `player_metric_board`, `civ_stats` +
   the per-community refresh job.
5. **Consumers cut over**, one per deploy: scouting report → quiz (player bank
   job per community; delete `utils/replay_quiz` parser + SQLite) → match cards
   → web APIs. Old read paths deleted as each lands.
6. **Retirements.** `disabled_guilds`, `cls_*` data tables, `rs_player_game_tags`,
   `rs_player_personas`, `rs_profiles`, `qc_profile_map` (absorbed), CSV seeds,
   `players` → `qc_user_prefs` rename.

Stage 3 forward-only means the scouting report and quiz start thin and grow.
That is accepted (decided above: no backfill) — but note the option exists to
re-run derived-global over the 1,126 already-parsed raw matches at any time,
because derived is rebuildable by construction. That is a *rebuild*, not a
backfill of missing raw data, and costs one script run if we ever want history.

---

## 6. Open decisions

1. **Label vocabulary** (stage 3): one namespace with `kind`
   (strategy/spawn/behaviour) or separate vocabularies in one table.
2. **Commentary**: per-community LLM generation can't self-serve inside the
   bot. Keep as optional operator-run batch, or retire the feature?
3. **Alt ratings**: retire `/leaderboard_alternate` + its CSV, or fold the
   what-if computation into the derived refresh job?
4. **Retention for `rs_player_events`/`rs_player_techs`** (55% of DB size,
   ~90 KB/match): keep forever vs roll up past a window. At 50 communities ≈
   5,500 matches/month ≈ 550 MB/month if kept verbatim.
5. **Community defaults**: what a fresh server gets on install (quiz off by
   default? which features are opt-in?).

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
