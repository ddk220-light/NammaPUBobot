# Live queue testing — 0.18.0

There are only two `/test` commands. Both require Administrator, Manage Server, or a Bot moderator role configured for the selected queue. Everyone else continues using the regular commands and buttons.

| Command | Behavior |
|---|---|
| `/test populate [queue] [count]` | Adds eight random real human members to the live queue. `count` accepts 2–16. An explicit queue name, alias or ID selects another queue in this channel; otherwise the normal default is used. Existing waiting players stay queued. |
| `/test checkin [match] [queue]` | Tags the same live match ID as **TEST** and checks in every roster member. With one active match in the selected queue, the ID is optional. With several, specify `match` explicitly. |

## Normal workflow

1. Use `/test populate`, optionally choosing a queue. The normal queue card updates and keeps its Join/Leave buttons. Existing auto-start settings apply. Otherwise use `/queue start` or Start queue on `/admin`.
2. Use `/test checkin` before reporting the result. The existing match card gains a TEST label, its full match ID, and an explanation that results and ratings are not recorded. A match that has already passed check-in can also be tagged while it is still active. Completed, cancelled and expired matches cannot be retroactively turned into tests.
3. Continue with ordinary map voting, civilization picking, lobby attachment, substitutions, captain reports or admin controls. `/test checkin` checks readiness only; it does not cast map votes. When map voting counts as check-in, everyone becomes ready but the normal map round must still finish. Vote normally, wait for its deadline, or disable maps before starting in the website.
4. Finish or cancel through the regular controls. There is no `/test start`, `/test maps`, `/test result`, `/test status` or `/test reset`. Use `/admin` for status and the existing queue-clear/match-cancel controls when needed.

**Population alone is normal live queue activity.** It uses current profiles and real ratings for team balancing, obeys queue eligibility and auto-start, and uses ordinary match notifications. The match becomes unrated only after `/test checkin` succeeds. No additional private success confirmation is left when the public card already shows the change.

Sampling excludes bots, anyone already waiting in a community queue, active players, pending returnees, restricted users, and members without the required queue role or with its blocked role. It samples up to 1,000 Discord members when available; without member-list access it verifies known players and recent channel authors individually, up to 32 candidates. Too few eligible members or a concurrent eligibility change rejects the batch without partial additions. No new token or privileged intent is needed.

## Result exclusion

The TEST marker is stored with the match ID in the normal community database, including who marked it and when. It survives restarts and roster changes. It cannot be removed by normal result correction or undo. Tagging removes that match from the player-history index and any observed civilization records. Subsequent lobby observations for a test do not infer profile links or record civilization statistics; normal lobby and picker controls remain available.

Reporting a test releases players and completes the regular match card, but the match’s stored `result` remains null. No rating calculation, rating changes, win/loss/draw counts, streaks, civilization results, match rewards or result-audit entry is written. Test matches are excluded from website match history, player history, the admin panel’s last-five results, historical banter and recent-map avoidance. The admin panel can still show a tagged match as the latest/active match so its ordinary controls remain usable. Minimal lifecycle state and the TEST marker remain stored for deduplication and recovery; this is not a recorded competitive result.

If predictions were placed before tagging, stakes are refunded once and betting activity for those books is excluded from participation statistics. Test matches cannot open/reopen betting or pay out. Refund ledger entries are retained so balances reconcile. A match whose predictions were already settled cannot be relabelled as a test. Unrelated wallets and records are unchanged.

There is no separate test queue, automatic one-hour reset or removal of regular controls. Live matches retain their normal configured lifetime and cleanup behavior. The older isolated test namespaces are retired on their next wake using their existing receipt-aware cleanup; their data never moves into the live queue.

## Verification and deployment

Tests cover live queue population and automatic/manual start, queue/role/channel selection, bulk readiness with ordinary map controls, normal captain/admin results across all rating engines, refund idempotency, substitutions, multiple active matches, duplicate commands, object restarts, result/history exclusion, unchanged normal ranked results, signed Discord transport, and retirement of old isolated sessions. Deploy the Worker, then run `npm run register:staging` to replace the staging guild command definition with the two subcommands. The legacy Railway app is unchanged.
