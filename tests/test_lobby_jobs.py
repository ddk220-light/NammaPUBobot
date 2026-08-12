# -*- coding: utf-8 -*-
"""Restart-safe launch polling and completion scheduling invariants."""
import asyncio

from nammaoe2bot.features.lobby.jobs import LobbyJobs


def test_completion_floor_is_measured_from_confirmed_launch():
	jobs = LobbyJobs()
	row = {"launched_at": 1_000, "last_edit_at": 0, "created_at": 1}
	assert jobs._due(row, 1_000 + jobs.FLOOR_SECONDS - 1) is False
	assert jobs._due(row, 1_000 + jobs.FLOOR_SECONDS) is True


def test_an_unconfirmed_row_is_never_due_for_completion():
	jobs = LobbyJobs()
	assert jobs._due({"launched_at": None, "last_edit_at": 0}, 9_999_999) is False


class _Db:
	def __init__(self):
		self.calls = []

	async def execute(self, sql, args=None):
		self.calls.append((sql, list(args or [])))
		return 1


def test_stale_reaper_cannot_expire_an_api_confirmed_game(monkeypatch):
	fake = _Db()
	# The package exports the singleton as `jobs`; patch its module-level db.
	module = __import__("nammaoe2bot.features.lobby.jobs", fromlist=["db"])
	monkeypatch.setattr(module, "db", fake)
	asyncio.run(LobbyJobs()._reap_stale(1_000))

	sql, args = fake.calls[0]
	assert "launched_at IS NULL" in sql
	assert args == [1_000]
