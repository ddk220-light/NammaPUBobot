# The Viking Sagas civ-pick trial

## Behavior

The official [Viking Sagas FAQ](https://www.ageofempires.com/news/faq-the-viking-sagas/)
confirms three new civilizations: Danes, Saxons and Varangians. Vikings are an
existing, reworked civilization and remain in the regular catalog.

From deployment through October 22, 2026 in Asia/Kolkata, `/civpick` offers the
usual 12 civilizations and unlimited Random, followed by a separate row of the
three new DLC civilizations. These three never take a slot in the normal 12
during the trial, and appear regardless of recent picks. Each can be claimed by
only one player per round, using the existing atomic first-successful-claim rule.
Players choose a civilization they own; there is no account ownership lookup.

The fixed window is September 23 00:00 through October 23 00:00 Asia/Kolkata
(exclusive end). Restarting does not extend it. New rounds and redos after the
cutoff have only the normal 12 plus Random; the three new civs then join the
regular catalog with the existing rolling 24-hour explicit-pick history rules.
A round opened before the cutoff can finish within its original 1–10 minute
deadline. Existing cards retain their original offers and final assignments.

## Implementation and risk review

- Keep Random at persisted choice ID 12. Store the extra choices in the existing
  round JSON as `bonus_options`, using IDs 13–15. Old snapshots without this field
  work unchanged. Use one shared choice-name mapping for validation, history and
  rendering so a DLC index cannot be read from the 12-item normal pool.
- Exclude the trial civs before selecting the normal pool. Snapshot the bonus
  offers and cutoff when a round starts. The date check uses the existing clock
  read inside the start transaction, with no new queries or scheduling.
- Preserve the row lock, one successful choice per player, stale-generation
  protection, unlimited Random and hidden picks. Taken buttons remain visually
  enabled while open; collisions are rejected privately. DLC choices use a
  separate fourth row, within Discord's component limits.
- Record accepted DLC picks in the existing history transaction. The trial
  bypasses that history only for bonus offers; after the cutoff the same history
  applies normally. No schema migration, new dependency, service, background job,
  extra database query or external ownership request is needed.
- If the trial needs to end early, move its cutoff earlier while retaining the
  new snapshot reader. Reverting to a build that cannot interpret bonus IDs would
  break saved final cards containing DLC picks.

## Validation

Cover both time boundaries, 12 distinct normal choices plus the exact three
bonuses, normal cooldown after expiry, simultaneous claims, repeat availability
across matches, redo at expiry, serialized snapshots spanning the cutoff, legacy
Random IDs, timeouts, and private choices with the separate fourth button row.
Run the picker tests, repository lint and full test suite. Validate the component
payload with the pinned nextcord runtime before production deployment.

Local results: 37 picker tests and 2,187 repository tests pass; the 17 MySQL
integration cases require CI's disposable database. Ruff and whitespace checks
pass. An isolated nextcord 2.6.0 check confirms 16 buttons in rows of 5/5/3/3,
the original 13-button layout outside the window, unchanged Random ID, valid
embed size, persistent render-only views, and disabled final assignments.
