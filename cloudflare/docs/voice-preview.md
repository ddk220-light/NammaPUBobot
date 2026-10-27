# Queue voice previews — 0.11.1

Enable **Voice presence → Show up to five people…** in a queue's organizer settings. The existing queue card gains an **In voice only · not in queue** section at the bottom, replacing the old Community Games footer. The voice-only names use italic text in an indented block, visually separate from the normal queue roster. The bot's Discord application name is unchanged.

The list includes up to five verified human members currently in public voice or stage channels in the same server. It excludes anyone waiting in any queue, playing in an active match, or under a local queue restriction. Names render as mentions with `allowed_mentions.parse: []`, so they do not notify anyone. Channels with a View Channel denial in any overwrite are conservatively excluded; the adapter does not reveal occupants of restricted rooms.

“Recent” means the latest observed voice join or move, not the date a member joined the server. Mute/deafen changes do not reorder people. Discord provides no original join time for occupants already present when the connection starts: these follow observed joins in stable user-ID order. After an unresumable restart, ordering starts again from the fresh snapshot.

Enabled queues with a published card share a five-minute refresh per community. New or unpublished queues do not start the connection. Queue actions immediately filter newly queued players out of that queue's cached preview. Departures, other queues' changes and new voice arrivals appear on the next refresh. Changed lists use the same last-five-message edit/replacement policy as roster updates. Unchanged lists generate no Discord history/edit calls. An unavailable feed clears names and shows “Voice list temporarily unavailable”; an empty healthy list shows “No other players in public voice channels.” Initial connection startup may take one refresh cycle.

## Connection and permissions

One `VoicePresence` Durable Object owns the outbound Discord Gateway connection for the entire bot installation. It uses only the unprivileged Guilds and Guild Voice States intents (129), and processes no voice audio or chat messages. The same socket serves `/rally`. The current adapter requires one shard; deployments requiring multiple shards are rejected pending a reviewed sharding implementation.

Preview refreshes renew a ten-minute lease. Rally requests renew a one-hour lease. Once all queues stop refreshing and neither lease remains, a one-minute maintenance alarm closes the socket. Pausing a queue or disabling its preview removes its list and refresh clock. Read-only recovery copies never subscribe. A Gateway disconnect makes the snapshot unavailable until reconnect/resume; the connection respects Discord session-start limits and heartbeat acknowledgements.

No new credentials or privileged intents are needed. The bot still needs View Channel, Read Message History, Send Messages and Embed Links for its queue cards. The source voice channel must be visible to everyone, with no View Channel denials in its overwrites.

## Cost estimate

Cloudflare outbound WebSockets cannot hibernate. At the published 0.128 GB Durable Object allocation, a continuously connected 30-day month uses **331,776 GB-seconds**. The Workers Paid plan starts at **$5/month** and includes **400,000 GB-seconds/month across the account**. Other objects, including the AoE2 feed, share this allowance. Excess duration is rounded up to million-GB-second units at **$12.50 per unit**. This is a usage estimate, not an account bill or a promise of zero extra charges.

Five-minute refreshes add **8,640 per community per 30-day month**, or 86,400 for ten communities. Multiple queues in one community share a refresh; only changed cards invoke Discord. The shared Gateway also has a one-minute maintenance alarm (43,200/month), incoming events and heartbeat traffic. Request and SQLite operations use their own allowances and rates. Ten communities using one bot do not create ten continuously billed sockets; ten independent bot installations do.

Sources, checked 2026-10-27: [Durable Object pricing](https://developers.cloudflare.com/durable-objects/platform/pricing/) and [WebSocket guidance](https://developers.cloudflare.com/durable-objects/best-practices/websockets/). Check the account usage before expanding the rollout.

## Operator rollout and recovery

The normal organizer save supports `queue.voicePreview` as an optional boolean, off by default. For the existing staging deployment, an OPS-authenticated narrow rollout endpoint accepts:

```http
POST /ops/communities/GUILD_ID/features/voice-preview
Content-Type: application/json

{"expectedRevision":70,"queueIds":["QUEUE_UUID"],"enabled":true,"apply":false}
```

Preview first, then submit the same revision and selection with `apply:true`. Only existing enabled queues may be activated. Set `enabled:false` to disable. Stale revisions, production, recovery copies and arbitrary settings are rejected. Changes are audited and do not alter queued players, matches, ratings or wallets. Unchanged queues are not republished during this operation. `GET /ops/voice` reports connection health and lease status without starting the connection or extending its lease. Never put OPS credentials in URLs or shared logs.

The derived public-voice snapshot and its refresh deadline live in Community Durable Object KV (`queueVoice`); the connection lease lives in VoicePresence KV (`wantedUntil`). These caches are not part of the SQL backup and are safe to rebuild. No SQL schema migration is needed (epoch 7). Older releases reject the new JSON setting: before a downgrade, remove every `voicePreview` key through a reviewed settings update while still running a compatible version, and verify no preview alarm remains. Prefer a compatible forward repair during active games.

`tests/voice-preview.test.ts` covers filtering, ordering, five-minute scheduling, unchanged-list suppression, stale clearing, restart/lease behavior, paused and unpublished queues, bounded rollout authorization, and a mocked Gateway connection that stays alive past the handshake timeout. Live deployment verification must separately confirm Gateway health and the actual Discord card.
