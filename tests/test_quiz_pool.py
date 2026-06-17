"""Tests for bot.quiz.pool — validation + category-rotating picker (no DB)."""
from __future__ import annotations

import json
import os
import random

import bot.quiz.pool as pool

_SAMPLE = os.path.join(os.path.dirname(__file__), "..", "data", "quiz_questions.sample.json")


def _load():
	with open(_SAMPLE, encoding="utf-8") as f:
		return json.load(f)


def test_validate_accepts_sample():
	items = pool.validate(_load())
	assert len(items) == 3
	assert all(len(q["options"]) == 4 for q in items)


def test_validate_rejects_bad_options_and_index():
	bad = [{"id": "x", "category": "c", "difficulty": "d", "prompt": "p",
			"options": ["a", "b"], "correct_index": 5, "explanation": "", "source": ""}]
	try:
		pool.validate(bad)
		assert False, "expected ValueError"
	except ValueError:
		pass


def test_validate_rejects_duplicate_id():
	dup = _load() + [_load()[0]]
	try:
		pool.validate(dup)
		assert False, "expected ValueError"
	except ValueError:
		pass


def test_load_missing_file_returns_empty():
	assert pool.load(path="/no/such/quiz_pool.json") == []


def test_pick_next_avoids_asked_and_rotates_category():
	items = pool.validate(_load())
	q = pool.pick_next(items, asked_ids={"q1"}, recent_categories=["bonus"], rng=random.Random(0))
	assert q["id"] != "q1"            # asked excluded
	assert q["category"] != "bonus"   # recent category de-prioritised when alternatives exist


def test_pick_next_falls_back_when_all_recent():
	items = pool.validate(_load())
	# every fresh question's category is "recent" -> must still return something
	q = pool.pick_next(items, asked_ids=set(),
					   recent_categories=["armor", "bonus", "mechanic"], rng=random.Random(0))
	assert q is not None


def test_pick_next_returns_none_when_exhausted():
	items = pool.validate(_load())
	assert pool.pick_next(items, asked_ids={"q1", "q2", "q3"}, recent_categories=[], rng=None) is None
