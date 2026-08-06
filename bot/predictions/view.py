# -*- coding: utf-8 -*-
"""Pure text builders for prediction messages. No nextcord here — embeds.py
wraps these into Embed objects. Keeping the formatting pure makes it
unit-testable (the bot/quiz/view.py pattern)."""
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
	"admin_adjust": "Adjustment",
}


def _mult_note(pool_side, pool_other):
	m = multiplier(pool_side, pool_other)
	return f" · pays ×{m:g}" if m and m > 1 else ""


def _side_line(emoji, team, pool_side, pool_other):
	return f"{emoji}  **{team}** — pool **{pool_side}** {GOLD}{_mult_note(pool_side, pool_other)}"


def open_lines(team0, team1, minutes, match_id, pool0=0, pool1=0):
	return [
		f"**Who takes match #{match_id}?**",
		"",
		_side_line(TEAM_EMOJIS[0], team0, pool0, pool1),
		_side_line(TEAM_EMOJIS[1], team1, pool1, pool0),
		"",
		f"Stake your gold with the buttons. Betting closes in {minutes} minutes.",
		"_Spectators only — players in this match cannot bet. Winners split the whole pot._",
	]


def frozen_lines(team0, team1, pool0, pool1, bettors0, bettors1):
	total = pool0 + pool1
	if not total:
		return [f"**Betting closed — no bets on {team0} vs {team1}.**"]
	lines = [
		"**Betting closed. The pots are locked:**",
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


def gold_lines(balance_amount, entries):
	lines = [f"You hold **{balance_amount}** {GOLD}."]
	if entries:
		lines.append("")
		for e in entries:
			sign = "+" if e["amount"] >= 0 else ""
			lines.append(f"`{sign}{e['amount']}` {_ENTRY_LABELS.get(e['entry_type'], e['entry_type'])}")
	return lines


def gold_top_lines(rows, page=1, per_page=10):
	"""rows: [{nick, balance}] already sorted richest-first."""
	if not rows:
		return ["Nobody holds any gold yet."]
	start = (page - 1) * per_page
	page_rows = rows[start:start + per_page]
	if not page_rows:
		return [f"No entries on page {page}."]
	return [f"`{start + n + 1:>2}.` **{r['nick']}** — {r['balance']} {GOLD}"
			for n, r in enumerate(page_rows)]


def leaderboard_lines(rows, page=1, per_page=10):
	"""rows: [{nick, correct, total}] already sorted best-first."""
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
		lines.append(f"`{place:>2}.` **{r['nick']}** — {correct} pt ({correct}/{total}, {pct}%)")
	return lines


def rank_field(correct, total):
	"""One-line summary for /rank. None when the user has never predicted."""
	if not total:
		return None
	return f"{correct} pt · {correct}/{total} correct ({round(100 * correct / total)}%)"
