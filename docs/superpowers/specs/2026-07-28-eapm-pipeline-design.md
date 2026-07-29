# Per-minute eAPM pipeline — design

**Date:** 2026-07-28
**Surfaces:** `utils/replay_quiz/extract.py`, `bot/replay_stats/` (schema, store, shape, chart), `bot/post_game.py`
**Status:** design agreed, pending implementation plan
**Sequencing:** ships **before** [the Match Cards revamp](2026-07-28-match-cards-revamp-design.md), which is parked awaiting this data.

## Problem

Every signal the bot currently derives from replays is production volume: units
queued, villagers queued, buildings placed, techs researched. None of it sees
whether a player was actually *doing* anything — a player who queues heavily and
then idles looks identical to one who queues the same and micros constantly.

mgz already computes an effective-APM figure per player, and it is already stored
in `rs_player_games.eapm`. But it is a single scalar for the whole game. There is
no way to see *when* a player was active, who was busiest during the critical
window, or what each player's peak looked like.

## Goals

- Store per-minute eAPM per player, per match.
- Post an eAPM-over-time chart with the existing post-match cards.
- Make average and peak eAPM available for the card revamp that follows.
- Forward-only. No backfill of the 1,061 already-parsed matches.

## Non-goals

- **Backfill.** Explicitly rejected: re-parsing means re-downloading from aoe.ms at
  one match per 150-second sweep (~44 hours), and 20–35% of replays are no longer
  available at all. Cards post at ingest, so every future match is covered from the
  first deploy; historical matches simply have no chart.
- **Combat outcome data.** APM measures activity, not effectiveness. A player
  frantically losing a fight has high APM. Nothing here should be framed as skill.
- **Card layout changes.** Placing average and peak eAPM on the card belongs to the
  parked revamp spec.

## Definition — mgz parity

mgz's effective-APM filter is much simpler than the name suggests
(`mgz/model/__init__.py:25` and `:281–310`):

```python
AI_ACTIONS = [ActionEnum.AI_ORDER]
...
if 'player_id' in action_data and action_data['player_id'] in players:
    if action_type not in AI_ACTIONS:
        eapm[action_data['player_id']] += 1
...
players[player_id].eapm = int(round(eapm[player_id] / ((timestamp/1000)/60)))
```

It counts every action carrying a `player_id` whose type is not `AI_ORDER`, and
divides by total game minutes. That is the whole filter.

We replicate it exactly, bucketed by minute. This gives a **testable invariant**:

> `sum(all buckets for a player) / total_game_minutes` must equal that player's
> stored `eapm`, up to rounding.

The chart and the stored average therefore agree by construction rather than being
two parallel approximations — which is the reason this option was chosen over
counting raw actions.

## Extraction

`extract_match` already iterates `m.actions` twice (`utils/replay_quiz/extract.py:161`
and `:179`). Bucketing is added inside the existing pass — no additional traversal.

Each `Action` carries everything needed (`mgz/model/definitions.py:71`):
`timestamp` (a `timedelta`), `type`, and `player`.

```
bucket  = int(action.timestamp.total_seconds()) // 60
include = action.player is not None and action.type not in AI_ACTIONS
```

New output key: `extracted["apm"]` — a list of
`{player_number, minute, actions}`.

Version bumps:

- `EXTRACT_VERSION` `"v4"` → `"v5"` (invalidates the offline parse cache used by
  `utils/` tooling).
- `PARSER_VERSION` `"mgz-a1683d8+3"` → `"mgz-a1683d8+4"`, with the existing comment
  convention: `+4: emit per-minute eAPM buckets -> rs_player_apm`.

Bumping `PARSER_VERSION` does **not** trigger a re-parse.
`store.reopen_pending_parser_update` only reopens rows with status
`pending_parser_update` — matches shelved because their save version was too new.
Forward-only therefore needs no special mechanism; deploying the new extract is
sufficient.

## Storage

New table, following the existing `rs_player_*` long-table conventions:

```
rs_player_apm
  aoe2_match_id   int
  player_number   int
  minute          int          -- 0-based minute index from game start
  profile_id      int  null    -- denormalised, consistent with the sibling tables
  actions         int  null    -- eAPM-filtered action count within that minute
  PRIMARY KEY (aoe2_match_id, player_number, minute)
```

Volume: a 40-minute 8-player game is 320 rows. At roughly 1,000 matches a year this
is trivial, and it is the reason bucketing happens at extract time — storing raw
actions would be ~32,000 rows per match.

Written in `store.write_match` alongside the other long tables, via a new
`shape.apm_rows`. `rs_player_apm` must be added to the delete-by-`aoe2_match_id`
list at `bot/replay_stats/store.py:96` so re-ingest stays idempotent.

**Derived values are not stored.** Average already exists as
`rs_player_games.eapm`; peak is a trivial max over ~40 rows. No denormalisation.

## Chart

New renderer `chart.render_apm_curve` in `bot/replay_stats/chart.py`, alongside the
existing `render_timeline` and `render_growth_curve`, following their conventions:
headless Agg, the object-oriented Figure API (never pyplot), matplotlib imported
lazily *inside* the function so the module stays importable in CI, returning a
`BytesIO`.

- One line per player, up to eight.
- Coloured by team — two distinct hue families so team shape is readable at a glance.
- **Three-minute rolling average** on the plotted line. At one-minute resolution
  with eight players the raw series is unreadable noise; peaks remain available as
  a number rather than as chart spikes.
- Legend labelled with nicknames.

## Discord integration

`post_match_analysis` attaches the chart to the Match Cards embed:

```python
embed.set_image(url="attachment://apm.png")
await channel.send(embeds=[cards, tale], file=chart_file)
```

Discord permits one image per embed, so the chart attaches to the cards embed.

### Rendering must not block the event loop

The chart renders **during ingest, on the `think()` tick path**, so it must be
offloaded with `asyncio.to_thread`, matching `bot/commands/stats.py:496` and the
pattern documented in `bot/player_profile.py`.

While in this code, fix a pre-existing inconsistency:
`bot/commands/player_details.py:52` calls `chart.render_growth_curve` **synchronously
on the event loop**. It is user-triggered rather than tick-driven, so the blast
radius is smaller, but it still stalls the whole bot — including the 1-second tick —
for the duration of a matplotlib render.

## Error handling

- **Chart render failure must never block the post.** Fall back to sending the cards
  with no image. `post_match_analysis` already wraps everything and returns False;
  the chart gets its own inner guard so it degrades independently.
- **No APM rows** (every match ingested before this deploy): render no chart, say
  nothing. No placeholder.
- **Fewer than two minutes of data**: skip the chart — a two-point line is noise.
- **matplotlib absent in CI**: preserved by the existing lazy-import pattern.

## Validation before ship

Re-parse the **five most recent matches** manually — directly, not through the
ingest sweep — and render their charts for review. This is a validation sample, not
a backfill; the rows may be discarded afterwards.

Checks:

1. The parity invariant holds for every player: bucket sum over game minutes equals
   the stored `eapm`.
2. The chart is legible with eight players at three-minute smoothing.
3. Confirm what share of actions carry usable timestamps — `extract.py:130` already
   drops null-timestamp queue actions as unplottable, and if the same gap affects
   the general action stream it would bias buckets. If a material share is
   untimestamped, that must be understood before this ships.

## Testing

- **Bucketing is a pure function** (`actions → per-minute counts`) and unit-tested
  without mgz, matching how the rest of `bot/replay_stats` keeps its logic testable
  under the CI import shim.
- **The parity invariant** is tested against a fixture: bucket sum ÷ minutes equals
  the expected eapm.
- **Row shaping and re-ingest idempotency** for `rs_player_apm`.
- **`chart.py` stays untested**, consistent with the two existing renderers — CI has
  no matplotlib. The validation sample above is what covers rendering.

## Risks

- **Eight lines may be unreadable regardless of smoothing.** The validation step
  exists specifically to catch this before it ships. Fallbacks if it fails: plot
  team averages instead of individuals, or plot only the match's top four by peak.
- **Untimestamped actions.** Quantified in validation check 3 above. If significant,
  buckets under-count and the parity invariant will fail — which is exactly why the
  invariant is a test rather than an assumption.
- **Per-ingest image upload.** Every ingested match now uploads a PNG to Discord.
  Small, but it is new I/O on the tick path and the render is CPU-bound; the
  `to_thread` offload is what keeps it off the event loop.
