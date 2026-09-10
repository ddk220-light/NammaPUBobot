# -*- coding: utf-8 -*-
import asyncio

from nammaoe2bot.pickup import noadds as module


class _Db:
	def __init__(self, row=None):
		self.row = row
		self.calls = []

	async def fetchone(self, sql, args):
		self.calls.append((sql, list(args)))
		return self.row

	async def fetchall(self, sql, args):
		self.calls.append((sql, list(args)))
		return []


class _Ctx:
	channel = type("Channel", (), {"guild": type("Guild", (), {"id": 7})()})()


def test_idle_noadd_job_never_writes_the_database(monkeypatch):
	fake = _Db()
	monkeypatch.setattr(module, "db", fake)
	asyncio.run(module.NoAdds().think(10_000))
	assert fake.calls == []


def test_active_ban_reads_filter_expired_rows_in_sql(monkeypatch):
	fake = _Db()
	monkeypatch.setattr(module, "db", fake)
	member = type("Member", (), {"id": 9})()
	assert asyncio.run(module.NoAdds.get_user(_Ctx(), member)) == 0
	assert "at+duration>%s" in fake.calls[0][0]
	assert fake.calls[0][1][:2] == [7, 9]


def test_list_reads_only_unexpired_active_bans(monkeypatch):
	fake = _Db()
	monkeypatch.setattr(module, "db", fake)
	assert asyncio.run(module.NoAdds.get_noadds(_Ctx())) == []
	assert "is_active=1" in fake.calls[0][0]
	assert "at+duration>%s" in fake.calls[0][0]
