# Available Commands

**44 commands. 14 of them are yours** — the rest are admin, and Discord hides
those from you unless you have Manage Messages.

If you only ever learn four: `/add`, `/lobby`, `/report`, `/rank`.

## Playing

| command | description |
|---|---|
| `/add` | Join the queue |
| `/remove` | Leave the queue |
| `/teams` | Show the teams for your current match |
| `/report` | Report your match result |

`++` and `--` are shorthand for `/add` and `/remove` — just type them as a
message. When the queue fills, react ☑ on the check-in card within the
check-in window or you'll be replaced by the next player waiting.

### Substitutions

| command | description |
|---|---|
| `/subme` | Ask for a substitute (toggles off if you change your mind) |
| `/subfor` | Take someone's place who asked for one |
| `/subauto` | Replace yourself with the next player in the queue and rebalance the teams |

A substitution under a live betting book voids it and refunds every stake, then
re-opens betting on the new teams — see [docs/GOLD.md](docs/GOLD.md).

## Linking your games

| command | description |
|---|---|
| `/profile_link` | Link your Discord account to your AoE2 profile — run it with no argument and it explains where to find your id |
| `/lobby` | Post a live lobby card for a game id, and link that game to your ranked match so the result posts itself |

**These two are what make everything else work.** Without `/profile_link` your
`/rank` says "Statistics pending linking" and you appear on no board. `/lobby`
is how 79% of games get connected to their replay.

## Stats

| command | description |
|---|---|
| `/rank [player] [detailed]` | Rating profile, recent form, eAPM and your scouting report. `detailed:true` adds streak, peak, civs, duos & rivals and recent rating changes |
| `/leaderboard [page]` | Rating leaderboard |

The web dashboard carries more than these do — leaderboards, match stats,
player pages, civ stats and play-style breakdowns.

## Gold, betting and the quiz

| command | description |
|---|---|
| `/predictions me [player]` | Your prediction record, gold balance and recent gold movements |
| `/predictions leaderboard [page]` | Prediction accuracy standings, with gold held |
| `/quiz_leaderboard` | This week's quiz standings |

Gold is play money. Everyone starts with 500. Ranked matches and the daily quiz
top you back up toward 500 but never above it — **only winning a bet takes you
past it**, which is why a full balance shows no reward. Betting cards post
themselves when a match's teams are settled; the quiz posts itself daily and you
vote with the buttons on the card. Full rules: [docs/GOLD.md](docs/GOLD.md).

---

# Admin

Hidden from players who lack Manage Messages. Individual commands still check
their own permission when run.

### `/channel`

| command | description |
|---|---|
| `/channel enable` | Enable the bot on this channel |
| `/channel disable` | Disable the bot on this channel |
| `/channel delete` | Delete stats/configs and disable the bot here |
| `/channel show` | List channel configuration |
| `/channel set` | Configure a channel variable |

### `/queue`

| command | description |
|---|---|
| `/queue create_pickup` | Create a new pickup queue |
| `/queue list` | List the channel's queues |
| `/queue show` | Show a queue's configuration |
| `/queue set` | Configure a queue variable |
| `/queue delete` | Delete a queue |
| `/queue add_player` | Add a player to a queue |
| `/queue remove_player` | Remove a player from queues |
| `/queue clear` | Remove all players from a queue |
| `/queue start` | Start a queue manually |
| `/queue split` | Split a queue into N separate matches |

`show` and `set` for both groups are also on the web dashboard, with forms
generated from the config definitions.

### `/match`

| command | description |
|---|---|
| `/match report` | Report an ongoing match's result as a moderator |
| `/match create` | Record a rating match manually |
| `/match sub_player` | Force-swap one player for another |
| `/match put` | Force a player into a team or the unpicked list |
| `/match undo` | Undo a finished match, reverting its rating changes |

### `/rating`

| command | description |
|---|---|
| `/rating seed` | Set a player's rating and deviation |
| `/rating hide_player` | Hide a player from the leaderboards |
| `/rating unhide_player` | Unhide a player |

Hiding covers every public board — rating, gold and predictions alike.

### `/noadds`

| command | description |
|---|---|
| `/noadds list` | List banned players |
| `/noadds add` | Ban a player from the queues |
| `/noadds remove` | Lift a ban |

### `/profile_identity`

| command | description |
|---|---|
| `/profile_identity link` | Link a member to an AoE2 profile id |
| `/profile_identity unlink` | Remove a member's link |
| `/profile_identity conflicts` | List open profile/Discord disagreements awaiting resolution |

The moderator counterpart to `/profile_link`.

### `/quiz`

| command | description |
|---|---|
| `/quiz disable` | Stop the daily quiz |

**One-way.** Nothing in the bot re-enables it — quiz settings are moving to the
web dashboard, and until that ships, turning it back on means a database change.
Treat this as an emergency stop.
