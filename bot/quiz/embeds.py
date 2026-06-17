# -*- coding: utf-8 -*-
"""nextcord assembly for quiz messages — a thin wrapper over bot.quiz.view (pure).
Only imported at runtime (by jobs.py lazily and by interactions.py), never during
the unit tests, so the top-level nextcord import is safe."""
import nextcord

from . import view as _v


def card_embed(category, difficulty, number, closes_in_h):
	return nextcord.Embed(
		title="Daily AoE2 quiz",
		description="\n".join(_v.card_lines(category, difficulty, number, closes_in_h)),
		colour=nextcord.Colour.blurple())


def card_view(post_id):
	v = nextcord.ui.View(timeout=None)
	v.add_item(nextcord.ui.Button(
		style=nextcord.ButtonStyle.primary, label="Reveal & start",
		custom_id=f"quiz:{post_id}:reveal"))
	return v


def question_embed(prompt, options, seconds_left):
	e = nextcord.Embed(
		description="\n".join(_v.question_lines(prompt, options)),
		colour=nextcord.Colour.gold())
	e.set_footer(text=f"{seconds_left // 60}:{seconds_left % 60:02d} left · one answer, no changes")
	return e


def answer_view(post_id, n_options):
	v = nextcord.ui.View(timeout=None)
	for i in range(n_options):
		v.add_item(nextcord.ui.Button(
			style=nextcord.ButtonStyle.secondary, label=chr(ord("A") + i),
			custom_id=f"quiz:{post_id}:ans:{i}"))
	return v


def result_embed(prompt, options, correct_index, explanation, winners):
	return nextcord.Embed(
		title="Quiz result",
		description="\n".join(_v.result_lines(prompt, options, correct_index, explanation, winners)),
		colour=nextcord.Colour.green())


def leaderboard_embed(tallied, week_label):
	return nextcord.Embed(
		title=f"Weekly quiz leaderboard · {week_label}",
		description="\n".join(_v.leaderboard_lines(tallied)),
		colour=nextcord.Colour.gold())
