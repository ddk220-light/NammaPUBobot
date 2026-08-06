# -*- coding: utf-8 -*-
"""Pins bot.quiz.store.create_post's contract for the poll re-render: every field
the card displays has to survive the round trip through quiz_posts, and this is
where difficulty and closes_at get pinned.

No pytest-asyncio in this repo (see tests/test_civ_stats.py's note), so the async
store calls are driven with asyncio.run() from plain `def test_...` -- an
`async def test_...` here would be silently skipped and falsely report as passing.

FakeDb mirrors tests/test_predictions_store.py's fake (a recorder monkeypatched
onto `store.db`), sized to what create_post actually calls: db.insert(table, d).
`inserted()` hands back the dicts recorded for a table, which is what this file's
assertions care about -- not the SQL, since db.insert's INSERT statement is
generated from the dict's own keys and has no decision in it to pin."""
from __future__ import annotations

import asyncio

import pytest

from bot.quiz import store


class FakeDb:
	""" `calls` keeps the original (table, dict) shape `inserted()` relies on, so
	the create_post tests above are untouched. `_rows` is the addition: a real
	table keyed on (post_id, user_id), so a "replace" insert genuinely
	overwrites the prior row (mirroring MySQL's REPLACE INTO) instead of just
	accumulating history the way a bare append would -- which is what the
	changed-her-mind test needs to be able to fail. """
	def __init__(self):
		self.calls = []  # [(table, dict)]
		self.executed = []  # [(sql, args)]
		self._rows = {}  # table -> {(post_id, user_id): dict}

	async def insert(self, table, d, on_duplicate=None):
		self.calls.append((table, dict(d)))
		if "post_id" in d and "user_id" in d:
			table_rows = self._rows.setdefault(table, {})
			key = (d["post_id"], d["user_id"])
			if on_duplicate == "ignore" and key in table_rows:
				pass  # existing row wins, mirrors INSERT IGNORE
			else:
				table_rows[key] = dict(d)
		return len(self.calls)

	async def execute(self, sql, args=None):
		args = list(args or [])
		self.executed.append((sql, args))
		if "UPDATE quiz_answers SET is_correct=" in sql:
			is_correct, post_id, user_id = args
			row = self._rows.get("quiz_answers", {}).get((post_id, user_id))
			if row is not None:
				row["is_correct"] = is_correct
		return 1

	def inserted(self, table):
		return [d for t, d in self.calls if t == table]

	def row(self, table, **keys):
		return self._rows.get(table, {}).get((keys["post_id"], keys["user_id"]))


@pytest.fixture
def fake_db(monkeypatch):
	fake = FakeDb()
	monkeypatch.setattr(store, "db", fake)
	return fake


def _question(**overrides):
	q = dict(id="q1", category="combat", difficulty="medium", prompt="p",
			options=["a", "b"], correct_index=0, correct_indices=[0],
			explanation="e", seq=1, week=1, day=1, source="game")
	q.update(overrides)
	return q


def test_create_post_stores_difficulty(fake_db):
	q = _question(difficulty="medium")
	asyncio.run(store.create_post(123, q, 1000, 87400))

	row = fake_db.inserted("quiz_posts")[-1]
	assert row["difficulty"] == "medium"


def test_create_post_stores_closes_at_as_passed(fake_db):
	""" closes_at is the 24-hour lock deadline a later task gates voting on --
	it has to land in the row exactly as the caller computed it, not be
	recomputed or defaulted here. """
	q = _question()
	asyncio.run(store.create_post(123, q, 1000, 87400))

	row = fake_db.inserted("quiz_posts")[-1]
	assert row["closes_at"] == 87400


def test_create_post_stores_none_when_difficulty_is_absent(fake_db):
	""" A live-generated player question (bot/quiz/player_bank.py) carries no
	difficulty key at all -- create_post must read it with .get, not [], or
	every player-day quiz crashes at post time instead of storing NULL. """
	q = _question()
	del q["difficulty"]
	asyncio.run(store.create_post(123, q, 1000, 87400))

	row = fake_db.inserted("quiz_posts")[-1]
	assert row["difficulty"] is None


def test_record_vote_is_a_replace_upsert(fake_db):
	""" The PK (post_id, user_id) is the one-vote rule; REPLACE is what makes a
	changed mind overwrite the row instead of erroring or piling up a second
	one. """
	asyncio.run(store.record_vote(9, 1, "Ann", 2, 1000))
	asyncio.run(store.record_vote(9, 1, "Ann", 0, 1001))          # changed her mind
	row = fake_db.row("quiz_answers", post_id=9, user_id=1)
	assert row["choice_index"] == 0
	assert row["choice_indices"] is None
	assert row["is_correct"] is None                       # graded at lock, not at press
	assert row["answered_at"] == 1001
	assert row["revealed_at"] is None and row["response_ms"] is None


def test_record_vote_multi_stores_sorted_set(fake_db):
	asyncio.run(store.record_vote_multi(9, 1, "Ann", [2, 0], 1000))
	row = fake_db.row("quiz_answers", post_id=9, user_id=1)
	assert row["choice_indices"] == "[0, 2]"
	assert row["choice_index"] is None


def test_write_grade(fake_db):
	asyncio.run(store.record_vote(9, 1, "Ann", 0, 1000))
	asyncio.run(store.write_grade(9, 1, True))
	assert fake_db.row("quiz_answers", post_id=9, user_id=1)["is_correct"] == 1
