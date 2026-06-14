# AOE2LobbyBOT Replication — Research & Plan

_Generated 2026-06-13 by a 10-agent research workflow (7 research + 2 adversarial verify + 1 synthesis). Both make-or-break feasibility claims were adversarially verified live._

---

## Agreed design (v2) — match-coupled lobby loop

Decisions locked 2026-06-13 (supersede the standalone /lobby plan below, which remains the verified technical reference):

- **Command:** `/lobby2` for the manual path (rename to `/lobby` after the old bot is removed). The PRIMARY path is automatic, coupled to pickup matches.
- **Lobby name (join key):** fixed `test123` for now. Assumes <=1 ranked match is awaiting a lobby at a time; switch to auto-unique `namma-<matchid>` before running concurrent ranked matches.
- **Scope:** ranked matches only (they already have captains, a WAITING_REPORT stage, and ratings). Unranked optionally gets an informational completed embed only.
- **Result confirmation:** a single `:white_check_mark:` by the LOSING captain -> auto `report_loss`. No dispute button. An ignored proposal does nothing. Drops are handled by re-creating the lobby (re-detected) or `/report abort`.
- **Sub-15-min games:** silent (no message).

### Flow 1 - announce + auto-detect (on ranked match start)
- Trigger: ranked match enters WAITING_REPORT (teams formed, the "... has started!" embed at bot/match/embeds.py:122).
- Add a field to that embed: "Create your AoE2 lobby named `test123`."
- Start a LobbyWatcher for the match: subscribe to the ALL-lobbies socket (wss://socket.aoe2companion.com/listen?handler=lobbies, no match_ids filter), filter incoming lobbies for name == "test123", for up to N minutes (config LOBBY_DETECT_WINDOW, default 10).
- Candidate -> roster-confirmed link: a `test123` lobby is only a CANDIDATE. Confirm the link when the lobby is full AND its player set matches the ranked match's player set (the bot owns both sides). This survives stray duplicate lobbies, cancel-and-restart, and two-people-create-one - none has the matching full roster at the right time. Confidence stacks: name + exactly-full count + timing window + any confirmed-player overlap (near-certain in single-active-match mode).
- On confirmed link: store gameId + every slot (profileId, name, team) on qc_lobbies.match_id; backfill any UNMAPPED slot to the DB by elimination (lobby set == match set, so the leftover slot is the leftover Discord player); optionally show the live fill embed.
- Re-detection: the watcher lives for the match's WAITING_REPORT lifetime and can detect MULTIPLE `test123` lobbies (drops/replays). Each new gameId = a new attempt; track seen gameIds to avoid double-proposing.

### Flow 2 - game over -> captain-confirmed loss
- When a linked gameId is finished AND duration >= 15 min (config LOBBY_MIN_MINUTES, default 15):
  - Identity is Discord-native: the reactor is a Discord member checked against the match's own teams (member in team[:1]), exactly like map voting (check_in.py process_reaction) and report_loss (match.py:351). The CORE loop never touches the profile CSV.
  - Best-effort winner hint: try to resolve the result via data.aoe2companion.com/api/matches/{gameId} and map slot profileIds -> Discord members to name the winner. This is OPTIONAL and must never block.
  - If the loser is confidently identified: post ":trophy: Looks like **Team A** won (34 min). Losing captain, react :white_check_mark: to confirm." and gate :white_check_mark: to the losing captain only (mis-click-safe; winner cannot self-report).
  - If NOT confidently mappable: degrade to "Game over (34 min) - losing captain, react :white_check_mark: or run /report loss." Do NOT guess a winner.
  - On :white_check_mark: by a (losing) captain -> match.report_loss(captain, draw_flag=False) -> existing finish_match + register_match_ranked. Whichever captain reacts reports THEIR team's loss, so a wrong/absent hint is self-correcting.
- Duration < 15 min -> silent.
- Proposal ignored (drop/cancel) -> nothing happens; players re-create `test123` (re-detected) or run `/report abort`.

### Flow 3 - unified results ledger
- Whether resolved via Flow 2 (:white_check_mark:) OR a manual /report loss | /report abort, the match's linked gameId(s) let the bot record the full game (gameId, civs, map, duration, winner/loser) in the SAME step as the rating update (finish_match -> register_match_ranked).
- This replaces the after-the-fact pipelines: civ_sync (scraping AOE2LobbyBOT embeds) and elo_sync (syncing ELO from the old Pubobot) become unnecessary. NammaPUBobot becomes self-sufficient.
- Storage: qc_lobbies gains a `match_id` column linking the detected lobby/game to the pickup match.

### Mapping to existing code
| Piece | Reuse |
|---|---|
| Announce lobby name | bot/match/embeds.py:122 final_message (add field) |
| Watcher start / teardown | tied to WAITING_REPORT in bot/match/match.py:332 (start_waiting_report) and finish_match:453 |
| Result reaction handling | bot.waiting_reactions + on_reaction_add (bot/events.py:184) - same as check-in |
| Auto loss report | match.report_loss (bot/match/match.py:347), unchanged |
| Record game + ratings | finish_match -> register_match_ranked (bot/match/match.py:453) |
| Winner/profile mapping | bot/civ_matcher.py _load_profile_map + data/*.csv |
| Live lobby feed | wss socket (Phase 0 spike confirms name field) |
| Completed result | GET data.aoe2companion.com/api/matches/{id} |

### Player <-> AoE2 profile mapping (DB-backed, self-healing)
- CSV's ONLY job today is civ attribution (which civ each Discord player played). Consumers: bot/civ_matcher.py (runtime), bot/civ_sync.py (runtime), utils/civ_analysis.py (offline). It is NOT used by ratings/identity/queue/matchmaking.
- Current state is unreliable: data/player_profile_map.csv has ~37 rows (4 blank) vs ~87 players (~40%), keyed on `nick` (renames break it) despite carrying the stable `user_id`. civ_sync.py:412 already auto-APPENDS new bindings, but (a) it learns them from AOE2LobbyBOT embeds (the dependency being replaced) and (b) it writes a FILE, lost on Railway redeploys unless a volume is mounted.
- New source of truth = the roster-confirmed lobby query. On link, each slot's (profileId, name) is authoritative for that match's Discord players. Persist discord_id <-> profile_id to a DB table (durable), backfilling unmapped slots by elimination; add a one-time /register <profile_id> fallback. The map self-heals every game; the CSV becomes a derived/seed artifact.
- The results/ratings loop never uses the map (Discord identity only). The map only powers the optional winner-name hint + per-player civ attribution, both best-effort and improving automatically.

### Deferred (not in v1)
- Auto-unique lobby names (needed once >1 ranked match can run concurrently).
- Best-of / multi-game series (v1: the first confirmed result resolves the match).
- Tunable winner-confidence threshold when the profile map is incomplete.

---

# FEASIBILITY VERDICT

FEASIBLE end-to-end, including the make-or-break live-lobby fill — but the live half must be built on a WebSocket subscription, not a REST poll.

LIVE LOBBY FILL (the load-bearing unknown) — CONFIRMED FEASIBLE via a public, unauthenticated WebSocket: wss://socket.aoe2companion.com/listen?handler=lobbies&match_ids=<gameId>. Adversarial verification connected anonymously and received only the requested lobby (1 lobbyAdded + N slotAdded), with per-slot profileId/name/civName/slot/team/color. The aoe2de://0/<id> numeric id IS the matchId. This is event-driven push (subscribe once, reassemble state from slotAdded/slotUpdated/slotRemoved/lobbyUpdated/lobbyRemoved deltas), NOT a few-second poll. The bot's existing aiohttp 3.13.5 supports ws_connect, so NO new dependency is needed. Caveat: undocumented/unofficial socket ("no public API yet") that can break on patches — isolate and degrade gracefully.

MATCH-COMPLETED EMBED — CONFIRMED FEASIBLE and largely already built. data.aoe2companion.com/api/matches/{matchId} (path param) returns the full match object in ONE call (teams, per-player civName/won/color/team/rating, mapName, started/finished → duration). Verified HTTP 200 live. A non-empty User-Agent is required (403 without; the bot already sends "NammaPUBobot/1.0"). Per-player replay links are deterministic (https://aoe.ms/replay/?gameId=...&profileId=...) and already constructed in bot/civ_matcher.py:185.

MATCH-END RESOLUTION CONSTRAINT: REST cannot fetch a live/open lobby by id (/api/matches?match_ids= → HTTP 422 "profile_ids must be specified"; /api/matches/<openId> → 404 until finished). You MUST capture every slot's profileId from the live socket WHILE the lobby is open, then after game-end query by-id (/api/matches/{id}) — and if that 404s during the lag window, fall back to /api/matches?profile_ids=<captured pids> + time/overlap pick, exactly as civ_matcher.py already does.

FALLBACKS (priority order): (a) consume the same aoe2companion socket poll-style (fresh &match_ids=<id> connection every N s, read <1s snapshot, close) — fits the existing think()/next_run idiom with no long-lived task; (b) Relic/World's Edge findAdvertisements (https://aoe-api.worldsedgelink.com/community/advertisement/findAdvertisements?title=age2) — unauthenticated open-lobby feed, but obscured positional-array needing a custom parser, lists ALL lobbies (filter client-side); (c) ship MVP completed-match-only and add live in Phase 2. NOT viable: Steam Web API GetLobbyData (needs publisher key + known id, can't enumerate), aoe2.net (sunset Oct 2025), aoe-api.reliclink.com host (dead — use worldsedgelink.com). Per-player .aoe2record download links are feasible (aoe.ms); live in-game spectating after launch (findObservableAdvertisements) needs Steam auth — correctly OUT OF SCOPE for v1.


# PLAN

## Goal
Replicate AOE2LobbyBOT in NammaPUBobot: a `/lobby <aoe2_gameid>` slash command that posts a live-updating LOBBY embed (join link `aoe2de://0/<id>`, map, server, slots filling as players join — driven by a live WebSocket), and on game-end posts a MATCH-COMPLETED embed (two teams, civ per player, winner trophy, duration, per-player recorded-game download links).

## High-level architecture
Four cooperating pieces, all on infrastructure that already exists in this repo:

1. **`/lobby <gameid>` slash command** — registered in `bot/context/slash/commands.py`, delegates via `run_slash()` to a new handler `bot/commands/lobby.py:lobby_cmd`. Defers immediately, validates the id, creates a `LobbyWatcher`, posts the initial LOBBY embed, persists a row to `qc_lobbies`.
2. **`LobbyWatcher`** (new `bot/lobby/watcher.py`) — owns one lobby's lifecycle: a state machine (CREATED → FILLING → IN_PROGRESS → COMPLETED, plus EXPIRED), the in-memory slot map, the editable `nextcord.Message` handle, and the captured profileIds. One persistent WebSocket subscription per active lobby reassembles slot state from socket deltas and edits the Discord message only when the roster changes.
3. **`LobbyJobs.think(frame_time)`** (new `bot/lobby/jobs.py`) — a recurring-job singleton copying `StatsJobs.think` (bot/stats/stats.py:401-407). Registered with ONE line in `bot/events.py:on_think` (alongside `bot.stats.jobs.think`). Drives: (a) the completed-match REST poll/backoff once a lobby reaches IN_PROGRESS, (b) reaping watchers that timed out / never filled, (c) on boot, rehydrating watchers from `qc_lobbies` and re-fetching their messages.
4. **Completed-match renderer** (new `bot/lobby/completed.py`) — fetches the finished match (reusing civ_matcher.py client + the by-id endpoint), assembles the two-team / civ / winner / duration / rec-links embed, posts a NEW message, marks the watcher COMPLETED.

The live socket runs as its own long-lived asyncio task per watcher (fire-and-forget, tracked in a module set to prevent GC — same pattern as `civ_matcher._pending`). The 1s `think()` tick is used only for slow/periodic work so a slow socket never blocks the tick.

---

## The `/lobby <gameid>` command
**Modify** `bot/context/slash/commands.py` — add (template copied from `/subauto`, commands.py:534-541):
```python
@dc.slash_command(name='lobby', description='Post a live-updating AoE2 lobby embed by game id', **guild_kwargs)
async def _lobby(
        interaction: Interaction,
        gameid: str = SlashOption(name="gameid", description="AoE2 lobby/game id (the number in aoe2de://0/<id>)")
): await run_slash(bot.commands.lobby_cmd, interaction=interaction, gameid=gameid)
```
**Create** `bot/commands/lobby.py` with `async def lobby_cmd(ctx, gameid)`:
1. `await ctx.interaction.response.defer()` immediately (the watcher's first socket fetch exceeds run_slash's ~2.5s inline window — `/suggest_civs` at commands.py:778 defers explicitly; do the same).
2. Validate `gameid`: strip a pasted `aoe2de://0/` prefix, require `\d+`. On bad input → `ctx.error(...)`.
3. Dedup: if a live `LobbyWatcher` exists for this gameid OR a non-terminal `qc_lobbies` row exists for `(channel_id, aoe2_game_id)`, reply with a jump link to the existing message.
4. One-shot socket snapshot (`&match_ids=<gameid>`, ~1s) to confirm the lobby exists. No `lobbyAdded` within timeout → `ctx.error("Lobby not found — may be private, friends-only, or already started.")`.
5. Build the LOBBY embed, `self.message = await ctx.channel.send(embed=...)` (check_in.py:48-49), store `message.id`.
6. `db.insert("qc_lobbies", {...}, on_dublicate="replace")`, register the watcher; watcher opens its persistent subscription.
7. `ctx.success("Tracking lobby <gameid>.")` (followup, since deferred).

**Register** the handler in `bot/commands/__init__.py` star-import set so `bot.commands.lobby_cmd` resolves.

---

## Polling job + lifecycle state machine

| State | Enter when | Behaviour | Exit |
|---|---|---|---|
| CREATED | `/lobby` invoked, embed posted | open socket; await first snapshot | snapshot → FILLING |
| FILLING | first lobbyAdded/slotAdded snapshot | reassemble slots from deltas; **edit message only when roster changed**; capture every slot.profileId | lobbyRemoved → IN_PROGRESS; lobby gone w/o launch → EXPIRED; watcher TTL → EXPIRED |
| IN_PROGRESS | lobbyRemoved (host launched) | close socket; edit LOBBY embed to "Game in progress…"; hand off to LobbyJobs REST backoff | finished match resolved → COMPLETED; result window exhausted → EXPIRED |
| COMPLETED | finished match resolved | post NEW MATCH-COMPLETED embed; status='completed' | terminal — deregister |
| EXPIRED | closed pre-launch or any TTL | grey out LOBBY embed footer; stop polling | terminal — deregister |

**Cadence & backoff:**
- FILLING is push-driven — no fixed cadence. A static lobby sends nothing (≠ failure). Debounce Discord edits: only `message.edit` when the rendered roster string differs from the last sent, clamped to ≥1 edit per ~3s.
- IN_PROGRESS result poll reuses `civ_matcher._RETRY_DELAYS = (60, 180, 420)` (civ_matcher.py:38) as the by-id backoff ladder (data lags minutes post-game). Driven from `LobbyJobs.think` via a per-watcher `next_poll_at` timestamp (the StatsJobs next_run idiom).
- Watcher TTL caps total life (e.g. 90 min) so a stuck id never polls forever.

**Register** in `bot/events.py:on_think` near events.py:95, after `await bot.stats.jobs.think(frame_time)`:
```python
await bot.lobby.jobs.think(frame_time)
```
Wrap lobby work in try/except inside `LobbyJobs.think` so a lobby error never breaks the tick (events.py already isolates match.think errors at 81-92).

---

## API client(s) + exact endpoints
Create `bot/lobby/api.py` reusing the civ_matcher.py pattern (ClientSession `User-Agent: NammaPUBobot/1.0`, `asyncio.Semaphore(5)`, `ClientTimeout(total=15)`, status guard, `(aiohttp.ClientError, TimeoutError, ValueError)` catch).

**LIVE LOBBY (WebSocket — the new piece):**
- `wss://socket.aoe2companion.com/listen?handler=lobbies&match_ids=<gameId>` — anonymous. On connect: lobbyAdded + per-slot slotAdded snapshot, then deltas: `lobbyAdded|lobbyUpdated|lobbyRemoved|slotAdded|slotUpdated|slotRemoved`. Use `aiohttp.ClientSession().ws_connect(url)` (aiohttp 3.13.5 in requirements — no new dep). Skip `type=='pong'` keepalives. Reassemble with a Python reducer ported from denniske/aoe2companion `src/api/socket/lobbies.ts`. Lobby fields: matchId, mapName, mapImageUrl, server (raw Azure region), totalSlotCount, blockedSlotCount, averageRating, gameModeName, leaderboardName, speedName, started(null open), finished(null). Slot fields: slot, profileId, name, civ/civName/civImageUrl(null pre-pick), rating, rank, color, colorHex, team, country, won.
- Reconnect: on close, reconnect + re-subscribe with backoff; de-dup identical consecutive snapshots; always clean up on lobbyRemoved/EXPIRED to avoid leaking connections.

**COMPLETED MATCH (REST — reuse civ_matcher.py):**
- PRIMARY: `GET https://data.aoe2companion.com/api/matches/{matchId}` (path param) — verified HTTP 200, bare match object in one call.
- FALLBACK during lag (by-id 404s a few min post-finish): `GET .../api/matches?profile_ids=<captured pids>&count=20&page=1` + overlap pick — exactly `_find_and_record` (civ_matcher.py:85-166). NOTE `?match_ids=` → HTTP 422; by-id works only as PATH form.
- Non-empty User-Agent mandatory (403 otherwise) — existing header covers it.

**REPLAY / JOIN LINKS (string construction, already in repo):**
- Download: `https://aoe.ms/replay/?gameId={matchId}&profileId={profileId}` (civ_matcher.py:185), gated on per-player `replay==true`.
- Watch: `{VISUALIZER_URL}/?match={matchId}&profile={pid}` (civ_matcher.py:184, env REPLAY_VISUALIZER_URL).
- Join: `aoe2de://0/<gameid>` — Discord does NOT auto-linkify this scheme, render as monospace/inline-code.

---

## Discord message create/edit lifecycle (reuse check_in.py)
- POST once: `self.message = await ctx.channel.send(embed=...)` (check_in.py:48-49).
- LIVE EDIT: `await self.message.edit(content=None, embed=new_embed)` (check_in.py:90), from the socket handler, wrapped in `try/except DiscordException` (check_in.py:91-92).
- After first response, edit the stored Message handle directly (no ctx needed) — the key reuse.
- Reaction machinery (bot.waiting_reactions, _TTLReactionDict bot/__init__.py:25-81) is NOT used — a live lobby only edits on events.

**LOBBY embed (FILLING):** title "Lobby created"; bold lobby name; monospace `aoe2de://0/<id>`; `Map: <mapName>` (+ mapImageUrl thumbnail); `Server: <server>`; Players section — color-numbered name per occupied slot, "Open" per empty slot, "+N slots remaining" (N = totalSlotCount − blockedSlotCount − filled).

**MATCH-COMPLETED embed:** title "Match completed"; `Map:` + `Duration: <M> min` (finished−started); two Team blocks each with winner 🏆 / loser ⬛ (per-player `won`), color-numbered rows (name as profile link), Civ column (civName index-zipped), Rec column (`[Download replay](aoe.ms/replay…)` where replay==true). The repo's reverse-engineered parser (civ_sync.py:80-234, golden fixture tests/test_civ_sync.py) is the authoritative layout reference — build the renderer parse-compatible with it.

---

## Match-end detection
1. **Launch:** socket emits `lobbyRemoved` for the watched matchId → FILLING → IN_PROGRESS, close socket.
2. **Finished + data:** LobbyJobs polls `/api/matches/{matchId}` on `_RETRY_DELAYS` until it returns a finished match → render COMPLETED. If lobbyRemoved was missed (bot down briefly), a periodic by-id probe still resolves completion.

---

## DB schema — `qc_lobbies`
Declare via `db.ensure_table` (auto-creates + ALTERs new columns at import) at the top of `bot/lobby/__init__.py` (imported from `bot/__init__.py`), mirroring civ_sync.py:15-33:
```python
db.ensure_table(dict(
    tname="qc_lobbies",
    columns=[
        dict(cname="id", ctype=db.types.int, autoincrement=True),
        dict(cname="aoe2_game_id", ctype=db.types.int),
        dict(cname="channel_id", ctype=db.types.int),
        dict(cname="message_id", ctype=db.types.int),
        dict(cname="completed_message_id", ctype=db.types.int, notnull=False),
        dict(cname="status", ctype=db.types.str),
        dict(cname="lobby_name", ctype=db.types.str),
        dict(cname="map_name", ctype=db.types.str),
        dict(cname="server", ctype=db.types.str),
        dict(cname="profile_ids", ctype=db.types.text),
        dict(cname="created_at", ctype=db.types.int),
        dict(cname="last_edit_at", ctype=db.types.int),
        dict(cname="requested_by", ctype=db.types.int, notnull=False),
    ],
    primary_keys=["id"],
))
```
- Writes: `db.insert(... on_dublicate="replace")` on create; `db.update("qc_lobbies", {...}, keys={"id": ...})` per transition (mysql.py:228-235). Completed-post idempotent — dedup on `(channel_id, aoe2_game_id)` before posting.
- Boot rehydration: on first `LobbyJobs.think`, `db.select(... where={"status": ...})` non-terminal rows, `channel.fetch_message(message_id)` to recover handles, re-open sockets for FILLING, resume polls for IN_PROGRESS.

---

## REUSE MAP

| Need | Reuse from | What |
|---|---|---|
| Async API client | bot/civ_matcher.py:69-83,106-108 | UA + Semaphore + timeout + status guard |
| Completed resolution (fallback) | bot/civ_matcher.py:85-166 | _find_and_record overlap by-pids |
| Retry/backoff + task set | bot/civ_matcher.py:38,203-225 | _RETRY_DELAYS + _pending |
| Replay/join/watch links | bot/civ_matcher.py:184-185 | aoe.ms + visualizer URLs |
| Completed-embed layout | bot/civ_sync.py:80-234, tests/test_civ_sync.py | field map + golden fixture |
| Post-once/edit-on-change | bot/match/check_in.py:48-49,90-92 | channel.send + edit + DiscordException guard |
| Recurring-job idiom | bot/stats/stats.py:401-407 | LobbyJobs.think; register events.py:95 |
| Tick driver | PUBobot2.py:133-145, bot/events.py:72-113 | tick + error isolation + 30s save |
| Table + migration | bot/civ_sync.py:15-33, mysql.py:161-244 | ensure_table + insert/update/select |
| Slash reg + defer | commands.py:534-541, :778 | /subauto template, explicit defer |
| Context helpers | bot/context/slash/context.py:10-47 | reply/notice/success/error after defer |
| Config var (3 places) | core/config.py:25-46, start.py:18-21, config.example.cfg | any LOBBY_* var in all three |

---

## Error / edge cases
- **Lobby never fills / closed pre-launch:** socket says gone without launch → EXPIRED; grey footer, stop, no completed embed.
- **Lobby not found at /lobby time** (private/started): no lobbyAdded in snapshot timeout → error, no watcher.
- **Socket down/changed:** isolate bot/lobby/api.py; fall back to poll-style snapshot or findAdvertisements; even if all live sources fail, still do the completed half via by-id REST.
- **API/socket slow:** /lobby deferred immediately (no "Unknown interaction"); FILLING tolerates "no message = no change".
- **Bot restart (Railway redeploy):** in-flight watchers are memory-only; qc_lobbies is the durable store. On boot rehydrate non-terminal rows, channel.fetch_message handles, re-open sockets/resume polls. Do NOT shoehorn lobbies into saved_state.json (bot/main.py:54-99 doesn't know about them) — qc_lobbies survives crashes the same way.
- **Duplicate /lobby for same id:** dedup on in-memory registry + non-terminal qc_lobbies row; reply with jump link.
- **Discord edit rate limits:** edit only on roster change, debounced ≥~3s, wrapped in try/except DiscordException.
- **Post-match data delay (minutes):** by-id poll on _RETRY_DELAYS; if 404 fall back to by-pids overlap; bound the window then EXPIRE with a graceful note.
- **server==null / map slug vs friendly:** prefer socket mapName/server; fall back to "Unknown".
- **Permissions:** bot needs Send/Embed/Read-history + fetch_message on boot; handle Forbidden/NotFound → EXPIRED.
- **replay==false:** dead per-player link — render only where replay==true, or pick one participant with replay==true as canonical (civ_matcher.py:164 already does this).

---

## Rate-limit / polling-cost analysis
- **FILLING:** push, not poll → ~0 requests idle. Cost = 1 WebSocket per active lobby. At pickup scale (0–3 concurrent) trivial; reconnect backoff caps churn.
- **IN_PROGRESS result poll:** ≤3 REST calls per lobby across (60,180,420)s (+ a few probes) → a handful of /api/matches/{id} calls/game. Semaphore(5) + 15s timeouts; Cloudflare-fronted, no published limit — keep concurrency low + descriptive UA.
- **Discord edits:** worst case ~1 edit/join, debounced ≥3s → ≤~16 edits/fill; far under budget.
- **aoe.ms:** links constructed, not fetched, by the bot → no bot-side 429 unless Phase 3 server-side download (then cache by matchId + honor Retry-After).
- Formula: cost ≈ (#concurrent lobbies × 1 socket) + (#completed games × ~3–6 REST calls) + (#roster changes, debounced, as edits). All small at this scale.

---

## Phased milestones (concrete files)
- **Phase 0 — Spike (½ day):** throwaway aiohttp ws_connect to `...&match_ids=<live id>`, capture exact event JSON into a fixture; confirm by-id REST on a finished id. De-risks the only unproven surface.
- **Phase 1 — MVP completed-only (1–2 days):** /lobby posts a "tracking" message; LobbyJobs polls /api/matches/{id} on the ladder; on finish post MATCH-COMPLETED (reuse civ_matcher links + civ_sync layout). Files: commands.py (+/lobby), bot/commands/lobby.py, bot/lobby/__init__.py (table), bot/lobby/api.py (REST), bot/lobby/completed.py, bot/lobby/jobs.py, register events.py:95 + bot/commands/__init__.py. Test tests/test_lobby_completed.py.
- **Phase 2 — Live lobby fill (2–3 days):** bot/lobby/watcher.py (state machine), bot/lobby/socket.py (ws client + reducer from lobbies.ts), live LOBBY embed + slot fill + "+N remaining", lobbyRemoved→IN_PROGRESS, profileId capture, dedup, boot rehydration, reconnect/cleanup. Test tests/test_lobby_reducer.py.
- **Phase 3 — richer/fallbacks (optional 1–2 days):** findAdvertisements + poll-style socket fallback; live win-probability; civ matchup context; mgz parsing offloaded to the Railway visualizer (never on the asyncio loop). Add LOBBY_* config vars in core/config.py _SCHEMA, start.py TEMPLATE, config.example.cfg.

**CI:** `ruff check .` (tabs, line-length 120) + `pytest tests/` per PR (.github/workflows/ci.yml). Keep pure logic (renderer, reducer, id parsing) test-covered like elo_sync/civ_sync.


# BETTER IDEAS

Extra data these sources expose, and how to BEAT AOE2LobbyBOT — prioritized by value ÷ effort.

### Tier 1 — High value, low/medium effort (do these)
- **Win-probability + balance on the LIVE lobby (killer feature).** The socket gives every slot's rating/rank/team in real time. Feed them into the bot's existing rating systems (bot/stats/ Flat/Glicko2/TrueSkill) to show a live "Team 1 62% favoured (avg 1180 vs 1095)" line that updates as players join. AOE2LobbyBOT shows raw names; NammaPUBobot can show predicted balance. Effort: low — ratings already in the payload; reuse existing math. **Best single differentiator.**
- **Civ matchup / win-rate context on the COMPLETED embed.** bot/civ_stats.py already loads data/player_civ_stats.csv + data/civ_elo_stats.csv and exposes lookups. Annotate each player's civ with its win-rate / a matchup note. Effort: low — data and loader exist.
- **Post-game rating deltas posted back.** The completed match object carries per-player ratingDiff. Surface "+18 / −15" per player (AOE2LobbyBOT shows the bracket but not deltas). Effort: low — field already present.
- **Recent-form / head-to-head.** While FILLING, fetch each captured profileId's recent matches (/api/matches?profile_ids= — already used by civ_matcher) to show "last 5: W-W-L-W-L" and H2H between opposing players. Effort: medium — cache per profileId, reuse client.

### Tier 2 — High value, higher effort
- **Auto-create a NammaPUBobot ranked match from a lobby (and auto-report).** When a tracked lobby launches, spin up a bot Match pre-filled with the captured players/teams, then on completion auto-report the result (completed object has per-player won). Closes the loop between the live lobby and the bot's own ELO/Glicko ledger — something AOE2LobbyBOT fundamentally cannot do (no rating ledger). Effort: medium-high — touches bot/match/match.py + bot/stats/. **Highest strategic value.**
- **mgz replay analysis (build orders / APM / eco).** pip install mgz>=1.8.46 (prefer the AoEInsights/aoc-mgz fork for current DE patches); download via aoe.ms (cache by matchId, honor 429 Retry-After — proven code in /d/AI/aoe2record/visualizer/server.py:1421-1474). Post "build order: Fast Castle, 27 pop, APM 142" after the completed embed. MUST run off the asyncio loop (thread/process pool) or offload to the Railway visualizer. Effort: high; header/summary robust, full action-stream lags new patches.
- **No-show / AFK / dodge detection.** The lobby socket shows who occupied a slot pre-launch; if a lobby fills then collapses (lobbyRemoved without a finished match in a window), flag a dodge. Cross-reference captured profileIds vs the completed roster to detect drops/subs. Effort: medium — pure state-machine logic over data already tracked.

### Tier 3 — Nice-to-have
- **Smurf / alt-account flags:** lobby payload has games/wins/losses/maxRating/verified; flag low-games + high-rating slots. Effort: low-medium.
- **Per-map and per-civ leaderboards** from accumulated qc_match_civs + completed history ("best Mongols winrate", "best on Arabia"). Effort: medium.
- **Map-vote integration:** reuse the check-in map-vote mechanism (check_in.py:29-35,115-125) to vote a map suggestion. Effort: medium.
- **Streamer links / spectate:** small config map of known streamers → Twitch links; add the aoe2de:// spectate affordance. Effort: low.
- **Avatars/thumbnails:** slot payload includes avatarSmallUrl/MediumUrl/FullUrl and civ/map *ImageUrl CDN pngs — richer than AOE2LobbyBOT. Effort: trivial.

### Why this beats AOE2LobbyBOT
AOE2LobbyBOT is closed-source, breaks on patches needing manual fixes, and has only its own "Lobby Rating". NammaPUBobot already owns a rating ledger (Glicko2/TrueSkill), historical civ stats, an ELO sync pipeline, and a replay visualizer — so it can layer live win-probability, post-game rating deltas, civ matchup context, dodge detection, and auto-ranked-match creation on top of the same lobby feed. The differentiator is not the embed; it's the analytics the bot already has the data and code to compute.


# PHASES

- [0.5 day] **Phase 0 — Live-socket + by-id REST spike** — Throwaway aiohttp ws_connect script subscribing to wss://socket.aoe2companion.com/listen?handler=lobbies&match_ids=<id>; captures exact lobbyAdded/slotAdded/Updated/Removed JSON shapes into a fixture; confirms GET /api/matches/{id} returns a finished match. De-risks the only unproven surface.
- [1-2 days] **Phase 1 — MVP: completed-match embed only** — /lobby <gameid> registered (commands.py + bot/commands/lobby.py); qc_lobbies table (bot/lobby/__init__.py); REST client (bot/lobby/api.py); completed renderer (bot/lobby/completed.py) reusing civ_matcher links + civ_sync layout; LobbyJobs backoff poll (bot/lobby/jobs.py) wired into events.py:95. Posts the two-team/civ/winner/duration/rec-links embed on game-end. Pure-function renderer test (tests/test_lobby_completed.py).
- [2-3 days] **Phase 2 — Live lobby fill embed** — bot/lobby/watcher.py (CREATED→FILLING→IN_PROGRESS→COMPLETED/EXPIRED state machine) + bot/lobby/socket.py (ws client + delta reducer ported from lobbies.ts). Live LOBBY embed with slot fill, '+N remaining', aoe2de://0/<id> join code; lobbyRemoved→IN_PROGRESS handoff; profileId capture; dedup; reconnect/cleanup; boot rehydration from qc_lobbies. Pure reducer test (tests/test_lobby_reducer.py).
- [2-4 days] **Phase 3 — Richer data, fallbacks & differentiators** — findAdvertisements + poll-style socket fallbacks; live win-probability from bot/stats ratings; civ matchup context from bot/civ_stats.py; post-game rating deltas; optional mgz replay parsing offloaded to the Railway visualizer. New LOBBY_* config vars added in core/config.py, start.py, config.example.cfg.


# OPEN QUESTIONS

- Profile-link convention on the completed embed: match AOE2LobbyBOT's aoe2insights.com/user/relic/<id>/ (keeps civ_sync.py's parser compatible) or standardize on the repo's own aoe.ms/Railway visualizer links?
- Auto-create a NammaPUBobot ranked Match from a launched lobby and auto-report its result, or keep /lobby purely informational in v1?
- Channel/permission policy: /lobby only in queue channels (run_slash currently rejects non-queue channels), admin-only, or anywhere the bot can post?
- Concurrency cap: how many simultaneous live lobby WebSocket subscriptions per channel/guild to bound connection count and abuse?
- Live socket is undocumented/unofficial — accept it as primary with findAdvertisements as documented fallback, or build both from day one for resilience?
- Watcher TTLs: max FILLING duration before EXPIRED, and max IN_PROGRESS result-poll window before giving up — what values fit your community's game lengths?
- Should per-player .aoe2record files be downloaded/cached server-side (enables mgz analysis but adds aoe.ms 429 handling), or only ever emitted as links?
- Confirm qc_lobbies is the sole durable store and lobbies are intentionally kept OUT of saved_state.json — agreed?


# PHASE 0 — SPIKE RESULTS (verified live 2026-06-14)

Run via `utils/lobby_spike.py` (throwaway diagnostic, kept for re-runs). Captured against the live socket; raw scratch capture lands in `tests/fixtures/lobby_events.json` (gitignored, ~1MB ambient public lobbies — a small curated test123 fixture is added in Phase 1).

### Three lobby-input methods (clarified) — one engine, three adapters
The tracker is a single **gameId-keyed `LobbyWatcher` core**; behaviour is driven by whether a `Match` is attached, not by entry point:
1. **Auto-search by name** (`test123`, unfiltered feed) — match-linked → full Flow 1/2/3 (captain-confirm loss).
2. **`/lobby2 <gameid>`** (filtered `&match_ids=`) — opportunistically link if the id's roster matches an active ranked match.
3. **`/lobby <gameid>`** (filtered) — standalone, informational only (live fill + completed embed). No existing `/lobby` command in the repo today; all three are to be built.

### CONFIRMED (automated half)
- **Unfiltered detect-by-name is FEASIBLE.** `wss://socket.aoe2companion.com/listen?handler=lobbies` (no `match_ids`) connects anonymously and streams every public lobby — a 936-entry snapshot then deltas — with `name` present on each. A `test123` lobby will surface; client-side name filter catches it. This closes the only load-bearing unknown for the auto path.
- **Wire protocol.** Each WS TEXT frame is a JSON **array** of `{"type", "data"}` events. Types: `lobbyAdded | lobbyUpdated | lobbyRemoved | slotAdded | slotUpdated | slotRemoved`. Initial frame = full state as `*Added` events (snapshot: 104 lobbyAdded + 832 slotAdded); subsequent frames = deltas. A delta frame already contained a `lobbyRemoved` — **the host-launched signal** the FILLING→IN_PROGRESS transition keys on. Skip `type=='pong'`.
- **Slot → lobby linkage:** `slot.data.matchId == lobby.data.matchId`. Roster = slots whose matchId matches. `matchId` IS the `aoe2de://0/<id>` id (sample 485355768).
- **Lobby `data` fields:** `matchId, name, server, mapName, mapImageUrl, started(null=open), finished, totalSlotCount, blockedSlotCount, averageRating, leaderboardId/Name, gameModeName, speedName, password, recordGame, …` — covers the LOBBY embed in full.
- **Slot `data` fields:** `profileId, name, civ, civName, civImageUrl, slot, team, color, colorHex, status, matchId, verified, games, wins, losses, drops, steamId, avatar{Small,Medium,Full}Url`. Everything the core loop needs (profileId capture, roster-confirm, profile-map self-heal, civ attribution, completed embed) is present.

### DISCREPANCY vs original verdict (note for Phase 3)
- The sampled (unranked) `slotAdded` had **no per-slot `rating`/`rank`** — only lobby-level `averageRating`. The live win-probability differentiator may need a **ranked** lobby (where per-slot rating may appear) or a REST rating lookup by profileId. **Does not affect the core Flow 1/2/3 loop.** Confirm when the first real ranked `test123` is captured.

### STILL TO VERIFY (needs a live ranked `test123` lobby — owner action)
Run while a real lobby is open, then after it finishes:
```
python utils/lobby_spike.py watch --name test123 --seconds 300   # capture join→fill→launch
python utils/lobby_spike.py rest <gameid>                         # confirm by-id completion fetch
```
1. A lobby named exactly `test123` is caught and its full join→fill→`lobbyRemoved` lifecycle records cleanly.
2. `GET data.aoe2companion.com/api/matches/{id}` returns the finished match (winner/civs/duration) after launch — and the lag window before it 200s.
3. Whether a **ranked** slot payload carries per-slot rating (win-prob feasibility).
This live capture also becomes the small curated golden fixture for the Phase-1 reducer test.