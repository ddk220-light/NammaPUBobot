"""Tenant boundaries for quiz configuration and scheduling."""
import asyncio
import sys

import pytest

from nammaoe2bot.features.quiz import store


jobs_module = sys.modules["nammaoe2bot.features.quiz.jobs"]


class _Tx:
	def __init__(self, db):
		self.db = db

	async def fetchone(self, sql, args=None):
		self.db.calls.append(("fetchone", sql, list(args or [])))
		if "FROM communities" in sql:
			return {"community_id": args[0]}
		if "FROM community_channels" in sql:
			return {"channel_id": args[1]} if self.db.linked else None
		if "FROM quiz_settings" in sql:
			return {"channel_id": args[0]} if self.db.existing else None
		return None

	async def execute(self, sql, args=None):
		self.db.calls.append(("execute", sql, list(args or [])))
		return 1

	async def insert(self, table, row, on_duplicate=None):
		self.db.calls.append(("insert", table, dict(row)))
		return 1


class _DB:
	def __init__(self, *, linked=True, existing=False, rows=None):
		self.linked = linked
		self.existing = existing
		self.rows = list(rows or [])
		self.calls = []

	def transaction(self):
		db = self

		class _Context:
			async def __aenter__(self):
				return _Tx(db)

			async def __aexit__(self, exc_type, exc, traceback):
				return False

		return _Context()

	async def fetchall(self, sql, args=None):
		self.calls.append(("fetchall", sql, list(args or [])))
		return list(self.rows)

	async def execute(self, sql, args=None):
		self.calls.append(("execute", sql, list(args or [])))
		return 1


def test_enabled_quizzes_are_loaded_with_their_community_boundary(monkeypatch):
	fake = _DB(rows=[{"channel_id": 10, "community_id": 1, "enabled": 1}])
	monkeypatch.setattr(store, "db", fake)

	rows = asyncio.run(store.enabled_configs())

	assert rows[0]["community_id"] == 1
	sql = fake.calls[0][1]
	assert "JOIN community_channels" in sql
	assert "ORDER BY cc.community_id" in sql
	assert "LIMIT 1" not in sql


def test_community_quiz_save_serializes_and_disables_only_that_tenant(monkeypatch):
	fake = _DB()
	monkeypatch.setattr(store, "db", fake)

	asyncio.run(store.configure_for_community(
		7, 70, enabled=True, quiz_hour=0, open_window=3600))

	assert fake.calls[0] == (
		"fetchone", "SELECT community_id FROM communities WHERE community_id=%s FOR UPDATE", [7])
	disable = next(call for call in fake.calls if call[0] == "execute" and "SET qs.enabled=0" in call[1])
	assert "WHERE cc.community_id=%s" in disable[1]
	assert disable[2] == [7]
	insert = next(call for call in fake.calls if call[0] == "insert")
	assert insert[1] == "quiz_settings"
	assert insert[2] == {"channel_id": 70, "enabled": 1, "quiz_hour": 0, "open_window": 3600}


def test_community_quiz_save_rejects_an_unlinked_channel_before_writing(monkeypatch):
	fake = _DB(linked=False)
	monkeypatch.setattr(store, "db", fake)

	with pytest.raises(ValueError, match="does not belong"):
		asyncio.run(store.configure_for_community(
			7, 999, enabled=True, quiz_hour=9, open_window=86400))

	assert not any(call[0] in ("execute", "insert") for call in fake.calls)


def test_community_quiz_store_rejects_invalid_values_even_outside_the_web_api(monkeypatch):
	fake = _DB()
	monkeypatch.setattr(store, "db", fake)

	with pytest.raises(ValueError, match="boolean"):
		asyncio.run(store.configure_for_community(
			7, 70, enabled="false", quiz_hour=9, open_window=86400))
	with pytest.raises(ValueError, match="0 to 23"):
		asyncio.run(store.configure_for_community(
			7, 70, enabled=True, quiz_hour=24, open_window=86400))

	assert fake.calls == []


def test_emergency_disable_serializes_and_stays_inside_one_community(monkeypatch):
	fake = _DB()
	monkeypatch.setattr(store, "db", fake)

	assert asyncio.run(store.disable_for_community(7)) is True

	assert "FOR UPDATE" in fake.calls[0][1]
	disable = next(call for call in fake.calls if call[0] == "execute")
	assert "WHERE cc.community_id=%s" in disable[1]
	assert disable[2] == [7]


def test_scheduler_runs_every_community_and_isolates_one_tenants_failure(monkeypatch):
	seen = []

	class _Store:
		async def enabled_configs(self):
			return [
				{"community_id": 1, "channel_id": 10, "enabled": 1},
				{"community_id": 1, "channel_id": 11, "enabled": 1},
				{"community_id": 2, "channel_id": 20, "enabled": 1},
			]

	monkeypatch.setattr(jobs_module, "store", _Store())
	monkeypatch.setattr(jobs_module.time, "time", lambda: 1000)
	job = jobs_module.QuizJobs()

	async def maybe(cfg, now):
		seen.append(("daily", cfg["community_id"], now))
		if cfg["community_id"] == 1:
			raise RuntimeError("one tenant is broken")

	async def close(now):
		seen.append(("close", now))

	monkeypatch.setattr(job, "_maybe_post_daily", maybe)
	monkeypatch.setattr(job, "_close_due", close)

	asyncio.run(job._run())

	assert seen == [("daily", 1, 1000), ("daily", 2, 1000), ("close", 1000)]


def test_quiz_store_has_no_deployment_wide_enable_or_disable_path():
	assert not hasattr(store, "disable_all")
