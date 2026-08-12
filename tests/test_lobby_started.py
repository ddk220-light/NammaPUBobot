# -*- coding: utf-8 -*-
"""nammaoe2bot/features/lobby/started.py — the one question betting asks the
lobby feature.

There is no logic here to speak of, which is the point: it is one durable fact
and a parameterised IN(). Status labels are workflow state, not launch proof.
"""
from __future__ import annotations

import asyncio

from nammaoe2bot.features.lobby import started


class FakeDb:
	def __init__(self, rows=()):
		self.calls = []
		self.rows = list(rows)

	async def fetchall(self, sql, args=None):
		self.calls.append((sql, list(args or [])))
		return list(self.rows)


def _run(coro):
	return asyncio.run(coro)


class TestLaunchedAmong:
	def test_asks_only_about_the_ids_it_was_given(self, monkeypatch):
		db = FakeDb(rows=[{"match_id": 7}])
		monkeypatch.setattr(started, "db", db)

		out = _run(started.launched_among([7, 8]))

		assert out == {7}
		sql, args = db.calls[0]
		assert "match_id IN (%s, %s)" in sql
		assert args == [7, 8], "the ids are bound, never interpolated"
		assert "launched_at IS NOT NULL" in sql
		assert "status IN" not in sql, "workflow labels are not launch evidence"

	def test_no_ids_asks_nothing_at_all(self, monkeypatch):
		""" An empty IN() is a SQL error in some dialects and a table scan in
		others. The sweep hits this every time no book is open, which is most
		of the day. """
		db = FakeDb()
		monkeypatch.setattr(started, "db", db)

		assert _run(started.launched_among([])) == set()
		assert db.calls == []

	def test_a_null_match_id_is_dropped_rather_than_queried(self, monkeypatch):
		""" `/lobby <id>` leaves rows with match_id NULL — informational lobbies
		belonging to no bot match. They must never match anything. """
		db = FakeDb()
		monkeypatch.setattr(started, "db", db)

		_run(started.launched_among([None, 4]))

		_sql, args = db.calls[0]
		assert args[0] == 4 and None not in args

	def test_two_rows_for_one_match_collapse_to_one_answer(self, monkeypatch):
		""" A match can own several lobbies rows — a lobby that filled, was
		abandoned and was remade. Any one of them having launched is enough,
		and the caller wants a membership test, not a bag. """
		db = FakeDb(rows=[{"match_id": 5}, {"match_id": 5}])
		monkeypatch.setattr(started, "db", db)

		assert _run(started.launched_among([5])) == {5}
