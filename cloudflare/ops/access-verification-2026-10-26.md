# Cloudflare and Discord access verification

Verified 26 October 2026, Pacific time, after the owner applied the documented
environment settings. These are real service checks, separate from the earlier
local rehearsal. No API tokens were created, no Worker was deployed, and no demo
match was started during this verification.

Subsequent deployment and the completed live demo are recorded in
[27 October deployment evidence](live-demo-2026-10-27.md). The deployment state
below describes this earlier access-check session.

## Results

| Check | Result |
| --- | --- |
| Codex credential bindings | Cloudflare, Discord bot, operations and optional connector bindings are available. The public account/application/key/guild/channel variables are available. |
| Cloudflare token | Active; account and existing Worker API reads succeeded. Deployment writes were not exercised. |
| Worker identity | `community-gaming-staging`, `APP_ENV=staging`, expected workers.dev origin and four Durable Object bindings. |
| Worker health | HTTP 200, version `0.8.4`. |
| Live logs | Wrangler tail connected and received the log from a deliberate health request; the temporary tail was closed afterward. |
| Discord application | Expected application `1559284610108752068`; public key, interaction endpoint and OAuth redirect matched. |
| Discord channel | Expected guild `706320010368516198`, text channel `test-bot`, ID `1559279290577850368`. |
| Discord permissions | View Channel, Send Messages, Embed Links and Read Message History are effective. |
| Message content | Message Content Intent is enabled. Read all 53 messages returned by the 100-message history request, including ordinary unmentioned human content. Reviewed earlier demo/results/refunds/corrections and current queue messages. |
| Message delivery | Posted one labelled connectivity card, read it, edited it, and read the edit back. No user/role mentions were sent. |
| Operations access | Authenticated inspection and checkpoint requests succeeded. |
| Backup | Private backup and SHA-256 manifest saved: 47 tables, 42 saved matches, schema epoch 5, zero active matches. Source application/guild identities matched. |
| Connector credential | An intentionally invalid empty event returned the app's validation error, HTTP 400 `invalid_settings`, after authentication. No valid event or lobby evidence was submitted. |
| State preservation | Match records, ratings, waiting queues, economy data and pending returns matched before and after the checks. Pickup still has one waiting player. |

[Connectivity card in #test-bot](https://discord.com/channels/706320010368516198/1559279290577850368/1564472802554155129)

## What this does not establish

- The deployed version is still the earlier checkpoint: latest deployment
  `b92ee6a0-4897-4b30-a326-6a101522459e`, created 18 October 2026.
  The test guild does not yet include `/lobby`.
- The new local lobby implementation uses schema epoch 6. It has not been
  deployed or accepted against a real AoE2 game. Do not claim that connector
  authentication validates live game-start/end detection.
- Hosted OAuth reports configured and its registered redirect matches. A fresh
  interactive browser sign-in was not performed in this round.
- Discord message send/edit/read-back works. This connection card is not the
  requested eight-player demo and is not evidence of real user button clicks.
- These checks do not change or validate Railway production.

## Private evidence

Detailed response summaries, channel history, app inspection, checkpoint and
backup files remain outside the repository under
`/workspace/.cloud-setup/nammapubobot/access-checks/`. History, inspection and
backup files are private; no raw token values are printed or included in this
report. The backup has a SHA-256 manifest. The delivery receipt contains the
message ID so rerunning its helper does not send another connectivity card.

Wrangler's JSON output suppresses its normal connection banners. The initial
tail probe therefore did not trigger its intended health request. Subsequent
checks used a bounded scheduled request with the same explicit User-Agent as
the successful API checks and verified the actual health log. Bare Python
default requests were rejected while the explicit API client requests succeeded;
the final results above use successful authenticated requests and read-backs.

The subsequent Worker deployment, command registration and separately scoped
full demo used this channel discussion and private backup as their baseline;
see the linked deployment report for the current state.
