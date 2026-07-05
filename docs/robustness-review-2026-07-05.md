# Robustness Review — lobby2, check-in, win reporting, dashboard

Date: 2026-07-05. Analysis only — no code changes. Every finding carries a
`file:line` reference against commit `4c3398a`.

Scope, from the reported symptoms:

1. `/lobby2` sometimes doesn't show a proper lobby card that updates live as players join.
2. The check-in phase should have an emoji to drop out of the match.
3. Post-match "who won and how" output is too much text/data — keep only the interesting parts.
4. General health check of the web dashboard.

---

## 1. `/lobby2` live lobby card

### How it works today

`/lobby2 <gameid>` (`bot/commands/matches.py:143`) posts a "Looking up the live
lobby…" embed as its own slash reply, then hands that message to a
`LobbyAnnouncer` (`bot/lobby/announce.py:38`) which subscribes to the
aoe2companion WebSocket filtered to that one game id and edits the reply in
place as socket events arrive. If the caller is in a ranked match awaiting a
report, the game is also silently linked for auto-result (`link_manual`,
`bot/lobby/completed.py:387`).

The design is sound; the "sometimes no proper lobby view / no live updates"
symptom traces to five concrete defects, all in the announcer/watcher edit
loop.

### Root causes, in likely order of impact

**1.1 — The card can hang on "Looking up the live lobby…" forever.**
`LobbyAnnouncer._run` (`bot/lobby/announce.py:67-79`) only evaluates its exit
conditions (`_expired`, `_not_found`) *inside* the `async for` body — i.e.
only when a socket frame for this lobby actually arrives. The filtered feed
(`&match_ids=<id>`, `bot/lobby/socket.py:44`) is silent when the lobby doesn't
exist or is private, and `pong` keepalives are dropped before yielding
(`socket.py:34`), so a bad/private/already-started game id produces **zero
frames** → the loop never wakes → the 25-second `NOT_FOUND_GRACE` message at
`announce.py:113-116` never posts and the card is stuck on the loading state
until process restart. Fix shape: race the frame iterator against a 5–10s
ticker (`asyncio.wait_for` per-frame timeout, or merge a timer into the
stream) so `_not_found`/`_expired` are re-checked even with no traffic. The
same structural issue exists in `LobbyWatcher._run` (`bot/lobby/watcher.py:76-83`),
though the unfiltered firehose it uses makes silence much rarer.

**1.2 — Trailing-edge debounce drops the last update.**
`_render` (`announce.py:87-100`, same pattern `watcher.py:128-144`) skips the
edit when it falls inside the 3s `EDIT_DEBOUNCE` window and **does not
schedule a retry** — `_last_text` stays stale and the change is only rendered
if a *later* socket event arrives. On the filtered feed, events only arrive
when the lobby changes, so the last join before a quiet period is never
displayed: the card sits one player behind reality ("not dynamically updated
as ppl join"). Fix shape: when debounced, arm a one-shot trailing edit
(`asyncio.call_later`-style task) instead of dropping the render.

**1.3 — A single failed edit permanently freezes the card.**
`_render` sets `_last_text = rendered` *after* `_safe_edit` regardless of
whether the edit succeeded (`announce.py:97-99`; `watcher.py:140-143` same).
`_safe_edit` swallows all `DiscordException`s (429 bursts, message deleted by
a mod, missing perms) and merely logs (`announce.py:144-150`). After one
failure, that state is marked rendered and identical re-renders are skipped —
if the message was deleted, every future edit fails silently and the "live"
card is gone for good. Fix shape: only advance `_last_text` on success, and on
a 404 (message deleted) either re-send the card or stop the announcer.

**1.4 — Duplicate `/lobby2` for the same id orphans the second reply.**
`announce.start` dedupes on `active[game_id]` and returns the existing
announcer (`announce.py:153-161`), but the *new* slash reply created at
`matches.py:172-177` is never edited by anyone — it displays "Looking up the
live lobby…" forever. The original plan called for replying with a jump link
to the existing card (`docs/aoe2-lobby-replication-plan.md:107`); that was
never implemented. Fix shape: when an announcer already exists, edit the new
reply to "Already tracking — [jump]" (or adopt the new message).

**1.5 — Defer race can kill the command's reply outright.**
`run_slash` shields the handler and, on a ~2.5s timeout, calls
`interaction.response.defer()` while the handler keeps running
(`bot/context/slash/commands.py:52-55`). `lobby2` checks
`inter.response.is_done()` and then calls `send_message`
(`matches.py:172-177`) — if the defer lands between the check and the send,
`send_message` raises `InteractionResponded`, the generic handler turns the
whole command into a "RuntimeError" embed (`commands.py:67-68`), and no lobby
card exists at all (while `link_manual` already committed the DB link). The
handler does slow work *before* replying (a `find` over matches plus up to
three DB round-trips in `link_manual`, `completed.py:393-426`), so crossing
2.5s is realistic under DB latency. Fix shape: send the loading embed (or
defer explicitly) **first**, then do the ranked-link work.

### Secondary observations

- `HARD_TTL` ends tracking after 90 min with only a greyed "closed" card
  (`announce.py:25`, `watcher.py:28`) — reasonable, but users get no hint the
  tracking has a lifetime.
- The ranked auto-watcher can flip its card between two same-named `test123`
  lobbies mid-fill because `pick_candidate` re-picks the "best" candidate on
  every event (`bot/lobby/view.py:11-25`) — confusing but harmless.
- `link_manual` backdates `created_at` by 16 min to defeat the poll floor
  (`completed.py:417`, vs `FLOOR_SECONDS` at `bot/lobby/jobs.py:26`) — works,
  but it falsifies a timestamp that `_reap_stale` also reads
  (`jobs.py:80-92`); a lobby linked but never launched gets reaped on the
  wrong clock. A dedicated `next_poll_at`/`poll floor` column would remove the
  hack (the row currently overloads `last_edit_at` for that too,
  `jobs.py:125-131`).
- Each ranked match's watcher opens its own unfiltered firehose subscription
  (`watcher.py:77`) — fine at one active match, worth a shared feed if
  concurrent matches become the norm.

---

## 2. Check-in phase: the drop-off emoji

**The feature already exists** — `⛔ NOT_READY_EMOJI`
(`bot/match/check_in.py:15`) backs the player out and subs in the next queued
player (`back_out`, `check_in.py:158-166`), gated on the `check_in_discard`
queue config which **defaults to on** (`bot/queues/pickup_queue.py:72-77`).
`/notready` (`set_ready(..., False)`) is the slash equivalent
(`check_in.py:125-134`). So the ask is really "make it reliable/visible".
Reasons it fails or appears missing in practice:

**2.1 — Reaction *adds* use the cache-dependent event; removes were already
fixed.** `on_reaction_add` (`bot/events.py:214-216`) only fires for messages
still in nextcord's message cache. The codebase itself documents this exact
failure mode when it fixed the remove side with `on_raw_reaction_remove`
(`events.py:220-253`, point 1 of the comment). In a busy channel, the check-in
message can be evicted → clicks on ☑/⛔ are **silently ignored** → players
"can't check in", the timeout fires and reverts the match. Fix shape: mirror
the remove fix — switch to `on_raw_reaction_add`.

**2.2 — One failed `add_reaction` aborts all remaining emojis.** The seed loop
wraps *all* adds in a single try/except (`check_in.py:66-70`); the first
`DiscordException` (rate limit, permission blip) means ⛔ and the map-vote
numbers never appear at all. Fix shape: per-emoji try/except.

**2.3 — Keycap emojis 6–9 are malformed.** `INT_EMOJIS`
(`check_in.py:16`) — entries 1–5 are `digit + U+FE0F + U+20E3` but 6–9 are
missing the `U+FE0F` variation selector (verified by codepoint dump). Discord
rejects/mismatches the short form: with ≥6 map options `add_reaction` fails
(and via 2.2 kills the rest of the loop), and the `str(reaction) in
INT_EMOJIS` comparison in `process_reaction` (`check_in.py:103`) misses votes
sent in canonical form.

**2.4 — Off-by-one in the vote handler crashes the reaction callback.**
`process_reaction` uses `if idx <= len(self.maps)` (`check_in.py:105`) — when
a user reacts with a number emoji one past the map count (or *any* number when
`maps == []`), `self.map_votes[idx]` raises `IndexError` inside the event
handler. Should be `idx < len(self.maps)`.

**2.5 — `finish()` is not idempotent and can enter a 1s error loop.**
`finish` pops the reaction callback **without a default**
(`check_in.py:85`) and calls `self.message.delete()` unguarded
(`check_in.py:92`). If the message was already deleted (mod cleanup, or a
prior partial finish), `delete()` raises; `next_state` never runs; the match
stays in CHECK_IN past `end_time`, so `think()` re-calls `finish()` **every
second** (`check_in.py:53-58`), now hitting `KeyError` on the second pop —
until `on_think`'s guard removes the match after 5 consecutive errors
(`bot/events.py:96-98`), i.e. the match evaporates. Every other exit path
(`revert_single` `check_in.py:169-173`, `abort_timeout` `check_in.py:184-188`)
already uses `pop(..., None)` + guarded delete; `finish` should too. This is
the most likely source of the observed intermittent check-in breakage.

**2.6 — Timeout policy is unresolved (recent revert).** Commit `e8c58b4` ("Fix
check-in timeout revert") changed timeout behavior to *revert when anyone is
not ready, else finish*, and was immediately reverted (`4c3398a`). Current
behavior: `check_in_discard` on → **the whole match aborts** even if 7 of 8
checked in (`abort_timeout`, `check_in.py:181-197`); off → `finish()` **starts
the match with AFK players in it**. The design doc
(`docs/superpowers/specs/2026-06-28-checkin-backout-and-flexible-subauto-design.md`)
already proposes the right middle ground: sub in queued players for the
no-shows first, revert only when nobody is available. Decide and land one
policy; the pure helper from `e8c58b4` (`check_in_timeout_action`) is a good
base to reinstate with the sub-first behavior added.

**2.7 — Cosmetics/consistency.**
- The embed always advertises "React with ⛔ to back out" even when
  `check_in_discard` is off (`bot/match/embeds.py:44-52` vs the gate at
  `check_in.py:64`) — users click an emoji that isn't there / does nothing.
- The check-in message is first sent as the literal text
  `!spawn message <id>` and then edited into the embed (`check_in.py:61-62`) —
  briefly visible junk; send the embed directly.
- `waiting_reactions` TTL-sweeps callbacks after 30 min
  (`bot/__init__.py:51`), but `check_in_timeout` validates up to 3600s
  (`pickup_queue.py:67`) — a 31+ minute check-in window would have its live
  subscription swept mid-flight. Clamp the config or derive the TTL from it.

---

## 3. Post-match "who won and how" output

### The problem, quantified

A single ranked match resolved through the auto-sync path currently produces
**~six separate Discord posts spread over several minutes**, from three
independent background jobs:

| # | Message | Source | When |
|---|---|---|---|
| B | "🏆 Game over — **Team** won… react ✅" | `bot/lobby/completed.py:218-278` | LobbyJobs poll |
| A | Rating results ```markdown``` block | `bot/match/match.py:417-443` via `bot/stats/stats.py:248` | on report |
| C | "🎬 Replay ready" links embed | `bot/civ_matcher.py:197-228` | +1–7 min (retry ladder) |
| D | "What the Civs Say" narrative embed | `bot/post_game.py:514-556` via `civ_matcher.py:193` | right after C |
| E | "🧾 Match Cards" + "⚔️ Final Tale of the Tape" (2 embeds, 1 msg) | `bot/post_game.py:639-662` | replay-stats 150s tick |

D, E-cards and E-analysis are **three overlapping narrations of the same
result** (civ-meta take, per-player impact cards with 👑 CARRY, team-read
lines). Pre-game there are three more embeds (teams card, "Tale of the Tape"
insights, suggested civ pools — `match.py:445-470`), and the pre-game insights
title ("⚔️ Tale of the Tape", `bot/team_insights.py:546-586`) nearly collides
with the post-game one ("⚔️ Final Tale of the Tape") — easy to confuse.

### Recommended consolidation (the "only the interesting parts" ask)

Target: **one result message** (plus the unavoidable earlier ✅-confirm prompt
B), assembled when the data is ready rather than dribbled out:

1. Single "Match #N — Team X won" embed containing: compact rating deltas
   (top-level winners/losers with avg team delta, per-player lines only for
   1v1/small games), the replay links as a field (folding C in), and **the
   best 2–3 narrative bullets** selected across what are now D and E — rank
   the candidate lines by "interestingness" (biggest z-score, carry margin,
   strongest civ-meta anomaly) and cut the rest. The tunables already exist:
   `MAX_BULLETS`, `MAX_ANALYSIS_LINES`, `MAX_CARD_TAGS` (`bot/post_game.py:29-37`).
2. Since replay parsing lags the report, post the embed once with what's known
   (result + ratings) and **edit it in place** when civ/replay data lands —
   the codebase already has the edit-in-place machinery from the lobby cards.
3. Move per-player impact cards (E-cards) behind an on-demand command
   (`/matchcard <id>` or a "details" button) instead of auto-posting.

### Bugs found in this pipeline

- **A can exceed the 2000-char message limit and fail the report.**
  `print_rating_results` builds one unchunked string and sends via a bare
  `channel.send` (`match.py:417-443`, `bot/context/context.py:116-118`) with
  **no try/except at the call site** (`stats.py:248`) — a big team match
  raises `HTTPException` that bubbles up through `finish_match` into the
  `/report` command. Chunk it or guard it.
- **Stray debug print**: `print(match['winner'])` in `undo_match`
  (`bot/stats/stats.py:271`).
- **C and D are two back-to-back `channel.send`s** (`civ_matcher.py:192-193`)
  — should be one message even before the bigger consolidation.
- The two-embed E message shares the 6000-char combined budget with no total
  check (`post_game.py:656`); currently safe due to per-field caps, but
  fragile.
- Broad `except Exception: log` around C/D/E (`civ_matcher.py:227,246`,
  `post_game.py:660`) is intentional isolation but means a half-posted story
  (C lands, D dies) is invisible — worth a single wrapper that posts/edit one
  message atomically.

---

## 4. Web dashboard

Overall: **in decent shape** — better than expected for this class of app.
CSRF is properly enforced with per-session tokens and `compare_digest`
(`bot/web.py:298-314`), OAuth state is validated and single-use
(`web.py:2168-2184`), sessions are DB-persisted so logins survive redeploys
(`web.py:99-119`), cookies are `HttpOnly`/`SameSite=Lax`/conditionally
`Secure` (`web.py:2226-2227`), the SPA consistently escapes API data through
`esc()` (`bot/web_page.html:1299-1304`), there are no open redirects, and the
`/health` probe + Railway supervision is well done (`web.py:327-382`,
`PUBobot2.py:66-108`, `railway.toml:12`).

Issues to fix, by priority:

1. **`/api/debug` is unauthenticated** (`web.py:2485-2496`) and leaks all
   guild ids/names, configured channel ids and queue counts to anyone.
   It's marked "temporary" — remove it or gate on a session + `_check_admin`.
2. **OAuth callback can 500 on transport errors.** The Discord token/user
   calls have no timeout and no `except aiohttp.ClientError`, and
   `token_data['access_token']` is a raw subscript (`web.py:2191-2210`).
   A flaky Discord API turns login into a bare 500.
3. **`WS_ENABLE` is dead config.** Documented as required (CLAUDE.md,
   `config.example.cfg:25`), written by `start.py:103`, **never read** —
   the web server starts unconditionally (`PUBobot2.py:177`). Either gate
   `start_web_server` on it or delete the knob everywhere. Similarly
   `WS_HOST`/`WS_PORT`/`WS_SSL_*` are written but ignored — the server
   hardcodes `0.0.0.0:$PORT` (`web.py:2578-2584`).
4. **No rate limiting / caching on heavy public endpoints.**
   `/api/player-stats` runs ~15 aggregate queries per anonymous request
   (`web.py:1971-2074`); `/auth/login` inserts a DB row per hit
   (`web.py:2138`). A cheap per-IP throttle + a 30–60s in-memory cache for
   the stats endpoints closes an easy DB-exhaustion vector.
5. **Per-request Discord fetches without timeout.** Config/guild handlers do
   `await guild.fetch_member(...)` in the request path on cache miss
   (`web.py:2287,2311,2343,2411,2436`) — a 429 stalls the dashboard.
6. **Leaderboard avatar lookup is O(rows × guilds × members)** on cache miss
   (`_avatar_for_user_id`, `web.py:547-559`, called per row up to 500 rows) —
   a scaling cliff.
7. Minor: dead branch on a never-selected `aoe2_name` column
   (`web.py:1545-1546`); config-save error text echoed verbatim to the client
   (`web.py:2389`); `qc.cfg.update` mutates live bot state with no
   synchronization against the think loop (low frequency, real race);
   always set `WS_ROOT_URL` in prod so the `Secure` cookie flag can't degrade
   under spoofed `X-Forwarded-Proto` (`web.py:171-177`).

The quiz pipeline was explicitly reported working well and was left alone.

---

## 5. Prioritized fix list

**P0 — active breakage users see**
1. Check-in `finish()` idempotency (2.5) — guarded delete + `pop(..., None)`.
2. `on_raw_reaction_add` for check-in (2.1).
3. Announcer watchdog so `/lobby2` can't hang on "Looking up…" (1.1).
4. Trailing-edge re-render + only-advance-on-success in `_render` (1.2, 1.3).

**P1 — correctness and UX**
5. Reply-first ordering in `lobby2` to kill the defer race (1.5); jump-link
   reply on duplicate `/lobby2` (1.4).
6. Decide the check-in timeout policy (sub-first, then revert) and re-land the
   reverted `e8c58b4` approach with it (2.6).
7. Per-emoji try/except + fix keycap 6–9 + off-by-one vote index (2.2–2.4).
8. Consolidate post-match output into one edited-in-place result embed;
   chunk/guard `print_rating_results` (section 3).
9. Remove `/api/debug`; harden the OAuth callback (4.1, 4.2).

**P2 — hygiene**
10. `WS_ENABLE` and dead `WS_*` knobs (4.3); rate-limit/cache public stats
    endpoints (4.4); avatar lookup cost (4.6).
11. Check-in embed/⛔ text consistency, `!spawn message` placeholder, TTL vs
    max timeout (2.7); `next_poll_at` column instead of backdated
    `created_at` (1 — secondary); stray `print` in `undo_match`.
