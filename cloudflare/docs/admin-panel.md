# Administrator panel — 0.15.0

Run **`/admin`** in a queue's Discord channel. An optional `queue` argument accepts a queue name, alias or ID. The response is public in that channel, so everyone with channel access can see it. Any server member can open it, refresh it and start a queue. Captains can report their own team’s loss. Clearing the queue, reporting other results, cancellation and closing betting require server administrator/manager or configured bot moderator access. It shows the waiting count and queue status, automatic-start and check-in settings, the latest match with its full ID, an ongoing match's teams, and the last five completed match attempts with their outcomes. Cancelled, failed-check-in and undone matches are explicitly labelled; they are not presented as wins.

History is scoped to that queue and channel. An older ongoing match remains selectable even when the newest match has ended. Queue and ongoing-match menus appear when needed; menus show at most 25 choices. The queue argument can select additional queues. Refresh panel reloads current state; the panel does not poll or add an always-running connection.

## Controls

| Control | Behavior |
|---|---|
| Start queue | Anyone can start with at least two eligible waiting players, up to queue capacity, using the saved check-in and map rules. Six players start 3v3; seven start 3v4 with everyone included. /queue start is an equivalent public command. Disabled when paused or fewer than two are waiting. Only the privileged /manage command can skip check-in. |
| Refresh queue card | Republishes the public card using its existing last-five-message policy. |
| Clear queue | Administrator/moderator only: confirms removal of waiting players; active matches are unaffected. |
| Alpha lost / Beta lost | The team’s captain or an administrator/moderator confirms the losing team, shows both rosters and the exact match ID, then records the opposite team's win. Available after check-in. |
| Draw | Administrator/moderator only: confirms and records a draw after check-in. Captains can still use /report draw for mutual agreement. |
| Cancel match | Administrator/moderator only: confirms cancellation without ratings; existing prediction refunds apply. |
| Refresh match card | Recovers the selected ongoing match's public card. |
| Close betting | Administrator/moderator only: confirms closure when the match uses predictions; the existing market-state rules remain authoritative. |
| Refresh panel / Back | Reloads the public panel without changing a queue or match. |

Actions use the same transactional matchmaking, rating and wallet handlers as existing commands. Results remain a human decision. Confirmation does not mutate state. Match versions and a separate queue-view revision reject old controls, including queue changes coalesced into one pending Discord publication. The existing `queue_runtime.roster_revision` column stores that revision; no new SQL table or column is needed. Repeated result confirmations cannot settle twice. A settings change or role removal is rechecked on subsequent clicks and before a queued action is applied.

The bot updates the existing public panel after an authorized action; it does not send another private success receipt. Any member can start or refresh; privileged actions check the current clicker, and the actual clicker is recorded in the audit. Destructive confirmations are public too and recheck the confirmer’s permissions and current state. Unauthorized or stale button presses return a private error without editing or deleting the public panel. Queue/match updates use the normal durable outbox. Controls require a message from this bot and cannot operate another channel, community or queue outside the clicker’s access. Recovery copies cannot use these controls. Old ephemeral panels retain their original caller restriction; run /admin again to create a public panel.

## Permissions and custom roles

Discord has **Administrator** and **Manage Server** permissions, but no role name shared by every server. Either permission grants moderation authority by default; server owners also have this authority. A role named “Admin” with neither permission does not automatically qualify.

To delegate queue administration without granting server-wide permissions:

1. Create or reuse a Discord role with any name, such as `Game Organizers`, and assign it to the intended people. It does not need Administrator or Manage Server permission.
2. In the settings website, open **Queues → your queue → Queue controls and access → Bot moderator roles**, tick the role by name, and save the queue. Multiple roles are supported. Bot-managed roles and `@everyone` are excluded. Role IDs are displayed to distinguish duplicate names and stored internally, so renaming a role does not revoke access. A deleted/unavailable saved role stays visible until you deselect it; saving rejects missing, foreign-server or managed roles.

`/admin` and `/queue start` are registered for everyone. If your server previously added a command-specific restriction in Discord Integrations, remove that override to let members use the command. The bot does not change server-owned overrides.

Discord allowing the command does not grant authority to clear queues or override results. The handler checks the signed interaction's member permissions and role IDs against current queue settings on every request. Role names are never used as authorization. Custom roles gain privileged panel controls only for their configured queues; they do not gain website settings, imports, player-rating repairs or `/manage` access. Only existing server administrators/managers can edit those settings. The setup response reuses its existing Discord role lookup for the picker; there is no polling, new connection or permission scope. Existing `queue.adminRoles` values remain compatible and preselected.

## Existing detailed commands

Discord cannot register a root command that both opens a panel and owns subcommands. **`/admin` now opens the panel; the previous Setup, Queue, Match and Player command groups move to `/manage`.** Those detailed commands keep their Manage Server/Administrator requirement and existing options, including `/manage match undo`, `/manage player seed` and `/manage queue start skip_check_in:true`. Older already-issued `/admin` subcommand interactions remain accepted with their existing permission checks during client refresh.

Deploy the Worker, then register the guild commands with `npm run register:staging`. That script verifies the staging application and replaces only its configured guild command namespace. It does not change the Railway bot, create roles, grant permissions or register global commands. Administrators configure any custom-role command overrides themselves in Discord; no new token scope is needed.

## Verification

Signed-request tests cover opening the panel, public visibility, other administrators using shared buttons, private rejection of unauthorized clicks without changing the public panel, legacy private-panel ownership, public command/button starts below capacity including 3v3 and 3v4, captain-only loss reporting and finite ratings for all engines with odd rosters, both default permissions, queue-scoped custom roles, member/configuration revocation, fresh roster eligibility checks on public starts, cross-channel/server rejection, confirmation, stale controls, result settlement and ledger reconciliation, queue clearing, cancellation, multi-match selection, scoped history, Discord payload limits and read-only recovery. Command-schema tests cover both `/admin` and `/manage`. Live deployment verification checks the registered definitions and deployed website without starting, clearing, cancelling or reporting a real match. Actual Discord client rendering is checked by an authorized human running `/admin`.
