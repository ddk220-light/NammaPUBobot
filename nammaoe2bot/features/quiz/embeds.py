# -*- coding: utf-8 -*-
"""nextcord assembly for quiz messages — a thin wrapper over nammaoe2bot.features.quiz.view (pure).
Imported at runtime (by jobs.py lazily and by interactions.py) against real
nextcord, and directly by tests/test_quiz_interactions.py against the
conftest nextcord STUB — so the top-level nextcord import must stay safe
under both."""
import json
import time

import nextcord

from . import view as _v


# The reveal era's four builders — card_embed / card_view (the teaser card and
# its "Reveal & start" button) and question_embed / answer_view (the ephemeral,
# privately-timed question that followed it) — are deleted. The poll card below
# IS the question, publicly, and vote_view is the only control it ships with.


def result_embed(prompt, options, correct_indices, explanation, winners, title="Quiz result", gold_note=None):
	return nextcord.Embed(
		title=title,
		description="\n".join(_v.result_lines(prompt, options, correct_indices, explanation, winners,
				gold_note=gold_note)),
		colour=nextcord.Colour.green())


def leaderboard_embed(tallied, week_label):
	return nextcord.Embed(
		title=f"Weekly quiz leaderboard · {week_label}",
		description="\n".join(_v.leaderboard_lines(tallied)),
		colour=nextcord.Colour.gold())


def poll_embed(post, votes):
	"""The living card, rebuilt from the post row on every render — which is
	why the row carries everything the card shows (incl. difficulty)."""
	options = json.loads(post["options_json"])
	closes_in_h = max(0, (int(post["closes_at"]) - int(time.time())) / 3600)
	return nextcord.Embed(
		title="Daily AoE2 quiz",
		description="\n".join(_v.poll_card_lines(
			post["category"], post.get("difficulty"), post["seq"], post["week"],
			post["day"], closes_in_h, post["prompt"], options, votes,
			source=post.get("source"))),
		colour=nextcord.Colour.blurple())


def vote_view(post_id, options, multi):
	# auto_defer=False is REQUIRED: these components carry no per-View callback
	# (every press routes through the global on_interaction handler in
	# nammaoe2bot.discord.events, so the buttons keep working across a Railway redeploy). With
	# nextcord's default auto_defer=True, the View's dispatch would silently ACK
	# (type-6 deferred update) the press after the no-op callback, and our
	# handler's response.edit_message/send_message would then raise
	# InteractionResponded — i.e. the button would appear to do nothing.
	v = nextcord.ui.View(timeout=None, auto_defer=False)
	if multi:
		v.add_item(nextcord.ui.StringSelect(
			custom_id=f"quiz:{post_id}:msel", placeholder="Select ALL that apply",
			min_values=1, max_values=len(options),
			options=[nextcord.SelectOption(label=f"{chr(65 + i)}. {o[:90]}", value=str(i))
					 for i, o in enumerate(options)]))
	else:
		for i in range(len(options)):
			v.add_item(nextcord.ui.Button(
				style=nextcord.ButtonStyle.secondary, label=chr(65 + i),
				custom_id=f"quiz:{post_id}:ans:{i}"))
	return v


def final_card_embed(post, votes):
	"""The card after lock: prompt + tally with the correct options marked.
	Sent with view=None — the components are stripped by the same edit."""
	options = json.loads(post["options_json"])
	correct = set(json.loads(post["correct_indices"]))
	lines = [f"**{post['prompt']}**", ""] + _v.tally_lines(options, votes, correct_indices=correct)
	return nextcord.Embed(
		title="Daily AoE2 quiz — locked",
		description="\n".join(lines),
		colour=nextcord.Colour.purple())
