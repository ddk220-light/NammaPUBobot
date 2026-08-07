# Runbook — schema migrations and rollback

**Applies from:** the unified-data-layer rebuild (stage 1, 2026-07-30) onward.
**Read this before deploying anything that adds a migration to `nammaoe2bot/runtime/migrations.py`.**

## How migrations run

`PUBobot2.py` calls `migrations.run_all(db)` **after** `database.db.connect()` and
**before** `import bot`. That ordering is load-bearing: every bot package declares
its tables with `db.ensure_table()` at import time, and `ensure_table` CREATEs any
name it does not find and cannot rename. Renaming first lets the updated
declarations find the renamed tables.

Migrations are gated by the `schema_migrations` ledger and are individually
idempotent. A migration that raises is **not** recorded, so it retries on the next
boot — and because it raises before `import bot`, no container can ever reach
`ensure_table` with a half-migrated schema.

## Before every migration deploy

1. **Take a backup and verify it.** Not advisory — migrations drop tables.

   ```bash
   brew install mysql-client   # once; mysqldump is not on PATH by default
   export PATH="/opt/homebrew/opt/mysql-client/bin:$PATH"
   DB_URI="$(python3.11 -c "import re;print(re.search(r'DB_URI\s*=\s*[\"\']([^\"\']+)', open('config.cfg').read()).group(1))")" \
     bash scripts/backup_db.sh
   ```

   Then confirm it is restorable, not merely present:

   ```bash
   gunzip -c db_backups/<file>.sql.gz | grep -c "CREATE TABLE"   # expect the full table count
   gunzip -c db_backups/<file>.sql.gz | tail -1                  # expect "-- Dump completed on ..."
   ```

2. Deploy at a quiet hour with no queue in progress — see "Cutover window" below.

## Rollback: there is exactly one safe path

**Restore the backup AND drop the ledger, then redeploy the old commit.**

```sql
DROP TABLE schema_migrations;
```

### Why the ledger must be dropped

`scripts/backup_db.sh` runs `mysqldump <db>` without `--add-drop-database`, so a
restore recreates only the tables in the dump and leaves everything else in place.
A dump taken *before* a migration deploy therefore contains no `schema_migrations`
row — but the live table survives the restore with the migration still recorded.
On the next boot `run_all` skips it, `ensure_table` finds none of the renamed
tables, CREATEs them all empty, and **the bot boots healthy while serving no
history**. `/health` returns 200 throughout.

`nammaoe2bot/runtime/migrations.py` now has a post-condition check that catches this and crashes
the boot instead, naming the offending tables. If you see that error, this is what
happened: drop `schema_migrations` and reboot.

### A code-only rollback is never valid

Redeploying an older commit without restoring does the same thing from the other
direction — the old code's `ensure_table` recreates the old names empty. Worse,
`nammaoe2bot/pickup/stats.py`'s `check_match_id_counter()` runs on the first think tick,
before `on_ready` and before anyone reads a log; against an empty match table it
resets the counter to 0, so every match played during the rollback window takes an
id that collides with real history. Rolling forward afterwards does not fail
loudly — it silently strands that data.

## Cutover window

`railway.toml` sets `healthcheckPath = "/health"` with `healthcheckTimeout = 300`,
and `/health` requires `discord_connected`, so Railway keeps the **old** container
serving until the new one finishes `on_ready` — up to 5 minutes, longer if Discord
rate-limits the login. Throughout that window the old code queries tables the
migration has already renamed away. Nothing is corrupted (every failure is caught
and logged, and no table is resurrected), but `/report` and queue writes error for
users, and `save_state_db` fails each tick, so **in-flight match state from the
cutover window is lost**. Deploy when no match is live.

## Verifying a migration deploy

`/health` returning 200 is **not** sufficient — it runs `SELECT 1` and passes
against a database whose tables are all empty. Always also check row counts:

```sql
SELECT COUNT(*) FROM matches;           -- expect the pre-deploy count
SELECT COUNT(*) FROM channel_settings;  -- expect >= 1, else no queue channels load
```

A migration that only *moves* rows is invisible to those two. Check its own
destination as well — e.g. after `004_identity_v2`:

```sql
SELECT COUNT(*) FROM match_replays;                                  -- was 0 before 004
SELECT COUNT(*) FROM replay_matches WHERE bot_match_id IS NOT NULL;  -- the upper bound it backfills from
```

(That table was called `rs_matches` until `007_raw_renames`. 004 predates 007 but
is gated on the ledger, not on the schema, so a ledger drop re-runs it against a
database 007 has already renamed — it therefore resolves its source at run time,
`replay_matches` first and `rs_matches` second, and names whichever it found in
its SQL and in every log line below. Use `replay_matches` when querying by hand
today. If it can find neither, it says so naming *both* spellings — that line
means a genuine fresh install and nothing else.)

Do **not** assume the gap between those two is all one thing. 004's log line
breaks it into four causes, and they have different fixes:

```
N of M match_replays pairing(s) verified present from R paired replay_matches row(s)
  (skipped A with no matches row, B whose channel is not enrolled in a community,
   C sharing a bot match id with an already-taken pairing;
   D already linked to a different replay)
```

`A`/`B` are the ordinary skips. `C` means two `replay_matches` rows claim the same
bot match — `match_replays` is keyed `(community_id, match_id)` so only one can
be stored, and each collapse is also logged as its own warning naming the match.
`D` means the live writer had already linked that match to a different replay
and `INSERT IGNORE` correctly left it alone. `N` is read back **from the table**
after the write, so it is what landed, not what the migration intended.

And confirm the migration actually ran:

```bash
railway logs -n 60 | grep migrations:
```

## After `007_raw_renames` (stage 3b)

007 renames the eight raw replay tables, renames their match-id column (plus
`civ_picks`'), and DROPS `rs_config`. Verify with:

```sql
SELECT COUNT(*) FROM replay_matches;   -- expect the pre-deploy rs_matches count
SELECT COUNT(*) FROM replay_players;   -- expect the pre-deploy rs_player_games count
SHOW COLUMNS FROM replay_players LIKE 'replay_match_id';  -- expect one row
SHOW COLUMNS FROM civ_picks      LIKE 'replay_match_id';  -- expect one row
SHOW TABLES LIKE 'rs_%';   -- expect ONLY rs_player_game_tags and rs_player_personas
```

`rs_config` held the replay-ingest on/off switch. It is now the
`REPLAY_INGEST_ENABLED` config var (Railway env var → `start.py` →
`config.cfg`), defaulting to enabled. `/replaystats status` reports its current
value; the old `/replaystats enable|disable` subcommands are gone, because a
deployment-wide switch is configuration, not state a command can flip. If
ingestion needs turning off, set `REPLAY_INGEST_ENABLED=false` in Railway and
redeploy.

Every boot prints the resolved value in the deploy log, both ways:

```
Replay ingest: ENABLED (REPLAY_INGEST_ENABLED='True', unset - defaulted)
Replay ingest: DISABLED (REPLAY_INGEST_ENABLED='')
```

The second line is the case worth knowing about: a Railway variable that
**exists but is empty** yields `""`, not the `"True"` default — the default only
applies when the name is absent entirely — and `""` coerces to False. If
ingestion has stopped, `railway logs | grep 'Replay ingest'` says so in one
line. To clear it, delete the variable rather than blanking it.

### Recovering from a rollback to pre-007 code

**Do not follow the generic "drop `schema_migrations` and reboot" advice for
this one — it produces a second, different crash.**

Rolling back to a pre-007 commit lets that container's `ensure_table` recreate
all nine `rs_*` tables empty (the eight rename sources plus `rs_config`) and add
`civ_picks.aoe2_match_id` back beside `replay_match_id`. Nothing is lost — the
populated `replay_*` tables are untouched — but rolling forward then crashes on
the `_assert_renames_landed` post-condition, whose message says to drop the
ledger and reboot. Doing that makes `007_raw_renames` re-run and hit
`rename_table: both exist; resolve manually`, because now both generations of
the table are present. Both crashes are loud and safe; the first one's remedy
just does not apply here.

The correct recovery, in this order:

1. **Confirm which side holds the data.** The `replay_*` tables are
   authoritative; the `rs_*` ones were created empty moments ago.

   ```sql
   SELECT 'replay_matches' t, COUNT(*) n FROM replay_matches
   UNION ALL SELECT 'rs_matches', COUNT(*) FROM rs_matches;
   ```

   Expect the full pre-deploy history on `replay_matches` and `0` on
   `rs_matches`. A non-zero `rs_matches` means the rolled-back container
   ingested during the window; those rows are replays that can simply be
   re-ingested, but read them first if you want to keep the ids.

2. **Drop the resurrected tables.** All nine, not just `rs_matches` — the
   post-condition names every source it finds, so a partial cleanup just moves
   the crash.

   ```sql
   DROP TABLE rs_matches, rs_player_games, rs_player_units, rs_player_techs,
              rs_player_buildings, rs_player_events, rs_player_apm, rs_ingest,
              rs_config;
   ALTER TABLE civ_picks DROP COLUMN aoe2_match_id;   -- only if it came back
   ```

   Leave `rs_player_game_tags` and `rs_player_personas` alone: those two are
   live tables that keep their names until stage 6.

3. **Reboot without touching the ledger.** 007 is still recorded, the schema now
   agrees with it, and the post-condition passes. Nothing needs to re-run.

If the ledger was already dropped before you got here, that is still fine — do
steps 1 and 2 and reboot. Every migration re-runs from the top, all of them are
idempotent, and `004_identity_v2` reads whichever generation of the raw tables
it finds (`replay_matches` first, `rs_matches` second), so the `match_replays`
backfill still rebuilds the historical pairings on a post-007 schema.

## Adding a migration

Every statement in a migration body must be individually idempotent — guarded by
`table_exists`/`column_exists`, or written as `IF EXISTS` / `INSERT IGNORE`. MySQL
DDL auto-commits and nothing wraps the body together with the ledger write, so a
migration that dies halfway re-runs from the top on the next boot.
