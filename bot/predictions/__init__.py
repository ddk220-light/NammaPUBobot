# -*- coding: utf-8 -*-
"""Audience match predictions — strictly additive, ranked queues only.

Spectators call a winner by reacting to a card posted when teams are formed.
Voting freezes on a timer, the tally is revealed, and once the match reports a
win/loss every correct caller banks a point.

Self-contained in the bot/quiz and bot/lobby mould: dedicated qc_prediction_*
tables declared here via ensure_table at import, imported by bot/__init__.py for
that side effect and the PredictionJobs singleton.

Votes are never held in memory. bot.waiting_reactions is a 30-minute TTL dict
that a redeploy wipes, and matches routinely run longer than that, so the freeze
sweep re-reads reactions off the message — Discord holds them server-side, which
makes the whole flow restart-safe for free.

jobs.py keeps its nextcord / core.client imports lazy so importing this package
stays safe under the unit-test conftest stubs (ensure_table is a no-op there).
"""
from core.database import db

db.ensure_table(dict(
	tname="prediction_posts",
	columns=[
		dict(cname="id", ctype=db.types.int, autoincrement=True),
		dict(cname="channel_id", ctype=db.types.int),
		dict(cname="match_id", ctype=db.types.int),
		dict(cname="message_id", ctype=db.types.int, notnull=False),
		dict(cname="team0_name", ctype=db.types.str),
		dict(cname="team1_name", ctype=db.types.str),
		dict(cname="opened_at", ctype=db.types.int),
		dict(cname="freezes_at", ctype=db.types.int),
		# open -> frozen -> resolved, or void at any point (match cancelled, roster
		# changed under the vote, or the match never reported a win/loss).
		dict(cname="status", ctype=db.types.str),
		dict(cname="votes0", ctype=db.types.int, notnull=False),
		dict(cname="votes1", ctype=db.types.int, notnull=False),
		dict(cname="winner_idx", ctype=db.types.int, notnull=False),
		dict(cname="resolved_at", ctype=db.types.int, notnull=False),
	],
	primary_keys=["id"],
))

db.ensure_table(dict(
	tname="prediction_votes",
	columns=[
		dict(cname="post_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="nick", ctype=db.types.str),
		dict(cname="team_idx", ctype=db.types.int),
		dict(cname="voted_at", ctype=db.types.int),
		dict(cname="is_correct", ctype=db.types.bool, notnull=False),
	],
	primary_keys=["post_id", "user_id"],
))

from .jobs import jobs  # noqa: E402,F401  (PredictionJobs singleton — bot.predictions.jobs.think)
