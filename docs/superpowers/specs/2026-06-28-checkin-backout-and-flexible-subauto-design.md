# Graceful check-in back-out + flexible `/subauto` — design

Date: 2026-06-28
Status: Approved (pending spec review)

## Problem

The check-in stage currently offers a back-out (`⛔` reaction / `/notready`) that, by
default (`check_in_discard_immediately=1`), **aborts the entire match** the moment one
player opts out — everyone is dumped back to gathering. That is heavy-handed: one person
leaving should not tear down the match for everyone else.

Separately, substitution is rigid:
- `/subauto` only subs out the **caller**, only works in DRAFT / WAITING_REPORT, and only
  when a player is already waiting in the queue.
- There is no way for one player to replace a **different** (inactive) player during
  check-in.

Players also get no visibility into how much time is left before check-in ends.

## Goals

1. Backing out of check-in removes **only that player**, never the whole match.
   - If a player is waiting in the queue → swap them in seamlessly; the match continues.
   - If nobody is waiting → revert to gathering with only the backed-out player removed;
     everyone else stays queued and someone new can add in.
2. `/subauto [player]` — anyone in the channel can replace a named (presumably inactive)
   player by auto-pulling the next available queued player. Works in check-in, draft, and
   waiting-report.
3. Show a live countdown of remaining check-in time, plus a one-time 1-minute warning.

## Non-goals

- No change to `/subfor` (it keeps its current behavior; see "Known issue left as-is").
- No moderator gating on the named replacement — it is trust-based, by request.
- No change to draft-stage rebalancing logic beyond letting `/subauto` target a named player.

## Decisions (from brainstorming)

- A swapped-in player **must check in themselves** (added to the not-ready set; everyone
  else keeps their ready status). Exception: if the swapped-in player has an active
  `/auto_ready`, they are auto-readied, consistent with players present at check-in start.
- The old abort-whole-match path is **fully removed** (not kept as an opt-in).
- **Anyone in the channel** can run `/subauto <player>`.
- Only `/subauto` is extended; `/subfor` is left as-is.

## Architecture

Approach A: the check-in stage owns its own swap/revert logic, reusing the two existing
primitives — `pick_available()` (`bot/match/subbing.py`) and `PickupQueue.revert()`. The
`/subauto` command routes by match state: check-in → new `CheckIn` method; draft /
waiting-report → existing draft path (generalized to accept a target player). The draft
path keeps its own swap because it must rebalance teams via `init_teams("matchmaking")`,
which do not exist yet at check-in.

### Component 1 — `CheckIn.replace_player(ctx, out_member) -> member | None`

Shared swap-out primitive used by both the back-out flow and `/subauto` during check-in.

- Build `busy_ids = {p.id for m in bot.active_matches for p in m.players}`.
- `candidate = pick_available(self.m.queue.queue, busy_ids)`.
- If `candidate is None`: return `None` (caller decides what to do).
- Otherwise:
  - `self.m.players.remove(out_member)`; `self.m.players.append(candidate)`.
  - Discard `out_member` from `ready_players`.
  - Add `candidate` to `ready_players` **only if** `candidate.id in bot.auto_ready`;
    otherwise they remain not-ready and must check in.
  - Pull the candidate from the queue and clear their timers:
    `await self.m.qc.remove_members(candidate, ctx=ctx)` and
    `await bot.remove_players(candidate, reason="pickup started")`.
  - Return `candidate`.

No rating recompute is needed at check-in (teams/ratings are computed later in draft).

### Component 2 — back-out flow

Triggered by the `⛔` reaction and by `/notready` (`set_ready(..., ready=False)`), only
when `check_in_discard` is enabled.

```
async def back_out(self, ctx, member):
    swapped = await self.replace_player(ctx, member)
    if swapped:
        await ctx.notice("{out} backed out and was replaced by {in}. {in}, please check in.")
        await self.refresh(ctx)   # embed now lists the new player as not-ready; timer continues
    else:
        # nobody waiting -> drop only this player, revert to gathering
        bot.waiting_reactions.pop(self.message.id, None)
        await self.message.delete()
        await ctx.notice("{out} backed out. Reverting {queue} to the gathering stage...")
        bot.active_matches.remove(self.m)
        await self.m.queue.revert(ctx, [member], [m for m in self.m.players if m != member])
```

`revert(ctx, not_ready, ready)` re-queues `ready` and drops `not_ready`; if autostart and
waiting players exist, it refills and may start a fresh match (new check-in).

### Component 3 — `/subauto [player]`

`bot/commands/matches.py::sub_auto` no longer uses the `@author_match` decorator (it must
locate the match by the *target*, not the caller):

```
async def sub_auto(ctx, player: Member = None):
    who = player or ctx.author
    match = find(lambda m: m.qc == ctx.qc and who in m.players, bot.active_matches)
    if match is None:
        raise bot.Exc.NotInMatchError(...)   # "is not in an active match"
    if match.state == bot.Match.CHECK_IN:
        swapped = await match.check_in.replace_player(ctx, who)
        if swapped is None:
            raise bot.Exc.NotFoundError("There are no available players in the queue to substitute in.")
        await ctx.notice("{out} was substituted by {in}. {in}, please check in.")
        await match.check_in.refresh(ctx)
    else:
        await match.draft.sub_auto(ctx, who)
```

`bot/match/draft.py::sub_auto(ctx, author)` is generalized to `sub_auto(ctx, out_member)`:
it removes `out_member` (instead of always the author), then runs the existing
pick / swap / `init_teams("matchmaking")` rebalance unchanged.

The slash definition in `bot/context/slash/commands.py` gains an optional `player: Member`
argument.

Note: unlike back-out, `/subauto` during check-in does **not** revert when no candidate is
available — it errors and leaves the named player in place ("sub someone in" semantics).

### Component 4 — live timer + 1-minute warning

- **Timer:** the check-in embed (`bot/match/embeds.py::check_in`) gains a field
  `Check-in ends: <t:END:R>` where `END = int(self.m.start_time + self.timeout)`. Discord
  renders this as a live, self-updating relative countdown for every viewer — set once,
  no re-editing.
- **Warning:** `CheckIn` gains a `self.warned = False` flag. In `think(frame_time)`, when
  `frame_time >= (self.m.start_time + self.timeout) - 60`, `not self.warned`, and there are
  still not-ready players, post a one-time notice pinging the not-ready players:
  "⏳ 1 minute left to check in, {mentions}! If you don't ready up you'll be removed and
  replaced by the next queued player, or the queue reverts to gathering." Set
  `self.warned = True`.

### Component 5 — timeout (unchanged behavior)

On timeout, `think()` calls the existing `abort_timeout` → `revert()`: not-ready players
are dropped, ready players go back into the queue, and if waiting players exist the queue
auto-refills and starts a fresh match/check-in; otherwise it sits in gathering. This already
matches "the next player is added, or it goes back to waiting with the not-ready players
removed," so no change beyond the warning above.

## Config & cleanup

- Remove the `check_in_discard_immediately` config variable entirely. Grep and strip every
  reference: `bot/queues/pickup_queue.py` (var definition + `_match_cfg()` pass-through),
  `bot/match/check_in.py` (`self.discard_immediately`), `start.py` config template,
  `config.example.cfg`, and the web dashboard (`bot/web.py` / `bot/web_page.html`) if
  present. Any orphaned value already stored in MySQL is harmless (simply no longer read).
- Keep `check_in_discard` (controls whether back-out is offered at all; default unchanged).
- Remove the now-dead `discarded_players` set and the "marked discard" branch in
  `CheckIn.refresh()`, plus the ❌ discarded marker in `embeds.py::check_in`. The
  immediate-swap model makes them vestigial; this simplifies `check_in.py`.
- Drop the decorative `🔸` reaction in `CheckIn.start()` (it has no handler).

## Files touched

- `bot/match/check_in.py` — `replace_player`, `back_out`, warning + timer wiring,
  simplified `refresh`, removed discard-immediately / discarded_players.
- `bot/match/embeds.py` — check-in embed: live timer field, removed ❌ marker.
- `bot/match/draft.py` — generalize `sub_auto` to take `out_member`.
- `bot/commands/matches.py` — `/subauto` routing + optional target.
- `bot/context/slash/commands.py` — `/subauto` optional `player` arg.
- `bot/queues/pickup_queue.py` — remove `check_in_discard_immediately` var + pass-through.
- `start.py`, `config.example.cfg`, web dashboard — strip removed config var if present.

## Testing

- `pick_available` (the selection rule both flows reuse) is already pure and unit-tested;
  keep/extend those tests.
- Add focused pure-function tests for the swap-vs-revert decision boundary (candidate
  available vs not) where logic can be isolated, following the repo's existing
  pure-function test style (`tests/`). Discord/DB-bound paths (`replace_player`, `back_out`)
  are integration-shaped and validated by manual run.

## Edge cases

- `/subauto <player>` where the named player is not in any channel match → error.
- `/subauto` with no candidate during check-in → error; named player stays.
- Swapped-in player who then also backs out → same flow recurses (swap again or revert).
- Back-out when the queue is empty → revert to gathering (only the backed-out player
  dropped, others stay queued).
- `process_reaction` already ignores users not in `self.m.players`; after a swap the new
  player is in the roster, so their reactions are accepted.

## Known issue left as-is

`bot/match/draft.py::sub_for` lists `CHECK_IN` as an allowed state but its body operates on
`self.m.teams` (absent at check-in) and calls `self.m.check_in.refresh()` without the
required `ctx` argument — so `/subfor` is effectively broken during check-in. Per the
decision to only extend `/subauto`, this is **not** fixed here. Flagged for a future change.
