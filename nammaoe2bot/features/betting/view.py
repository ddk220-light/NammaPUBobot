# -*- coding: utf-8 -*-
"""Pure text builders for prediction messages. No nextcord here — embeds.py
wraps these into Embed objects. Keeping the formatting pure makes it
unit-testable (the nammaoe2bot/features/quiz/view.py pattern)."""
from .scoring import multiplier, pools

# Team 0 / team 1 vote emoji. Blue and red mirror the in-game player colours, and
# both are single codepoints so `str(reaction)` compares cleanly.
TEAM_EMOJIS = ("\U0001F535", "\U0001F534")  # 🔵 🔴

GOLD = "\U0001FA99"  # 🪙

_ENTRY_LABELS = {
	"seed": "Starting gold",
	"match_reward": "Match played",
	"bet": "Bet placed",
	"cancel": "Bet cancelled",
	"refund": "Refund",
	"payout": "Winnings",
	"quiz_correct": "Quiz — correct answer",
	"quiz_played": "Quiz — played",
	"admin_adjust": "Adjustment",
}


def fmt_multiplier(m):
	"""A payout multiplier as a punter reads it: at most two decimals, no
	trailing zeros. ×2, ×1.5, ×1.44, ×3.25.

	`{m:g}` gave six SIGNIFICANT digits, so a perfectly ordinary 450/200 book
	advertised "pays ×1.44444" on every card. Odds are a number people compare
	at a glance while deciding how much to stake, and five decimal places of
	spurious precision is harder to read, not more honest.

	PRESENTATION ONLY. scoring.multiplier keeps returning the exact float and
	nothing else consumes it — scoring.payouts divides the real pools in
	integer arithmetic and never sees this string, so what is displayed can
	never drift into what is paid.

	The `.` that `:.2f` always emits is what makes the rstrip pair safe: it
	walls off the significant digits, so ×10 renders as "10" and not "1".
	"""
	return f"{m:.2f}".rstrip("0").rstrip(".")


def _mult_note(pool_side, pool_other):
	m = multiplier(pool_side, pool_other)
	return f" · pays ×{fmt_multiplier(m)}" if m and m > 1 else ""


def _side_line(emoji, team, pool_side, pool_other):
	return f"{emoji}  **{team}** — pool **{pool_side}** {GOLD}{_mult_note(pool_side, pool_other)}"


def open_lines(team0, team1, match_id, pool0=0, pool1=0):
	return [
		f"**Who takes match #{match_id}?**",
		"",
		_side_line(TEAM_EMOJIS[0], team0, pool0, pool1),
		_side_line(TEAM_EMOJIS[1], team1, pool1, pool0),
		"",
		"Stake your gold with the buttons. Betting closes when the game starts.",
		# THE RULE, as Amendment 1 §A actually left it. This line said
		# "Spectators only — players in this match cannot bet" long after the
		# router stopped enforcing anything of the kind, on every ranked match
		# and re-rendered on every press: for most people the card IS the
		# rulebook, and it was telling half the channel they were barred.
		"_Playing? Back your own team. Watching? Either side. Winners split the whole pot._",
	]


FROZEN_HEADLINE = "Betting closed. The pots are locked:"


def frozen_lines(team0, team1, pool0, pool1, bettors0, bettors1, headline=None):
	"""`headline` lets the caller say WHY the book closed — the game starting is
	the other reason now, and it lands mid-window where an unexplained close
	reads as a bug. Everything below it is identical either way, because the
	two closes lock identical pots by identical rules."""
	total = pool0 + pool1
	if not total:
		return [f"**Betting closed — no bets on {team0} vs {team1}.**"]
	lines = [
		f"**{headline or FROZEN_HEADLINE}**",
		"",
		f"{TEAM_EMOJIS[0]}  **{team0}** — **{pool0}** {GOLD} from {bettors0} bettor(s)"
		f"{_mult_note(pool0, pool1)}",
		f"{TEAM_EMOJIS[1]}  **{team1}** — **{pool1}** {GOLD} from {bettors1} bettor(s)"
		f"{_mult_note(pool1, pool0)}",
		"",
	]
	if pool0 == pool1:
		lines.append(f"Dead even at {total} {GOLD}.")
	else:
		favourite = team0 if pool0 > pool1 else team1
		lines.append(f"The gold says **{favourite}**.")
	return lines


def no_action_lines(team0, team1):
	return [
		f"**Betting closed — one-sided book on {team0} vs {team1}.**",
		"Nobody took the other side, so there are no odds to settle. "
		"All stakes have been refunded.",
	]


def voided_lines(reason):
	return [reason, "All stakes have been refunded."]


def bet_confirm_lines(team_name, stake, total_stake, pool0, pool1, balance_after):
	total_note = f" (your total: {total_stake})" if total_stake != stake else ""
	lines = [f"Bet **{stake}** {GOLD} on **{team_name}**{total_note}."]
	lines.append(f"Pools: {pool0} vs {pool1}. Your balance: **{balance_after}** {GOLD}.")
	return lines


def bet_cancelled_lines(amount, balance_after):
	return [f"Bet cancelled — **{amount}** {GOLD} returned.",
			f"Your balance: **{balance_after}** {GOLD}. You can bet again while the book is open."]


NOTHING_TO_CANCEL_NOTICE = "You have no bet on this match to cancel."


def _named(rows, max_named, fmt):
	lines = [fmt(r) for r in rows[:max_named]]
	if len(rows) > max_named:
		lines.append(f"+{len(rows) - max_named} more")
	return lines


def _backed_note(bet):
	"""Annotation for a participant who staked on their own team. `.get` because
	posts made before the is_player column exists carry no flag at all."""
	return " *(backed themselves)*" if bet.get("is_player") else ""


def report_lines(team0, team1, winner_idx, bets, paid, max_named=25):
	"""The post-match betting report: the pot, every winner's stake -> payout
	(net gain shown), every loser's stake — annotating participants who backed
	their own team. bets come from store.bets_for() (already biggest-stake-first);
	paid from scoring.payouts()."""
	winner_name = team0 if winner_idx == 0 else team1
	lines = [f"**{winner_name}** won it."]
	if not bets:
		lines.append("Nobody bet on this one.")
		return lines
	pool0, pool1 = pools(bets)
	lines.append(f"Pot: **{pool0 + pool1}** {GOLD} — {pool0} on {team0}, {pool1} on {team1}.")
	winners = [b for b in bets if b["side"] == winner_idx]
	losers = [b for b in bets if b["side"] != winner_idx]
	if winners:
		lines.append("")
		lines.extend(_named(winners, max_named, lambda b: (
			f"\U0001F3C6 **{b['nick']}**{_backed_note(b)} staked {b['stake']} → "
			f"**{paid.get(b['user_id'], 0)}** {GOLD} (+{paid.get(b['user_id'], 0) - b['stake']})")))
	if losers:
		lines.append("")
		lines.extend(_named(losers, max_named, lambda b: (
			f"\U0001F4B8 {b['nick']}{_backed_note(b)} staked {b['stake']} on "
			f"{team0 if b['side'] == 0 else team1} — gone")))
	return lines


def leaderboard_lines(rows, page=1, per_page=10):
	"""rows: [{nick, correct, total, balance}] already sorted best-first.

	`balance` is optional and may be None per row: a channel outside a community
	has a valid accuracy board and no gold at all, and a rated player who has
	never been seeded genuinely holds nothing. Both render as accuracy alone
	rather than as a zero -- absent and empty are different claims.
	"""
	if not rows:
		return ["No predictions have been scored yet."]
	start = (page - 1) * per_page
	page_rows = rows[start:start + per_page]
	if not page_rows:
		return [f"No entries on page {page}."]
	lines = []
	for offset, r in enumerate(page_rows):
		place = start + offset + 1
		total = int(r["total"] or 0)
		correct = int(r["correct"] or 0)
		pct = round(100 * correct / total) if total else 0
		line = f"`{place:>2}.` **{r['nick']}** — {correct} pt ({correct}/{total}, {pct}%)"
		if r.get("balance") is not None:
			line += f" · {r['balance']} {GOLD}"
		lines.append(line)
	return lines


# Covers BOTH ways this board comes back empty, because it cannot tell them
# apart and must not guess: nobody holds a balance yet, or nobody has reached
# the channel's rating leaderboard, which is what this board is built from.
# "Nobody holds any gold here yet" would be a flat lie in the second case —
# people hold plenty, they are just not on a board yet.
GOLD_BOARD_EMPTY = ("No gold standings yet — players appear here once they're on the "
					"channel leaderboard.")

# Per column. Two lists of ten fit a phone screen side by side; more turns the
# thing everybody actually wants to see — who is top and who is broke — into
# scrolling.
GOLD_PER_SIDE = 10


def _gold_line(place, row):
	return f"`{place:>2}.` {row['nick']} — **{row['balance']}**"


def gold_board_columns(rows, per_side=GOLD_PER_SIDE):
	"""rows: [{user_id, nick, balance}] already sorted richest-first.
	-> (richest_lines, poorest_lines), each ready to be one embed field.

	THE TWO COLUMNS NEVER SHARE A NAME. With fewer than 2*per_side holders the
	naive bottom slice overlaps the top one, and a board that lists the same
	person as both the richest and the poorest is not a rounding error — it is
	the board contradicting itself. The bottom slice therefore starts at
	whichever is later, per_side or n-per_side, so it can only shrink into
	nothing as the community gets small. At or below per_side holders there is
	no second column at all: one list IS the whole distribution, and splitting
	it would be pure decoration.

	Places are counted from the top in both columns, so the poorest column
	reads `23.` rather than restarting at 1 — the number is the person's
	standing, and a second numbering that means something else in the same
	card is a trap.
	"""
	if not rows:
		return [], []
	richest = [_gold_line(i + 1, r) for i, r in enumerate(rows[:per_side])]
	start = max(per_side, len(rows) - per_side)
	poorest = [_gold_line(start + i + 1, r) for i, r in enumerate(rows[start:])]
	return richest, poorest


def me_lines(display_name, correct, total, balance_amount, entries, seeded_now=False):
	"""The personal card: prediction record, then gold, then recent movements.

	Absorbed /gold. A member who has never predicted still gets their balance --
	the two halves are independent, and answering "no record" to someone who
	only wanted their gold would be a worse answer than showing both.
	"""
	lines = [f"**{display_name}**"]

	record = rank_field(correct, total)
	lines.append(record if record else "No matches predicted yet.")

	if balance_amount is not None:
		lines.append("")
		if seeded_now:
			lines.append(f"Welcome to the betting floor — you start with {balance_amount} {GOLD}.")
		else:
			lines.append(f"Holding **{balance_amount}** {GOLD}.")

	if entries:
		lines.append("")
		for e in entries:
			sign = "+" if e["amount"] >= 0 else ""
			lines.append(f"`{sign}{e['amount']}` {_ENTRY_LABELS.get(e['entry_type'], e['entry_type'])}")
	return lines


def rank_field(correct, total):
	"""One-line summary for /rank. None when the user has never predicted."""
	if not total:
		return None
	return f"{correct} pt · {correct}/{total} correct ({round(100 * correct / total)}%)"
