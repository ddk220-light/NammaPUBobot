# Match Cards revamp — readiness and considerations

**Written:** 2026-07-29
**Status:** pre-implementation. Nothing built yet.
**Design spec:** [docs/superpowers/specs/2026-07-28-match-cards-revamp-design.md](superpowers/specs/2026-07-28-match-cards-revamp-design.md)

> **Decision update (2026-07-29).** Part 2 (the replay archive, §10–17) is
> **dropped**. The card work is strictly forward-looking: no archive, no backfill,
> no bulk download. Read-only access to the live Railway DB was authorized for the
> card's pre-work, which resolves the constraints Part 2 existed to work around —
> coverage measurement, calibration and preview all run as SELECT-only live-DB
> reads with results committed to the repo. The open questions in §7 are resolved
> in the revised spec (eAPM on the stats line as average + peak, display-only;
> stored values shown as-is for early-eliminated players; medals re-confirmed on
> raw counts; strategy coverage measured before the implementation plan is
> finalized). Part 2 is retained below for historical context only. The
> `download.py` resume bug (§15) is tracked as its own task, outside this work.

This document is the "what do we actually need, and what could bite us" pass before an
implementation plan gets written. The design decisions themselves are settled and live in
the spec — this covers **data availability, environment, calibration, blockers, and the
decisions still outstanding.**

It has two parts:

- **Part 1 (§1–9)** — the card revamp itself: where each signal lives, what has to be built,
  what could go wrong.
- **Part 2 (§10–15)** — the **replay archive**, which turned out to be a prerequisite for
  calibrating and previewing the card. Neither machine has a local replay cache, and
  re-downloading from aoe.ms every time is not viable.

---

## 1. Where the data actually lives

This is the part that needed investigating. Every signal the new card shows, and whether we
can currently reach it.

| Card element | Source | Reachable offline? | Notes |
|---|---|---|---|
| Villagers, military, age splits | `rs_player_games` (live) / derivable from `units` (offline) | **Partly** | Offline requires summing `units` by `is_military` / `unit='Villager'` with the `pre_*` columns — the totals are not stored as columns in the committed DB |
| Winner, team | `qc_matches` / `qc_player_matches` (live), **and** committed CSVs | **Yes** | `data/qc_matches.csv` (2,920 rows, `winner_team`), `data/qc_player_matches.csv` (22,479 rows, `team`) |
| bot_match_id ↔ aoe2_match_id | `rs_matches` (live), **and** committed CSV | **Yes** | `data/match_id_map.csv`, 2,648 pairs |
| Upgrade research (army/eco tech terms) | `rs_player_techs` (live) / `techs` (offline) | **Yes** | 64,453 rows offline, with `click_s` and `phase` |
| Farms, TCs | `rs_player_buildings` (live) / `buildings` (offline) | **Yes** | 37,228 rows offline, counts only — **no timestamps anywhere** |
| Age-up times | `rs_player_games.{feudal,castle,imperial}_s` (live) / derivable from `techs` (offline) | **Partly** | Offline: the age techs carry `click_s`. Note timing leaves the *score* but age data is still needed for the pre-age army splits |
| Production continuity | `rs_player_events` (live only) | **No** | Not in the committed DB. Needs the live DB or a re-parse |
| **Strategy label** | `cls_results` (live only) | **No** | Not in the committed DB. Computing it offline needs `classify_game`, which takes **extract output** — i.e. a raw replay, not a DB row |
| Spawn context | `cls_result_metrics` (live only) | **No** | Same constraint as strategy. Also gated: Nomad, 6/8 players, balanced result |
| **Average eAPM** | `rs_player_games.eapm` (live) / `players` has no per-match eapm (offline) | **No** | Offline `players` table is a 50-row registry (rating/games), not per-match |
| **Peak eAPM** | `rs_player_apm` (live only, forward-only) | **No** | Only exists for matches ingested after the eAPM deploy |

### The two things that are genuinely unavailable offline

**Strategy and spawn.** Both come from `cls_*`, which the committed dataset does not include,
and both are computed from extract output rather than from stored rows — so reproducing them
means re-parsing a raw replay. Since strategy is the card's *headline label*, a fully faithful
offline preview is not possible without raw replays.

**eAPM.** Average comes from a live column; peak needs `rs_player_apm`, which is forward-only
and therefore empty for every historical match.

---

## 2. Answering the database question

**There is a live online database.** `config.cfg` points `DB_URI` at Railway MySQL —
`shuttle.proxy.rlwy.net:10509/railway`. That is the production database the bot writes `rs_*`,
`cls_*` and `qc_*` to. Credentials are in the local `config.cfg` (not committed). I have not
connected to it and will not without you saying so explicitly.

**There is also a committed offline dataset** — this is the GitHub location you were thinking of:

- **`data/replay_quiz.db`** — 8.3 MB SQLite, **tracked in git**. 350 parsed matches:
  `matches` (350), `units` (14,242), `techs` (64,453), `buildings` (37,228), plus quiz tables.
- **Tracked CSVs** carrying the bot-side roster: `qc_matches.csv`, `qc_player_matches.csv`,
  `qc_players.csv`, `qc_rating_history.csv`, `match_id_map.csv`, `profile_resolved.csv`,
  `player_profile_map.csv`.

**Raw replay files are NOT published.** `data/replays/` is gitignored, as are
`data/analysis.db`, `data/.replay_extract_cache/` and the profile cache. So the `.aoe2record`
files exist only on machines that downloaded them — worth checking your other PC, since a
populated cache there would remove the biggest constraint above.

Relevant caveat found during the eAPM work: **`utils/replay_quiz/download.py --limit N`
currently downloads zero files on a fresh checkout** ([download.py:188](../utils/replay_quiz/download.py:188)) —
the todo filter trusts the git-tracked manifest without checking whether the untracked cache
still holds the file. Anyone rebuilding a replay cache from scratch hits this first. Fix this
before relying on it.

---

## 3. What has to be built

Per the spec's fork decision — `scoring.py` is **not** touched, so nothing downstream of it
(the stored `rs_player_game_tags` table, the web profile API, the tag leaderboard, or the
Tale of the Tape embed) changes.

| Unit | Responsibility |
|---|---|
| `bot/replay_stats/card_scoring.py` (new) | Two-component model (army/eco with tech terms), medal allocation, shape-tag allocation, continuity. Pure — no DB, no `core` imports |
| `bot/post_game.py` (modify) | `build_match_cards_embed` switches to `card_scoring`; new render for medals / strategy / stats line / spawn. **Uses TABS** |
| Card-side queries | Reads for `cls_results`, `rs_player_techs`, `rs_player_buildings`, `rs_player_events`, `cls_result_metrics`, `rs_player_apm` |
| `utils/tag_calibration.py` (modify) | Currently imports `scoring.py` **by path** ([line 34](../utils/tag_calibration.py:34)); needs a parameter or variant to target `card_scoring.py` |
| Tests | Medal allocation, tag allocation, component mixes, continuity, plus the `REQUIRED_COLUMNS` guard |

---

## 4. Calibration is a prerequisite, not a follow-up

`card_scoring.py` cannot inherit `scoring.py`'s `TH` values. Those are percentile anchors for a
three-component mix with no tech terms; the new model drops timing and folds in upgrades, so
every distribution moves. Thresholds must be re-derived against the live history (1,061 matches
/ 8,371 player-games) **before** the card ships, or tags fire at unintended rates.

Because the existing module is untouched, this calibration is purely additive — it cannot
change how often any tag fires on the surfaces we don't own.

**Consideration:** calibration reads the live DB. If we want it reproducible offline, the
committed 350-match dataset is a smaller sample (~1/3), and lacks continuity and strategy
entirely. Decide whether calibration is a live-DB operation or whether we snapshot more data
into the repo first.

---

## 5. Carried over from the eAPM work

Three things that now bear directly on the card:

1. **`mean_active` is not the stored eAPM.** `apm_query.apm_series` returns `mean_active`,
   whose denominator is the last minute with any action — *not* whole game minutes. It was
   renamed from `mean` during final review precisely because the card is the consumer that
   would have mistaken it for the parity-preserving average. **The card must read average eAPM
   from `rs_player_games.eapm`** and take only *peak* from the buckets.
2. **mgz's eAPM deflates anyone eliminated early**, because it divides every player's actions
   by the full game duration. Reviewed and accepted for the graph, where a line falling to zero
   is honest. On a card, a bare "avg eAPM" number for a player who died at minute 15 is more
   misleading — decide how to present it.
3. **Peak eAPM is forward-only.** Historical matches have no `rs_player_apm` rows, so the card
   must render without it rather than showing a zero or a blank.

---

## 6. Known correctness issues the card path must fix

All three apply to the card path only; they stay live in `scoring.py` by design.

1. **`age_reliable` is ignored on the card path.** Not even SELECTed, while the web profile
   correctly excludes unreliable ages ([query.py:82](../bot/replay_stats/query.py:82)). Timing
   leaves the score, but `mil_pre_castle` and `mil_pre_imperial` inside the army mix both
   depend on age clicks.
2. **`_z` scores missing data as match-average.** A `None` is indistinguishable from "exactly
   average", so a partial replay yields a confident mid score from absent data.
3. **Never-reached-Imperial inflation.** `extract.py` documents that "before age X when never
   reached X counts the whole game", so a player who never clicked Imperial has
   `mil_pre_imperial == military` — the Castle-Age turtle scores as though their whole army
   were early. Matters more now that army carries a larger share.

---

## 7. Open questions needing a decision

1. **How do we preview?** Options: (a) build from the committed 350-match DB, accepting that
   strategy, spawn and eAPM will be absent or stubbed; (b) point at the live Railway DB for a
   faithful preview; (c) re-parse raw replays locally, which yields strategy and spawn but
   needs a populated replay cache. Depends partly on what's on your other PC.
2. **Is calibration a live-DB operation**, or do we snapshot more history into the repo first?
3. **How is average eAPM presented** for a player eliminated early, given the whole-game
   denominator?
4. **Does the card show anything when strategy coverage is empty?** Unmeasured — see risks.
5. **Do the medals rank on the eAPM-aware model or on raw counts?** The spec says raw counts
   ("who made the most"), deliberately distinct from the impact score. Worth re-confirming now
   that activity data exists.

---

## 8. Risks

- **Strategy coverage is unmeasured.** Nobody has checked what share of players match at least
  one of the 17 classifications per match. If coverage is low, the card's headline label is
  usually blank and the design gets sparse. **Measure this before building around it** — it is
  the cheapest way to invalidate a core design assumption.
- **Calibration drift.** If calibration is skipped or rushed, the flatness the revamp exists to
  fix could return in a new form.
- **Character budget.** Three lines per player at ~110 characters is ~440 for a 4-player team,
  inside Discord's 1024-char field cap — but adding eAPM to the stats line eats into that. The
  existing hard truncation at `[:1024]` must stay as a backstop, and should drop the stats line
  before dropping a player.
- **Scope mixing.** Medals are match-wide while tags are team-scoped. Deliberate, but a mixed
  frame that may need revisiting after live use.
- **No combat data, still.** Every signal remains production volume, timing, investment or
  activity. Nothing here measures whether a fight was won. Tag and medal naming must not imply
  otherwise.

---

## 9. Suggested order

1. Measure strategy coverage against the live DB — it can invalidate the headline design.
2. Build the replay archive (Part 2) — calibration and preview both depend on it.
3. Produce a preview for ~5 recent matches from the archive.
4. React to the preview; amend the spec if the design doesn't survive contact.
5. Write the implementation plan.
6. Calibrate, then build.

Step 1 before step 5 is the important ordering. Everything else can move.

---
---

# Part 2 — Replay archive

## 10. Why this is needed at all

**The card does not need historical replays to operate.** It renders per-match at ingest, from
that match's own freshly-parsed data. Nothing about the running feature depends on an archive.

The archive is needed for three other things:

1. **Calibration** — a hard prerequisite (§4). Deriving thresholds needs a large sample.
2. **Preview** — seeing the design against real matches before committing to build it.
3. **Optional backfill** — retroactive `rs_player_apm`, `cls_*`, or any future extract field.

There is a second-order benefit worth naming: the eAPM spec chose **forward-only, no backfill**
specifically because re-downloading ~1,000 matches from aoe.ms was ~44 hours of sweeping with a
fifth to a third permanently unavailable. **Once an archive exists, that cost disappears** and
the no-backfill decision becomes cheap to revisit — retroactive charts on the web profile and
`/player_details` would become a re-parse rather than a re-download.

## 11. Scope — which matches, and how we identify "ranked"

The set is *ranked NammaNomad pickup matches only*. Casual games shared in the lobby with people
outside the group — no captains, no ranking — are excluded.

**The authoritative filter is `qc_matches.ranked`.** That boolean column exists in the live
schema ([bot/stats/stats.py:66](../bot/stats/stats.py:66)) but is **not** in the committed
export: `data/qc_matches.csv` carries only `match_id, at, queue, winner_team, maps`.

**Action required:** one-time query, or re-export `qc_matches.csv` including `ranked` and
`channel_id`. Everything else in Part 2 depends on having that list.

What the committed data already tells us:

| Fact | Value |
|---|---|
| `qc_matches` rows | 2,920 — **all** with `queue = "4v4"` |
| …with a reported `winner_team` | 2,912 (8 without) |
| `match_id_map.csv` bot↔aoe2 pairs | 2,648 |
| In `match_id_map` but not `qc_matches` | 20 — worth a look; possibly `/lobby2` manual links or another channel |
| In `qc_matches` but not `match_id_map` | 292 — never resolved to an aoe2 game, so no replay exists to fetch |

So the download set is `qc_matches WHERE ranked=1` ∩ `match_id_map`, **bounded above by 2,648**.

## 12. Size and time

Measured from the 10 replays currently cached locally:

| Metric | Value |
|---|---|
| Average replay | **3.15 MB** (range 1.99 – 4.14 MB) |
| 2,648 matches, gross | **~8.3 GB** |
| Realistic, after aoe.ms's 65–80% availability | **~5.5–6.6 GB**, roughly 1,700–2,100 files |

**Time is the real cost, not bytes.** aoe.ms rate-limits per IP, `download.py` paces at
`--space 5` between successes and backs off `[15, 30, 60, 120]s` on HTTP 429. Best case for
2,648 attempts is ~4 hours; realistically **8–15 hours** with rate limiting. The job must be
resumable and safe to stop and restart — it will not finish in one sitting.

## 13. Storage — Railway Storage Buckets

Railway now offers exactly what you were reaching for: **private, S3-compatible Storage
Buckets**, separate from persistent Volumes.

| | Railway Buckets | Railway Volumes | Cloudflare R2 | Backblaze B2 |
|---|---|---|---|---|
| Storage | **$0.015 / GB-month** | ~$0.15 / GB-month | $0.015 / GB-month | $0.006 / GB-month |
| Egress | **free, unlimited** | n/a | free | free up to 3× stored |
| API operations | **free, unlimited** | n/a | billed | billed |
| Our ~6.6 GB | **~$0.10 / month** | ~$1.00 / month | ~$0.10 / month | ~$0.04 / month |

**Recommendation: Railway Buckets.** B2 is nominally cheaper but the difference is six cents a
month, and Railway wins on everything that actually matters here — the bot already runs there,
so credentials inject as service variables (`ACCESS_KEY_ID`, `SECRET_ACCESS_KEY`, `BUCKET`,
`ENDPOINT`, `REGION`) with no new vendor, no new billing relationship, and no cross-provider
networking. **Volumes are the wrong tool** — 10× the price, and block storage rather than object
storage.

**Free unlimited egress is the property that matters most.** Every calibration run, every
preview, every re-parse can pull the whole archive down at no cost. That is what makes the
archive genuinely reusable rather than something we hoard and avoid touching.

Caveats from the docs, both worth planning around:

- **Region is fixed at bucket creation** — cannot be changed later.
- **No versioning, object lock, or lifecycle rules yet.** An accidental delete is
  unrecoverable, so the archive should not be single-homed. Keep the local copy, or sync to a
  second destination.
- Buckets are fully S3-compatible (put/get/delete, list, copy, presigned URLs, tagging,
  multipart), so `boto3` works directly — no bespoke client.

## 14. Proposed layout and tooling

### Bucket layout

```
replays/{aoe2_match_id}.aoe2record     raw replay, the irreplaceable artifact
extract/{aoe2_match_id}.json           cached extract_match() output
manifest.jsonl                          one record per match (see below)
```

**Archiving the extract output as well as the raw replay is worth doing.** It is far smaller,
and it means calibration and preview need neither mgz nor a parse step — just a download. The
raw replay stays the source of truth for when `EXTRACT_VERSION` bumps and everything must be
re-derived.

Manifest record per match: `aoe2_match_id`, `bot_match_id`, `bytes`, `sha256`, `save_version`,
`parse_ok`, `extract_version`, `fetched_at`, and `unavailable_reason` for the ones aoe.ms will
never serve — so we stop retrying known-dead matches forever.

### Tooling to build

| Piece | Purpose |
|---|---|
| **Fix `download.py`'s resume filter** | **Blocker.** See §15 |
| Bulk-download driver | Reads the ranked set, paces politely, fully resumable, records unavailable matches so they aren't retried indefinitely |
| Bucket sync (`boto3`) | Idempotent upload, skip-if-present by size + hash |
| Bucket fetch | So calibration/preview pull from the bucket, never from aoe.ms |
| Extract cache writer | Optional but recommended — see layout above |

## 15. Blocker: `download.py` does nothing on a fresh checkout

[`utils/replay_quiz/download.py:188`](../utils/replay_quiz/download.py:188) builds its todo list
from the **git-tracked manifest** without checking whether the file still exists in the
**untracked** `data/replays/` cache. On any machine that has the manifest but no cache — which
is now both of ours — it concludes there is nothing to do and downloads zero files.

There is also dead code immediately above it: the `todo` assignment at lines 185–187 is
unconditionally overwritten by line 188.

**This must be fixed before any bulk download is attempted.** It is the first thing anyone
following the existing instructions hits.

## 16. Open decisions

1. **Where does the bulk download run?** A local machine over several days, or a one-off Railway
   service? Railway would hit aoe.ms from a datacenter IP, which is more likely to be
   rate-limited hard or blocked outright. Local is slower but probably safer — worth a small
   test from Railway before committing either way.
2. **Do we archive extract JSON as well as raw replays?** Recommended above; costs a little
   space, removes mgz from the calibration path entirely.
3. **Do we revisit the eAPM forward-only decision** once the archive exists? Retroactive charts
   become a re-parse instead of a re-download.
4. **Is the whole 2,648 the right set**, or do we bound it by date? Older matches are likelier
   to be permanently gone from aoe.ms.
5. **Re-hosting question.** These are replays of your own community's games, pulled from a public
   endpoint, archived privately for your own analysis — a reasonable use. But it is still
   third-party-served content being re-hosted, so keep the bucket **private** rather than
   public, and it is worth a moment's thought about aoe.ms / aoe2companion terms before
   publishing anything derived from it more broadly.

## 17. Risks

- **Rate limiting or an IP block mid-pull.** A multi-thousand-file sweep is a different animal
  from the current one-match-per-150s trickle. Politeness and resumability are not optional.
- **The archive will never be complete.** 20–35% of matches are permanently unavailable.
  Calibration has to be robust to that.
- **Possible sample bias.** If availability correlates with match age, the recoverable subset
  skews recent — and thresholds calibrated on it may not describe older play. Worth measuring
  availability *by date* during the pull rather than assuming it's uniform.
- **No versioning on Railway Buckets.** An accidental `delete` is unrecoverable. Do not
  single-home the archive.
- **8–15 hours of wall clock**, spread over days, before calibration can even start. This is the
  long pole in the card revamp — worth starting early even while other decisions are open.
