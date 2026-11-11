# Operations runbook

Day-to-day operator tasks for a deployed Worker: find a community, export its data, put it back to an earlier point in time, read usage, and get told when something is wrong.

Every `/ops/*` call needs the deployment's `OPS_TOKEN` as a bearer token. Keep the token in your shell environment, never in a command argument or a file you share:

```sh
export ORIGIN=https://your-deployment.example   # the Worker's public origin
export OPS_TOKEN=...                            # from your private secrets file
ops() { curl -fsS -H "Authorization: Bearer $OPS_TOKEN" "$@"; }
```

A Discord server ID ("guild ID") identifies a community everywhere below.

## List communities

```sh
ops "$ORIGIN/ops/communities?limit=100"
```

Returns `{ communities, total, next }`, newest activity first. Each row has:

| Field             | Meaning                                                                         |
| ----------------- | ------------------------------------------------------------------------------- |
| `guild`           | Discord server ID                                                               |
| `firstSeenAt`     | First time the server used the bot or saved settings (milliseconds since epoch) |
| `settingsSavedAt` | Last successful settings save in the console, or `null`                         |
| `lastActivityAt`  | Last Discord interaction or save, accurate to about one hour                    |
| `hasSettings`     | The last save had at least one game or queue                                    |

When `next` is not `null`, pass it back as `cursor` for the following page: `ops "$ORIGIN/ops/communities?limit=100&cursor=$NEXT"`.

The index is written in two places, both best-effort and after the user's response: a successful console settings save, and any signed Discord interaction from a server (at most once an hour per server per Worker instance). It starts empty on a new deployment and fills as servers are used; a server that has never used the bot since this index existed will not be listed until it does. The list lives in the `PublicDirectory` object (table `communities`), not in each community's own storage.

Operator notes for a community (for example every point-in-time restore) are at `ops "$ORIGIN/ops/communities/$GUILD/notes"`.

## Export one community

### Operator backup

```sh
OPS_TOKEN=$OPS_TOKEN npm run operator:backup -- backup "$ORIGIN" "$GUILD" private/backups/$GUILD-$(date -u +%F).json
```

The script reads the table list (`GET /ops/communities/:guild/export`), then every table page by page (`GET /ops/communities/:guild/export/:table?after=ROWID`, up to 2,000 rows or about 2 MB per page). There is no row limit. It writes the same artifact as before (`format`, `communityId`, `sourceApplicationId`, `createdAt`, `tables`) plus a `.manifest.json` with the SHA-256, byte count and per-table row counts.

Pages are not one database transaction. If a table changes while the backup runs, the script prints a `NOTE` naming it; repeat the backup in a quiet period when you need an exact snapshot. For an exact point in time, rely on point-in-time recovery below; the JSON backup is the independent, inspectable copy you keep outside Cloudflare.

`GET /ops/communities/:guild/backup` returns the same artifact in a single streamed response, which is convenient for small communities. A dropped connection produces invalid JSON rather than a partial file that looks complete.

Restoring a JSON backup (`operator:backup -- restore ...`) still only creates an isolated, read-only inspection copy in a non-production deployment, limited to 25 MB and 50,000 rows per table. It is for inspection and forensics; use point-in-time recovery to repair a live community.

### Organizer self-service download

Server managers can download their own data from the console: **Setup → Your data → Download community data** (`GET /api/communities/:guild/export`, signed in with Manage Server). The file is JSON:

```json
{
  "format": 1,
  "kind": "community-data-export",
  "communityId": "…",
  "manifest": {
    "schemaVersion": 9,
    "exportedAt": "…",
    "tables": [{ "name": "matches", "rows": 120 }],
    "excluded": [
      "budget",
      "cards",
      "deadlines",
      "limits",
      "outbox",
      "play_actions",
      "record_actions"
    ]
  },
  "tables": { "matches": [{ "…": "…" }] }
}
```

It contains only that server's own database. Runtime delivery and idempotency tables (`excluded`) and the deployment's application ID are left out. No secrets are stored in community databases.

## Restore a community to a point in time

Each community's SQLite database can be returned to any moment in the last 30 days. This works in production; it is the real disaster-recovery path.

1. **Decide the time** (UTC). Pick a moment just before the damage. Note anything that happened after it (finished matches, gold payouts, settings changes): it will be undone.
2. **Take a JSON backup first** (above). It is your record of the state you are about to replace.
3. **Restore:**

   ```sh
   ops -X POST "$ORIGIN/ops/communities/$GUILD/restore-to" \
     -H 'Content-Type: application/json' \
     -d '{"time":"2026-11-10T18:00:00Z","confirm":"'"$GUILD"'","reason":"Queue deleted by mistake"}'
   ```

   `confirm` must repeat the server ID. Times in the future or older than 30 days are refused. You may pass `"bookmark"` instead of `"time"` (for example a value from `GET /ops/communities/:guild/checkpoint`).

4. **Save the response.** It contains `restoredTo` and `undoBookmark`. The undo bookmark is also kept in the operator notes (`/notes`), which live outside the community's storage so the restore cannot rewind them. The object restarts immediately and the restore applies as it starts again.

### Undo a restore

Restore again, to the undo bookmark:

```sh
ops -X POST "$ORIGIN/ops/communities/$GUILD/restore-to" \
  -H 'Content-Type: application/json' \
  -d '{"bookmark":"<undoBookmark>","confirm":"'"$GUILD"'","reason":"Undo restore"}'
```

That call returns its own undo bookmark too.

### After a restore, check

- `ops "$ORIGIN/ops/communities/$GUILD/inspect"`: matches, queues, wallets and recent changes look like the chosen moment.
- `ops "$ORIGIN/ops/communities/$GUILD/notes"`: the restore is recorded with its undo bookmark.
- In Discord, run `/setup status` in a queue channel and press a queue button. On start the object re-arms its timers from the restored tables, so check-in timers and queue cards resume from the restored state.
- Matches that finished after the restore point are active again and their results and gold movements are gone. Tell the server's organizers, and use the console's administrator actions to report or cancel them again.
- Discord messages posted after the restore point remain in Discord; cards are refreshed by the bot on the next change, and stale ones can be refreshed from Matches → Administrator actions.
- Public pages: the public directory keeps the newest settings revision it saw. If the restore went back past settings saves, the public page entry updates once the community's settings revision passes the old value again; public requests always recheck the saved policy, so nothing private is exposed meanwhile.

### Rehearse on staging

The local test runtime (`vitest`, `wrangler dev`) does not implement point-in-time recovery, so the automated tests exercise the handler with a stand-in for the storage calls. Rehearse on staging before relying on it:

1. On a staging test server, save settings, play or create a manual match, and note the time.
2. Delete a queue in the console.
3. Restore to the noted time with the call above; confirm the queue is back (`/inspect` and the console).
4. Undo with the returned `undoBookmark`; confirm the queue is gone again.
5. Check `/notes` lists both restores.

## Usage report

```sh
export CLOUDFLARE_API_TOKEN=...   # Account Analytics: Read, Workers Scripts: Read
export CLOUDFLARE_ACCOUNT_ID=...  # 32-character account ID
npm run usage:report -- --worker community-gaming-production --days 30
```

It reads Cloudflare's GraphQL analytics (`workersInvocationsAdaptive`, `durableObjectsInvocationsAdaptiveGroups`, `durableObjectsPeriodicGroups`, `durableObjectsSqlStorageGroups`) and the Worker's Durable Object namespaces, then prints:

- **Worker requests, errors and CPU** for the named Worker only.
- **Durable Object calls** by kind (`jsrpc` is a call from the Worker, `alarm` a timer) and their errors.
- **Durable Object time** in GB-seconds. This is the billed duration and usually the first allowance a pickup bot approaches: an object is billed while it is active, including while a long call or connection keeps it awake.
- **Rows read and written** and **storage** (the latest day's stored bytes).
- **By class**: which object class uses the time and storage.
- **A 30-day projection** against the Workers Paid plan's included usage, with a `WARNING` at 80% of any allowance. The duration allowance is 400,000 GB-s a month. Check Cloudflare's current pricing page if these numbers matter for a decision.

`--days` accepts 1 to 31. `--json` prints the structured summary. The command exits with status 2 when it prints a warning, so it can run on a schedule. Analytics can lag by several minutes, and very recent days may be incomplete.

## Set up alerts

1. **Uptime check.** In any external uptime service, add an HTTPS check of `GET $ORIGIN/health` every 1–5 minutes. Alert when the status is not 200 or the body lacks `"ok":true`. Send alerts to email and a phone. This catches deploy failures, DNS problems and Worker exceptions on the request path.
2. **Budget alert (Cloudflare).** On a Pay-as-you-go account: Manage Account → Billing → Billable Usage → **Create budget alert**, or Notifications → Add → Budget Alert. Set a dollar threshold for monthly usage-based spend and add your email. Cloudflare processes usage once a day, so this fires the day after the threshold is crossed. It does not cap or pause usage, and the Workers Paid subscription fee itself is not counted.
3. **Usage-based billing notification (Cloudflare).** Notifications → Add → Billable Usage lets you choose a product and a threshold for a single metric. Products available vary by account; use it if Workers or Durable Objects appear in the list.
4. **Weekly usage report.** Run `npm run usage:report` weekly (or from a scheduled job on your own machine) and act on any warning before the month ends.
5. **Errors.** Check Workers & Pages → your Worker → Observability (logs) after each deploy and when the uptime check fires. Request IDs in user-facing errors match the `requestId` in logs.
