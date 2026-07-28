# -*- coding: utf-8 -*-
"""nextcord wrappers around the pure builders in bot.predictions.view.

Imported lazily by jobs.py so the package stays importable under the unit-test
stubs, which have no nextcord."""
from nextcord import Embed, Colour

from . import view

_OPEN = 0x5865F2     # blurple — voting live
_FROZEN = 0x9B59B6   # purple — locked in
_RESULT = 0xF1C40F   # gold — payout, matches the results card


def open_embed(team0, team1, minutes, match_id):
	return Embed(
		title="\U0001F52E Audience prediction",
		description="\n".join(view.open_lines(team0, team1, minutes, match_id)),
		colour=Colour(_OPEN))


def frozen_embed(team0, team1, votes0, votes1):
	return Embed(
		title="\U0001F512 Predictions locked",
		description="\n".join(view.frozen_lines(team0, team1, votes0, votes1)),
		colour=Colour(_FROZEN))


def result_embed(winner_name, correct_nicks, total_voters):
	return Embed(
		title="\U0001F3AF Prediction results",
		description="\n".join(view.result_lines(winner_name, correct_nicks, total_voters)),
		colour=Colour(_RESULT))


def leaderboard_embed(rows, page=1, per_page=10):
	return Embed(
		title="\U0001F52E Prediction leaderboard",
		description="\n".join(view.leaderboard_lines(rows, page, per_page)),
		colour=Colour(_RESULT))
