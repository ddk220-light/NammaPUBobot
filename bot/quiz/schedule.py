# -*- coding: utf-8 -*-
"""Load + query the committed data/quiz_schedule.json. Pure: the bot passes in the
set of already-posted seqs (from MySQL). Tolerant loader (returns [] on a missing /
invalid file) so the bot never crashes before the schedule is generated."""
from __future__ import annotations

import json
import os

_DEFAULT_PATH = os.path.join(
	os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "quiz_schedule.json")
_REQUIRED = ("id", "category", "prompt", "options", "correct_indices", "explanation",
			 "seq", "week", "day")


def load(path=None):
	try:
		with open(path or _DEFAULT_PATH, encoding="utf-8") as f:
			data = json.load(f)
		return [q for q in data if all(k in q for k in _REQUIRED)]
	except (OSError, ValueError):
		return []


def entry_for_seq(items, seq):
	return next((q for q in items if q["seq"] == seq), None)


def week_is_complete(items, week, posted_seqs):
	"""True iff every seq scheduled for `week` has been posted."""
	wanted = {q["seq"] for q in items if q["week"] == week}
	return bool(wanted) and wanted <= set(posted_seqs)
