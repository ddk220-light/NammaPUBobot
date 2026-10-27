# Agent access for Cloudflare and Discord

This is the consolidated setup for deploying and testing the existing Community
Games Worker, reading Discord test-channel history, and running a visible demo.
The entire Cloudflare installation is currently the test environment; Railway
remains production. No second Worker installation or Discord application is needed.

This guide supersedes the conflicting credential instructions in the setup
conversation. It describes the desired configuration, not verified live access.
Reuse existing credentials. No API tokens are created by following the agent's
setup scripts, and the agent must not create additional Cloudflare tokens.

The owner's applied configuration passed the
[live access checks on 26 October 2026](../ops/access-verification-2026-10-26.md).
That report separates credential/connectivity checks from pending feature and
full-demo acceptance.

## Network secrets, environment variables, and Worker secrets

| Setting | What it means | Use it for |
| --- | --- | --- |
| **Codex environment variable** | A named value available to workspace programs. An ordinary variable contains the actual value; the name does not itself protect a credential. | Account/application IDs, public keys, server IDs, URLs. |
| **Codex network secret** | A credential held by the platform. Code receives a proxy placeholder through a named variable; the HTTPS proxy substitutes the real credential only on configured destination hosts. | Credentials this workspace sends to external services. |
| **Cloudflare Worker secret** | A real secret stored for the deployed Worker and available to its code through an `env` binding. This is separate from Codex settings. | The deployed bot token, OAuth client secret, and app operations/connector secrets. |

A network secret **also has an environment-variable name**. The difference is
whether local code receives the actual value or a placeholder. The code must
still send the correct authorization header; the proxy does not authenticate an
otherwise unauthenticated request automatically. A placeholder must not be
printed, decoded, or rejected simply because it differs from a provider's token
format.

Use network secrets for outgoing credentials and ordinary variables for public
configuration. A credential installed on Cloudflare is not automatically available
in Codex, and a Codex network secret is not automatically installed on the Worker.
Cloudflare deployment access does not reveal existing Worker secret values or
provide direct Discord API access. Supplying the existing bot token as a Codex
network secret enables direct history/permission checks without adding a special
Discord-access endpoint to the Worker.

## 1. Exact credential inventory for Codex

Enter values through the environment's secure settings, not Git or chat.
For each network-secret row, use the listed name for both the secret's binding
name and its exposed environment-variable name.

| Variable name | Store as | Allowed HTTPS destination | Purpose and source |
| --- | --- | --- | --- |
| `CLOUDFLARE_API_TOKEN` | **Network secret** | `api.cloudflare.com` | Deploy/inspect the Worker and inspect live logs. **Reuse the existing token currently named `CloudFlareKey`**, with the permissions below. |
| `DISCORD_BOT_TOKEN` | **Network secret** | `discord.com` | Read #test-bot history; identify the application; read/post/edit the bot's messages; register this application's test-guild commands. Use the existing **Community Games Staging** bot token. |
| `OPS_TOKEN` | **Network secret** | `community-gaming-staging.deepakdhanavel.workers.dev` | Authenticate the app's private inspection, backup, recovery and import endpoints. Use the same existing value stored as `OPS_TOKEN` on the Worker. Demo preparation uses inspection/checkpoint/backup. |

These authorize three different services. There is **one Cloudflare API token**,
not separate deployment, logging or token-management tokens.

For a consistent final configuration, rename/migrate the Codex binding
`CloudFlareKey` to `CLOUDFLARE_API_TOKEN`, keeping **the same existing value**.
This changes a variable name, not the Cloudflare token. Wrangler reads the
standard name directly. During the transition, the agent can map the old name
inside its invoking process without generating or displaying any token.

`OPS_TOKEN` is an application-owned shared secret. It authorizes the Worker's
operations routes, not arbitrary game actions or Discord impersonation. Its
authority is not limited to a single guild by the secret itself; the agent must
use only the documented test guild.

### Optional credentials: do not add for the current demo by default

| Name | Needed when | Codex storage/destination |
| --- | --- | --- |
| `DISCORD_CLIENT_SECRET` | Running a real OAuth exchange from this local workspace. The hosted web login already uses the secret on the Worker. | Network secret → `discord.com`. |
| `CONNECTOR_TOKEN` | Testing the external `/connectors/game-ended` push endpoint. Current HTTP polling and a demo that skips the real lobby do not require it. | Network secret → `community-gaming-staging.deepakdhanavel.workers.dev`; same value as the Worker secret. |

The current public AoE2 provider requires no API key. This workflow does not need
a Railway token, production database password, personal Discord token, or
Discord password.

## 2. Cloudflare: edit the existing token

Open [Cloudflare API Tokens](https://dash.cloudflare.com/profile/api-tokens/) and
edit the existing user API token. Suggested display name:
**NammaPUBobot Codex Test Deployment**. The display name need not match the
environment-variable name.

Use this permission set for deployment and live diagnostics:

| Scope | Permission | Access | Purpose |
| --- | --- | --- | --- |
| Account | Workers Scripts | Edit | Read/deploy the Worker and manage its configuration/bindings. |
| Account | Account Settings | Read | Discover and verify the account. |
| Account | Workers Tail | Read | Inspect live execution logs with `wrangler tail`. |

The first two are the previously requested deployment permissions. Workers Tail
Read is the additional read-only permission for live log diagnostics; reading
Discord messages does not require it.

1. Under **Account Resources**, select **Include → Specific account → the account
   containing `community-gaming-staging`**.
2. Remove **User → API Tokens → Edit** from this token; token creation/management
   is not part of this workflow.
3. No Zone, DNS, Workers Routes, billing, KV or R2 permissions are required by the
   current `workers.dev` deployment. Application storage is SQLite Durable Objects.
4. Set an expiry appropriate to the test period. If using IP restrictions, use a
   verified stable workspace egress range, not a laptop IP.
5. Save the existing token's permissions and supply its existing value as the
   Codex network secret `CLOUDFLARE_API_TOKEN`.

Workers Scripts Edit includes create/read/update/delete access across the
selected account's Workers; it cannot be restricted here to one Worker name.
The agent must target only the documented test Worker.

API-token authentication does not require `wrangler login` or workspace access to
`dash.cloudflare.com`. Validate the intended account/Worker operations directly;
do not add unrelated user-profile permissions just to satisfy a broad CLI check.

## 3. Discord: existing bot, channel permissions, message content

Use the existing application in the
[Discord Developer Portal](https://discord.com/developers/applications):

| Identity | Expected value |
| --- | --- |
| Application | Community Games Staging |
| Application ID | `1559284610108752068` |
| Server/guild ID | `706320010368516198` |
| Text channel | `#test-bot` |
| Text channel ID | `1559279290577850368` |

Use its **Bot token**, not its OAuth client secret, public key, webhook URL or a
personal account token. Discord bot tokens do not have Cloudflare-style permission
scopes; access comes from server roles, channel overrides and app settings.

Reuse the existing saved token. If the owner no longer has its value, **Reset
Token** invalidates the value on the running Worker. Any necessary reset must be
a coordinated rotation in the Worker and Codex settings, not a casual step to
enable agent access. Cloudflare secret listings do not reveal stored token values.

### Installation scopes and channel permissions

The bot installation scopes are **`bot`** and **`applications.commands`**. An
already-installed bot does not need to be recreated or invited again to reuse
its token.

Ensure these permissions are effective in **#test-bot**, including category and
channel overrides:

| Permission | Purpose |
| --- | --- |
| View Channel | Access the channel. |
| Read Message History | Read prior discussion and the demo messages. |
| Send Messages | Post the demo's messages and replies. |
| Embed Links | Render match/result cards. |

These match the repository's base permission bits: **84992**. The bot does not
need Administrator, Manage Server or Manage Messages for this workflow; it can
edit/delete its own messages. A **human organizer** needs Manage Server, ownership
or Administrator to use the app's admin controls. That is a separate permission.

Threads, file uploads, rank-role changes and nickname changes require additional
feature-specific permissions only if those features are explicitly tested. The
current text-channel demo does not require them.

### Enable Message Content Intent to read the conversation

Developer Portal → this application → **Bot → Privileged Gateway Intents →
Message Content Intent: ON**.

This permits reading ordinary, unmentioned guild conversation. Discord applies
content restrictions to HTTP history responses as well as Gateway events. Without
the intent, IDs can be readable while content, embeds, attachments and components
are empty. The bot's own messages and certain exceptions remain readable; those
exceptions are insufficient for reviewing what everyone said. Applications
eligible for verification may need Discord's approval for the intent.

Leave Presence Intent and Server Members Intent off for the current HTTP/demo
workflow; no member-list crawl is needed. Earlier instructions to leave privileged
intents off apply to ordinary slash/button gameplay. Conversation review is the
additional use case requiring Message Content Intent.

### Preserve the existing application URLs

- Interactions endpoint:
  `https://community-gaming-staging.deepakdhanavel.workers.dev/interactions`
- OAuth2 redirect:
  `https://community-gaming-staging.deepakdhanavel.workers.dev/auth/callback`
- Web sign-in scopes: **`identify guilds`**, separate from bot-installation scopes.
- Register this application's commands in the documented test guild, not globally
  for an unrelated bot.

## 4. Secrets and variables on the Worker itself

Cloudflare dashboard → **Workers & Pages → community-gaming-staging → Settings →
Variables and Secrets**. Preserve these existing bindings:

| Worker binding | Worker storage | Role |
| --- | --- | --- |
| `DISCORD_BOT_TOKEN` | Secret | Use the Discord API as the existing bot. |
| `DISCORD_CLIENT_SECRET` | Secret | Complete the hosted dashboard's OAuth login. |
| `OPS_TOKEN` | Secret | Protect operations endpoints; must match Codex's value. |
| `CONNECTOR_TOKEN` | Secret, optional | Protect external connector pushes if used. |
| `DISCORD_APPLICATION_ID` | Plain variable is sufficient; an existing secret binding is also fine | Application identity. |
| `DISCORD_PUBLIC_KEY` | Plain variable is sufficient; an existing secret binding is also fine | Locally verify Discord's Ed25519 signatures. This key is public. |
| `APP_ENV` | Plain variable | `staging`, the code's name for the existing test installation. |
| `APP_ORIGIN` | Plain variable | `https://community-gaming-staging.deepakdhanavel.workers.dev` |

The OAuth client secret can stay on the Worker when testing hosted login. The
Cloudflare account API token does not belong on the Worker.

If `OPS_TOKEN` does not exist, the owner can generate **32 cryptographically random
bytes encoded as 64 hexadecimal characters** once using a trusted local generator
or password manager. Save the same value on the Worker and in Codex's network
secret. This is an app shared secret, not another Cloudflare API token. Reuse an
existing value where present; the agent does not generate/rotate it automatically.

**Do not upload Codex proxy placeholders with `wrangler secret put/bulk`.** A bot
token allowed only to `discord.com` will not be substituted when uploaded to
`api.cloudflare.com`; the Worker could store an unusable placeholder. Install
actual Worker secret values through the owner's dashboard or another explicitly
supported secure provisioning route. A network placeholder also cannot replace a
public key used for local cryptography or a secret used for local equality checks.

Some current installation/doctor helpers load every Discord credential together,
even for checks that do not use the OAuth client secret. That helper limitation is
not a reason to request an unnecessary secret: direct read-only API checks can
verify the application and channel without it.

## 5. Ordinary environment variables in Codex

These values are not credentials:

| Name | Value/source |
| --- | --- |
| `CLOUDFLARE_ACCOUNT_ID` | The existing Worker's account ID from Cloudflare; do not guess it from the hostname. |
| `DISCORD_APPLICATION_ID` | `1559284610108752068` |
| `DISCORD_PUBLIC_KEY` | This app's public key from Developer Portal → General Information. |
| `STAGING_GUILD_ID` | `706320010368516198` |
| `STAGING_CHANNEL_NAME` | `test-bot` |

Verify the resulting channel ID equals `1559279290577850368` before live actions.
The current doctor resolves the channel by name; an arbitrary new channel-ID
variable would not automatically be enforced by the application.

Worker name, `APP_ENV` and `APP_ORIGIN` already live in `wrangler.jsonc` under
`env.staging`; do not introduce competing workspace overrides.

## 6. Network destinations and applying settings

Keep existing Git/package access presets. The additional HTTPS destinations are:

| Host | Use |
| --- | --- |
| `api.cloudflare.com` | Cloudflare account/Worker API; destination for the Cloudflare token. |
| `discord.com` | Discord API/OAuth; destination for the bot token and optional local OAuth secret. |
| `community-gaming-staging.deepakdhanavel.workers.dev` | Existing app, health and operations; destination for operations/optional connector tokens. |
| `data.aoe2companion.com` | Later real lobby/profile validation; no credential. |

Live log tailing may use a service-returned WebSocket URL. Verify its actual host
and proxy WebSocket support before adding any additional destination; do not
allow the Cloudflare credential on all `*.workers.dev` hosts.

Configure the **three network secrets** and **five ordinary variables** in the
Codex environment attached to this task, then save the editor changes. Runtime
propagation can be asynchronous. Saving an agent-written draft, applying runtime
changes and publishing the reusable environment are separate operations. Review
and save the final draft and publish the environment to retain its configuration
and installed workspace. Verify actual API access after applying settings.

After the standard Cloudflare binding works, remove the obsolete `CloudFlareKey`
workspace alias/requirement from the editor. This does not revoke the existing
Cloudflare token. Avoid competing ordinary-variable and network-secret entries
containing the same credential.

## 7. How access and the demo will be tested

1. Verify the Cloudflare user token and read the intended account/Worker settings.
   Confirm the test Worker identity. Variable presence alone is not proof of access.
2. Verify the Discord application, guild and channel with the bot token. Read
   recent #test-bot messages and confirm ordinary conversation content is available.
3. Check `OPS_TOKEN` on the private inspection/checkpoint endpoint. Take a private
   backup before state-changing tests and reconcile stored match/rating/wallet data.
4. Test the hosted OAuth login with an authorized organizer when testing the web
   dashboard. Do not request a personal Discord token, password or copied session.
5. Run the demo in isolated test-game state in the existing #test-bot channel,
   inspect messages, and record real message links and the final state.

A bot token cannot log in as eight people or forge human slash/button interactions.
Current admin routes require an organizer OAuth session; `OPS_TOKEN` is not a
simulation API. Simulated players need supported organizer actions or a deliberately
scoped test harness. Real Discord interaction acceptance still requires real user
actions. Do not silently revive an expired simulator or bypass authentication.
Reading API payloads also does not produce a pixel screenshot of Discord; visual
review happens in the Discord client.

## Sources and implementation references

- [Cloudflare token creation/editing](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/)
- [Cloudflare permission names](https://developers.cloudflare.com/fundamentals/api/reference/permissions/)
- [Discord permissions](https://docs.discord.com/developers/topics/permissions)
- [Discord privileged intents and HTTP restrictions](https://docs.discord.com/developers/events/gateway#http-restrictions)
- [Discord Message Content Intent](https://docs.discord.com/developers/events/gateway#message-content-intent)
- [Discord channel message history](https://docs.discord.com/developers/resources/message#get-channel-messages)
- Code: `src/env.ts`, `src/discord/client.ts`, `src/worker.ts`, `scripts/env.mjs`,
  `scripts/operational-backup.mjs` and `wrangler.jsonc`.

Permission names and content restrictions were checked against official provider
documentation sources. Codex network-secret behavior follows the onboarding
skill's proxy/placeholder contract. Actual authorization still needs verification
after the owner applies this configuration.
