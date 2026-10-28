# Administrator panel — 0.14.0

Run **`/admin`** in a queue's Discord channel. An optional `queue` argument accepts a queue name, alias or ID. The response is private to the caller. It shows the waiting count and queue status, automatic-start and check-in settings, the latest match with its full ID, an ongoing match's teams, and the last five completed match attempts with their outcomes. Cancelled, failed-check-in and undone matches are explicitly labelled; they are not presented as wins.

History is scoped to that queue and channel. An older ongoing match remains selectable even when the newest match has ended. Queue and ongoing-match menus appear when needed; menus show at most 25 choices. The queue argument can select additional queues. Refresh panel reloads current state; the panel does not poll or add an always-running connection.

## Controls

| Control | Behavior |
|---|---|
| Start queue | Starts a full waiting roster with the saved check-in and map rules. Disabled when paused or below capacity. |
| Refresh queue card | Republishes the public card using its existing last-five-message policy. |
| Clear queue | Confirms removal of waiting players; active matches are unaffected. |
| Alpha lost / Beta lost | Confirms the losing team, shows both rosters and the exact match ID, then records the opposite team's win. Available after check-in. |
| Draw | Confirms and records a draw after check-in. |
| Cancel match | Confirms cancellation without ratings; existing prediction refunds apply. |
| Refresh match card | Recovers the selected ongoing match's public card. |
| Close betting | Confirms closure when the match uses predictions; the existing market-state rules remain authoritative. |
| Refresh panel / Back | Reloads the private panel without changing a queue or match. |

Actions use the same transactional matchmaking, rating and wallet handlers as existing commands. Results remain a human decision. Confirmation does not mutate state. Match versions and a separate queue-view revision reject old controls, including queue changes coalesced into one pending Discord publication. The existing `queue_runtime.roster_revision` column stores that revision; no new SQL table or column is needed. Repeated result confirmations cannot settle twice. A settings change or role removal is rechecked on subsequent clicks and before a queued action is applied.

The bot updates the existing private panel after an action; it does not send another private success receipt. Public queue/match updates use the normal durable outbox. Buttons require an owned, ephemeral message from this bot and bind to the caller. A copied control cannot operate another channel, community, queue outside the caller's access, or another person's panel. Recovery copies cannot use these controls.

## Permissions and custom roles

Discord has **Administrator** and **Manage Server** permissions, but no role name shared by every server. Either permission allows access by default; server owners also have this authority. A role named “Admin” with neither permission does not automatically qualify.

To delegate queue administration without granting server-wide permissions:

1. Create or reuse a Discord role with any name, such as `Game Organizers`, and assign it to the intended people. It does not need Administrator or Manage Server permission.
2. Copy its role ID with Discord Developer Mode. In the settings website, open the queue's **Queue controls and access**, add it to **Additional queue admin role IDs**, and save. Multiple IDs are comma-separated; `@everyone` is rejected.
3. In Discord **Server Settings → Integrations → this bot → /admin**, allow that role to use the command in the intended channels. Command visibility defaults to Manage Server, so this step is necessary for a role without that permission. Discord Administrator permission overrides command visibility restrictions.

Both gates matter: Discord allowing the command alone does not grant bot authority. The handler checks the signed interaction's member permissions and role IDs against current queue settings on every request. Role names are never used as authorization. Custom roles can use only the panel controls for their configured queues; they do not gain website settings, imports, player-rating repairs or `/manage` access. Only existing server administrators/managers can edit those settings.

## Existing detailed commands

Discord cannot register a root command that both opens a panel and owns subcommands. **`/admin` now opens the panel; the previous Setup, Queue, Match and Player command groups move to `/manage`.** Those detailed commands keep their Manage Server/Administrator requirement and existing options, including `/manage match undo`, `/manage player seed` and `/manage queue start skip_check_in:true`. Older already-issued `/admin` subcommand interactions remain accepted with their existing permission checks during client refresh.

Deploy the Worker, then register the guild commands with `npm run register:staging`. That script verifies the staging application and replaces only its configured guild command namespace. It does not change the Railway bot, create roles, grant permissions or register global commands. Administrators configure any custom-role command overrides themselves in Discord; no new token scope is needed.

## Verification

Signed-request tests cover opening the panel, private message ownership, both default permissions, queue-scoped custom roles, member/configuration revocation, revocation during asynchronous eligibility checks, cross-channel/server rejection, confirmation, stale controls, result settlement and ledger reconciliation, queue clearing, cancellation, multi-match selection, scoped history, Discord payload limits and read-only recovery. Command-schema tests cover both `/admin` and `/manage`. Live deployment verification checks the registered definitions and deployed website without starting, clearing, cancelling or reporting a real match. Actual private client rendering is checked by an authorized human running `/admin`.
