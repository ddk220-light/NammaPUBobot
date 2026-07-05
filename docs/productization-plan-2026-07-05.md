# Productization Plan — multi-server NammaPUBobot

Date: 2026-07-05. Planning only — no code changes. Companion to
`docs/robustness-review-2026-07-05.md` (fix that list first; a product ships
on top of a bot that doesn't wobble). File:line references are against
commit `f1528c7`.

## The target flow

1. A community admin **invites the bot** to their server.
2. They open the **dashboard**, see their server, **enable a channel**, and
   create/configure their pickup queues.
3. They **seed players** — manually or by **uploading an export of their
   existing PUBobot data** — so ratings/history carry over.
4. Matches get played and recorded; the **dashboard shows only their
   channel's players/matches**.
5. They toggle and tune features per channel: lobby tracking, post-match
   banter, quiz, replay analysis.
6. Admin rights follow Discord roles, both in slash commands and in the UI;
   everyone else gets read-only stats.

## Current state in one paragraph

The inherited PUBobot2 core is already multi-tenant: per-channel config
(`qc_configs`/`pq_configs` via CfgFactory), per-channel ratings
(`qc_players` keyed `(user_id, channel_id)`), matches and rating history all
carry `channel_id`, and the enable flow (`/admin channel enable`,
`bot/context/slash/commands.py:172`) creates a channel cleanly. **Everything
this fork added is global or hardwired to one community**: replay stats (11
`rs_*` tables, no channel key, one global on/off row), classifications (6
`cls_*` tables, no channel key), the quiz (schema is per-channel but the
code enforces exactly one enabled channel bot-wide,
`bot/quiz/store.py:17,29`), the lobby auto-watcher (hardcoded lobby name
`test123`, `bot/lobby/watcher.py:27`), all five public dashboard stats APIs
(no channel filter — `bot/web.py:1883` leaderboard aggregates every channel),
and a `data/` directory of this community's own history baked into the repo
(civ win-rates, player quiz bank, profile map, and a ratings CSV that is
**auto-seeded into every new channel on boot**, `bot/events.py:16-54`).

---

## Workstream A — Backend tenancy model

### A1. Pick the tenant boundary (decision needed)

Recommendation: **guild = the account, channel = the workspace/data scope.**
The data key stays `channel_id` (that's what every core table already uses,
and `rating_channel` lets channels within one guild share a rating pool —
`bot/queue_channel.py:144`). The guild level owns: who may administer,
feature entitlements/quotas, timezone default, and the dashboard entry point.
Add a small `tenants` table (`guild_id`, owner, plan/flags, created_at,
status) so "a server using the product" is a first-class row rather than an
emergent property of `qc_configs.cfg_info.guild_id`.

### A2. Table-by-table changes

| Table(s) | Today | Change |
|---|---|---|
| `qc_configs`, `pq_configs`, `qc_players`, `qc_matches`, `qc_player_matches`, `qc_rating_history`, `qc_match_civs`, `qc_lobbies`, `qc_quiz_*`, `qc_phrases` | per-channel ✓ | none (schema) — audit every read for a missing `WHERE channel_id` (found: `civ_elo_from_db` aggregates all channels, `bot/civ_stats.py:46`) |
| `rs_*` (replay stats, `bot/replay_stats/__init__.py`) | global corpus, keyed `aoe2_match_id`/`profile_id` | add `channel_id` to `rs_matches` (derivable from `bot_match_id` → `qc_matches`), and make **all** reads in `bot/replay_stats/query.py:80-226`, `tag_leaderboard.py` filter through it. Player-history queries should scope to "games from this channel" by joining through `rs_matches` rather than adding channel to every per-player row. |
| `cls_*` (classifications) | global, written offline | add `channel_id` to `cls_results`/`cls_match_ingest` (derivable the same way); `/insights` (`bot/classifications/query.py:45`) and `/api/strategies` (`web.py:442`) filter on it. `cls_player_totals` becomes per `(identity, channel_id)`. |
| `rs_config` | one global row (`__init__.py:15`) | per-channel enable — fold into the feature-flags var set (A3) and drop the table, or re-key by `channel_id`. |
| `qc_quiz_config` | per-channel schema, single-tenant code | delete `disable_all()` semantics (`store.py:29`), make `get_config()` return all enabled rows, make `QuizJobs._run` (`jobs.py:53`) loop over them. |
| `qc_profile_map` | global (`bot/lobby/__init__.py:53`) | keep global — an AoE2 profile id is globally true — but any *display* of it goes through channel membership. |
| `qc_match_id_counter` | one global sequence (`stats.py:75`) | keep (IDs just need uniqueness), but show per-channel match numbering in UI if cosmetics matter. |
| `qc_civ_reconcile`, `qc_saved_state`, `players`, `web_sessions` | global | fine as-is (keyed by global match id / instance-level). |
| `noadds`, `qc_douche`, `disabled_guilds` | per-guild ✓ | fine. |

### A3. Per-channel feature flags — one mechanism, not five

Don't invent a new toggle store per feature (the quiz and replay-stats each
did, incompatibly). Extend the existing **CfgFactory** channel config
(`bot/queue_channel.py:35-303`) with a new `"Features"` section — it already
gives typed vars, DB persistence, the dashboard form auto-generation, and
`on_change` hooks for free. Every fork feature then reads
`qc.cfg.<flag>` instead of module constants/global rows. (Details of which
flags: Workstream C.)

### A4. Kill the single-community hardcodes

- **`test123`** (`watcher.py:27`): auto-generate a per-match lobby key
  `namma-<match_id>` (the plan doc itself recommends this,
  `docs/aoe2-lobby-replication-plan.md:12`) and put it in the match-start
  embed; optionally allow a per-channel custom prefix.
- **Boot-time CSV rating seed** (`seed_ratings_from_csv`, `bot/events.py:16`):
  remove from startup entirely; it becomes the tenant-initiated import
  (Workstream B). This is the single most dangerous line for a second tenant
  today — it writes another community's ratings into every new channel.
- **`PUBOBOT_USER_ID`/`LOBBYBOT_USER_ID`** (elo/civ-sync source bots,
  `start.py:74-85`): per-channel config vars, default empty = feature off.
- **IST timezone** (`bot/civ_stats.py:20`) and **UTC quiz hours**
  (`bot/quiz/jobs.py:19`): add one per-channel `timezone` var (IANA name) in
  the channel config; quiz hour, "civs played today", and rating decay
  windows all read it.
- **`data/player_profile_map.csv`** (`bot/civ_matcher.py:26`): finish the
  migration to the DB `qc_profile_map` that `bot/lobby/profile_map.py`
  already started; delete the CSV read.

### A5. The static-data problem (civ stats, quiz banks, commentary)

The repo ships one community's derived data and several features depend on it:

- **Civ win-rates** (`data/civ_elo_stats.csv`): keep as a clearly-labeled
  **global default dataset** ("community averages") that any tenant can use,
  while `civ_elo_from_db` becomes per-channel and takes over once the channel
  has ≥N recorded civ games (the fallback logic already exists,
  `civ_stats.py:42-57` — it just needs the channel filter and labeling).
- **Quiz banks**: the *game* bank (`data/quiz_bank.json` — unit/tech facts)
  is tenant-agnostic — every tenant can use it from day one. The *player*
  bank (`quiz_bank_player.json` — "which player…") is inherently per-tenant
  and generated offline from replays; for the product it's either (a) a
  scheduled per-tenant regeneration job once a tenant has replay data, or
  (b) disabled per-tenant until then, with the scheduler
  (`utils/quiz_gen/build_schedule.py`) falling back to game-only weeks.
  Store schedules per channel (`data/quiz_schedule.json` is one global,
  exhaustible file today — `bot/quiz/schedule.py:10`).
- **Offline pipelines generally** (`utils/classifications/runner.py`,
  commentary, quiz gen): these run by hand against one DB today. Product
  version needs them as scheduled jobs iterating enabled tenants, or an
  explicit "regenerate" button per tenant with rate limits. This is real
  work — flag it as its own epic (Phase 3).

---

## Workstream B — Onboarding & data import

### B1. Onboarding flow

1. **Invite** with a fixed scope set (bot + applications.commands; document
   required channel perms: send, embed, add reactions, manage messages).
2. **First-run detection**: on `on_guild_join`, post a short welcome in the
   system channel with the dashboard link and `/admin channel enable` hint.
   Register the guild in `tenants`.
3. **Dashboard-driven enable**: today a channel can only be enabled from
   Discord (`commands.py:172`). Add `POST /api/guilds/{gid}/channels/{cid}/enable`
   (admin-gated) that calls the same `QueueChannel.create` path, so the
   dashboard's guild → channel list gets an "Enable" button next to
   channels the bot can see.
4. **Queue templates**: a one-click starter set (e.g. `1v1`, `2v2`,
   `4v4 draft`) instead of raw `create_pickup` + 30 settings. Templates are
   just canned `pq_configs` payloads; expose 3–4 in the dashboard wizard.
5. **Setup checklist** panel on the dashboard channel view: bot invited ✓ /
   channel enabled ✓ / queue created ✓ / players seeded (n) / first match
   recorded ✓ / features configured — each linking to the relevant form.
   This doubles as the empty-state view (B3).

### B2. Player/data import ("upload a zip of current pubobot player details")

There is **no importer today** — `update_db.py` is a schema migration for an
existing v1 MySQL DB (config only, not players), and the only seeding path is
the hardcoded boot CSV being removed in A4. Build an import wizard:

- **Formats, in order of value:**
  1. **CSV of player ratings** (`nick, rating, wins, losses, draws[, deviation]`)
     — matches the shape of `data/qc_players.csv` and what
     `seed_ratings_from_csv` already parses (`events.py:16-54`); this covers
     "seed players" with the least friction.
  2. **PUBobot2 table export zip** (CSV dumps of `qc_players`,
     `qc_matches`, `qc_player_matches`, `qc_rating_history` — exactly the
     files sitting in `data/` today) — full history import, remapping
     `channel_id` to the new channel and match ids through a fresh sequence
     block (the global counter makes naive id reuse collide; keep an
     `import_id_map` table).
  3. (Later) PUBobot v1 dump via an adapted `update_db.py`.
- **Identity mapping is the hard part**: exports key players by nick or old
  Discord id. Wizard flow: upload → dry-run parse → show a mapping table
  (auto-match by exact nick / by id if the member is in the guild) → admin
  resolves the unmatched rows (pick member / skip) → commit. Unmatched rows
  can be imported as "unclaimed" and claimed later via a `/claim` command or
  admin action.
- **Safety**: dry-run preview with counts, idempotency key per upload, an
  `imports` audit table, and a one-click revert (delete rows tagged with the
  import id). Cap zip size, parse in a worker task, never on the tick loop.

### B3. Empty states (feature behavior with zero data)

The good news from the audit: nearly every fork feature already degrades
silently on empty tables (insights return `None`, post-game embeds return
`None`, quiz does nothing when disabled). "Silently" is right for Discord
and wrong for the dashboard — a new tenant sees blank tabs with no
explanation. Plan:

- Dashboard: every stats view gets an explicit empty state with the reason
  and the unlock action ("No matches recorded yet — play your first pickup"
  / "Replay analysis is off — enable it in Features" / "Needs ≥50 games
  with civ data; you have 12"). The thresholds already exist as constants
  (`MIN_CIV_GAMES=50` `civ_stats.py:11`, `lb_min_matches` channel var) —
  surface them instead of empty tables.
- Discord: civ suggestions should say when they're using the **default
  dataset vs your channel's data** (one footer line); insights/banter stay
  suppressed below their sample thresholds (already the case).
- The checklist from B1 is the primary "why is this empty" surface.

---

## Workstream C — Feature controls (the settings matrix)

All per-channel, in the new `"Features"` CfgFactory section (A3), so they
appear in the dashboard automatically. Suggested variables:

**Lobby tracking**
- `lobby_tracking` (off / manual-only / auto+manual; default manual-only) —
  today the watcher is forced on for every ranked match (`match.py:349`)
- `lobby_name_prefix` (default `namma-`; replaces `test123`)
- `lobby_result_autoconfirm` (post ✅-gated result vs informational only)
- (global constants stay global: TTLs, debounce, poll intervals)

**Post-match output** (pairs with the consolidation in the robustness review)
- `postmatch_verbosity` (off / result-only / highlights / full) — controls
  which of rating-results, replay link, civ take, match cards post
- `postmatch_banter` (bool) — the jokey narrative lines specifically
- `postmatch_max_bullets` (int, default 3) — replaces the `MAX_BULLETS` /
  `MAX_ANALYSIS_LINES` constants (`post_game.py:29-37`)

**Pre-match**
- `prematch_insights` (bool) — `team_insights` embed
- `civ_suggestions` (off / own-data / own-data+default-fallback)
- `civ_min_games` (int, default 50)

**Quiz**
- `quiz_enabled` (bool, per channel — after the single-tenant invariant is
  removed), `quiz_hour` + channel `timezone`, `quiz_days` (dow mask),
  `quiz_banks` (game / player / both), `answer_window`,
  `leaderboard_dow/hour`, `min_difficulty` — most already exist in
  `qc_quiz_config` (`bot/quiz/__init__.py:55-72`); keep that table, drop the
  exclusivity.

**Replay analysis**
- `replay_ingest` (bool, per channel — replaces global `rs_config`)
- `replay_scope` (ranked-only / all matches)
- `replay_post_cards` (bool — decouples ingest from the Discord output, so a
  tenant can have dashboard analytics without channel spam)
- `replay_retention_days` (int — rs_* rows and fetched replays are unbounded
  today)
- Global (operator-level, not per-tenant): aoe.ms rate budget, parser
  version pin, per-tenant ingest quota/fairness (round-robin the single
  `POLL_INTERVAL=150s` sweep across channels so one busy tenant can't starve
  others — `jobs.py:20,55`).

**Insights/classifications**
- `insights_enabled` (bool), and the `/insights` + `/api/strategies` queries
  scoped per channel (A2)

**Channel-level (new General vars)**
- `timezone` (IANA; used by quiz, "civs today", decay)
- `stats_visibility` (public / guild-members / admins) — drives dashboard
  read access (D2)
- `elo_sync_bot_id` / `civ_sync_bot_id` (replace the global
  `PUBOBOT_USER_ID`/`LOBBYBOT_USER_ID`)

Ratings/queue settings need no new work — the existing ~40 channel vars and
~30 queue vars (`queue_channel.py:35-303`, `pickup_queue.py:13-266`) are
already per-tenant and dashboard-editable.

---

## Workstream D — Dashboard: per-channel views & permissions

### D1. Information architecture

- URL scheme: `/g/{guild_id}/c/{channel_id}/{tab}` with a guild+channel
  picker (the `/api/guilds` → channels endpoints already exist,
  `web.py:2274,2301`). Everything currently under the global tabs
  (leaderboard, match stats, player pages, strategies, civ stats) moves
  under the channel context.
- **Every stats endpoint gains a required `channel_id` and a `WHERE`**:
  `/api/leaderboard` (`web.py:1883` — join condition exists, filter doesn't),
  `/api/match-stats` (`:1861`), `/api/player-stats` (`:1949`),
  `/api/strategies` (`:442`, needs `cls_results.channel_id` from A2),
  `/api/civ-stats` (`:387` — becomes per-channel `qc_match_civs` aggregate
  with the labeled default-dataset fallback). Player profile pages show only
  that channel's history; the same player in two guilds is two separate
  views.
- The SPA is a single 4000-line inline-JS file (`bot/web_page.html`); adding
  a tenant dimension to every view is the moment to split it into modules
  (or accept the cost — but budget for it either way).

### D2. Access model (reconcile the two permission systems)

Today slash commands honor per-channel `admin_role`/`moderator_role`
(`bot/context/context.py:47-65`) while the web checks Discord
`manage_guild`/guild owner (`web.py:270-295`) — a configured admin-role
holder can admin from Discord but not from the web. Unify on the bot's view
of roles (the bot can read a member's roles via its own guild cache, so no
extra OAuth scopes are needed):

| Level | Who (per channel) | Slash | Web |
|---|---|---|---|
| Owner/operator | `DC_OWNER_ID` (instance operator) | everything | everything (support access — log it) |
| Admin | guild owner, Discord admin/manage-guild, or `admin_role` | feature toggles, config, import, seed, report_admin | config forms, feature flags, import wizard |
| Moderator | `moderator_role` | match interventions (sub_force, put, report_admin?) | moderation views (optional Phase 3) |
| Viewer | guild member (checked via bot's member cache) | player commands | read-only stats, gated by `stats_visibility` |
| Anonymous | — | — | only if `stats_visibility=public` |

Implementation notes: `_get_session` already identifies the Discord user;
add a per-request `(guild, user) → level` resolver using the bot's caches
with a short TTL memo. Log config writes to an `audit_log` table
(who/when/what — a product needs this for support). The unauthenticated
`/api/debug` endpoint (robustness review 4.1) must be gone before any of
this ships.

### D3. Onboarding UI

The B1 checklist, the enable-channel button, queue templates, the import
wizard (B2), and the Features tab (auto-generated from the new CfgFactory
section). The existing config-form generation from variable types is the big
asset here — most of the "controls UI" is free once flags live in CfgFactory.

---

## Workstream E — Operational productization (summary)

- **Fairness/quotas**: all background jobs (`civ_matcher` retries,
  replay ingest, quiz posts, lobby polls) iterate global work queues today;
  make each round-robin across tenants and cap per-tenant external-API usage
  (aoe.ms, aoe2companion socket connections — the watcher opens one
  unfiltered firehose per active ranked match, `watcher.py:77`).
- **Rate limiting + caching** on public endpoints (robustness review 4.4) —
  mandatory once strangers can create load.
- **Lifecycle**: `on_guild_remove` → mark tenant inactive; retention policy
  and a delete-my-data path (per-guild purge across all scoped tables — the
  A2 scoping work is what makes this possible at all).
- **Ops**: per-tenant error visibility (the "best-effort, log and swallow"
  pattern hides tenant-facing failures; add a per-channel health panel),
  backups (`docs/superpowers/specs/backup_db.sh` exists), and a staging bot.
- Out of scope for now: billing, horizontal sharding (one instance +
  nextcord sharding covers a long way), i18n of fork features (they emit
  hardcoded English while the core honors per-channel `lang`).

---

## Phasing

**Phase 1 — safe for a second server (tenancy correctness).**
Remove the boot CSV seed; `test123` → per-match key; channel filters on all
five stats APIs; per-channel feature flags for quiz/replay/banter/lobby
(replacing the global `rs_config` row and the quiz `disable_all`); channel
`timezone` var; `channel_id` columns on `rs_matches`/`cls_results` +
scoped reads; remove `/api/debug`. *Until Phase 1 is done, inviting a second
community actively corrupts data (they get seeded with your ratings, share
your lobby key, and see your stats).*

**Phase 2 — onboarding.**
Dashboard enable-channel + queue templates + setup checklist; per-channel
dashboard IA (`/g/{gid}/c/{cid}`); unified permission resolver + audit log;
empty states everywhere; import wizard format 1 (ratings CSV) then format 2
(table-export zip) with the nick-mapping flow.

**Phase 3 — per-tenant data products & ops.**
Per-tenant quiz schedules (game-bank default, player-bank once replays
exist) and scheduled regeneration; per-tenant civ stats replacing the CSV
default as data accrues; classifications/commentary pipelines as tenant
jobs; job fairness + quotas; retention/purge; moderation views.

## Decisions needed from you

1. **Tenant boundary**: guild-as-account with channel data scoping (as
   recommended), or strictly channel-only?
2. **Default stats visibility**: public, or guild-members-only? (Affects
   D2 and how much the current public API surface must be locked down.)
3. **The bundled dataset**: is this community's civ/quiz data OK to ship as
   the labeled global default for other tenants, or should new tenants start
   with the game-bank quiz + no civ suggestions until they have data?
4. **Import ambition for v1**: ratings-CSV only (cheap, covers "seed
   players") or the full table-export zip with history (real work, mostly in
   identity mapping + id remapping)?
5. **Match-id cosmetics**: keep global match numbering (cheapest) or
   introduce per-channel display numbering?
