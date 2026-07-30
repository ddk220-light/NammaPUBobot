# Tale of the Tape — closing the loop

**Date:** 2026-07-30
**Status:** approved design, ready for an implementation plan

## Summary

The pre-game storyline embed asks a question — *"does the curse break tonight?"* —
and nothing ever answers it. This change makes the match answer it.

Three things happen:

1. **All history is windowed to the last 90 days.** Hard stop, every line type.
2. **A payoff embed posts the moment a match reports**, reacting to each storyline
   that was teased. It replaces the current replay-derived "Final Tale of the Tape",
   which is deleted.
3. **Combos grow past pairs** — a trio line and an exact-lineup line join the six
   existing types, and every line now names the rest of the side it is about.

No replay parsing is involved anywhere. The whole feature is win/loss and team
composition, so the payoff is instant at report time.

## Why these choices

Measured read-only against the live channel on 2026-07-30 (3,312 ranked matches
in one channel, 913 days, **109 matches/month**). Replayed the last 400 matches,
rebuilding each one's history from scratch.

**The 90-day window is an upgrade, not a cost.** Over a lifetime everyone's
win-rate with everyone converges toward their own baseline, so the 15% swing that
drives the best/worst-teammate line stops clearing:

| line type | 90-day | lifetime |
| --- | --- | --- |
| best/worst teammate | **255** | 103 |
| perfect / cursed pair | **26** | 8 |
| teammate streak | 277 | 401 |
| H2H streak | 433 | 478 |

Windowing more than doubles the two combo types people react to most, at the cost
of some streak lines. Coverage is unaffected: 399 of the 400 matches still
produced at least one line, and 66% produced the full four.

**Firing rates for the two new types**, same window and sample:

| bar | fires | per month |
| --- | --- | --- |
| trio, ≥75% one direction, ≥5 games together | 1 in 7 | ~15 |
| exact lineup, any record, ≥2 prior games | 1 in 17 | ~6 |

Exact lineup is deliberately a rare gem — only 10.4% of team-sides have *ever*
shared a side before inside 90 days. The line leans on that rarity rather than on
the tally, so a mundane 2-1 still reads as an event.

**Draws are negligible** — 8 in 3,312 ranked matches (0.24%), and the database
holds no unranked matches at all. A draw simply posts no payoff.

## Scope

**Changed**

- `bot/team_insights.py` — 90-day window, two new candidate types, team-framed
  phrasing, seeded RNG, pinned title.
- `bot/match/match.py` — `finish_match` posts the payoff.
- `bot/post_game.py` — the old analysis embed and its private helpers are removed.
- `utils/preview_insights.py` — renders the payoff as well as the pre-game embed.
- `tests/test_team_insights.py`, `tests/test_post_game.py`.

**New**

- `bot/storyline_payoff.py` — resolution truth table, payoff phrasing, embed.

**Deleted**

From `bot/post_game.py`: `build_match_analysis_embed`, `_impact_payload`,
`_match_analysis_lines`, `_team_tag_summary`, `_tag_word`, and the module's
`scoring` import once those go.

Verified safe: `bot/web.py` carries its own separate `_impact_payload`
(`bot/web.py:766`), so the web profile is untouched, and `bot/replay_stats/scoring.py`
keeps its other two consumers (the stored `rs_player_game_tags` and the web
profile API).

**Untouched**

`post_match_analysis` still posts the Match Cards embed and the APM chart when
the replay lands. `bot/replay_stats/` is not modified at all.

## The pre-game embed

Trigger is unchanged — `Match.final_message` calls `build_insights_embed` when
teams are formed (`bot/match/match.py:461`).

### Windowing

`_fetch_history` gains `AND m.at >= %s` bound to `now - 90 days`. `qc_matches.at`
is already a unix timestamp written by `register_match_ranked`, so no migration.

The window is applied in SQL rather than in Python so the query stays cheap as
history grows.

### Two new candidate types

**`lineup`** — the exact set of players on one side has ≥2 prior decisive games
together inside the window. "Together" means that whole set appeared united on a
single side of a prior match, whichever side index that was. At most one per
embed. Drama weight **9.0**, above everything else: it is the rarest thing the
module can say.

**`trio`** — a 3-player subset of one side has ≥5 decisive games together inside
the window with ≥75% of them going one direction. At most one per embed. Drama
weight **7.0**.

The per-type cap of one matters for both. `_overlaps` tests subset relationships
in both directions, so a trio line correctly suppresses a pair line drawn from
its own members — but two *different* trios on the same side (A/B/C and A/B/D)
are neither subset nor superset of each other, so without a hard cap both could
appear.

### Team framing

Every line gains a clause naming the complement of its subjects within their own
team:

- pair in a 4v4 → two teammates named
- trio → one teammate named
- `lineup` → complement is empty, so the clause addresses the whole side instead
- complement larger than two → "the rest of Alpha" rather than a list

Examples of the intended shape:

> 🪦 **A** & **B** have lost their last 4 as teammates. Good luck to **C** and **D**.

> 💯 **A**, **B** & **C** win 5 of every 6 together — can **D** keep the run alive?

> ⚔️ **A** has beaten **B** four straight. Does the rest of Beta answer for them?

`_phrase` already receives `teams_meta` and currently ignores it; the framing
gives it a use, so team names finally appear in the copy.

### Determinism

`_select` and `_phrase` already accept an injected `rng`. Both call sites pass
`random.Random(match_id)`. The same history and match id therefore always yield
the same lines, which is what lets the payoff recompute instead of storing state.

The title stops being randomised. Pre-game is pinned to **⚔️ Tale of the Tape**
— the name the community already uses — and the payoff to **⚔️ Final Tale of the
Tape**, so the two read as a matched pair.

## The payoff embed

### Trigger

`Match.finish_match` (`bot/match/match.py:468`), immediately after
`register_match_ranked` and before `resolve_for_match`. The channel then reads:

1. rating changes (`print_rating_results`)
2. **the payoff** — what the result meant for the storylines
3. the audience prediction payout

`finish_match` is the single funnel for `/report loss`, the admin `report win`
and `report scores`, so all three paths are covered by one hook. A cancelled
match never reaches it, so an abort posts nothing.

### Recompute, not stored state

The payoff re-derives the storylines rather than reading back what was posted:
same `_fetch_history` call, same seeded RNG, and prior restricted to
`match_id < this match`. This match is already persisted by the time the payoff
runs, hence the explicit exclusion.

**What the recompute reads from is pinned, not recomputed.** The design
originally assumed the window anchor, the roster and `match.id` were all stable
across a match's lifetime. None of the three is:

- the 90-day window slides as the game runs, because `qc_matches.at` is the
  *report* time — a historical match can fall inside the pre-game read and
  outside the payoff's
- `/subfor` and `sub_auto` are legal during `WAITING_REPORT` and mutate
  `match.teams` in place; `sub_auto` re-splits **both** sides
- `Match.from_json` assigns a restored match a **new** id, and a restored
  `WAITING_REPORT` match never re-posts the tease

So `build_insights_embed` stashes the cutoff, roster and seed it used on the
match object, and the payoff requires them: it reads the stashed cutoff and
seed, and posts nothing if the stash is missing (a redeploy) or if the roster no
longer matches (a substitution). The storylines are still recomputed — this only
pins what they are recomputed *from*.

### Resolution

Every claim reduces to one boolean — **did the claim's subject side win?**

| type | subject side |
| --- | --- |
| `lineup`, `trio`, `perfect`, `mate`, `mate_wr` | the named players' team |
| `h2h` | the streak holder's team |
| `form` | that player's team |
| `deadlock` | the first-named player's side |

Each type then renders one of exactly two texts:

| type | subject won | subject lost |
| --- | --- | --- |
| `lineup` | the rare reunion delivers, record now X-Y | the reunion falls flat, record now X-Y |
| `trio` | the lean holds, now X-Y | the lean breaks |
| `perfect` (unbeaten) | flawless record survives at n+1 | the perfect record is gone |
| `perfect` (cursed) | curse broken at last | curse deepens to 0-(n+1) |
| `mate_wr` (best) | the pairing delivers again | not tonight |
| `mate_wr` (worst) | they bucked their history | history repeats |
| `h2h` | streak extends to k+1 | streak dies at k |
| `mate` | streak extends to k+1 | streak dies at k |
| `deadlock` | one of them finally pulls ahead | the other does |
| `form` | streak extends to k+1 | streak ends at k |

The team-framing carries into the payoff, so a resolved line can credit or blame
the complement — *"**C** and **D** dragged them out of it."*

The embed posts only when at least one claim resolved. A draw resolves nothing.

## Thresholds

New:

```
WINDOW_DAYS       = 90     # applied to the history query, every line type
LINEUP_MIN_GAMES  = 2      # prior decisive games as this exact side, in-window
TRIO_MIN_GAMES    = 5      # prior decisive games as this exact trio, in-window
TRIO_MIN_SHARE    = 0.75   # fraction going one direction
W_LINEUP          = 9.0
W_TRIO            = 7.0
LINEUP_TYPE_CAP   = 1
TRIO_TYPE_CAP     = 1
```

Every existing threshold and weight in `bot/team_insights.py` is unchanged —
`PERFECT_MIN`, the `BW_*` block, `H2H_*`, `MATE_*`, `DEADLOCK_*`,
`FORM_MIN_STREAK`, `PER_PLAYER_CAP`, `PER_TYPE_CAP` and the drama weights all
keep their current values. Small-sample noise in the best/worst-teammate line is
accepted deliberately.

## Error handling

The pre-game call and the payoff are both wrapped per-candidate so a render
failure costs one line, not the whole embed, and both log through
`core.console.log.error` — matching how `post_game.py` reports its own
failures — rather than swallowing silently. Neither embed may ever raise into
the report or rating path.

## Testing

`tests/test_team_insights.py` gains:

- the window filter — a match outside 90 days contributes to nothing
- the `lineup` generator at ≥2 prior games, including that `_group_series`
  matches any prior match where the group shared a side (not just an exact
  side match) -- a larger side that contained the group still counts
- the `trio` generator at ≥5 games / ≥75%, including the one-per-embed cap and
  the two-overlapping-trios case
- determinism — the same seed and history produce the same chosen lines
- the complement rule, including the empty complement on a `lineup` line and the
  "rest of Alpha" collapse past two names

A new `tests/test_storyline_payoff.py` covers the resolution truth table: every
type × subject-won × subject-lost, plus a draw resolving nothing and an empty
candidate set posting nothing.

`tests/test_post_game.py` loses the two tests covering the deleted analysis embed.

Harness constraints carry over from the card work: there is no `pytest-asyncio`,
so an `async def test_` is silently skipped — use a sync test driving
`asyncio.run`. Use the object form of `monkeypatch.setattr`, since `core/` is a
namespace package. Patch the consuming module's `db`, not `core.database.db`.

`utils/preview_insights.py` is extended to render the payoff alongside the
pre-game embed for the last N real matches, read-only, so the copy can be read
end to end before this ships.

## Known limitations

Two sources of tease/payoff divergence remain accepted. Three others — a
sliding window, a substituted roster and a reassigned `match.id` — were found
during implementation and are now pinned by the stash described above.

**Concurrent-match drift.** Matches overlap. A match that formed while yours was
live can finish and persist before yours reports, landing in the window with a
lower `match_id`. The report-time recompute therefore sees one or two matches the
pre-game read did not. Rate-based lines will not move; a streak line occasionally
will, so the payoff can resolve a line nobody was shown, or miss one that was.
Accepted: storing the claims themselves was considered and rejected in favour of
keeping the storylines recomputed.

**Code drift.** Changing a threshold, a generator or a phrasing pool between a
match forming and reporting changes the recomputed set. In practice that window
is minutes to hours, and a deploy inside it also clears the stash, which makes
the payoff skip rather than answer the wrong tease.

## Out of scope

- Replay-derived storylines. This feature stays purely win/loss + team composition.
- Pair-vs-pair ("A&B are 5-1 over C&D") — measured at 1 lopsided duel per 25
  matches, considered, and dropped to keep the surface small.
- Any change to Match Cards, the APM chart, the civ report, or `bot/replay_stats/`.
