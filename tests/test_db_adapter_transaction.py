"""Transaction context manager on the MySQL adapter — begin/commit/rollback
ordering and the rowcount-returning Transaction handle, driven by fakes.
No MySQL involved: the fakes record the calls the pool/connection receive.

The adapter module arrives through the `adapter_module` fixture in conftest.py,
which fakes aiomysql and pymysql — the adapter imports both at module load and
CI installs pytest only.
"""
from __future__ import annotations

import asyncio


class FakeCursor:
	def __init__(self, conn):
		self.conn = conn
		self.rowcount = 0
		self.executed = []

	async def execute(self, sql, args=None):
		self.executed.append((sql, list(args) if args else []))
		self.conn.log.append("execute")
		# INSERT IGNORE hitting a duplicate reports 0 affected rows
		self.rowcount = 0 if getattr(self.conn, "duplicate_next", False) else 1
		self.conn.duplicate_next = False

	async def fetchone(self):
		return {"balance": 500}

	async def fetchall(self):
		return [{"balance": 500}]

	async def close(self):
		pass

	async def __aenter__(self):
		return self

	async def __aexit__(self, *exc):
		await self.close()


class FakeConn:
	def __init__(self):
		self.log = []
		self.duplicate_next = False
		self._cur = FakeCursor(self)

	async def begin(self):
		self.log.append("begin")

	async def commit(self):
		self.log.append("commit")

	async def rollback(self):
		self.log.append("rollback")

	def cursor(self):
		return self._cur


class FakePool:
	def __init__(self, conn):
		self._conn = conn

	def acquire(self):
		pool = self

		class _Ctx:
			async def __aenter__(self):
				return pool._conn

			async def __aexit__(self, *exc):
				return False

		return _Ctx()


def make_adapter(adapter_module):
	a = adapter_module.Adapter("user:pass@host:3306/dbname")
	conn = FakeConn()
	a.pool = FakePool(conn)
	return a, conn


class TestTransaction:
	def test_commits_on_clean_exit(self, adapter_module):
		a, conn = make_adapter(adapter_module)

		async def run():
			async with a.transaction() as tx:
				await tx.execute("UPDATE t SET x=1")
		asyncio.run(run())
		assert conn.log == ["begin", "execute", "commit"]

	def test_rolls_back_and_reraises_on_exception(self, adapter_module):
		a, conn = make_adapter(adapter_module)

		async def run():
			async with a.transaction() as tx:
				await tx.execute("UPDATE t SET x=1")
				raise RuntimeError("boom")
		try:
			asyncio.run(run())
			assert False, "should have raised"
		except RuntimeError:
			pass
		assert conn.log == ["begin", "execute", "rollback"]

	def test_execute_returns_rowcount(self, adapter_module):
		a, conn = make_adapter(adapter_module)

		async def run():
			async with a.transaction() as tx:
				return await tx.execute("UPDATE t SET x=1 WHERE y=%s", [2])
		assert asyncio.run(run()) == 1

	def test_insert_ignore_duplicate_returns_zero(self, adapter_module):
		a, conn = make_adapter(adapter_module)

		async def run():
			async with a.transaction() as tx:
				conn.duplicate_next = True
				return await tx.insert("gold_ledger", {"a": 1}, on_duplicate="ignore")
		assert asyncio.run(run()) == 0

	def test_insert_builds_insert_ignore_sql(self, adapter_module):
		a, conn = make_adapter(adapter_module)

		async def run():
			async with a.transaction() as tx:
				await tx.insert("gold_ledger", {"a": 1, "b": 2}, on_duplicate="ignore")
		asyncio.run(run())
		sql, args = conn._cur.executed[0]
		assert sql.startswith("INSERT IGNORE INTO gold_ledger")
		assert args == [1, 2]
