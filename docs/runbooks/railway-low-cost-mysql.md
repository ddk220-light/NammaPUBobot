# Railway low-cost MySQL runbook

This deployment is intentionally optimized for one small Discord community:
roughly 20 players, a few play nights per week, and about 10 matches on an
active day. The target is **under $2/month of project resource usage** while
remaining on Railway and MySQL. Railway's Hobby subscription still has a $5
monthly floor, with its first $5 of resource usage included; the target here is
the project usage shown in Railway, not a promise that the account invoice can
be below the subscription price.

Railway currently meters RAM at $10/GB-month and CPU at $20/vCPU-month. A MySQL
process holding 1 GB continuously therefore costs about $10/month even when it
serves no queries. Serverless can make an inactive service sleep after ten
minutes, but an open connection or periodic query prevents that. See
[Railway pricing](https://docs.railway.com/pricing/plans) and
[Railway Serverless behavior](https://docs.railway.com/deployments/serverless).

## The 15 cost controls and their state

| # | Control | Implemented behavior |
|---|---|---|
| 1 | Pause replay downloads/parsing | Off by default and hard-disabled in hosted mode. |
| 2 | Disable replay-heavy product reads | Match Cards, scouting, rank charts, and replay-derived dashboard endpoints are independently off by default. |
| 3 | Remove the heavy runtime stack | Matplotlib, `mgz`, `aocref`, Requests, and tqdm are absent from the default image; `requirements-replay.txt` is opt-in for self-hosting. |
| 4 | Stop unbounded message/file logs | General channel capture is removed, upstream-bot evidence is a bounded redacted stdout line, and duplicate file logging is opt-in. |
| 5 | Shrink Discord's cache | Raw reaction events preserve check-in behavior with a 100-message cache. |
| 6 | Write state only when changed | Canonical snapshots prevent the old MySQL write every 30 seconds while the queue is idle. |
| 7 | Remove the queue-ban maintenance write | Ban expiry is derived in SQL when read; there is no once-per-minute update. |
| 8 | Make lobby polling demand-driven | A writer arms two-second launch checks; no live lobby means one recovery pass per day. |
| 9 | Make prediction recovery demand-driven | New books arm the job; no open/frozen book means one recovery pass per day. |
| 10 | Schedule quizzes by deadline | The job wakes at the next configured post/close time instead of polling MySQL continuously. |
| 11 | Reduce repair scans | Civ linking and paused-mode civ aggregates use event hooks plus one daily recovery pass. Replay repair/retention jobs do not run while replay ingestion is paused. |
| 12 | Hold zero idle MySQL sockets | The pool starts at zero, is capped at two connections, and clears free sockets after 60 seconds. |
| 13 | Survive a sleeping database | Connection acquisition retries before any SQL is sent; statements themselves are never blindly replayed. |
| 14 | Keep probes and the web process light | `/live` and `/health` issue no SQL, `/ready` performs one bounded query for deploys, and `WS_ENABLE=False` imports only a tiny probe app. Fatal supervised tasks exit non-zero. |
| 15 | Right-size and sleep the MySQL service | Use the canary replacement below, private networking, replay-detail purge after a verified backup, Serverless, resource limits, and a usage alert. |

## What is retained

The lightweight product continues to retain the data that implements its live
features:

- communities, channel configuration, identities, player ratings and rating history;
- matches and match rosters/results;
- lobby links and confirmed launch/completion timestamps;
- prediction books, bets, gold balances and the immutable gold ledger;
- quiz configuration, posts and answers;
- civilization picks and compact per-community civilization totals;
- the compact snapshot needed to restore in-flight queues and matches.

The five high-cardinality replay-detail tables are no longer required by an
enabled feature: `replay_events`, `replay_techs`, `replay_buildings`,
`replay_units`, and `replay_apm`. The compact replay spine and derived summaries
are deliberately preserved, so the purge does not erase match/result history
or the frozen historical aggregates.

## Before changing Railway

1. Set these bot variables and deploy the application change:

   ```text
   DEPLOYMENT_MODE=hosted
   REPLAY_INGEST_ENABLED=False
   REPLAY_POSTGAME_CARDS_ENABLED=False
   SCOUTING_REPORT_ENABLED=False
   RANK_ELO_CHART_ENABLED=False
   REPLAY_DASHBOARD_ENABLED=False
   DB_POOL_MAX_SIZE=2
   DB_IDLE_CLOSE_SECONDS=60
   FILE_LOG_ENABLED=False
   ```

2. Confirm `/ready` passes during deployment and that `/health` does not create
   MySQL traffic afterwards.
3. Make a backup to storage **outside** the database volume:

   ```bash
   BACKUP_DIR=/safe/external/path ./scripts/backup_db.sh
   ```

   The script includes routines/events/triggers, gzip-tests the result, and
   writes a SHA-256 sidecar. A checksum proves file integrity, not
   restorability. Restore the dump into the canary service in the next section
   before deleting or replacing anything.

## Replace, do not downgrade in place

The existing database has previously reported a MySQL 9.x server. Do not point
a MySQL 8.4 image at that volume: MySQL data directories are not a safe
downgrade interface.

Create a second Railway MySQL service from the official `mysql:8.4` image with
a new volume. Use the existing generated credentials or create fresh ones. Set
this literal custom start command (a Docker/image start override replaces the
image entrypoint, so the `docker-entrypoint.sh` prefix is required):

```text
/usr/local/bin/docker-entrypoint.sh mysqld --innodb-buffer-pool-size=64M --innodb-log-buffer-size=8M --max-connections=20 --performance-schema=OFF --skip-mysqlx --disable-log-bin --temptable-max-ram=8M --temptable-max-mmap=0 --tmp-table-size=4M --max-heap-table-size=4M --thread-cache-size=0 --table-open-cache=400 --table-open-cache-instances=1 --table-definition-cache=400
```

These settings trade cache hit rate and built-in performance instrumentation
for a much smaller idle process. They fit this workload, not a general-purpose
database. MySQL documents a 5 MB minimum buffer pool, notes additional InnoDB
overhead, and documents that Performance Schema sizing can materially affect
memory; 64 MB is a conservative useful cache rather than an assertion that the
whole server will use 64 MB. See
[MySQL memory use](https://dev.mysql.com/doc/refman/8.4/en/memory-use.html),
[InnoDB variables](https://dev.mysql.com/doc/refman/8.4/en/innodb-parameters.html),
and [Performance Schema startup configuration](https://dev.mysql.com/doc/refman/8.4/en/performance-schema-startup-configuration.html).

Give the canary a 256 MB memory limit for its first restore/boot. After the
restore and smoke test, use Railway metrics to set a limit at least 25% above
the observed peak; 192 MB is the intended steady-state limit, and 128 MB should
only be used if an actual play-night trace stays safely below it. A low limit
does not itself reduce billing—actual resident memory does—but it prevents a
regression from becoming a surprise bill.

Restore the dump into the new service, then compare at minimum:

```sql
SELECT COUNT(*) FROM communities;
SELECT COUNT(*) FROM player_ratings;
SELECT COUNT(*) FROM matches;
SELECT COUNT(*) FROM match_players;
SELECT COUNT(*) FROM prediction_posts;
SELECT COUNT(*) FROM prediction_bets;
SELECT COUNT(*) FROM gold_ledger;
SELECT COUNT(*) FROM quiz_posts;
SELECT COUNT(*) FROM quiz_answers;
SELECT COUNT(*) FROM civ_picks;
```

Also run `CHECK TABLE` for the core tables, point a non-production bot instance
at the canary, and exercise queue add/remove, match formation/report, one bet,
one quiz response, `/rank`, and the dashboard's core leaderboards. Only then
change the production bot's `MYSQL_URL` reference to the new private service.

## Reclaim replay storage

First run the census; it changes nothing:

```bash
python scripts/purge_paused_replay_detail.py
```

After the restored canary works and the backup is kept outside both database
volumes, run the explicit purge against the canary:

```bash
REPLAY_INGEST_ENABLED=False REPLAY_DASHBOARD_ENABLED=False \
python scripts/purge_paused_replay_detail.py --apply \
  --backup /safe/external/path/railway-YYYY_MM_DD_HH_MM_SS.sql.gz \
  --confirm PURGE_REPLAY_DETAIL
```

The script refuses to apply with either replay switch missing or enabled. It
truncates only the five allowlisted detail tables and prints their allocation
beforehand. Keep the old database service intact until the new service has
survived a real play night; that is the rollback.

## Networking, Serverless and monitoring

- Reference `MYSQL_URL`/`MYSQLHOST` from the MySQL service so the bot uses
  Railway's private network. Remove the database TCP Proxy after the cutover if
  no external client needs it. Railway documents private connection variables
  in its [MySQL guide](https://docs.railway.com/databases/mysql).
- Enable Serverless on the **MySQL service only**. Do not enable it on the bot:
  Discord gateway heartbeats are outbound traffic, so the bot is intentionally
  always awake. The zero-idle pool lets MySQL sleep after quiet periods.
- Do not point an external uptime monitor at `/ready`; it wakes MySQL. Monitor
  `/health` or `/live`, which are DB-free. Railway health checks run during a
  deployment, so `railway.toml` correctly uses `/ready` there.
- Set one replica for both services. Set a project usage alert at $1.50 and a
  hard limit appropriate to the workspace. Compare a seven-day play-night trace
  with the prior baseline before deleting the old service.

At Railway's current rates, an always-on bot averaging 80–100 MB is roughly
$0.80–$1.00/month of RAM. A 100–150 MB MySQL service awake only 10–20% of the
month adds roughly $0.10–$0.30/month of RAM; CPU, volume and egress should be
small for this workload. That makes sub-$2 **resource usage** plausible. The
actual result must be verified from Railway's minutely metrics because Discord
gateway traffic keeps the bot awake and MySQL cold-start/restore peaks are
workload-dependent.
