# -*- coding: utf-8 -*-
"""The quiz calendar, and the committed game queue it draws from.

Two things live here, and the split is the point of stage 5b:

THE CALENDAR IS ARITHMETIC, not a file. A channel's Nth quiz is day
((N-1) % 7) + 1 of week ((N-1) // 7) + 1, and odd days are player days. That
rule -- day 1/3/5/7 player, day 2/4/6 game, player first, keyed on
day-within-week so week 2 also opens on a player question -- used to be baked
into data/quiz_schedule.json by the offline scheduler, one stamped row per day
for 26 weeks. It is expressed once here instead, because the stamped version
could only ever describe a channel that posts every single day from day one:
a channel enabled later, disabled for a fortnight, or given a forced post by an
admin would read someone else's calendar. `seq` (max posted seq + 1, from
quiz_posts) is the only input, so the calendar follows what the channel
actually posted.

THE QUEUE IS STILL A FILE. data/quiz_schedule.json holds the game bank's
curated order (utils/quiz_gen/build_schedule.py), and game days still read it,
unchanged. It no longer carries seq/week/day: those are the calendar's, above,
and an entry that carried its own would be a second opinion about which day it
is. Game days therefore draw the first entry the channel has not already asked
rather than indexing by seq -- which also means regenerating the bank cannot
re-post a question a channel has already seen.

Pure throughout: the bot passes in the already-posted seqs / asked ids (from
MySQL). Tolerant loader (returns [] on a missing / invalid file) so the bot
never crashes before the queue is generated."""
from __future__ import annotations

import json

from nammaoe2bot.runtime.paths import data as data_path

_DEFAULT_PATH = data_path("quiz_schedule.json")
_REQUIRED = ("id", "category", "prompt", "options", "correct_indices", "explanation")

DAYS_PER_WEEK = 7


def load(path=None):
	try:
		with open(path or _DEFAULT_PATH, encoding="utf-8") as f:
			data = json.load(f)
		return [q for q in data if all(k in q for k in _REQUIRED)]
	except (OSError, ValueError):
		return []


def slot_for_seq(seq):
	"""The (week, day) a channel's `seq`-th quiz falls on. 1-based on both axes."""
	return (seq - 1) // DAYS_PER_WEEK + 1, (seq - 1) % DAYS_PER_WEEK + 1


def source_for_day(day):
	"""Which bank a day-within-week draws from. THE alternation rule -- odd days
	player, even days game -- and the only place it is spelled."""
	return "player" if day % 2 == 1 else "game"


def seqs_of_week(week):
	"""Every seq that belongs to `week`."""
	first = (week - 1) * DAYS_PER_WEEK + 1
	return range(first, first + DAYS_PER_WEEK)


def week_is_complete(week, posted_seqs):
	"""True iff every seq belonging to `week` has been posted.

	Keyed off the calendar rather than off the queue file's contents: half of a
	week's questions are generated live and were never in any file, so "the file
	says week 3 holds these four seqs" stopped being answerable."""
	return set(seqs_of_week(week)) <= set(posted_seqs)


def completed_weeks(posted_seqs):
	"""Every week fully posted so far, ascending."""
	posted = set(posted_seqs)
	if not posted:
		return []
	last_week = slot_for_seq(max(posted))[0]
	return [w for w in range(1, last_week + 1) if week_is_complete(w, posted)]


def next_game_entry(items, asked_ids=()):
	"""The first queue entry this channel has not already been asked, or None
	when the game bank is exhausted."""
	asked = set(asked_ids)
	return next((q for q in items if q["id"] not in asked), None)
