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
		if getattr(self.conn, "raise_next", None) is not None:
			exc, self.conn.raise_next = self.conn.raise_next, None
			raise exc
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
		self.raise_next = None      # a driver error for the next statement
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


class TestDriverErrorsAreTranslated:
	""" Transaction wraps every driver error in the adapter's own type, and that
	translation is not cosmetic: nammaoe2bot/features/betting/gold.py implements the side lock
	by catching `db.errors.IntegrityError` around the prediction_bets INSERT. A
	raw pymysql IntegrityError escaping the handle would sail past that except
	clause, roll the transaction back through the outer guard, and report a
	crash instead of "you're on Alpha this match" — while a wrap that mapped it
	to the WRONG type would be caught by nothing at all. Every branch below was
	unexercised, and deleting the wrap_exc call from Transaction.execute left the
	whole suite green. """

	def driver_error(self, adapter_module, name):
		import pymysql
		return getattr(pymysql.err, name)(f"simulated {name}")

	def test_execute_raises_the_adapters_integrity_error(self, adapter_module):
		a, conn = make_adapter(adapter_module)

		async def run():
			async with a.transaction() as tx:
				conn.raise_next = self.driver_error(adapter_module, "IntegrityError")
				await tx.execute("INSERT INTO prediction_bets ...")
		try:
			asyncio.run(run())
			assert False, "should have raised"
		except adapter_module.IntegrityError:
			pass
		assert conn.log == ["begin", "execute", "rollback"], "and the stake is not taken"

	def test_insert_raises_it_too(self, adapter_module):
		""" The side lock's actual call: tx.insert routes through execute. """
		a, conn = make_adapter(adapter_module)

		async def run():
			async with a.transaction() as tx:
				conn.raise_next = self.driver_error(adapter_module, "IntegrityError")
				await tx.insert("prediction_bets", {"post_id": 1, "user_id": 2})
		try:
			asyncio.run(run())
			assert False, "should have raised"
		except adapter_module.IntegrityError:
			pass

	def test_fetchone_and_fetchall_translate_as_well(self, adapter_module):
		a, conn = make_adapter(adapter_module)

		for method, sql in (("fetchone", "SELECT 1"), ("fetchall", "SELECT 2")):
			async def run(method=method, sql=sql):
				async with a.transaction() as tx:
					conn.raise_next = self.driver_error(adapter_module, "OperationalError")
					await getattr(tx, method)(sql)
			try:
				asyncio.run(run())
				assert False, f"{method} should have raised"
			except adapter_module.OperationalError:
				pass

	def test_each_driver_error_maps_to_its_own_type(self, adapter_module):
		""" One wrong mapping is one except clause that stops working. """
		for driver_name, expected in (
				("IntegrityError", adapter_module.IntegrityError),
				("OperationalError", adapter_module.OperationalError),
				("InternalError", adapter_module.OperationalError),
				("DataError", adapter_module.DataError),
				("ProgrammingError", adapter_module.ProgrammingError)):
			adapter, connection = make_adapter(adapter_module)

			async def run(a=adapter, conn=connection, driver_name=driver_name):
				async with a.transaction() as tx:
					conn.raise_next = self.driver_error(adapter_module, driver_name)
					await tx.execute("UPDATE t SET x=1")
			try:
				asyncio.run(run())
				assert False, f"{driver_name} should have raised"
			except expected:
				pass

	def test_the_type_gold_catches_is_the_type_the_adapter_publishes(self, adapter_module):
		""" gold.place_bet reaches it as `db.errors.IntegrityError`. """
		assert adapter_module.Adapter.errors.IntegrityError is adapter_module.IntegrityError
