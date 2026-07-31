# Identity v2 — ID-only linking, self-service, and deduction

**Date:** 2026-07-30
**Status:** APPROVED (design conversation, 2026-07-30). Amends §3.4 of
`2026-07-30-unified-data-layer-design.md` (v5); implemented as stage 2.5 of
`docs/superpowers/plans/2026-07-30-unified-data-architecture.md`.

## Why v2

Stage 2 unified five identity stores into `identities`, but the post-stage-2
audit confirmed three structural flaws survived it:

1. **Names were load-bearing.** The pipeline merged the Discord nick and the
   AoE2 in-game name into one field, polluting `identities.aoe2_name` for 30 of
   the 54 CSV-known profiles and breaking the web's civ-history name-join.
2. **Four uncoordinated writers, three shadow stores still live.** Two CSVs were
   still read (one on every ingest), one was appended at runtime into a
   dead-end, `rs_profiles` was dual-written, and the web unioned three legacy
   stores.
3. **Flagship-only bootstrap.** Everything was seeded from hand-curated CSVs. A
   partner community has no CSVs and no way to ever populate identity — which
   silently disables civ stats, player quizzes, match-card attribution and the
   scouting report there.

## Decisions (user, 2026-07-30)

- **IDs bind to IDs. No name inference anywhere, ever.** In-game names and
  Discord nicks are both mutable; a name is a display-only observation, never a
  matching input.
- **Bootstrap is automatic.** Players do at most one action, ever (`/link`).
  Admins are the correction layer, not the data-entry layer.
- **Players cannot change their own link.** Only admins re-link or unlink.
- **A mistyped or nonexistent profile id must never link.** `/link` validates
  the id against the AoE2 data source before writing anything.
- **Admin relink is atomic.** Superseding a wrong binding must not require a
  separate unlink step; removed claims are recorded, not lost.
- **Unlinked players play freely.** Core loop (queue, ratings, leaderboards)
  never depends on identity. Their analysis surfaces read exactly
  **"Statistics pending linking"**, and their full history backfills
  automatically once a link lands.
- Importing another system's match history to pre-populate: **out of scope** —
  a future feature.

## 1. The binding model

`identities` stays the single ID↔ID table and is empty for a new community
(bindings are global — a person linked in any community is linked in all).

```
identities  profile_id (pk) -> user_id (nullable), aoe2_name (observed,
            display-only), confidence, first_seen_at, last_seen_at
```

The confidence lattice gains one tier:

```
seed (0)  <  learned (1)  <  self (2)  <  manual (3)
```

- `seed` — flagship-historical rows and the "profile known, owner unknown"
  state (`user_id` NULL).
- `learned` — written only by the deduction solver (§4).
- `self` — the player's own one-time `/link`.
- `manual` — admin commands and, later, the admin UI.

**Write rules (`learn()`), tightened from stage 2:**

- A strictly **higher** tier may change `user_id`; the losing claim is recorded
  in `identity_conflicts` as `superseded`.
- Same or lower tier, **same** user: refresh `last_seen_at` / `aoe2_name` only.
- Same or lower tier, **different** user: no change + an `open` conflict row.
  (Stage 2 let an equal tier overwrite; v2 forbids it — an equal-confidence
  disagreement is a fact for the admin, not a coin flip.)

`aoe2_name` is the last observed in-game name — refreshed by replay parses and
by `/link` validation, used only for display (confirmation messages, quiz
options, insights URLs). It is never an input to any matching.

`identity_conflicts` (added by user decision during stage 2; recorded in the
design here, which stage 2 failed to do):

```
identity_conflicts  profile_id, claimed_user_id, source, noticed_at,
                    status: open | superseded | unlinked
```

Every losing, superseded, or removed claim lands here. `/identity conflicts`
and the future UI read it; nothing is ever silently discarded.

`identity_aliases` is **dropped** — it was write-never, and Discord supplies
display names live. Its §3.4 (v4) `aoe2_names` column idea dies with it; name
history, if ever needed, comes from per-replay observations.

## 2. Player `/link` — one command, three behaviors

Registered as a bare top-level `/link` (repo convention: no prefixes).

1. **Unlinked, no id given** → an instructions message: open
   `aoe2insights.com`, search your in-game name, your profile id is the number
   in the page URL (worked example included). No state change.
2. **Unlinked, id given** →
   - **Validate first.** Resolve the id against the aoe2companion API family
     already used by `bot/lobby/api.py` (exact endpoint pinned during plan
     elaboration). Nonexistent id → error + pointer to the instructions,
     nothing written. Service unreachable → "try again later", nothing
     written. The two failures are distinguished in the reply.
   - Valid id already bound to **another** player → refuse ("that profile is
     linked to another player — ask an admin") + an `open` conflict row
     recording the attempt.
   - Valid and unclaimed → bind at `self` tier. The confirmation echoes the
     profile's current in-game name from validation — "Linked to profile
     2593442 (*HenryTheGreat*) —
     https://www.aoe2insights.com/user/relic/2593442/" — so a
     wrong-but-existing id is caught by the player's own eyes.
3. **Already linked** (with or without an id) → view-only: current profile id,
   observed name, insights URL, and "only an admin can change this." A player
   can always verify who they are linked to; they can never change it.

## 3. Admin commands

- **`/identity link <member> <profile_id>`** (exists; becomes atomic relink) —
  `manual` tier. Binds the profile to the member; any previous owner of that
  profile and any previous profile of that member are superseded in the same
  operation and recorded in `identity_conflicts` as `superseded`. An optional
  `additional: true` flag adds the profile as a second account instead of
  replacing (multi-account players exist in the flagship data).
- **`/identity unlink <member> <profile_id>`** (new) — removes a binding with
  no replacement: `user_id` → NULL, confidence → `seed` (the unowned state),
  the removed claim recorded as `unlinked`. Rarely needed once relink is
  atomic; exists for "this link is simply wrong."
- **`/identity show`, `/identity conflicts`** — unchanged, except `show` drops
  its dead Nick line (aliases are gone).
- **`/identity status`** (new, moderator) — the visibility surface: "N of M
  players seen in the last 90 days are linked", plus which analysis features
  are gated below their thresholds. Silent feature-failure is replaced by a
  number an admin can act on.
- The rebuilt web UI later reads and writes these same tables (full link list,
  relink/unlink, conflict resolution). No new storage for it.

## 4. Auto-link: the pairing + deduction solver

Replaces `profile_map.eliminate()`, the lobby watcher's inference, and
`civ_sync`'s name auto-mapper — one module, zero names.

**Input** (per community): its paired matches — bot matches joined to AoE2
matches via the lobby flow (`lobbies.match_id` + `match_replays`), each pairing
carrying both rosters: Discord users with team + outcome on one side, profile
ids with team + outcome on the other.

**Constraints per paired match:**
- *Participation*: a profile in the game belongs to one of the users in the
  game.
- *Team + outcome*: the winning team's profiles map to the winning team's
  users, likewise losers (the winner orientation is known on both sides).

**Deduction**: intersect constraints across all of the community's paired
matches. Team shuffles and attendance differences split the candidate sets;
this pins individuals without any anchor player — eight strangers resolve in
roughly three or four varied games.

**Write rule**: bind at `learned` only when exactly one candidate remains for a
profile **and** the deduction is supported by at least two paired matches (one
mispaired game must never create a binding alone). An empty candidate set is a
contradiction — evidence that a pairing is wrong: no writes from the
contradicted subset, and a conflict row flags it for the admin.

**Runs**: after each ingested paired match, and after every new `self`/`manual`
link — a fresh link is a new constraint that can immediately resolve teammates.
Stateless: a pure function over stored raw data, no solver state tables.

## 5. Unlinked players: gating and backfill

- Raw and derived-global data accrue keyed by `profile_id` regardless of
  linking. Nothing waits, nothing is lost.
- **Attribution resolves at refresh-time, not write-time.** Consequence for
  stage 3 (contract change): `game_stats` and `game_labels` carry **no
  `user_id` column**; every consumer resolves profile → user through
  `identities` when it computes (stage-4 rollups join it; stage-5 readers get
  it from rollups). A link therefore backfills the player's entire recorded
  history by construction: the link marks them dirty, the refresh recomputes,
  the history lights up. No backfill job exists.
- Analysis output for an unlinked player reads exactly
  **"Statistics pending linking"** — scouting report, match-card attribution,
  player-quiz eligibility, web profile alike.

## 6. Retirements pulled forward into this stage

Every day these live, they create more drift to reconcile later — they die in
stage 2.5, not stage 6:

- the `profile_resolved.csv` read on every live ingest
  (`bot/replay_stats/jobs.py` → `utils/replay_quiz/extract.py`); ingest uses
  the replay's own per-game names as observations and `identities` for
  attribution
- `civ_sync.load_profile_map()` + `_auto_add_profile_mappings()` (runtime CSV
  read + dead-end append) and the elo-sync lobby matching that depended on
  them — retrospective pairing keys on time + size + map, identity-free
- the `rs_profiles` dual-write, after cutting `bot/web.py`'s
  `_mapped_profiles_by_user()` three-store union over to `identities`
- migration `004_identity_v2` then: repairs the 30 polluted
  `identities.aoe2_name` values from `profile_resolved.csv`'s real
  `aoe2_name` column (one-time, flagship-only data), and **drops**
  `rs_profiles`, `qc_profile_map`, `identity_aliases`
- audit corrections ride along: registry `writers` fixes (`identities`,
  `identity_conflicts`, `player_ratings`), the `store.py` write-order swap,
  and this spec itself closes the "identity_conflicts undocumented" gap

The CSV *files* are still deleted in stage 6 with the other data-file
retirements; after 2.5 nothing reads them at runtime.

## 7. Multi-community properties

- The binding is global; the solver runs over one community's paired matches
  at a time, but a binding earned anywhere benefits everywhere.
- Coverage (`/identity status`) is per-community — it counts that community's
  recent players.
- A community that never adopts the lobby flow still gets `/link` + admin
  linking; one that adopts it gets hands-free deduction. Both paths write
  through the same `learn()` lattice and surface the same conflicts.
