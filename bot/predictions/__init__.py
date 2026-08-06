# -*- coding: utf-8 -*-
"""Audience match predictions — strictly additive, ranked queues only.

Spectators call a winner by betting gold on a card posted when teams are
formed, via buttons rather than reactions. Betting freezes on a timer, the
tally is revealed, and once the match reports a win/loss the winning pool
pays out pari-mutuel to every correct bettor.

Self-contained in the bot/quiz and bot/lobby mould: dedicated prediction_*
tables declared here via ensure_table at import, imported by bot/__init__.py for
that side effect and the PredictionJobs singleton (see the export at the bottom
for why the module holding it is called flow.py rather than jobs.py).

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

# ── gold betting (stage: gold-betting spec, 2026-08-05) ─────────────────
# gold_ledger is APPEND-ONLY: no UPDATE, no DELETE, ever. idem_key makes
# every non-bet movement impossible to apply twice at the schema level
# (unique index; MySQL unique ignores NULLs, and bet rows are NULL because
# a user may press the buttons many times). Balance truth is
# SUM(amount) per (community_id, user_id); gold_balances is the spendable
# cache written in the same transaction — see bot/predictions/gold.py.
db.ensure_table(dict(
	tname="gold_ledger",
	columns=[
		dict(cname="id", ctype=db.types.int, autoincrement=True),
		dict(cname="community_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		# seed | match_reward | bet | refund | payout | admin_adjust
		dict(cname="entry_type", ctype=db.types.str),
		dict(cname="amount", ctype=db.types.int),          # signed; negative only for 'bet'
		dict(cname="match_id", ctype=db.types.int, notnull=False),
		dict(cname="post_id", ctype=db.types.int, notnull=False),
		dict(cname="created_at", ctype=db.types.int),
		dict(cname="idem_key", ctype=db.types.str, notnull=False, unique=True),
	],
	primary_keys=["id"],
	indexes=[("ix_gold_ledger_holder", ["community_id", "user_id"])],
))

db.ensure_table(dict(
	tname="gold_balances",
	columns=[
		dict(cname="community_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="balance", ctype=db.types.int),
		dict(cname="updated_at", ctype=db.types.int),
	],
	primary_keys=["community_id", "user_id"],
))

# One row per bettor per post — the composite PK IS the side lock: a second
# side for the same (post, user) has nowhere to live. `stake` accumulates
# across presses; `nick` is display-only, captured at press time for the
# betting report and the leaderboard (the prediction_votes pattern).
db.ensure_table(dict(
	tname="prediction_bets",
	columns=[
		dict(cname="post_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="nick", ctype=db.types.str),
		dict(cname="side", ctype=db.types.int),
		dict(cname="stake", ctype=db.types.int),
		dict(cname="updated_at", ctype=db.types.int),
	],
	primary_keys=["post_id", "user_id"],
))

# The singleton bot/events.py drives as `bot.predictions.jobs.think(frame_time)`,
# the same shape bot/quiz, bot/lobby and bot/replay_stats all use.
#
# IT COMES FROM flow.py, AND THAT MODULE IS NOT CALLED jobs.py FOR A REASON.
# It used to be, and binding the singleton onto the package under its own
# submodule's name shadowed the module: `from bot.predictions import jobs`
# handed back the INSTANCE, and so did `import bot.predictions.jobs as m`
# (since 3.7 that binds the package attribute, not sys.modules). Every call site
# in bot/match/ did the former and then reached for a module function on it —
#
#     AttributeError: 'PredictionJobs' object has no attribute 'open_for_match'
#
# — caught by the best-effort guard each site wraps itself in, logged, and never
# seen again. The feature was dead from the day the shadowing landed:
# prediction_posts held ZERO rows across 3312 ranked matches, and a second bug
# behind it (the freeze sweep calling `self._freeze` on a module function) could
# not even be reached to be noticed.
#
# A distinct module name removes the ambiguity outright rather than asking every
# future caller to remember it. tests/test_predictions_wiring.py pins both
# halves: that these names resolve to flow.py's functions, and that no caller
# goes back to the shape that failed.
from .flow import (  # noqa: E402,F401
	jobs, open_for_match, restart_for_match, resolve_for_match, void_for_match,
)
