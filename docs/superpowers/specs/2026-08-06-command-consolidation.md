# Command consolidation — decisions and cleanup ledger

_Agreed 2026-08-06. Not yet implemented._

The bot exposes **101 slash commands** (46 top-level + 55 subcommands across 12
groups), plus four text commands nobody documented. This records what survives,
what goes, and every piece of code that becomes unreachable as a result.

**Result: 101 → 44 slash commands. A regular player's menu goes from 101 to 14.**

That second number is the one that matters, and most of it comes from one change
that deletes nothing: see [Permissions](#permissions-the-biggest-single-win).

---

## Why the surface got this big

Almost nothing cut below is a working feature people disliked. Upstream PUBobot2
is built for many servers, many channels, many queues, map pools, promotion
roles, and offline enforcement. This deployment runs **one channel, one queue**
(`namma_nomad`, size 8, captain-based matchmaking), no maps, and no roles.

A third of the surface is machinery for options that are switched off, plus
commands that cannot succeed under this configuration. Each entry below records
which.

---

## Permissions — the biggest single win

Nothing in `bot/context/slash/groups.py` declares `default_member_permissions`.
Admin rights are checked *inside* the handler, after the player has already
found and typed the command — so **every admin subcommand is in every player's
slash menu today**.

Declaring it on the surviving admin groups removes ~29 commands from ~98
players' menus with zero behaviour change. Do this regardless of any deletion.

---

## Section 1 — Queue & match flow: 20 → 7

**Keep:** `/add` `/remove` `/report` `/teams` `/subme` `/subfor` `/subauto`

| Removed | Reason |
|---|---|
| `/capfor` `/capme` `/pick` | **Unreachable.** `bot/match/draft.py:23` is the only place the `DRAFT` state is entered and it is gated on `pick_teams == "draft"`. This queue is `"captain based matchmaking"`, which computes both full teams itself (`match.py:230`). All three raise *"The match is not on the draft stage."* every time. |
| `/auto_ready` | **Broken.** `max_auto_ready` is `null`, so `misc.py:18` evaluates `min([300, None])` → `TypeError`, and the explicit-duration branch does `float > None` → same. Both paths, every call. (`default=15*60` never applies: `Config.load` uses `cfg_data.get(name, default)` and the key exists holding `null`.) |
| `/expire` | Controls a dormant subsystem — channel `expire_time` is `null` and `player_prefs` has 0 rows, so no player has ever had an expire timer. |
| `/maps` `/map` | Queue map list is `[]`. Zero of the 134 matches since 2026-06-29 recorded a map; the last was 2026-06-28. |
| `/server` | `server: null` → always errors *"Server for namma_nomad is not set."* |
| `/promote` | `promotion_role` and `promotion_msg` are both `null` — posts a generic line and pings nobody. |
| `/matches` | One channel, at most one live match. `/teams` covers it. |
| `/who` | Works; pure re-display of what the bot already posts on every add. |
| `/ready` `/notready` | Typed equivalents of the ☑/⛔ reactions on the check-in message. |

**Before removing `/ready`:** `check_in.py:74` wraps `add_reaction` in
`try/except DiscordException: pass`. If the bot ever loses *Add Reactions*, it
posts a check-in with no buttons and logs nothing — and `/ready` is currently
the only escape. **Make that `except` log loudly as part of this work.**

### Code that becomes unreachable

- `bot/match/draft.py` — `cap_me`, `cap_for`, `pick`, plus `pick_order` and
  `captains_role_id`. **`Draft` itself survives**: `sub_for` and `sub_auto` back
  `/subfor`, `/subforce` and `/subauto`, and both call `restart_for_match` to
  void live betting books. Rename the class — it manages substitutions, not drafts.
- `bot/expire.py` — the whole `ExpireTimer`; `queue_channel.update_expire`;
  the expire block in `saved_state.json` serialization.
- `bot.auto_ready` dict; its read at `check_in.py:31`; its cleanup at
  `queue_channel.py:518`; the `max_auto_ready` config variable.
- Map voting in `check_in.py` (`self.maps`, `map_votes`, the INT_EMOJIS
  reactions); the `maps`, `vote_maps`, `map_count`, `map_cooldown` queue vars;
  `queue.last_maps`. **Keep the `matches.maps` column** — 1,739 rows of history.
- `server`, `promotion_role`, `promotion_msg` queue vars; `promotion_delay` and
  `qc.last_promote`.

---

## Section 2 — Personal settings: 5 → 0

| Removed | Reason |
|---|---|
| `/subscribe` `/unsubscribe` | **Cannot succeed.** `queues.py:181` builds the role list from `qc.cfg.promotion_role` (`null`) and the queue's (`null`), so `roles` is always `[]` and both branches end at `raise ValueError("No changes to apply.")`. |
| `/allow_offline` | Grants immunity from a disabled feature — `remove_offline: 0`, so `events.py:361` never removes anyone for being offline. |
| `/expire_default` | Writes `player_prefs.expire`; 0 rows, and the channel fallback is `null` too. |
| `/nick` | **Redundant and wrong.** `rating_nicks: 1` already rewrites every nickname to `[rating] Name` after each rating update (`queue_channel.py:505`). And `misc.py:133` queries `player_ratings` with `channel_id = ctx.author.id` — zero rows in the table have `channel_id = user_id`, so the lookup never matches and it always stamps `init_rp` instead of the real rating. Self-heals at the next update, which is why nobody noticed. |

### Code that becomes unreachable

- `bot/commands/misc.py` — `allow_offline`, `set_nick`; `bot.allow_offline` list
  and its uses at `events.py:357` and `queue_channel.py:518`.
- `bot/commands/queues.py` — `subscribe`.
- `player_prefs` table (`expire`, `allow_dm`) — 0 rows; drop after confirming
  nothing else reads it.

---

## Section 3 — Stats & leaderboards: 11 → 2

**Keep:** `/rank` (gains a `detailed` flag) · `/leaderboard` (gains a sort option)

| Removed | Reason |
|---|---|
| `/rank_detailed` | Literally the same function — `stats.py:117-124` both call `_rank_profile(ctx, player, detailed=…)`. That is a boolean, not a command. |
| `/eapm` | `/rank` already prints eAPM (`scouting_report.py:364`). Merging is a pure deletion. **Lost: the cross-player ranking** — "who has the highest eAPM" becomes unanswerable. |
| `/eapm_explained` | Static help text in a top-level slot. |
| `/mapstats` | Maps ended 2026-06-28. `/mapstats 1M` already returns nothing. |
| `/top` | Ranks by matches played, not rating — a second "who's best" board with a different definition. Fold into `/leaderboard` as a sort. **Also fixes a latent leak:** `/top` does not filter `is_hidden` while `/leaderboard` does (`queue_channel.py:474`). 4 players are hidden; none are currently in the top 12, so nothing is exposed today. |
| `/lastgame` | Agreed cut. |
| `/activity` | Agreed cut. |
| `/insights` | Capability survives on the web Strategies page. **Does not unblock the retired-table cleanup** — `card_query.py:154` also joins `cls_classifications` for post-game card labels, so that table stays load-bearing. |
| `/player_details` | **The only genuine capability loss in this whole exercise.** The build-timeline chart has no web equivalent. Removing it does *not* orphan the 630k `replay_events` rows — `card_query.py:83,111` reads them for post-game cards. |

### Code that becomes unreachable

- `bot/commands/stats.py` — `rank_detailed`, `top`, `last_game`, `eapm`,
  `eapm_explained`, `mapstats`, `activity` and their chart helpers.
- `bot/scouting_report.py` — `eapm_board` (only caller was `stats.py:422`).
- `bot/stats/stats.py` — `top`, `last_games`.
- `bot/commands/insights.py` (whole module); the `insights:full:` interaction
  route in `bot/events.py`; `bot/classifications/query` if it has no other caller.
- `bot/commands/player_details.py` (whole module);
  `replay_stats.query.gather_growth_curve`; `replay_stats.chart.render_growth_curve`.

---

## Section 4 — Replay linking: 5 → 2

**Keep:** `/lobby` · **`/link` renamed to `/profile_link`**

This section is load-bearing and healthy. `/lobby` created **185 of 233 lobbies
(79%)** — the automatic watcher produced only 48 — and **245 of the last 261
matches (94%) are linked to a replay**. `/link` is an unfinished funnel: 91
identity rows but only **54 distinct users, 53% of 102 rated players**, which
matches `player_rollups` exactly. Half the community currently gets "Statistics
pending linking".

| Removed | Reason |
|---|---|
| `/replaystats status` | Diagnostic. |
| `/replaystats reingest` | One-match re-ingest. |
| `/replaystats backfill` | **Not a capability.** `store.find_new_match` (`store.py:46`) selects matches `NOT IN (SELECT replay_match_id FROM replay_ingest)` — anything with an ingest row of any status is excluded, `done` included. The tick sweep calls **the same function** (`jobs.py:56`) with no age limit, so it is strictly broader. Backfill's only advantage is pace: 10s per match vs `POLL_INTERVAL` 150s. At 150s the tick absorbs ~576 matches/day against an inflow of ~4. |

**Correct the comment at `bot/replay_stats/__init__.py:11`** — it claims `done`
rows "are re-done by an explicit backfill". They are not, by any tool.

**Known gap, pre-existing:** there is no way to re-parse the 1,132 already-`done`
replays after an extractor change. Not backfill, not the tick, and `reingest`
(one id at a time) is going. A future `PARSER_VERSION` bump needing a full
re-parse will require a new `utils/` script that clears `replay_ingest` rows and
lets the sweep re-collect them.

### Code that becomes unreachable

- `bot/commands/replay_stats.py` (whole module)
- `bot/replay_stats/backfill.py` (whole module)

---

## Section 5 — Gold & betting: 4 → 2

**Keep:** `/predictions me` (absorbs balance + recent movements) ·
`/predictions leaderboard` (absorbs the gold column)

**Removed:** `/gold` `/gold_top`

### Required with the merge, not after

`_LB_SQL` never joins `player_ratings`, so `/predictions leaderboard` applies
**none** of the three gates `/leaderboard` uses: `is_hidden` (4 players),
`lb_min_matches: 40`, and `lb_last_match_limit: 2592000` (30 days). `/gold_top`
— the command being deleted — *does* filter hidden players (`predictions.py:71`).
Consolidating onto the unfiltered command without adding the gates is a
regression. All 15 people currently on the board pass all three, so this is
latent today; the board is about to grow considerably once betting runs.

### Also required

- **Lazy seeding.** `predictions.py:51` — `/gold` calls `bank.ensure_seeded()`,
  one of two paths granting a new player their opening 500. Move it to
  `/predictions me`. (The bulk seed on boot covers most cases.)
- **Privacy:** decided — public is acceptable, no special-casing. `/gold` was
  ephemeral; `/predictions me` is public and takes a `player` argument, so
  balances and movement history become queryable by name.

### Context on what these show today

All 102 holders sit at exactly 500 gold and `prediction_bets` is **0** — not one
bet has ever been placed. `/predictions leaderboard` is ranking 15 people on 45
votes from an era whose table no longer has a writer.

### Naming

With gold folded in, `/predictions` is the economy command, not the forecasting
one. Consider renaming in the final pass.

---

## Section 6 — Quiz: 8 → 2

**Keep:** `/quiz_leaderboard` · `/quiz disable`

**Removed:** `/quiz enable` `/quiz config` `/quiz post_now` `/quiz status`
`/quiz skip` `/quiz reveal_now` — settings move to the website later.

### Two things to know

**`/quiz disable` becomes a one-way door.** With `enable` removed, nothing in the
codebase sets `enabled=1`. Pulling it stops the quiz permanently until the web UI
ships or someone runs SQL. Acceptable for an emergency stop; should not be
discovered mid-incident.

**There is no other control surface.** No `QUIZ_ENABLED` env var (unlike
`REPLAY_INGEST_ENABLED`), and `bot/web.py` and `bot/web_page.html` contain
**zero** references to quiz — the API, the CRUD and the UI all have to be built.

**Three config fields are dead and must not be carried into the web UI:**

| Field | Read anywhere? |
|---|---|
| `quiz_hour`, `open_window`, `test_interval` | Yes |
| `leaderboard_dow`, `leaderboard_hour` | **Never** — `_maybe_week_leaderboard` (`jobs.py:253`) keys off completed *schedule weeks*, not a day/hour |
| `min_difficulty` | **Never** — written by `config`, read by nothing |

Plus `answer_window`, already a documented dead column from the reveal era. Drop
all four from the schema as part of this.

### Code that becomes unreachable

`bot/commands/quiz.py` — all but `quiz_disable`; `jobs.force_post`,
`jobs.next_up`, `jobs.reveal_now`; the `_INT_FIELDS` validator.
**Keep `_reveal`** — it still has two automatic entry points (`_close_due` and
`_reveal_previous`).

---

## Section 7 — Fun & meta: 8 → 0

**Removed:** `/changelog` `/help` `/commands` `/cointoss` `/test_teams`
`/douche add|summary|leaderboard`

| Command | Note |
|---|---|
| `/douche` ×3 | `douche_log` — 0 rows across the bot's entire history. |
| `/test_teams` | A development diagnostic comparing actual teams to what the matchmaker would produce — and it has **no `check_perms`**, so every player can run it. |
| `/commands` | Sends `cfg.COMMANDS_URL`, which defaults to **upstream's** command list. See below. |
| `/help` | DMs `cfg.HELP`, which defaults to one sentence naming the wrong bot. Channel `description` is `null`, so there is no fallback. |

### Code that becomes unreachable

- `bot/redo_teams.py` (whole module — `/test_teams`'s backend)
- `bot/commands/misc.py` — `cointoss`, `show_help`
- `bot/commands/admin.py` — `douche_add`, `douche_summary`, `douche_leaderboard`;
  the `douche_log` table
- `scripts/gen_changelog.py` **and its Dockerfile build step**; the changelog
  command handler
- `cfg.HELP`, `cfg.COMMANDS_URL` and their `start.py` template entries

---

## Section 8 — Admin: 40 → 29

**Keep whole:** `/channel` (5) · `/queue` (10) · `/noadds` (3)

**`/match` → 5:** `report`, `create`, `sub_player`, `put`, **+ `undo`** (moved
from `/stats undo_match`, where it never belonged).

**`/rating` → 3:** `seed`, `hide_player`, `unhide_player`.
Removed: `penality`, `reset`, `snap`.

**`/identity` → `/profile_identity`, 3:** `link`, `unlink`, `conflicts`
(4 conflicts are open, and only 53% of players are linked).
Removed: `show`, `status`.

**`/stats` group removed entirely** — `show` is on the web; `reset`,
`reset_player`, `stats_replace_player` are rare and destructive.
(`stats_replace_player` also reads as `/stats stats_replace_player`.)

**`/phrases` group removed** — `player_phrases`, 0 rows.

### Already replaced by the web dashboard

`web.py:2800-2804` exposes `GET/POST /api/channels/{id}/config` and
`GET/POST /api/channels/{id}/queues/{name}/config`, with forms auto-generated
from CfgFactory. `/channel show|set` and `/queue list|show|set` duplicate a
working UI **today** — kept by decision, but they are not the only path.

### Code that becomes unreachable

- `bot/commands/admin.py` — `rating_penality`, `rating_reset`, `rating_snap`,
  `stats`, `stats_reset`, `stats_reset_player`, `stats_replace_player`,
  `phrases_add`, `phrases_clear`, `identity_show`, `identity_status`
- `player_phrases` and `douche_log` tables

---

## PUBobot2 / Leshaka references

Three categories. Only the first is urgent.

### 1. Player-visible — fix

| Where | What players see |
|---|---|
| `STATUS` unset on Railway | **The bot's Discord presence reads "PUBobot2."** |
| `COMMANDS_URL` unset | `/commands` links to `github.com/Leshaka/PUBobot2/blob/main/COMMANDS.md` — upstream's list, wrong in both directions, and carrying upstream's `#avaible-commands` typo. Moot once `/commands` is deleted; set the var anyway. |
| `HELP` unset | `/help` DMs *"PUBobot2 is a discord bot for pickup games organisation."* Moot once `/help` is deleted. |
| `bot/events.py:160-163` | **`!enable_pubobot` and `!disable_pubobot` are live text commands** anyone can type in any text channel. They duplicate `/channel enable|disable`. Remove or rename. |

**Survey gap, worth recording:** the 101 count is slash commands only.
`bot/events.py:169` also handles **`++` and `--`** as add/remove shorthands —
deliberately kept after Layer 5, documented nowhere, and absent from
`COMMANDS.md`. Four text commands exist in total.

### 2. Internal — rename freely

- `bot/exceptions.py` — `PubobotException` base class, ~10 references
- `bot/message_logger.py:30` — docstring
- `bot/redo_teams.py` — being deleted anyway
- `ruff.toml`, `CLAUDE.md`, `requirements.txt`, `core/config.py` comments

### 3. Legitimate — do not rename

- **`PUBOBOT_USER_ID` / `bot/elo_sync.py`** — this syncs ELO from the *original
  Pubobot bot* running in the same Discord. It refers to a genuinely different
  bot; renaming would make it wrong.
- `utils/import_pubobot_export.py` — one-off import of original Pubobot data.
- `README.md` below the divider — upstream's own README, retained deliberately
  for install/credits/licence (GPLv3 attribution to Leshaka is a **legal
  requirement**, not a leftover).
- `docs/plans/*`, `docs/superpowers/*` — historical records.

### 4. Open question

`PUBobot2.py` is still the entrypoint filename. Renaming touches the Dockerfile,
`ruff.toml`, `CLAUDE.md`, `start.py` and several docs. Worth doing, but it is its
own change — not part of this one.

---

## Housekeeping found along the way

- **A stale git worktree** at `.claude/worktrees/jolly-visvesvaraya-729c6f/`
  holds a full second copy of the repo.
- `COMMANDS.md` documents ~60 of the 101 commands and has three wrong names
  (`/rating hide` → `hide_player`, `/rating unhide` → `unhide_player`,
  `/stats replace_player` → `stats_replace_player`). It has to be rewritten
  wholesale here anyway.
- **No command-usage telemetry exists.** Every "unused" verdict above rests on
  table traces (`douche_log`, `player_phrases`, `queue_bans`, `player_prefs` all
  at 0 rows) and on configuration, never on invocation counts.

---

## Final tally

| Section | Before | After |
|---|---|---|
| Queue & match flow | 20 | 7 |
| Personal settings | 5 | 0 |
| Stats & leaderboards | 11 | 2 |
| Replay linking | 5 | 2 |
| Gold & betting | 4 | 2 |
| Quiz | 8 | 2 |
| Fun & meta | 8 | 0 |
| Admin | 40 | 29 |
| **Total** | **101** | **44** |

**A regular player's menu, once `default_member_permissions` is declared: 14.**

`/add` `/remove` `/report` `/teams` `/subme` `/subfor` `/subauto`
`/profile_link` `/lobby` `/rank` `/leaderboard` `/predictions me`
`/predictions leaderboard` `/quiz_leaderboard`

(`/quiz disable` is admin-gated and does not appear.)
