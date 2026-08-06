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
	def __init__(self):
		self.calls = []  # [(table, dict)]

	async def insert(self, table, d, on_duplicate=None):
		self.calls.append((table, dict(d)))
		return len(self.calls)

	def inserted(self, table):
		return [d for t, d in self.calls if t == table]


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
