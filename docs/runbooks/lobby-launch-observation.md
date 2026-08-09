# Lobby launch observation runbook

The betting cutoff is temporarily **timer-only: ten minutes after teams are
formed**. Lobby socket events are being observed but cannot close a betting
book during this period.

## What is logged

Both lobby paths emit one structured `LOBBY_SOCKET_TRACE` line per relevant
socket event:

- `source="auto:match=<bot match id>"` — automatic `NammaNomad` watcher;
- `source="manual:game=<AoE2 game id>"` — `/lobby <gameid>` announcer;
- `event` — `lobbyAdded`, `lobbyUpdated`, `lobbyRemoved`, or a slot event;
- socket `started`, `finished`, and `status` fields when the event carries them;
- on every event, the last-known occupied/open slot counts and whether the
  lobby was full.

The trace deliberately excludes lobby passwords, player names, profile IDs,
and unknown payload fields.

## Match-night checklist

For at least one manually linked match and, if practical, one automatically
detected match:

1. Record the approximate wall-clock time when teams form and betting opens.
2. Create/link the lobby and record the AoE2 game ID.
3. Record when the lobby becomes full.
4. Record when the host presses **Start Game**.
5. Preserve every Railway log line containing `LOBBY_SOCKET_TRACE` for that
   game, plus the existing `game ... launched` line immediately around it.
6. If practical, create a spare lobby and close it without launching. Preserve
   that trace too; distinguishing cancellation from launch is the important
   comparison.

## Evidence needed for the cutover

Before betting is allowed to close from lobby state, the captured traces must
answer:

- Does a real host launch always emit `lobbyRemoved`?
- Does a pre-launch cancellation emit the same event and payload?
- Does any preceding `lobbyUpdated` carry a non-null `started` value or another
  field that distinguishes the two?
- Does the filtered manual socket receive the same sequence as the automatic
  all-lobbies socket?

Once those answers are known, replace the overloaded `lobbies.status` inference
with an explicit durable launch fact, add launch/cancellation fixtures, and only
then remove the ten-minute betting deadline.
