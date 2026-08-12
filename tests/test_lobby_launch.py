# -*- coding: utf-8 -*-
"""The match API, not a websocket removal or workflow label, proves launch."""
import asyncio

from nammaoe2bot.features.lobby import launch


class FakeTx:
	def __init__(self, changed=1, row=None):
		self.changed = changed
		self.row = row
		self.calls = []

	async def execute(self, sql, args=None):
		self.calls.append(("execute", sql, list(args or [])))
		return self.changed

	async def fetchone(self, sql, args=None):
		self.calls.append(("fetchone", sql, list(args or [])))
		return self.row


class FakeContext:
	def __init__(self, tx):
		self.tx = tx

	async def __aenter__(self):
		return self.tx

	async def __aexit__(self, *_args):
		return False


class FakeDb:
	def __init__(self, tx):
		self.tx = tx

	def transaction(self):
		return FakeContext(self.tx)


def test_started_at_requires_a_parseable_api_timestamp():
	assert launch.started_at(None) is None
	assert launch.started_at({"started": None}) is None
	assert launch.started_at({"started": "not-a-date"}) is None
	assert launch.started_at({"started": "2026-08-09T01:02:03.000Z"}) == 1786237323


def test_mark_confirmed_is_a_compare_and_set_on_the_exact_row(monkeypatch):
	tx = FakeTx(changed=1)
	monkeypatch.setattr(launch, "db", FakeDb(tx))

	assert asyncio.run(launch.mark_confirmed(4, 498336470, 1_700, observed_at=1_710)) is True

	kind, sql, args = tx.calls[0]
	assert kind == "execute"
	assert "launched_at IS NULL" in sql
	assert "aoe2_game_id=%s" in sql
	assert "status NOT IN ('completed','expired')" in sql
	assert args == [1_700, 4, 498336470]


def test_losing_the_confirmation_race_accepts_an_already_confirmed_row(monkeypatch):
	tx = FakeTx(changed=0, row={"launched_at": 1_700})
	monkeypatch.setattr(launch, "db", FakeDb(tx))

	assert asyncio.run(launch.mark_confirmed(4, 498336470, 1_700)) is True
	assert "FOR UPDATE" in tx.calls[1][1]


def test_a_cancelled_or_not_yet_published_lobby_is_not_confirmed(monkeypatch):
	async def _missing(_game_id):
		return None

	monkeypatch.setattr(launch.api, "fetch_match_by_id", _missing)
	assert asyncio.run(launch.verify_row({"id": 4, "aoe2_game_id": 498336082})) is False
