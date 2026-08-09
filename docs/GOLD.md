# Gold — betting and the daily quiz

Gold is a play-money currency. It buys nothing, converts to nothing, and exists
so that watching a match and answering the quiz have some stakes. This page is
the whole rulebook.

## Getting gold

| Source | Pays | Notes |
|---|---|---|
| Starting grant | **500**, once | Everyone the bot has ever rated already has it |
| Playing a ranked match | up to **100** | Both teams, win or lose |
| Quiz — correct answer | up to **50** | |
| Quiz — voted but wrong | up to **10** | |
| Winning a bet | your share of the pot | See below |

### The 500 ceiling — read this before reporting a bug

Playing and the quiz **only ever top you back up toward 500. They never lift you
above it.** At exactly 500 they pay nothing at all — a match gives you 0, a
correct quiz answer gives you 0, and the results card shows no gold line.

That is deliberate: these are lifelines, not income. The only way to get past
500 is to win a bet. If everyone in the channel is sitting at 500 and nobody has
bet yet, the first quiz will pay literally nothing to anyone. Nothing is broken.

The top-up is partial where it needs to be: at 460 gold, a match pays 40, not
100.

Winnings are *not* capped. A bet can take you to any balance.

## Betting on matches

When a ranked match's teams are settled, the bot posts a betting card with six
buttons — 10 / 50 / 100 on each team. Betting stays open for **10 minutes**,
then the book freezes and the card locks.

The lobby tracker is currently recording the live socket events around real
game launches, but those events do **not** close betting yet. Until launch and
lobby-cancellation signals have been verified separately, the ten-minute timer
above is the only automatic cutoff.

**How the odds work (pari-mutuel).** There is no bookmaker and no fixed price.
Every stake goes into one pot. When the match is reported, the whole pot is split
among everyone who backed the winning team, in proportion to what they staked.
The card shows each side's pool and its current multiplier live, and those
numbers move as people bet. The multiplier you see when you press is *not* the
one you get — only the final pools matter.

So backing the unpopular team pays more, and if everyone piles onto one side the
payout there approaches nothing.

**Rules:**

- **Your first press locks your side.** After that you can add more to the same
  side, but the other side is refused.
- **If you are playing in the match, you may only back your own team.** A press
  on the opponent is refused. This is what makes it impossible to profit by
  losing. Spectators may back either side.
- **You can cancel** while the book is still open — a *Cancel my bet* button sits
  on the private confirmation you get after each press. Cancelling returns your
  **entire** stake for that match and unlocks your side, so you may bet again,
  either way. There is no partial cancel, and no cancelling after the freeze.
- **If nobody backs the other side, the bet is off.** A one-sided book has no
  odds to settle, so it is declared no-action and every stake is returned.
- **If the roster changes under a live book** (a substitution), the book is
  voided, everyone is refunded, and it reopens. Otherwise a spectator could be
  substituted onto the team they bet against.
- **If the match is cancelled or never reported,** every stake is refunded.

Payouts round down, and the rounding remainder is destroyed rather than carried
over.

When the match is reported, the bot posts a betting report: who backed whom, who
won, what each was paid, and which players backed themselves.

## The daily quiz

One AoE2 question a day, posted publicly. Everyone sees the question and the
options immediately, and the card shows a **live tally with voter names** — the
point is that people can see the split and argue about it.

- Vote by pressing an option button. **You can change your vote** any time before
  it locks; your latest press is the one that counts.
- The poll runs for **24 hours**, closing right as the next day's question posts.
- When it locks, every vote is graded and paid: **50 for correct, 10 for having
  voted** (subject to the 500 ceiling above — correct pays 50 *total*, not 50+10).
- The results post names who got it right, gives the explanation, and reports the
  gold paid.
- Questions alternate between AoE2 game knowledge and trivia about the
  community's own players.
- Multi-answer questions use a dropdown and are graded all-or-nothing.

Not voting pays nothing. Voting wrong still pays 10 — showing up is worth
something.

## Checking your balance

- `/predictions me` — your prediction record, your balance, and your last few
  movements labelled by kind (Starting gold, Match played, Bet placed, Bet
  cancelled, Refund, Winnings, Quiz). Pass a `player` to look up someone else's
  record and balance; the movement list is only ever your own.
- `/predictions leaderboard` — richest and poorest gold standings side by side.
  Use `/predictions leaderboard sort:accuracy` for the paged accuracy record,
  blending the old free-vote era with graded bets.

There were once four commands here — `/gold` and `/gold_top` alongside these
two — which was a 2×2 of {yours, everyone's} × {gold, accuracy} over a single
activity. The two that survived absorbed the other two.

Because players may now back themselves, the accuracy board mixes reading
matches with winning them.

Players hidden from the rating leaderboard are hidden from this one too, as are
players below the channel's minimum-games and recent-activity thresholds — one
definition of who appears on a board, applied everywhere.

## Fine print

Gold lives per community, not per channel. Every movement is written to an
append-only ledger, and a balance is always exactly the sum of its ledger rows —
if those two ever disagree, that is a bug worth reporting.
