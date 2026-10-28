# Interaction feedback review — 0.10.3

Successful actions use their public queue, match, agreement, invitation or civilization card as the receipt. They do not also leave a private success message. The policy is centralized in `src/discord/interaction-replies.ts`; adding a new domain action requires choosing its feedback surface.

| Action | Successful feedback |
| --- | --- |
| `/add`, `/remove`, Join/Leave, queue selection | Updated public waiting roster; duplicate requests stay quiet |
| `/queue`, organizer add/remove/start/clear/split | Public queue or newly created match cards |
| Check-in, map vote/pick, map redo | Public readiness, vote progress or chosen maps |
| Substitute request/cancellation, substitution, team placement, confirmed back-out | Updated public match; outstanding substitute requests now appear by name |
| Captain loss/draw/abort, opposing captain agreement | Public result or agreement card |
| Organizer cancel/report/undo/recover/manual record | Updated or recovered public match card |
| `/civpick`, including redo | Public civilization picker |
| `/lobby attach`, `/lobby unlink` | Public lobby status on the match card |
| Close betting | Public pool status and controls |
| Organizer rally | Public invitation |

These private replies remain because they serve a purpose beyond acknowledging a public update:

| Private interaction | Why it remains |
| --- | --- |
| Errors, permissions, stale/invalid buttons, unavailable actions | No successful public result communicates what went wrong |
| Choosing a queue, side/stake, or confirming back-out | The user still needs to supply input or confirm a disruptive action; completed owned private panels are removed |
| Civilization choice receipt | The chosen civilization is hidden on the public card until the reveal |
| Personal bet/cancel panel | Own stake, balance and cancellation controls are not the public aggregate pool; the existing owned panel updates in place |
| `/teams`, `/maps show`, `/lobby status`, rank/profile/leaderboard/gold queries | These answer an explicit information request and do not publish a second channel update |
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

`/admin` is an explicit private status/control view. It keeps and edits the caller's private panel, including result/destructive-action confirmation screens. It adds no separate success receipt alongside public queue/match updates. Permission failures clear its controls. Detailed administrator commands are now under `/manage` and retain the public/private feedback choices above. See [the admin panel guide](admin-panel.md).
