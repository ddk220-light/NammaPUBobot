# Implementation Spec — robustness fixes + productization Phase 1/2

Written 2026-07-05 for a later implementation session (target executor:
Claude Sonnet 5). This document is self-contained: it translates
`docs/robustness-review-2026-07-05.md` and
`docs/productization-plan-2026-07-05.md` (including the resolved decisions
and risk register in that file) into ordered work packages. Read those two
documents first for the *why*; this one is the *what and how*.

Line numbers were verified against commit `dbd4fef`. **Re-verify every
file:line before editing — the code may have drifted.** If a cited symbol
moved, find it by name; if it's gone, stop and flag rather than guess.

## Ground rules for the implementing session

- **Conventions**: the original codebase uses TABS for indentation
  (`bot/`, `core/`); newer files (`utils/`, `bot/civ_stats.py`, `bot/web.py`
  helpers) use 4 spaces — match whichever the file you're editing uses.
  Lint: `ruff check .` (config `ruff.toml`, line length 120, py311).
  Tests: `pytest tests/` (pure-function tests only; CI has **no MySQL**, so
  never write a test that needs a live DB — use fakes).
- **Verification per work package**: run `ruff check .` and `pytest tests/`
  after each package; each package below lists its own acceptance criteria.
  Commit per package with a descriptive message (not per file).
- **Philosophy to preserve**: fork-added features (lobby, quiz, replay
  stats, banter) are *best-effort and isolated* — they must never break the
  core queue/match/report flow. Keep their try/except walls; the fixes below
  make failures visible in logs and idempotent, they do not remove the
  isolation.
- **Do not** touch `saved_state` serialization formats, the rating math in
  `bot/stats/rating.py`, or `update_db.py` unless a package says so.
- **Branching**: work on a feature branch off the default branch; never push
  to main. One PR per part (Part A, Part B, …) is a sensible granularity.
- **Event handler caveat**: `core/client.py`'s `@dc.event` decorator
  *replaces* nextcord's built-in dispatch and allows multiple handlers per
  event (see comment at `bot/events.py:198`). New event handlers must be
  registered through `@dc.event`, not nextcord idioms.

---

# PART A — Check-in reliability (P0)

### A1. Make `CheckIn.finish()` idempotent

File: `bot/match/check_in.py:84-97`.
Current: `bot.waiting_reactions.pop(self.message.id)` (no default) and an
unguarded `await self.message.delete()`. If the message was already deleted,
`delete()` raises, `next_state` never runs, and `Match.think()` re-calls
`finish()` every second (`check_in.py:53-58`) until `on_think`'s
5-consecutive-errors guard silently drops the match (`bot/events.py:96-98`).
Change:
- `pop(self.message.id, None)`.
- Wrap `await self.message.delete()` in `try/except DiscordException: pass`
  (match the pattern already used in `revert_single` at `check_in.py:169-173`
  and `abort_timeout` at `:184-188`).
- Guard against `self.message is None` (send may have failed in `start()`).
- Add an idempotency latch (e.g. `self._finished = True` checked at entry)
  so a second call is a no-op even if the first died midway.
Acceptance: calling `finish()` twice in a row raises nothing; a deleted
check-in message no longer blocks `next_state`.

### A2. Switch check-in reaction *adds* to the raw event

File: `bot/events.py:213-216`.
Current: `on_reaction_add` only fires for messages in nextcord's message
cache; evicted check-in messages silently ignore ☑/⛔ clicks. The remove
side was already fixed — copy that implementation exactly:
`on_raw_reaction_remove` at `bot/events.py:219-253` (payload → skip self →
lookup `bot.waiting_reactions[payload.message_id]` → resolve guild + member
→ call the callback with `payload.emoji` and `remove=False`, wrapped in
try/except that logs with traceback). Replace `on_reaction_add` with an
`on_raw_reaction_add` mirror; keep the callback signature
`(reaction, user, remove=False)` — `PartialEmoji.__str__` is compatible with
how `process_reaction` compares (`str(reaction)`).
Acceptance: check-in reactions work on a message that is not in the client
message cache (verifiable by unit-testing the handler with a stub payload
and a fake `waiting_reactions` entry; the handler function should be
importable/testable without a gateway connection).

### A3. Seed reactions one-by-one

File: `bot/match/check_in.py:64-70`.
Current: one try/except around the whole `for emoji in emojis` loop — the
first failure (rate limit, bad emoji) aborts all remaining reactions,
including ⛔. Change to per-iteration try/except with a
`log.warning` naming the emoji that failed.

### A4. Fix keycap emojis 6–9

File: `bot/match/check_in.py:16`.
`INT_EMOJIS` entries 1–5 are `digit + U+FE0F + U+20E3`; 6–9 are missing the
`U+FE0F` variation selector (verified by codepoint dump). Normalize all nine
to the 3-codepoint form. Also make `process_reaction`'s membership test
tolerant of both forms (strip `️` before comparing, so a client that
sends the short form still matches).

### A5. Fix the map-vote off-by-one

File: `bot/match/check_in.py:103-112`.
`if idx <= len(self.maps)` must be `idx < len(self.maps)`; with
`maps == []` any 1️⃣ reaction currently raises `IndexError` inside the event
handler. Add a regression test in `tests/` for a pure extraction of this
guard if practical (the method itself needs Discord objects; testing the
boundary logic via a small pure helper is acceptable).

### A6. Re-land the timeout policy with sub-first semantics

Files: `bot/match/check_in.py:43-58`, `bot/match/subbing.py`,
`tests/test_subauto.py`.
History: commit `e8c58b4` extracted `check_in_timeout_action()` (pure) and
changed the timeout branch; it was reverted wholesale in `4c3398a`. Current
behavior on timeout: `check_in_discard` on → **abort the entire match** even
if 7/8 checked in; off → **start the match with AFK players**.
Desired policy (per
`docs/superpowers/specs/2026-06-28-checkin-backout-and-flexible-subauto-design.md`):
1. On timeout, for each not-ready player, attempt
   `replace_player()` (`check_in.py:136-156`) to pull the next available
   queued player.
2. Players successfully replaced: announce the swaps, refresh the check-in
   message, and **extend the deadline** by a grace window (suggest 60s,
   constant) for the newcomers to ready up.
3. If any not-ready player cannot be replaced (queue empty): revert via
   `abort_timeout()` as today.
Re-introduce the pure `check_in_timeout_action(frame_time, end_time,
num_not_ready)` helper from `e8c58b4`
(`git show e8c58b4 -- bot/match/subbing.py` has the exact code) and extend
it, or add a sibling helper, so the branch decision stays unit-testable.
Restore/extend the tests from `e8c58b4`'s `tests/test_subauto.py` diff.
**Why the previous attempt was reverted is not recorded** — before starting,
check `git log`/PR discussion for `4c3398a`; if the revert was behavioral
(not just a bad deploy), confirm the desired policy with the owner via a
question rather than assuming.

### A7. Check-in cosmetics

- `bot/match/embeds.py:44-52`: only mention ⛔ when
  `self.m.check_in.allow_discard` is true (both branches of the embed text).
- `bot/match/check_in.py:61-62`: send the embed directly
  (`ctx.channel.send(embed=self.m.embeds.check_in(not_ready))`) instead of
  the `!spawn message {id}` placeholder text + edit.
- `bot/__init__.py:51` (`_TTLReactionDict.TTL_SECONDS = 30*60`) vs
  `bot/queues/pickup_queue.py:67` (`check_in_timeout` validated
  `0 < i < 3601`): either clamp the config validator to `<= 1800` or set the
  TTL from the max configured timeout + margin. Pick the validator clamp
  (simpler) unless the owner objects.

---

# PART B — `/lobby2` live card reliability (P0/P1)

All in `bot/lobby/`. Keep every change inside the module's best-effort
walls. The pure logic additions (debounce state machine) belong in
`bot/lobby/view.py` or a new pure module so they're testable in
`tests/test_lobby_view.py` style.

### B1. Watchdog: never hang on "Looking up the live lobby…"

File: `bot/lobby/announce.py:67-79` (and the same pattern in
`bot/lobby/watcher.py:76-83`).
Current: exit conditions are only evaluated when a socket frame arrives; a
silent filtered feed (bad/private/started id) never wakes the loop, so the
25s `NOT_FOUND_GRACE` message never posts.
Change: restructure `_run` so the loop wakes at least every ~5s with no
frame. Two acceptable shapes:
- (preferred) wrap the frame-iterator in a helper that yields `None` on a
  5s `asyncio.wait_for` timeout, i.e.
  `async for events in _with_ticks(socket.iter_frames(...), 5.0):` where
  `events is None` means "tick" — then evaluate `_not_found()/_expired()`
  every iteration;
- or run a separate ticker task that cancels the main task on expiry.
Note `socket.iter_frames` is an async generator that reconnects internally —
don't break its cancellation semantics (`socket.py:62-67`); the wait_for
wrapper must pump the *same* generator instance (get `__anext__()` futures,
don't recreate the generator per tick).
Acceptance: with a stubbed frame source that never yields, the announcer
posts the "not found" edit within ~30s and terminates.

### B2. Trailing-edge debounce

Files: `bot/lobby/announce.py:87-100`, `bot/lobby/watcher.py:128-144`.
Current: an edit skipped inside the 3s `EDIT_DEBOUNCE` window is dropped —
`_last_text` stays stale and nothing re-renders until the *next* socket
event, so the final state before quiescence never displays.
Change: when a render is suppressed by the debounce, record
`self._dirty = True`; on the next tick from B1's watchdog (or a one-shot
`asyncio` timer armed at suppress time), re-run `_render`. Ensure only one
pending trailing render exists at a time.
Acceptance (pure test): a sequence \[render, +1s change, silence\] ends with
the second state rendered.

### B3. Only mark rendered on success; handle deleted messages

Files: `bot/lobby/announce.py:97-99,144-150`;
`bot/lobby/watcher.py:140-143,219-231`.
Change `_safe_edit` (and watcher `_safe_send`) to return `True/False`; only
advance `_last_text`/`_last_edit` on `True`. On a 404/NotFound
(`nextcord.errors.NotFound`), stop the announcer (message is gone) — do not
retry forever. On other `DiscordException`s, leave state stale so the next
tick retries.

### B4. Reply-first ordering in the `lobby2` command

File: `bot/commands/matches.py:143-178`.
Current order: parse id → find ranked match → `link_manual` (up to 3 DB
round-trips) → *then* send the loading embed; combined with `run_slash`'s
shielded 2.5s auto-defer (`bot/context/slash/commands.py:52-55`), the
`is_done()` check at `matches.py:173` races `defer()` and can raise
`InteractionResponded`, failing the whole command after the DB link already
committed.
Change: send the loading embed (or `await inter.response.defer()` then
followup) **first**, immediately after parsing/validating `gameid`; do the
ranked-match `link_manual` work after the message exists. Wrap the send in a
`try/except InteractionResponded` fallback to `followup.send` for the
residual race.

### B5. Duplicate `/lobby2` → jump link

Files: `bot/lobby/announce.py:153-161`, `bot/commands/matches.py`.
Current: second invocation for the same id returns the existing announcer
and orphans the new reply on the loading embed forever.
Change: `announce.start` should signal "already active" (return a tuple or
expose `active.get(game_id)` to the command); the command then edits its
reply to a small embed: "Already tracking this lobby — \[jump\](url)" using
`existing.message.jump_url` when available.

### B6. Watcher parity

Apply B1–B3 to `LobbyWatcher` (`bot/lobby/watcher.py`) with the same
mechanics. Additionally, `pick_candidate` re-picks the best candidate every
event (`bot/lobby/view.py:11-25`) and can flip the card between two
same-named lobbies; once `self.linked` is true, pin rendering to
`self.game_id` only.

---

# PART C — Post-match output consolidation (P1)

Goal (owner decision): **one result message per match**, edited in place as
slow data arrives, replacing today's ~6 posts (see robustness review §3
table). Components today:
- rating results markdown block — `Match.print_rating_results`,
  `bot/match/match.py:417-443`, called from `bot/stats/stats.py:248`
- replay-link embed — `bot/civ_matcher.py:197-228`
- civ-meta narrative — `bot/post_game.py:514-556` via `civ_matcher.py:193`
- match cards + analysis — `bot/post_game.py:639-662` via
  `bot/replay_stats/jobs.py:116-123`

### C1. Result-message registry

Create `bot/match/result_message.py` (or similar): on ranked match finish,
`register_match_ranked` posts ONE embed ("Match #N — {winner} won" +
rating-delta field) and persists `(bot_match_id, channel_id, message_id)` —
suggest a new small table `qc_result_messages` via `db.ensure_table` (follow
the `bot/lobby/__init__.py:26` pattern). Provide
`async def attach_field(bot_match_id, name, value)` and
`async def attach_lines(bot_match_id, lines)` helpers that fetch the message
(channel → `fetch_message`) and edit the embed, each fully guarded
(NotFound → give up silently; log other failures with `channel_id` in the
line).

### C2. Convert the producers

- `print_rating_results`: becomes the field builder for C1's initial embed.
  Keep a compact format: team avg lines always; per-player lines only when
  `len(players) <= 4` (1v1/2v2); otherwise "top gain/loss" single line.
  **Chunk guard**: total embed description/fields must respect 1024/6000
  caps — truncate with an ellipsis line, never raise. Wrap the whole call
  site at `stats.py:248` in try/except log (today an exception here fails
  the report — see robustness review §3 bug 1).
- `civ_matcher._post_replay_link` + `_post_civ_summary`
  (`civ_matcher.py:190-193`): replace with one `attach_field("🎬 Replay",
  links)` + `attach_lines(best civ bullets)` call.
- `post_game.post_match_analysis` (`post_game.py:639-662`): instead of
  posting two embeds, select the **top 2–3 lines** across analysis+cards by
  an interest score (carry margin, largest |z|) and `attach_lines` them.
  Keep the full-cards rendering code — it becomes an on-demand
  `/matchcard <id>` command later (do NOT build the command in this part;
  just keep the builder functions and their tests).
- Remove the stray `print(match['winner'])` at `bot/stats/stats.py:271`.

### C3. Verbosity control hook

Gate C2's attachments behind a channel config read
(`postmatch_verbosity`: `off | result_only | highlights | full`) — the
variable itself is added in Part E (E2); until then read it with
`qc.cfg.get(...)`-style tolerant access defaulting to `highlights`.
`result_only` = C1 embed only; `highlights` = + best bullets + replay link;
`full` = also auto-post the match-cards embed as today.

Acceptance for Part C: a simulated ranked report path produces exactly one
new message; pure tests cover the line-selection scorer and the truncation
guard; `pytest tests/` green.

---

# PART D — Dashboard hardening (P1, pre-product)

File: `bot/web.py` unless noted.

- **D1** Remove `/api/debug` (route at `:2565` area, handler `:2485-2496`).
- **D2** OAuth callback: wrap the token exchange + `/users/@me` fetch
  (`:2191-2210`) in try/except (`aiohttp.ClientError`, `asyncio.TimeoutError`,
  `KeyError`) → redirect to `/` with an error query param; add
  `aiohttp.ClientTimeout(total=10)` to the session.
- **D3** Honor `WS_ENABLE`: in `PUBobot2.py` (web start at `:168-177`),
  skip `init_web()` when `cfg.WS_ENABLE` is falsy. Note in
  `RAILWAY_SETUP.md`/`config.example.cfg` that lobby join buttons and the
  dashboard both require it.
- **D4** Simple throttle + cache: an in-memory token-bucket per client IP on
  `/auth/login` and the five stats endpoints; a 60s TTL cache keyed by
  endpoint+query for `handle_leaderboard`, `handle_match_stats`,
  `handle_player_stats`. No new dependencies — a dict + monotonic timestamps
  is fine at this scale.
- **D5** `fetch_member` calls in request handlers (`:2287,2311,2343,2411,2436`):
  wrap with `asyncio.wait_for(..., 5)` and a small TTL memo
  `(guild_id, user_id) → member|None` (this memo becomes the Part F
  permission resolver's substrate — build it as a reusable helper).
- **D6** `_avatar_for_user_id` (`:547-559`): replace the all-guilds member
  scan with a lookup scoped to the guild in request context + TTL cache of
  misses.

---

# PART E — Productization Phase 1: safe for a second server

Resolved decisions that bind this part (see productization plan §Decisions):
guild=account/channel=data-scope; stats guild-members-only by default;
game-bank quiz only for new tenants (no bundled civ CSV fallback); global
match ids stay.

### E1. Remove the boot-time rating seed

`bot/events.py:16-54` (`seed_ratings_from_csv`) and its call at `:271`.
Delete the startup call. Keep the function body only if trivially reusable
by the future import wizard; otherwise delete it and note the CSV format in
a comment in the import-wizard section of the productization plan. **This is
the single most important change in Part E — another community's ratings
must never auto-seed.**

### E2. `Features` config section (per-channel flags)

File: `bot/queue_channel.py` — extend `sections=` at `:38` with
`"Features"` and add variables (follow the existing `Variables.BoolVar` /
`StrVar` / `IntVar` / `OptionVar` patterns at `:39-303`):

| var | type | default | read sites to wire |
|---|---|---|---|
| `timezone` | StrVar (IANA name, verify with `zoneinfo.ZoneInfo`) | `"UTC"` | quiz post hour (`bot/quiz/jobs.py:19-23`), `get_today_civs` (`bot/civ_stats.py:131`, replaces hardcoded IST at `:20`) |
| `lobby_tracking` | OptionVar `off/manual/auto` | `manual` | `match.py:349` (auto watcher only when `auto`), `matches.py:143` (`/lobby2` denied when `off`) |
| `lobby_name_prefix` | StrVar | `namma-` | E5 |
| `postmatch_verbosity` | OptionVar `off/result_only/highlights/full` | `highlights` | Part C hooks |
| `postmatch_banter` | BoolVar | 1 | `post_game` narrative lines |
| `prematch_insights` | BoolVar | 1 | `match.py:454-460` |
| `civ_suggestions` | BoolVar | 1 (self-suppresses below sample) | `match.py:464-470` |
| `civ_min_games` | IntVar | 50 | `civ_stats.py:11` read site |
| `replay_ingest` | BoolVar | 0 | E7 |
| `replay_post_cards` | BoolVar | 1 | `replay_stats/jobs.py:116-123` |
| `stats_visibility` | OptionVar `members/public` | `members` | Part F |
| `elo_sync_bot_id` / `civ_sync_bot_id` | IntVar, default 0=off | replaces global `PUBOBOT_USER_ID`/`LOBBYBOT_USER_ID` reads in `bot/events.py` on_message sync paths |

CfgFactory persists to `qc_configs` automatically and the dashboard
auto-renders the section — no web form work needed. Existing channels get
defaults on first read (CfgFactory behavior); verify `factory_version`
doesn't need bumping (check `core/cfg_factory.py:84-90` — adding variables
should not require `update_db.py`; confirm by loading a pre-existing config
row in a scratch test with a fake adapter).

### E3. De-globalize the quiz

Files: `bot/quiz/store.py`, `bot/quiz/jobs.py`, `bot/commands/quiz.py`.
- `store.get_config()` (`store.py:12`): add `get_enabled_configs()` returning
  all `enabled=1` rows; keep `get_config(channel_id)` for targeted reads.
- Delete `disable_all()` (`store.py:29-32`) and its call in `quiz_enable`
  (`commands/quiz.py:32-35`); enabling channel B no longer disables A.
- `QuizJobs._run` (`jobs.py:53-58`): loop over `get_enabled_configs()`,
  posting per channel; per-channel bookkeeping columns
  (`last_post_ymd`, `last_leaderboard_week`) already exist on the row.
- Quiz hour: interpret `quiz_hour` in the channel's `timezone` (E2).
- Schedule: keep the single global `data/quiz_schedule.json` for now but add
  a per-channel *cursor* (new column `schedule_cursor` on `qc_quiz_config`)
  so each tenant progresses independently instead of sharing day indices;
  filter to `source == "game"` entries for channels other than the original
  community's (decision 3: game-bank only until per-tenant banks exist —
  hardcode the original channel id in config, `QUIZ_PLAYER_BANK_CHANNELS`
  list in `config.cfg`, rather than in code).

### E4. De-globalize replay stats

Files: `bot/replay_stats/jobs.py`, `bot/replay_stats/store.py`,
`bot/commands/replay_stats.py`.
- Replace `store.is_enabled()` (global row, `jobs.py:48`) with: ingest a
  match only when its `qc_matches.channel_id` has `replay_ingest=1` (E2
  flag). `find_new_match`/`find_due_retry` (`jobs.py:55-60`) need the
  channel join, or post-filter in Python via `bot.queue_channels` config.
- `/replaystats enable|disable` (`commands/replay_stats.py:27-38`) writes
  the channel flag instead of the global row. Keep `rs_config` table
  readable for migration (one release), then drop.
- Add operator kill switch: a global `RS_KILL` config var (in `config.cfg`
  schema + `start.py` template) that, when set, makes the job a no-op
  regardless of channel flags (risk R3 mitigation). Same pattern for the
  lobby feature (`LOBBY_KILL`).

### E5. Per-match lobby key (kill `test123`)

Files: `bot/lobby/watcher.py:27`, `bot/match/embeds.py:206`,
`bot/lobby/completed.py:390-398`, `bot/lobby/reducer.py:136` callers.
- `LobbyWatcher` takes `target_name` in its constructor:
  `f"{qc.cfg.lobby_name_prefix}{match.id}"` — computed at `match.py:349`.
- The match-start embed field (`embeds.py:206`) prints the same computed
  name.
- `_ingest`'s name filter (`watcher.py:100-105`) compares against
  `self.target_name` (case-insensitive, trimmed — keep current semantics).
- Remove the module constant; grep for remaining `test123` references
  (docs excepted).

### E6. Tenant scoping for `rs_*` / `cls_*`

- Add `channel_id` column to `rs_matches` (`bot/replay_stats/__init__.py:23`)
  and `cls_results` + `cls_match_ingest`
  (`bot/classifications/__init__.py:33,72`) — `db.ensure_table` handles
  ALTER-add (it's the established pattern per `bot/lobby/__init__.py:17`
  docstring; verify the adapter really ALTERs on new columns before relying
  on it — check `core/DBAdapters/mysql.py` `ensure_table`).
- Backfill: one-shot idempotent UPDATE joining through
  `rs_matches.bot_match_id → qc_matches.channel_id` (run from a small
  `scripts/backfill_channel_ids.py`, not on the tick).
- Writers: `replay_stats/jobs.py` ingest writes `channel_id` on insert;
  the offline `utils/classifications/runner.py` gets the same column
  (documented TODO if the offline runner is out of scope this pass —
  the *bot-side* reads are the priority).
- Readers: `bot/replay_stats/query.py:80-226`, `tag_leaderboard.py`,
  `bot/classifications/query.py:45-69` — every query takes and applies a
  `channel_id` filter (join through `rs_matches` for the per-player tables
  rather than adding the column to all 11 tables).
- `/insights` (`bot/commands/insights.py`) passes `ctx.qc.id`.
- `civ_elo_from_db` (`bot/civ_stats.py:42-57`): add `channel_id` param +
  WHERE; per decision 3, **remove the CSV fallback for all channels except**
  an explicit allowlist config (`CIV_CSV_CHANNELS` in `config.cfg`) covering
  the original community. `build_suggestion_embed` returns None (feature
  silent) below `civ_min_games`.

### E7. Tenant-scoped query layer + stats API scoping (risk R1)

New module `core/tenant_query.py` (or `bot/web_queries.py`):
- Helpers like `async def fetch_channel_rows(channel_id: int, sql: str,
  params: list)` that **require** a channel id and interpolate the
  `WHERE/AND channel_id = %s` themselves (accept a `{chan}` placeholder in
  the SQL so the helper controls where the filter lands). All five stats
  endpoints convert to it:
  - `/api/leaderboard` `bot/web.py:1883` (the join at `:1919-1920` lacks a
    WHERE — this is a live cross-tenant leak, fix first)
  - `/api/match-stats` `:1861`
  - `/api/player-stats` `:1949`
  - `/api/strategies` `:442` (needs E6's `cls_results.channel_id`)
  - `/api/civ-stats` `:387` (switch from the CSV file to per-channel
    `qc_match_civs` aggregate; empty → the SPA shows the unlock hint)
- Every endpoint gains a required `channel_id` query/path param; requests
  without it are 400. The SPA passes the currently-selected channel (the
  guild/channel picker endpoints already exist, `web.py:2274,2301`).
- **Lint guard**: add a CI grep (extend `.github/workflows/ci.yml` with a
  small script step) failing on `db.fetchall|db.execute` matches inside
  `bot/web.py` outside the query-layer module, with an allowlist comment
  token `# tenant-scope-ok: <reason>` for the exceptions.

### E8. Membership gating (decision 2)

`bot/web.py`: a helper `_check_viewer(request, channel_id)` — resolves the
session user, finds the channel's guild via `bot.queue_channels`
(`cfg_info.guild_id`), and checks membership through the D5 member memo.
Applied to all five stats endpoints and the SPA data routes; behavior:
- `stats_visibility == "public"` (E2 var): anonymous allowed.
- `"members"` (default): 401 without a session, 403 without membership.
The SPA needs a login-wall state for 401 on stats tabs (it already handles
401 in `authFetch`, `bot/web_page.html:1321-1326` — extend to the stats
fetch paths).

### E9. Two-tenant isolation test (Phase 1 exit gate, risk R1)

`tests/test_tenant_isolation.py`. CI has no MySQL, so:
- Implement `FakeDBAdapter` in `tests/` mimicking the adapter interface used
  by the query layer (`fetchall/fetchone/select/insert` — check
  `core/DBAdapters/mysql.py` for exact names), backed by dicts of canned
  rows tagged with `channel_id`.
- Tests assert: (1) every function in the tenant query layer refuses to run
  without a channel id; (2) each converted endpoint's query-builder, given
  channel A, produces SQL/params whose executed result (against the fake)
  contains zero channel-B rows; (3) the CI grep guard file list is in sync.
- Keep the tests import-light: they must not import nextcord (follow the
  existing pattern — `bot/lobby` keeps nextcord imports lazy for this exact
  reason).

### E10. `tenants` table + guild lifecycle

- `db.ensure_table` for `tenants` (`guild_id` PK, `name`, `owner_user_id`,
  `status` active/inactive, `created_at`) — declare in a new
  `bot/tenants.py`.
- `@dc.event on_guild_join`: upsert row + post a short welcome (dashboard
  URL + `/admin channel enable` hint) to the first writable channel;
  `on_guild_remove`: mark inactive (no data deletion — retention is
  Phase 3).

Part E acceptance: with two channels configured in the fake-adapter tests,
no endpoint or feature query returns foreign rows; a fresh channel enabled
with defaults gets: no rating seed, no civ suggestions, game-bank quiz only
if enabled, no replay ingest, manual-only lobby tracking.

---

# PART F — Phase 2: onboarding & per-channel dashboard (outline)

Lower resolution deliberately — re-plan details when Part E lands. Order:

1. **Unified permission resolver** (`bot/permissions.py`): levels
   owner/admin/moderator/viewer per (guild, user), sourced from
   `DC_OWNER_ID`, guild owner, Discord admin/manage-guild, and the
   per-channel `admin_role`/`moderator_role` (`bot/context/context.py:47-65`
   is the slash-side today; `bot/web.py:270-295` the web side). Both sides
   call the one resolver. Uses the D5 member memo.
2. **Audit log**: `audit_log` table; every config POST
   (`web.py:2387,2479`) and feature-flag slash command writes
   (who, channel, key, old→new, ts).
3. **Dashboard enable-channel**:
   `POST /api/guilds/{gid}/channels/{cid}/enable` → `QueueChannel.create`
   path (`bot/queue_channel.py:319`) with admin gate; disable endpoint
   mirrors `/admin channel disable` **and** must also delete config rows
   (note: the slash disable currently leaks rows — `commands.py:206` vs
   `main.py:53-56`; unify on full cleanup with a confirm step).
4. **Queue templates**: 3 canned `pq_configs` payloads (1v1, 2v2, 4v4
   draft) + a wizard endpoint; payloads defined as JSON fixtures.
5. **Per-channel SPA IA**: URL scheme `#/g/{gid}/c/{cid}/{tab}`; convert the
   **leaderboard tab first end-to-end** (risk R5 mitigation) — scoped API +
   picker + empty state — then replicate the pattern to the other tabs.
6. **Setup checklist / empty states**: checklist component driven by a new
   `GET /api/channels/{cid}/setup-status` (bot enabled, queues≥1, players
   seeded n, matches recorded n, features configured); every stats tab
   renders the relevant unlock hint when empty (thresholds surfaced from
   config: `civ_min_games`, `lb_min_matches`).
7. **Ratings CSV import wizard** (decision 4): upload (multipart, ≤1 MB) →
   parse+dry-run report → nick→member mapping table (auto-match exact
   nick against guild members; unmatched → "unclaimed" rows in a new
   `import_pending` table) → commit writes `qc_players` for the channel →
   `imports` audit row + revert endpoint (delete by import id). Never
   auto-merge fuzzy matches (risk R4).
8. **Per-channel health panel** (risk R6): last quiz post, last replay
   ingest state (`rs_ingest`), lobby feed connectivity, last 24h error count
   per channel (requires feature logs to carry `channel_id` — add while
   touching each feature in Parts B–E).

---

## Suggested execution order & sizing

| Order | Package | Size | Depends on |
|---|---|---|---|
| 1 | A1–A7 check-in | S–M | — |
| 2 | B1–B6 lobby | M | — |
| 3 | D1–D6 dashboard hardening | S–M | — |
| 4 | C1–C3 result consolidation | M | none hard; C3 reads E2 flag tolerantly |
| 5 | E1 seed removal | XS | — |
| 6 | E2 Features section | S | — |
| 7 | E3 quiz / E4 replay / E5 lobby-key | M | E2 |
| 8 | E6 rs/cls scoping + backfill | M | — |
| 9 | E7–E9 query layer + gating + isolation test | L | E6 |
| 10 | E10 tenants table | S | — |
| 11 | Part F (re-plan first) | XL | E complete |

Definition of done for each: `ruff check .` clean, `pytest tests/` green,
acceptance criteria met, behavior change noted in the commit message, and —
for anything touching the report/check-in/lobby flow — a manual smoke
scenario written into the PR description (the repo has no integration
harness; state what you exercised).
