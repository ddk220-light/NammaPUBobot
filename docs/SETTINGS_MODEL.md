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
  create a queue, and seed player ratings when a ranked queue exists. A full
  historical import is offered as an optional path before live play begins.
  Identity linking is currently recommended because lobby matching and
  replay-derived player analysis benefit from it, but it is not required for
  basic queues.
- **Capabilities** reports both status and the actual control scope. A feature
  may be controlled per community, channel, queue, deployment, or be built in.
- **Diagnostics** compares persisted enrollment, live bot configuration and
  Discord visibility, then reports community-scoped data counts.

All stored counts either carry `community_id` directly or join through
`community_channels`. Global replay and identity tables are never counted in
isolation.

## Access and compute policy

Each community has one `community_policies` row, edited through:

```
GET  /api/admin/communities/<community_id>/policy
POST /api/admin/communities/<community_id>/policy
```

The public dashboard visibility can be `public`, `members`, or `admins`.
Member and administrator reads require a valid Discord OAuth session and a
current guild-member lookup; private tenants return the same 404 response as
an unknown tenant so the API does not disclose their existence. The policy is
enforced by the shared community resolver before any public statistics query
runs, including the legacy flagship URL aliases.

Replay analysis has two independent gates. `DEPLOYMENT_MODE=hosted` is a hard
product boundary and never permits replay downloads or parsing.
`DEPLOYMENT_MODE=self_hosted` permits the pipeline only when the operator's
`REPLAY_INGEST_ENABLED` switch and the community's replay-analysis preference
are both enabled. The work and retry selectors join through
`community_channels` and `community_policies`, so disabling one tenant does
not stop an opted-in tenant on the same self-hosted installation. An absent
policy row preserves the historical defaults: a public dashboard and replay
analysis requested.

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

This remains deliberately a **ratings-only seed**. It does not fabricate
matches, win/loss counts, or historical rating changes. Uploads are capped at
700 KB compressed, 2 MB expanded, 25 ZIP members, and 500 player rows.

### One-time AoE ranked-team seed

Admins can alternatively initialize linked current members from the ranked
team rating observed on their AoE2 profiles:

```
/api/admin/communities/<community_id>/channels/<channel_id>/ratings/seed/aoe-team/preview
/api/admin/communities/<community_id>/channels/<channel_id>/ratings/seed/aoe-team/apply
```

The preview reads the global identity map but keeps only non-bot members
currently visible in the authorized Discord guild. It checks at most 200
linked profiles belonging to players without a live rating, with six
concurrent requests and a 45-second overall bound.
If a member owns multiple profiles, the highest current `rm_team` rating is
selected and every observed candidate is shown. A missing team rating is a
legitimate skip; a missing profile, malformed response, timeout, or service
failure blocks the whole apply so a partial outage cannot silently become the
community's initial ladder.

The external ladder is Elo-only, so the selected channel's configured initial
deviation is used. As with manual/CSV seeding, writes are insert-only or
conditional on `rating IS NULL`; an existing live rating is never replaced.
Apply refetches the source, checks the observation digest, rechecks active
match state, writes rating-history audit rows, and records a unique per-channel
ledger entry. The unique `(channel_id, kind)` constraint makes this source
apply exactly once even if two administrators race it.

## Full historical migration

A separate one-time workflow imports a complete Pubobot export into a new
pickup channel:

```
/api/admin/communities/<community_id>/channels/<channel_id>/migration/pubobot/preview
/api/admin/communities/<community_id>/channels/<channel_id>/migration/pubobot/apply
```

The ZIP must contain `qc_matches.csv`, `qc_players.csv`,
`qc_player_matches.csv`, and `qc_rating_history.csv`. The administrator also
chooses the timezone in which the export's naive timestamps were written. The
parser reads members in memory, accepts UTF-8 only, validates relational
references and duplicate keys, and enforces limits of 5 MB compressed, 25 MB
expanded, 40 archive members, 10,000 matches, 2,000 players, and 100,000 rows
in each relation/history export.

This path intentionally does not merge two live histories. Preview and apply
both require:

- the pickup channel owns its rating table rather than sharing another
  channel's rating host;
- no recorded match exists in the target pickup channel;
- no rating history exists in the target rating channel;
- no match is active in the pickup channel; and
- any existing ratings-only seed has zero activity and exactly matches the
  archive's rating/deviation snapshot.

Apply reparses the archive, verifies the preview digest, and rechecks the
database state inside a single transaction. Legacy match IDs are never reused
directly: the importer locks `match_counter`, reserves one consecutive global
range, and rewrites matches, roster rows, and rating-history references to the
new IDs. Normal match creation uses that same locked counter. An import ledger
and source-to-target match map make the operation auditable and prevent the
same archive from being applied twice to the same target.

The offline `utils/import_pubobot_export.py` remains available for operator-led
recovery work, but guided community onboarding should use the web preflight.

## Identity onboarding

The identity workflow maps current Discord members to AoE2 profile IDs. It
accepts manual rows, a CSV with `user_id` and `profile_id`, or a ZIP containing
`profile_resolved.csv` or `player_profile_map.csv`. Multiple profiles for one
member are valid and additive.

Identity truth is global: an AoE2 profile belongs to the same Discord person
regardless of which community observes it. That makes the write boundary
stricter than rating import:

- every target Discord user must currently be a non-bot member of the
  authorized guild;
- a new or currently unowned profile can be linked;
- a profile already linked to the same user is an idempotent no-op;
- a profile owned by another user is reported as a conflict without revealing
  that other user's ID, and bulk apply is blocked;
- imports are additive and never release a member's other profiles.

Preview and apply use the same digest/recheck pattern as rating onboarding:

```
/api/admin/communities/<community_id>/identities/import/preview
/api/admin/communities/<community_id>/identities/import/apply
```

Apply uses `INSERT IGNORE` for a new profile and a conditional
`UPDATE ... WHERE user_id IS NULL` for a known unowned profile. A race therefore
cannot reassign an existing owner. The resolver cache is invalidated only after
the transaction commits.

An uploaded AoE2 name is useful preview context but is not written to
`identities.aoe2_name`: that column promises an in-game name observed through
the AoE2 API or a replay, while a CSV label is only an administrator's claim.

## Current control scopes

| Feature | Current scope | Current control |
| --- | --- | --- |
| Pickup queues | Community/channel | Channel and queue configuration |
| Ratings | Channel | Channel configuration and insert-only onboarding seed |
| Flash predictions | Queue | `predictions_enabled` on ranked queues |
| Gold economy | Community | Automatic; no admin switch yet |
| Daily quiz | Channel, but singleton deployment scheduler | No web editor yet |
| Lobby tracking | Built in for ranked matches | No admin switch yet |
| Replay analysis | Community inside a self-hosted deployment | Community preference, bounded by `DEPLOYMENT_MODE` and `REPLAY_INGEST_ENABLED` |
| Public dashboard | Community | `public`, `members`, or `admins` policy |

The overview deliberately exposes these limitations. It must not label the
quiz as tenant-configurable until its runtime contract is changed accordingly.

## Still to build

- a tenant-safe quiz scheduler and editor;
- guided resolution for identity ownership conflicts;
- actionable diagnostics and repair flows;
- the full basic/advanced settings information architecture.
