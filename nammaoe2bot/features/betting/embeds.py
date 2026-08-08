# -*- coding: utf-8 -*-
"""nextcord wrappers around the pure builders in nammaoe2bot.features.betting.view.

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
	# auto_defer=False is REQUIRED — same rule as nammaoe2bot/features/quiz/embeds.vote_view:
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


def frozen_embed(team0, team1, pool0, pool1, bettors0, bettors1, headline=None):
	return Embed(
		title="\U0001F512 Bets locked",
		description="\n".join(
			view.frozen_lines(team0, team1, pool0, pool1, bettors0, bettors1, headline)),
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


def me_embed(display_name, correct, total, balance_amount, entries, seeded_now=False):
	return Embed(
		title="\U0001F52E Prediction record",
		description="\n".join(
			view.me_lines(display_name, correct, total, balance_amount, entries, seeded_now)),
		colour=Colour(_RESULT))


def leaderboard_embed(rows, page=1, per_page=10):
	return Embed(
		title="\U0001F52E Prediction leaderboard",
		description="\n".join(view.leaderboard_lines(rows, page, per_page)),
		colour=Colour(_RESULT))


def gold_leaderboard_embed(rows):
	"""The gold standings: richest and poorest side by side.

	Two INLINE fields, which is what makes them columns — Discord lays inline
	fields out three to a row, so exactly two of them sit next to each other on
	a desktop and stack on a phone. A single description with both lists in it
	would be one column on every client.
	"""
	richest, poorest = view.gold_board_columns(rows)
	embed = Embed(title=f"{view.GOLD} Gold standings", colour=Colour(_RESULT))
	if not richest:
		embed.description = view.GOLD_BOARD_EMPTY
		return embed
	embed.add_field(name="\U0001F451 Richest", value="\n".join(richest), inline=True)
	if poorest:
		embed.add_field(name="\U0001FAB4 Poorest", value="\n".join(poorest), inline=True)
	embed.set_footer(text=f"{len(rows)} holders · /predictions leaderboard sort:accuracy "
						  f"for the prediction record")
	return embed
