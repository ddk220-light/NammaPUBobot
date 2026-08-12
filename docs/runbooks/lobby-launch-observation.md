# Lobby launch confirmation runbook

## What production evidence established

Logs from the 2026-08-08 weekend contained 20 distinct lobby removals: 16 real
games and four cancelled/remade lobbies. Every terminal socket sequence had the
same shape: slot removals followed by `lobbyRemoved`; the removal payload carried
only `matchId`, and no terminal socket field reliably distinguished launch from
cancellation.

The match endpoint does distinguish them. A real game returns a parseable
`started` timestamp at `GET https://data.aoe2companion.com/api/matches/{id}`;
cancelled lobby ids remained 404. In the observed real games, the API start time
preceded socket removal by 3–24 seconds (10.5-second median). One captured false
positive demonstrated the old bug directly: removed lobby `498336082` remained
404, while its replacement `498336470` returned a real start.

Therefore:

- `lobbyRemoved` means only “verify this candidate”;
- `lobbies.status` is workflow state, not launch proof;
- only a parseable API `started` value may populate `lobbies.launched_at`;
- betting closes only for a bot match with a non-null `launched_at` row.

## Runtime flow

1. Automatic or manual linking writes an unconfirmed `filling` row.
2. The recurring lobby job polls linked game ids every two seconds. This starts
   before removal, so confirmation normally trails the real start by seconds.
3. Socket removal changes the local workflow to `verifying`; the automatic
   watcher remains alive so a replacement lobby can still be found.
4. An API response with `started` atomically writes `launched_at` and advances
   the row to `in_progress`. Concurrent watcher/job checks are safe.
5. The betting sweep reads `launched_at` through `started.launched_among` and
   atomically freezes the book. There is no elapsed-time fallback.
6. Completion polling begins no earlier than 15 minutes after `launched_at`.

If Railway redeploys or the match API is temporarily unavailable, the database
row remains unconfirmed and the recurring job resumes checking it. Failure to
confirm leaves betting open; it never guesses from a socket disappearance.

## Logs to monitor after deployment

Socket diagnostics remain enabled and exclude player identity and passwords:

- `LOBBY_SOCKET_TRACE` with `source="auto:match=<bot match id>"`;
- `LOBBY_SOCKET_TRACE` with `source="manual:game=<AoE2 game id>"`;
- `Lobby launch confirmed: game <id> started at <epoch> (observed <n>s later)`;
- `Bets locked for match <id>: ... (game started)`.

For a normal match, confirm that the launch-confirmed line names the correct
game id and precedes (or is followed within one 15-second betting sweep by) the
bets-locked line. For a cancelled/remade lobby, confirm there is no launch line
for the cancelled id and that the replacement id is later confirmed.

## Database checks

These are read-only checks for diagnosing an incident:

```sql
SELECT id, match_id, aoe2_game_id, status, created_at, launched_at, last_edit_at
FROM lobbies
ORDER BY id DESC
LIMIT 30;

SELECT id, match_id, status, opened_at, freezes_at, terminal_intent
FROM prediction_posts
ORDER BY id DESC
LIMIT 30;
```

An open prediction post uses the signed-BIGINT maximum as its `freezes_at`
sentinel. When it closes, the same atomic update replaces that sentinel with the
actual close time; abandoned-book recovery continues to age from that value.
