# Available Commands

#### Queues
| command               | description                                             |
|-----------------------|---------------------------------------------------------|
| /add                  | Join queues                                             |
| /remove               | Leave queues                                            |
| /who                  | List queue players                                      |
| /promote              | Promote a queue                                         |
| /server               | Show queue server setting                               |
| /maps                 | Show queue map list                                     |
| /map                  | Show random map from a queue map list                   |
| /auto_ready           | Confirm check-in automatically on next queue start      |
| /expire               | Show or set your current expire timer                   |

#### Matches
| command               | description                                             |
|-----------------------|---------------------------------------------------------|
| /ready                | Confirm check-in                                        |
| /notready             | Decline check-in                                        |
| /capfor               | Become a captain                                        |
| /pick                 | Pick a player                                           |
| /subme                | Request a substitute                                    |
| /subauto              | Replace yourself with the next queued player, rebalance |
| /subfor               | Become a substitute                                     |
| /teams                | Show current teams                                      |
| /report               | Report match result                                     |

#### Personal settings
| command               | description                                             |
|-----------------------|---------------------------------------------------------|
| /expire_default       | Configure your default auto-remove behaviour            |
| /subscribe            | Subscribe to queue or channel promotion roles           |
| /unsubscribe          | Unsubscribe from queue or channel promotion roles       |
| /nick                 | Change your nick with rating prefix included            |

#### Stats
| command               | description                                             |
|-----------------------|---------------------------------------------------------|
| /leaderboard          | Show rating leaderboard                                 |
| /rank                 | Show your or another player's rating stats              |
| /lastgame             | Show last played match                                  |
| /top                  | Show top active players on the channel                  |
| /stats show           | Show overall channel stats                              |

#### Gold and match betting
Gold is play money. Everyone starts with 500. When a ranked match's teams are
settled the bot posts a betting card with six buttons — 10/50/100 on each team —
open for 10 minutes. All stakes go into one pot and the winning side splits the
whole thing in proportion to what each backed, so the odds come from the pools,
not from a bookmaker. Players in the match may bet **only on their own team**;
spectators may take either side. You can cancel your whole bet until the freeze
via the button on your private confirmation. A one-sided book pays no one and
refunds everyone.

Playing a ranked match pays up to 100 gold, but the match and quiz faucets only
top a balance back up toward 500 and never above it — at 500 they pay nothing.
Only winning a bet takes you past 500. Full rules: [docs/GOLD.md](docs/GOLD.md).

| command               | description                                             |
|-----------------------|---------------------------------------------------------|
| /gold                 | Your balance and recent movements (only you see it)     |
| /gold_top             | The richest bettors in the community                    |
| /predictions leaderboard | Prediction accuracy standings                        |
| /predictions me       | Your (or another player's) prediction record            |

#### Daily AoE2 quiz (opt-in)
One question a day, posted as a public poll that stays open for `open_window`
(24h by default). Vote with the buttons on the card — you can change your vote
until it locks, and the card shows a live tally with voter names. When it locks,
every vote is graded and paid: 50 gold for a correct answer, 10 for playing,
never lifting a balance above 500.

| command               | description                                             |
|-----------------------|---------------------------------------------------------|
| /quiz_leaderboard     | Show this week's quiz leaderboard                       |
| /quiz enable          | (admin) Enable the daily quiz in a channel at a UTC hour|
| /quiz disable         | (admin) Disable the daily quiz                          |
| /quiz config          | (admin) Set a quiz setting (quiz_hour, open_window, leaderboard_dow, leaderboard_hour, test_interval, min_difficulty) |
| /quiz post_now        | (admin) Post a quiz immediately                         |
| /quiz status          | (admin) Show the quiz schedule status and next question |
| /quiz skip            | (admin) Skip the next scheduled question                |
| /quiz reveal_now      | (admin) Lock and reveal the currently open question now |

#### Miscellaneous
| command               | description                                             |
|-----------------------|---------------------------------------------------------|
| /cointoss             | Flip a coin                                             |
| /help                 | Show channel or queue help                              |

#### Administration and Moderation
| command               | description                                             |
|-----------------------|---------------------------------------------------------|
| /channel enable       | Enable the bot this channel                             |
| /channel disable      | Disable the bot this channel                            |
| /channel show         | Show channel configuration                              |
| /channel set          | Configure a channel variable                            |
|-                      |                                                         |
| /queue create_pickup  | Create a new pickup queue                               |
| /queue list           | List all queues on the channel                          |
| /queue show           | Show queue configuration                                |
| /queue set            | Configure a queue variable                              |
| /queue delete         | Delete a queue                                          |
| /queue add_player     | Add a player to queue                                   |
| /queue remove_player  | Remove a player from all or selected queue              |
| /queue clear          | Remove all players from all or selected queue           |
| /queue start          | Manually start a queue                                  |
| /queue split          | Split queue into multiple matches                       |
|-                      |                                                         |
| /match report         | Report an ongoing match result as a moderator           |
| /match create         | Manually create a rating match result                   |
| /match sub_player     | Forcefully swap one player with another                 |
| /match put            | Forcefully put a player in a team or unpicked list      |
|-                      |                                                         |
| /noadds list          | List banned players                                     |
| /noadds add           | Add a player to the ban list                            |
| /noadds remove        | Remove a player from the ban list                       |
|-                      |                                                         |
| /phrases add          | Add a player phrase on an add command                   |
| /phrases clear        | Clear player's phrases list                             |
|-                      |                                                         |
| /rating seed          | Set player rating and deviation                         |
| /rating penality      | Substract points from a player rating                   |
| /rating hide          | Hide a player from the leaderboard                      |
| /rating unhide        | Unhide a player from the leaderboard                    |
| /rating reset         | Reset channels rating data                              |
| /rating snap          | Snap players ratings to their rank minimum value        |
|-                      |                                                         |
| /stats show           | Show channel statistics                                 |
| /stats reset          | Reset all channel data except configs                   |
| /stats reset_player   | Reset a player statistics (including rating)            |
| /stats replace_player | Replace player1 with player2 in the database            |
