# -*- coding: utf-8 -*-
"""The composition root: the ONE module that imports both the domain and the
features built on it, and the one place their connection is written down.

Everything below used to be a function-local `from bot.predictions import ...`
or `from bot.lobby import watcher` inside bot/match/. Eleven of them, scattered
across five methods, each with its own hand-rolled try/except — which meant the
answer to "what happens when a match finishes?" could only be assembled by
reading the domain and grepping for imports. It is this file.

The dependency now runs one way: `pickup` knows nothing about `features`, and
nothing under bot/match/ may import a feature again. Whether that stays true is
checked by tests/test_match_lifecycle.py.

READ THE ORDER NOTES BEFORE REORDERING ANYTHING. Handlers run in registration
order, and two of these sequences encode a real constraint rather than a
preference.
"""
from core.console import log

from bot import predictions
from bot.lobby import watcher as lobby_watcher
from bot import storyline_payoff
from bot import team_insights


# ── lobby: the opt-in live-game watcher ──────────────────────────────────
async def _start_lobby_watcher(match, ctx):
	if match.ranked:
		lobby_watcher.start_for(match, ctx.channel)


async def _stop_lobby_watcher(match, _ctx):
	"""A linked+launched watcher has already stopped itself; this is the
	teardown for every other way a match can end."""
	await lobby_watcher.stop_for(match.id)


# ── storylines: the pre-game tease and its post-game payoff ──────────────
async def _post_team_insights(match, ctx):
	try:
		embed = await team_insights.build_insights_embed(match)
		if embed is not None:
			await ctx.notice(embed=embed)
	except Exception as e:
		# The stash on the match means "a tease was computed". If the send
		# failed nobody saw it, and a payoff answering an invisible tease is
		# worse than silence — so drop the context. This has to be caught here
		# rather than left to the dispatcher: only the storyline feature knows
		# that its own failure invalidates state it stored earlier.
		match.storyline_ctx = None
		log.error(f"Storyline insights failed for match {match.id}: {e}")


async def _post_storyline_payoff(match, ctx):
	"""Purely win/loss, so it posts the instant the match reports — no replay
	needed."""
	if match.ranked:
		embed = await storyline_payoff.build_payoff_embed(match)
		if embed is not None:
			await ctx.notice(embed=embed)


# ── betting: the audience book ───────────────────────────────────────────
# Each of these looks up the function on the package at call time rather than
# binding it at import. That is what lets a test swap one out, and it costs
# nothing: they are all module-level names on bot/predictions/__init__.py.
async def _open_book(match, _ctx):
	"""Unranked matches never report a winner to score against, and a queue can
	turn the feature off outright."""
	if match.ranked and match.cfg['predictions_enabled']:
		await predictions.open_for_match(match)


async def _restart_book(match, _ctx):
	if match.ranked:
		await predictions.restart_for_match(match)


async def _settle_book(match, _ctx):
	"""A match that ends without a clean win/loss (a draw) is voided inside
	resolve_for_match rather than settled."""
	if match.ranked:
		await predictions.resolve_for_match(match)


async def _void_book(match, _ctx):
	await predictions.void_for_match(match.id)


def wire_match_lifecycle(app):
	"""Subscribe every feature to the match lifecycle. Called once, at boot."""
	events = app.match_events

	events.on("teams_posted", _post_team_insights)

	events.on("live", _start_lobby_watcher)
	events.on("live", _open_book)

	events.on("roster_changed", _restart_book)

	events.on("ending", _stop_lobby_watcher)

	# ORDER IS LOAD-BEARING HERE. The payoff is a chat message; settlement moves
	# gold. Settling last means a payoff that fails cannot take the payout with
	# it — the dispatcher isolates handlers from each other, but a reader
	# reordering these should know which way the risk points.
	events.on("finished", _post_storyline_payoff)
	events.on("finished", _settle_book)

	# The watcher first, so it stops holding a match that is about to have its
	# stakes handed back; then the refund.
	events.on("cancelled", _stop_lobby_watcher)
	events.on("cancelled", _void_book)
