# -*- coding: utf-8 -*-
"""Pure text builders for prediction messages. No nextcord here — embeds.py
wraps these into Embed objects. Keeping the formatting pure makes it
unit-testable (the bot/quiz/view.py pattern)."""
from .scoring import split_pct

# Team 0 / team 1 vote emoji. Blue and red mirror the in-game player colours, and
# both are single codepoints so `str(reaction)` compares cleanly.
TEAM_EMOJIS = ("\U0001F535", "\U0001F534")  # 🔵 🔴


def open_lines(team0, team1, minutes, match_id):
	return [
		f"**Who takes match #{match_id}?**",
		"",
		f"{TEAM_EMOJIS[0]}  **{team0}**",
		f"{TEAM_EMOJIS[1]}  **{team1}**",
		"",
		f"React to call it. Voting closes in {minutes} minutes.",
		"_Spectators only — players in this match are not counted._",
	]


def frozen_lines(team0, team1, votes0, votes1):
	pct0, pct1 = split_pct(votes0, votes1)
	total = votes0 + votes1
	if not total:
		return [f"**Voting closed — no predictions for {team0} vs {team1}.**"]
	lines = [
		"**Voting closed. The audience says:**",
		"",
		f"{TEAM_EMOJIS[0]}  **{team0}** — {votes0} ({pct0}%)",
		f"{TEAM_EMOJIS[1]}  **{team1}** — {votes1} ({pct1}%)",
		"",
	]
	if votes0 == votes1:
		lines.append(f"Dead split across {total} voter(s).")
	else:
		favourite = team0 if votes0 > votes1 else team1
		lines.append(f"**{favourite}** favoured by {total} voter(s).")
	return lines


def result_lines(winner_name, correct_nicks, total_voters):
	"""Post-match payout. correct_nicks is already ordered and capped by the caller."""
	lines = [f"**{winner_name}** won it."]
	if not total_voters:
		lines.append("Nobody predicted this one.")
		return lines
	if not correct_nicks:
		lines.append(f"All {total_voters} voter(s) called it wrong. Brutal.")
		return lines
	lines.append(f"{len(correct_nicks)}/{total_voters} called it — +1 point each:")
	lines.append(", ".join(correct_nicks))
	return lines


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
