# -*- coding: utf-8 -*-
"""nextcord assembly for quiz messages — a thin wrapper over bot.quiz.view (pure).
Only imported at runtime (by jobs.py lazily and by interactions.py), never during
the unit tests, so the top-level nextcord import is safe."""
import nextcord

from . import view as _v


def card_embed(category, difficulty, seq, week, day, closes_in_h):
	return nextcord.Embed(
		title="Daily AoE2 quiz",
		description="\n".join(_v.card_lines(category, difficulty, seq, week, day, closes_in_h)),
		colour=nextcord.Colour.blurple())


def card_view(post_id):
	# auto_defer=False is REQUIRED: these buttons carry no per-View callback (we route
	# every click through the global on_interaction handler in bot.events so it works
	# across a Railway redeploy). With nextcord's default auto_defer=True, the View's
	# dispatch would silently ACK (type-6 deferred update) the click after the no-op
	# callback, and our handler's response.send_message would then raise
	# InteractionResponded — i.e. the button would appear to do nothing.
	v = nextcord.ui.View(timeout=None, auto_defer=False)
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


def answer_view(post_id, options, multi):
	# Routed via the global on_interaction handler (redeploy-safe), so auto_defer=False
	# and the components carry DB-resolvable custom_ids. See card_view's note.
	v = nextcord.ui.View(timeout=None, auto_defer=False)
	if multi:
		v.add_item(nextcord.ui.StringSelect(
			custom_id=f"quiz:{post_id}:msel", placeholder="Select ALL that apply, then click away",
			min_values=1, max_values=len(options),
			options=[nextcord.SelectOption(label=f"{chr(65 + i)}. {o[:90]}", value=str(i))
					 for i, o in enumerate(options)]))
	else:
		for i in range(len(options)):
			v.add_item(nextcord.ui.Button(
				style=nextcord.ButtonStyle.secondary, label=chr(65 + i),
				custom_id=f"quiz:{post_id}:ans:{i}"))
	return v


def result_embed(prompt, options, correct_indices, explanation, winners, title="Quiz result"):
	return nextcord.Embed(
		title=title,
		description="\n".join(_v.result_lines(prompt, options, correct_indices, explanation, winners)),
		colour=nextcord.Colour.green())


def leaderboard_embed(tallied, week_label):
	return nextcord.Embed(
		title=f"Weekly quiz leaderboard · {week_label}",
		description="\n".join(_v.leaderboard_lines(tallied)),
		colour=nextcord.Colour.gold())
