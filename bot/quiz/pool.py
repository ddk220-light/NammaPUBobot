# -*- coding: utf-8 -*-
"""Load + validate the committed question pool and pick the next question.

Pure: callers pass the asked-id set and recent categories (read from MySQL) so the
selection logic stays testable. The on-disk file is data/quiz_questions.json; the
runtime loader is tolerant of a missing/invalid file (returns []) so the bot never
crashes when the pool has not been generated yet."""
from __future__ import annotations

import json
import os
import random as _random

_REQUIRED = ("id", "category", "difficulty", "prompt", "options", "correct_index",
			 "explanation", "source")
_DIFF_RANK = {"easy": 0, "medium": 1, "hard": 2}
_DEFAULT_PATH = os.path.join(
	os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
	"data", "quiz_questions.json")


def validate(raw):
	"""Return the list of valid question dicts, raising ValueError on the first
	malformed entry (used by the generator and tests; the runtime loader catches)."""
	out = []
	seen = set()
	for i, q in enumerate(raw):
		for k in _REQUIRED:
			if k not in q:
				raise ValueError(f"entry {i} missing key {k!r}")
		if not isinstance(q["options"], list) or len(q["options"]) != 4:
			raise ValueError(f"entry {q.get('id')} must have exactly 4 options")
		if not isinstance(q["correct_index"], int) or not (0 <= q["correct_index"] < 4):
			raise ValueError(f"entry {q.get('id')} correct_index out of range")
		if q["id"] in seen:
			raise ValueError(f"duplicate id {q['id']!r}")
		seen.add(q["id"])
		out.append(q)
	return out


def load(path=None):
	"""Runtime loader — returns [] if the file is missing or invalid. Never raises."""
	path = path or _DEFAULT_PATH
	try:
		with open(path, encoding="utf-8") as f:
			return validate(json.load(f))
	except (OSError, ValueError, json.JSONDecodeError):
		return []


def pick_next(items, asked_ids, recent_categories=(), rng=None, min_difficulty=None):
	"""Pick a not-yet-asked question, preferring categories not in recent_categories.
	When min_difficulty is set (easy|medium|hard) only questions at least that hard are
	eligible, but the filter never blocks a post — if it would empty the pool it is
	ignored. Returns None only when every question has been asked. Deterministic given
	rng."""
	rng = rng or _random.Random()
	fresh = [q for q in items if q["id"] not in asked_ids]
	if min_difficulty in _DIFF_RANK:
		floor = _DIFF_RANK[min_difficulty]
		hard_enough = [q for q in fresh if _DIFF_RANK.get(q.get("difficulty"), 1) >= floor]
		fresh = hard_enough or fresh
	if not fresh:
		return None
	recent = set(recent_categories or ())
	preferred = [q for q in fresh if q["category"] not in recent]
	bucket = preferred or fresh
	return rng.choice(bucket)
