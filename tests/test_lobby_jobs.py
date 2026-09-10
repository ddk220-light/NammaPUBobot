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


def test_idle_lobby_job_sleeps_for_a_day_and_writer_can_rearm(monkeypatch):
	module = __import__("nammaoe2bot.features.lobby.jobs", fromlist=["time"])
	monkeypatch.setattr(module.time, "time", lambda: 1_000)
	job = LobbyJobs()

	async def no_live():
		return False

	async def no_launches(_now):
		return False

	async def no_completions(_now):
		return False

	async def no_reap(_cutoff):
		return None

	job._rehydrate = no_live
	job._poll_launches = no_launches
	job._poll_completions = no_completions
	job._reap_stale = no_reap
	asyncio.run(job._run())

	assert job._active is False
	assert job.next_run == 1_000 + job.RECOVERY_INTERVAL
	job.arm()
	assert job._active is True
	assert job.next_run == 0
