# Interaction feedback review — 0.10.3

Successful actions use their public queue, match, agreement, invitation or civilization card as the receipt. They do not also leave a private success message. The policy is centralized in `src/discord/interaction-replies.ts`; adding a new domain action requires choosing its feedback surface.

| Action | Successful feedback |
| --- | --- |
| `/add`, `/remove`, Join/Leave, queue selection | Updated public waiting roster; duplicate requests stay quiet |
| `/queue`, organizer add/remove/start/clear/split | Public queue or newly created match cards |
| Check-in, map vote/pick, map redo | Public readiness, vote progress or chosen maps |
| Substitute request/cancellation, substitution, team placement, confirmed back-out | Updated public match; outstanding substitute requests now appear by name. A substitute pulled from the queue is mentioned in a short public notice (“<@X>, you’re in for <@Y> on Alpha.”) |
| Captain loss/draw/abort, win claim, opposing captain confirm/agree/decline/dispute | Public result or request card; a declined, expired or auto-confirmed request card is edited to show its outcome |
| Any recorded result, cancellation, withdrawal or correction | The match card edit plus one short, ping-free channel note (not for test matches) |
| Organizer cancel/report/undo/recover/manual record | Updated or recovered public match card |
| `/civpick`, including redo | Public civilization picker |
| `/lobby attach`, `/lobby unlink` | Public lobby status on the match card |
| Close betting | Public pool status and controls |
| Organizer rally | Public invitation |

These private replies remain because they serve a purpose beyond acknowledging a public update:

| Private interaction | Why it remains |
| --- | --- |
| Errors, permissions, stale/invalid buttons, unavailable actions | No successful public result communicates what went wrong |
| Choosing a queue, side/stake, or confirming back-out or Sub me | The user still needs to supply input or confirm a disruptive action; completed owned private panels are removed |
| Report result | Captains choose We lost / We won / Draw / Cancel match; organizers get the /admin result controls with their confirmation; anyone else learns who the captains are |
| Refused early start | Explains that only administrators, managers and the queue’s bot moderators may start before the queue is full |
| Civilization choice receipt | The chosen civilization is hidden on the public card until the reveal |
| Personal bet/cancel panel | Own stake, balance and cancellation controls are not the public aggregate pool; the existing owned panel updates in place |
| `/help`, `/teams`, `/lobby status`, rank/profile/leaderboard/gold queries | These answer an explicit information request and do not publish a second channel update |
| Profile linking/unlinking, rating seed/hide/unhide/sync, access restrictions/forgiveness | The mapping, access change or administrative outcome is not recorded on the main channel card |
| Setup status/web link | Configuration diagnostics and organizer navigation have no matching public update |

Queue expiry rules now appear on the public queue card when configured. Substitute requests appear on the public match card and disappear when cancelled. This keeps those details available when their private success replies are removed.

## Discord transport

Successful public button/select actions use Discord's silent component acknowledgement. Only an owned **private** confirmation/selection panel is eligible for deletion through its interaction webhook; public cards stay under the durable outbox's control, including the existing last-five-message queue rule.

Slash commands require an initial response. They acknowledge with a temporary private “thinking” state before potentially slow work, then delete that response on success. Errors replace it with an explanation. Hidden-choice and query responses remain private. Discord may briefly display the thinking indicator; there is no persistent success receipt in the normal path. Cleanup runs independently of the durable public-card delivery, so a slow card request cannot leave the private response waiting on the outbox.

Webhook cleanup retries at most three times, handles acknowledgement races and respects Retry-After. It never repeats the underlying queue/match/betting action. Tokens remain in memory and are never placed in the durable outbox or logs. Exhausted webhook failures log only the operation, so cleanup may remain incomplete during a Discord outage. Public-card delivery retains its existing durable retry and organizer recovery behavior.

Signed-request integration tests cover queue changes, check-in, organizer actions, substitutions, results, lobby operations, civilization privacy and consumed private panels. Transport tests cover slow work, private errors, lost/rate-limited cleanup, finite retries and credential-safe logging. These use mocked Discord boundaries; client rendering still requires human interaction.

The queue **Ping** button acknowledges silently (type 6) while its public invitation is prepared. Errors and cooldowns use a new ephemeral followup; the public queue card is never edited or deleted through the interaction webhook.

## Administrator control panel

`/admin` publishes a shared status/control panel in the channel. Anyone can open it and start or refresh a queue. Captains can report their own team’s loss; administrators and configured bot moderators can clear queues and override results. Actions and public confirmations recheck the actual clicker’s authority. Unauthorized/stale clicks receive private errors and cannot overwrite or delete the public panel. No extra private success receipt is sent. Old ephemeral panels remain private until replaced by a fresh `/admin`. Detailed administrator commands remain under `/manage` with their existing feedback choices. See [the admin panel guide](admin-panel.md).

## Reporting from the match card

The playing match card has **Report result** and **Sub me**, and the teams list names both captains and says that either captain presses Report result after the game. Report result opens a private panel chosen by the clicker’s current authority:

- **Captain:** **We lost** records the loss immediately (the same handler as `/report loss`). **We won** posts a public request that mentions only the opposing captain, with Confirm and Dispute; it confirms automatically after 10 minutes (a `proposal` deadline in the existing deadline table and alarm). Dispute cancels the claim, edits the request to say an organizer can record the result from `/admin` or the website, and — when the queue has bot-moderator roles — posts one message mentioning those roles. **Draw** and **Cancel match** use the two-captain agreement, which now has Decline and also expires after 10 minutes.
- **Organizer or bot moderator:** the `/admin` result buttons, owned by the clicker (`adm:<user>:…`), so the existing confirmation, permission recheck and stale-version checks apply unchanged.
- **Anyone else:** a private note naming the two captains.

`/report` keeps working as before. Request buttons carry the match version and the request ID (`a:<match>:<version>:<request>:win|draw|abort|decline`); a second click on an ended request returns a private “already ended”. Accepted, replaced and superseded requests are retired as before.

**Sub me** during play asks for a private confirmation naming the waiting substitute (`m:<match>:<version>:confirm_sub`). Matches formed from `/test populate` places (and matches marked by `/test checkin`) never mention members: no match-found mentions, no reminder mentions, no request or substitute pings. Early starts below capacity follow the queue’s **Who can start a match before the queue is full** setting (`earlyStart`, default organizers and moderators).
