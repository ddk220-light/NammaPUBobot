# Runbook — schema migrations and rollback

**Applies from:** the unified-data-layer rebuild (stage 1, 2026-07-30) onward.
**Read this before deploying anything that adds a migration to `core/migrations.py`.**

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

`core/migrations.py` now has a post-condition check that catches this and crashes
the boot instead, naming the offending tables. If you see that error, this is what
happened: drop `schema_migrations` and reboot.

### A code-only rollback is never valid

Redeploying an older commit without restoring does the same thing from the other
direction — the old code's `ensure_table` recreates the old names empty. Worse,
`bot/stats/stats.py`'s `check_match_id_counter()` runs on the first think tick,
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

And confirm the migration actually ran:

```bash
railway logs -n 60 | grep migrations:
```

## Adding a migration

Every statement in a migration body must be individually idempotent — guarded by
`table_exists`/`column_exists`, or written as `IF EXISTS` / `INSERT IGNORE`. MySQL
DDL auto-commits and nothing wraps the body together with the ledger write, so a
migration that dies halfway re-runs from the top on the next boot.
