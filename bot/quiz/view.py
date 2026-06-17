# -*- coding: utf-8 -*-
"""Pure text/structure builders for quiz messages. No nextcord here — bot/quiz/
embeds.py wraps these into Embed/View. Keeping the formatting pure makes it
unit-testable (the bot/lobby/view.py pattern)."""
from __future__ import annotations

_LETTERS = ["A", "B", "C", "D"]


def letter_options(options):
	return [f"{_LETTERS[i]}. {opt}" for i, opt in enumerate(options)]


def card_lines(category, difficulty, number, closes_in_h):
	return [
		f"**Daily AoE2 quiz · #{number}**",
		f"Category: {category} · {difficulty}",
		"Tap **Reveal & start** — a private 3:00 timer starts, then lock one answer.",
		f"Closes in ~{int(closes_in_h)}h · weekly leaderboard on the configured day.",
	]


def question_lines(prompt, options):
	return [f"**{prompt}**", ""] + letter_options(options)


def leaderboard_lines(tallied):
	out = []
	for i, e in enumerate(tallied, start=1):
		pct = round(100 * e["correct"] / e["answered"]) if e["answered"] else 0
		out.append(f"`{i}.` {e['nick']} — **{e['correct']}/{e['answered']}** ({pct}%)")
	return out or ["No answers this week."]


def closed_notice():
	return "This quiz has closed — check the channel for the answer."


def already_answered_notice():
	return "You already locked in an answer for this quiz."


def too_late_notice():
	return "Your 3-minute window has passed — no answer recorded."


def result_lines(prompt, options, correct_index, explanation, winners):
	letters = letter_options(options)
	who = ", ".join(winners) if winners else "nobody"
	return [
		f"**{prompt}**",
		f"Correct answer: **{letters[correct_index]}**",
		explanation,
		f"Got it right: {who}",
	]
