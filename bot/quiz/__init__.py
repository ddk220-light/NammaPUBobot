# -*- coding: utf-8 -*-
"""AoE2 unit-quiz subsystem — strictly additive, opt-in.

Self-contained: the bot reads only data/quiz_questions.json + these qc_quiz_* tables
at runtime and NEVER touches the aoe2_matchup SQLite. Disabled by default (no
qc_quiz_config row with enabled=1 -> the think() job does nothing). Mirrors the
bot/lobby/ isolation: dedicated tables declared here via ensure_table at import,
imported by bot/__init__.py for that side effect and the QuizJobs singleton.

jobs.py keeps nextcord/core.client/embeds imports lazy, so `from .jobs import jobs`
below stays safe under the unit-test conftest stubs (ensure_table is a no-op there)."""
from core.database import db

db.ensure_table(dict(
	tname="qc_quiz_posts",
	columns=[
		dict(cname="id", ctype=db.types.int, autoincrement=True),
		dict(cname="channel_id", ctype=db.types.int),
		dict(cname="message_id", ctype=db.types.int, notnull=False),
		dict(cname="question_id", ctype=db.types.str),
		dict(cname="category", ctype=db.types.str),
		dict(cname="prompt", ctype=db.types.text),
		dict(cname="options_json", ctype=db.types.text),
		dict(cname="correct_index", ctype=db.types.int),
		dict(cname="explanation", ctype=db.types.text),
		dict(cname="opened_at", ctype=db.types.int),
		dict(cname="closes_at", ctype=db.types.int),
		dict(cname="status", ctype=db.types.str),  # open | closed
	],
	primary_keys=["id"],
))

db.ensure_table(dict(
	tname="qc_quiz_answers",
	columns=[
		dict(cname="post_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="nick", ctype=db.types.str),
		dict(cname="revealed_at", ctype=db.types.int),
		dict(cname="deadline_at", ctype=db.types.int),
		dict(cname="choice_index", ctype=db.types.int, notnull=False),
		dict(cname="is_correct", ctype=db.types.bool, notnull=False),
		dict(cname="answered_at", ctype=db.types.int, notnull=False),
		dict(cname="response_ms", ctype=db.types.int, notnull=False),
	],
	primary_keys=["post_id", "user_id"],
))

db.ensure_table(dict(
	tname="qc_quiz_config",
	columns=[
		dict(cname="channel_id", ctype=db.types.int),
		dict(cname="enabled", ctype=db.types.bool),
		dict(cname="quiz_hour", ctype=db.types.int, notnull=False),
		dict(cname="answer_window", ctype=db.types.int, notnull=False),
		dict(cname="open_window", ctype=db.types.int, notnull=False),
		dict(cname="leaderboard_dow", ctype=db.types.int, notnull=False),
		dict(cname="leaderboard_hour", ctype=db.types.int, notnull=False),
		dict(cname="min_difficulty", ctype=db.types.str, notnull=False),
		dict(cname="last_post_ymd", ctype=db.types.str, notnull=False),
		dict(cname="last_leaderboard_ymd", ctype=db.types.str, notnull=False),
	],
	primary_keys=["channel_id"],
))

from .jobs import jobs  # noqa: E402,F401  (QuizJobs singleton — bot.quiz.jobs.think)
