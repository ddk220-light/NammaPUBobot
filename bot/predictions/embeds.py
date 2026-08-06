# -*- coding: utf-8 -*-
"""nextcord wrappers around the pure builders in bot.predictions.view.

Imported lazily by jobs.py so the package stays importable under the unit-test
stubs, which have no nextcord."""
from nextcord import Embed, Colour, ui, ButtonStyle

from . import view
from .scoring import STAKES
from .view import TEAM_EMOJIS

_OPEN = 0x5865F2     # blurple — betting live
_FROZEN = 0x9B59B6   # purple — locked in
_RESULT = 0xF1C40F   # gold — payout, matches the results card


def bet_view(post_id):
	# auto_defer=False is REQUIRED — same rule as bot/quiz/embeds.vote_view:
	# these buttons carry no per-View callback (clicks route through the global
	# on_interaction handler so they work across a Railway redeploy), and
	# nextcord's default auto_defer would silently ack the click first.
	v = ui.View(timeout=None, auto_defer=False)
	for side, style in ((0, ButtonStyle.primary), (1, ButtonStyle.danger)):
		for stake in STAKES:
			v.add_item(ui.Button(
				style=style, row=side, label=str(stake), emoji=TEAM_EMOJIS[side],
				custom_id=f"bet:{post_id}:{side}:{stake}"))
	return v


def cancel_view(post_id):
	# Same rules as bet_view: routed by the global on_interaction handler, so
	# timeout=None and auto_defer=False.
	v = ui.View(timeout=None, auto_defer=False)
	v.add_item(ui.Button(
		style=ButtonStyle.secondary, label="Cancel my bet",
		custom_id=f"betcancel:{post_id}"))
	return v


def open_embed(team0, team1, minutes, match_id, pool0=0, pool1=0):
	return Embed(
		title="\U0001F52E Match betting",
		description="\n".join(view.open_lines(team0, team1, minutes, match_id, pool0, pool1)),
		colour=Colour(_OPEN))


def frozen_embed(team0, team1, pool0, pool1, bettors0, bettors1):
	return Embed(
		title="\U0001F512 Bets locked",
		description="\n".join(view.frozen_lines(team0, team1, pool0, pool1, bettors0, bettors1)),
		colour=Colour(_FROZEN))


def no_action_embed(team0, team1):
	return Embed(
		title="\U0001F512 Bets refunded",
		description="\n".join(view.no_action_lines(team0, team1)),
		colour=Colour(0x95A5A6))


def voided_embed(reason):
	return Embed(
		title="\U0001F52E Match betting",
		description="\n".join(view.voided_lines(reason)),
		colour=Colour(0x95A5A6))


def report_embed(team0, team1, winner_idx, bets, paid):
	return Embed(
		title="\U0001F3C6 Betting report",
		description="\n".join(view.report_lines(team0, team1, winner_idx, bets, paid)),
		colour=Colour(_RESULT))


def gold_embed(balance_amount, entries, seeded_now=False):
	desc = view.gold_lines(balance_amount, entries)
	if seeded_now:
		desc.insert(0, f"Welcome to the betting floor — you start with {balance_amount} {view.GOLD}.")
	return Embed(title=f"{view.GOLD} Your gold", description="\n".join(desc), colour=Colour(_RESULT))


def gold_top_embed(rows, page=1):
	return Embed(
		title=f"{view.GOLD} Gold leaderboard",
		description="\n".join(view.gold_top_lines(rows, page=page)),
		colour=Colour(_RESULT))


def leaderboard_embed(rows, page=1, per_page=10):
	return Embed(
		title="\U0001F52E Prediction leaderboard",
		description="\n".join(view.leaderboard_lines(rows, page, per_page)),
		colour=Colour(_RESULT))
