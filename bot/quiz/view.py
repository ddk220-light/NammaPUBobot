# -*- coding: utf-8 -*-
"""Pure text/structure builders for quiz messages. No nextcord here — bot/quiz/
embeds.py wraps these into Embed/View. Keeping the formatting pure makes it
unit-testable (the bot/lobby/view.py pattern)."""
from __future__ import annotations

import json

_LETTERS = ["A", "B", "C", "D"]

_SOURCE_TAG = {"game": "\U0001F3AE Game knowledge", "player": "\U0001F464 Player trivia"}


# card_lines (the reveal-era teaser) and question_lines (the ephemeral question
# that followed it) are deleted — poll_card_lines below renders the question on
# the public card instead, so there is no second, private copy of it to build.
# letter_options went with question_lines, its only caller: tally_lines is the
# one place a lettered option list is built now, and it builds a richer line
# (mark, count, voter names) that never shared the plain "A. Knight" form.


def leaderboard_lines(tallied):
	out = []
	for i, e in enumerate(tallied, start=1):
		pct = round(100 * e["correct"] / e["answered"]) if e["answered"] else 0
		out.append(f"`{i}.` {e['nick']} — **{e['correct']}/{e['answered']}** ({pct}%)")
	return out or ["No answers this week."]


def closed_notice():
	return "This quiz has closed — check the channel for the answer."


# already_answered_notice / too_late_notice went with the reveal era: a vote can
# now be changed until the poll locks, so there is no "you already answered" to
# refuse and no private 3-minute window to run out of. closed_notice survives —
# the clock gate in bot/quiz/interactions.py still needs it.


def result_lines(prompt, options, correct_indices, explanation, winners, gold_note=None):
	correct = ", ".join(_LETTERS[i] for i in sorted(correct_indices))
	who = ", ".join(winners) if winners else "nobody"
	lines = [
		f"**{prompt}**",
		f"Correct answer{'s' if len(correct_indices) > 1 else ''}: **{correct}**",
		explanation,
		f"Got it right: {who}",
	]
	if gold_note:
		lines.append(gold_note)
	return lines


_NAME_CAP = 12


def _option_voters(options, votes):
	"""Voter nicks per option index, in vote order. A multi-answer voter
	appears under EACH option they picked; a row with no choice at all (an
	old-era Reveal ghost) appears nowhere."""
	per = [[] for _ in options]
	for v in votes:
		nick = v.get("nick") or str(v.get("user_id"))
		raw_multi = v.get("choice_indices")
		if raw_multi:
			for i in json.loads(raw_multi):
				if 0 <= int(i) < len(per):
					per[int(i)].append(nick)
		elif v.get("choice_index") is not None:
			i = int(v["choice_index"])
			if 0 <= i < len(per):
				per[i].append(nick)
	return per


def tally_lines(options, votes, correct_indices=None):
	"""One line per option: letter, text, count, capped names — the live
	scoreboard while the poll is open, and (with correct_indices) the final
	card's marked version."""
	per = _option_voters(options, votes)
	out = []
	for i, opt in enumerate(options):
		names = per[i]
		mark = "✅ " if (correct_indices is not None and i in correct_indices) else ""
		who = ""
		if names:
			shown = ", ".join(names[:_NAME_CAP])
			extra = f" +{len(names) - _NAME_CAP} more" if len(names) > _NAME_CAP else ""
			who = f" — {shown}{extra}"
		out.append(f"{mark}{_LETTERS[i]}. {opt} · **{len(names)}** vote(s){who}")
	return out


def poll_card_lines(category, difficulty, seq, week, day, closes_in_h, prompt, options, votes, source=None):
	"""The open-poll card: header, the question, the live tally, the rules.
	Never receives correct answers — the open card cannot leak what it does
	not know."""
	lines = [f"**Daily AoE2 quiz · Week {week} · Day {day} · #{seq}**"]
	tag = _SOURCE_TAG.get(source)
	meta = f"Category: {category}" + (f" · {difficulty}" if difficulty else "")
	lines.append(f"{tag} · {meta}" if tag else meta)
	lines += ["", f"**{prompt}**", ""]
	lines += tally_lines(options, votes)
	lines += [
		"",
		"Vote with the buttons — you can change your vote until it locks.",
		f"Locks in ~{int(closes_in_h)}h · correct pays 50 \U0001FA99, playing pays 10 \U0001FA99.",
	]
	return lines
