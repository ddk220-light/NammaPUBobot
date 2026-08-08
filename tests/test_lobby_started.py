# -*- coding: utf-8 -*-
"""nammaoe2bot/features/lobby/started.py — the one question betting asks the
lobby feature.

There is no logic here to speak of, which is the point: it is a status set and
a parameterised IN(), and both are the kind of thing that is wrong silently.
A launch set that included `expired` would close books because somebody
abandoned a lobby; one that missed `completed` would let a short game's book
stay open after the completion poller had already moved the row on.
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


class TestLaunchedStatuses:
	def test_expired_is_not_a_launch(self):
		""" THE ONE THAT LOOKS LIKE IT BELONGS. LobbyJobs._reap_stale writes
		'expired' over created/filling rows precisely when a lobby was seen and
		the game NEVER started. Counting it would freeze a book because players
		gave up on a lobby — closing betting on the strength of a game that did
		not happen. """
		assert "expired" not in started.LAUNCHED_STATUSES

	def test_the_pre_launch_statuses_are_not_launches(self):
		""" A lobby that exists, or is filling, has not started. Betting has to
		stay open through both — that is the whole window. """
		assert "created" not in started.LAUNCHED_STATUSES
		assert "filling" not in started.LAUNCHED_STATUSES

	def test_everything_from_in_progress_onward_is(self):
		""" `completed` included: a game that has finished has certainly
		started, and on a short game the completion poller can reach the row
		before the next betting sweep does. """
		assert set(started.LAUNCHED_STATUSES) == {"in_progress", "awaiting_confirm", "completed"}


class TestLaunchedAmong:
	def test_asks_only_about_the_ids_it_was_given(self, monkeypatch):
		db = FakeDb(rows=[{"match_id": 7}])
		monkeypatch.setattr(started, "db", db)

		out = _run(started.launched_among([7, 8]))

		assert out == {7}
		sql, args = db.calls[0]
		assert "match_id IN (%s, %s)" in sql
		assert args[:2] == [7, 8], "the ids are bound, never interpolated"
		assert args[2:] == list(started.LAUNCHED_STATUSES), "so is the status set"

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
