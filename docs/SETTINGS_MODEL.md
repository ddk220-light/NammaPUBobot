# Community settings model

The settings surface is rooted at a `communities` row, not at a caller-supplied
Discord channel. Authenticated routes use:

```
/api/admin/communities/<community_id>/...
```

Every request requires a valid Discord OAuth session and guild-administrator
authority. A channel settings request is accepted only when all three sources
agree:

1. `community_channels` assigns the channel to the requested community;
2. the running bot has a configured queue-channel object for the same guild;
3. Discord resolves the channel inside that guild.

POST requests additionally require the session CSRF token. The older
`/api/guilds/...` and `/api/channels/...` routes remain compatibility aliases,
but use the same resolver and authorization checks.

## Overview contract

`GET /api/admin/communities/<community_id>/overview` returns three blocks:

- **Onboarding** reports the required setup sequence: load a pickup channel,
  create a queue, and seed player ratings when a ranked queue exists. Identity
  linking is currently recommended because lobby matching and replay-derived
  player analysis benefit from it, but it is not required for basic queues.
- **Capabilities** reports both status and the actual control scope. A feature
  may be controlled per community, channel, queue, deployment, or be built in.
- **Diagnostics** compares persisted enrollment, live bot configuration and
  Discord visibility, then reports community-scoped data counts.

All stored counts either carry `community_id` directly or join through
`community_channels`. Global replay and identity tables are never counted in
isolation.

## Rating onboarding

The community overview links to a rating seed workflow for each configured
pickup channel. The selected pickup channel resolves its real
`rating.channel_id`; this matters because channels may share a rating host.
The host is accepted only when Discord confirms that it belongs to the same
guild as the authorized community.

The workflow accepts either:

- a manual row containing Discord user ID, rating, and optional nickname and
  deviation;
- a UTF-8 CSV with `user_id` and `rating` columns (`discord_id`/`elo` aliases
  are accepted), plus optional `nick` and `deviation`; or
- a Pubobot export ZIP. The reader selects `qc_players.csv` and does not
  extract any archive member to disk. If no `qc_players.csv` exists, a ZIP is
  accepted only when it contains exactly one CSV.

Preview and apply are separate CSRF-protected POSTs:

```
/api/admin/communities/<community_id>/channels/<channel_id>/ratings/seed/preview
/api/admin/communities/<community_id>/channels/<channel_id>/ratings/seed/apply
```

Preview reports every row as new, unrated, existing, or invalid. Apply must
return the preview digest and reparses/rechecks the same input. It runs in one
database transaction and uses only `INSERT IGNORE` or a conditional
`UPDATE ... WHERE rating IS NULL`. Consequently it can add a missing player or
initialize an existing empty rating, but it cannot replace a live rating even
if another write races the preview. Each applied row gets a `rating_history`
audit entry identifying the web administrator.

This is deliberately a **ratings-only seed**. Pubobot win/loss counts, matches,
and rating history are not presented as current NammaAoe2Bot records. The
offline `utils/import_pubobot_export.py` remains the operator tool for a full
historical migration. Uploads are capped at 700 KB compressed, 2 MB expanded,
25 ZIP members, and 500 player rows.

## Current control scopes

| Feature | Current scope | Current control |
| --- | --- | --- |
| Pickup queues | Community/channel | Channel and queue configuration |
| Ratings | Channel | Channel configuration and insert-only onboarding seed |
| Flash predictions | Queue | `predictions_enabled` on ranked queues |
| Gold economy | Community | Automatic; no admin switch yet |
| Daily quiz | Channel, but singleton deployment scheduler | No web editor yet |
| Lobby tracking | Built in for ranked matches | No admin switch yet |
| Replay analysis | Deployment | `REPLAY_INGEST_ENABLED` environment setting |
| Public dashboard | Community | Always public; privacy control not built yet |

The overview deliberately exposes these limitations. It must not label the
quiz or replay pipeline as tenant-configurable until their runtime contracts
are changed accordingly.

## Still to build

- community-level feature policy and public/private dashboard settings;
- a tenant-safe quiz scheduler and editor;
- identity import and mapping onboarding;
- full historical migration UI and one-time AoE team-rating seeding;
- actionable diagnostics and repair flows;
- the full basic/advanced settings information architecture.
