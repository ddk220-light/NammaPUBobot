"""Transaction context manager on the MySQL adapter — begin/commit/rollback
ordering and the rowcount-returning Transaction handle, driven by fakes.
No MySQL involved: the fakes record the calls the pool/connection receive.

The adapter module arrives through the `adapter_module` fixture in conftest.py,
which fakes aiomysql and pymysql — the adapter imports both at module load and
CI installs pytest only.
"""
from __future__ import annotations

import asyncio
import pytest


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

	async def executemany(self, sql, args):
		rows = [list(row) for row in args]
		self.executed.append((sql, rows))
		self.conn.log.append("executemany")
		if getattr(self.conn, "raise_next", None) is not None:
			exc, self.conn.raise_next = self.conn.raise_next, None
			raise exc
		self.rowcount = len(rows)

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
		self.freesize = 1
		self.cleared = 0
		self.released = []

	async def acquire(self):
		return self._conn

	def release(self, conn):
		self.released.append(conn)

	async def clear(self):
		self.cleared += 1
		self.freesize = 0


def make_adapter(adapter_module):
	a = adapter_module.Adapter("user:pass@host:3306/dbname")
	conn = FakeConn()
	a.pool = FakePool(conn)
	return a, conn


class TestTransaction:
	def test_cancelled_transaction_rolls_back(self, adapter_module):
		a, conn = make_adapter(adapter_module)

		async def run():
			async with a.transaction() as tx:
				await tx.execute("UPDATE t SET x=1")
				raise asyncio.CancelledError()

		with pytest.raises(asyncio.CancelledError):
			asyncio.run(run())
		assert conn.log == ["begin", "execute", "rollback"]

	def test_commit_failure_rolls_back_before_releasing_connection(self, adapter_module):
		a, conn = make_adapter(adapter_module)

		async def fail_commit():
			conn.log.append("commit failed")
			raise ConnectionError("commit acknowledgement lost")

		conn.commit = fail_commit

		async def run():
			async with a.transaction() as tx:
				await tx.update("t", {"x": 1}, keys={"id": 2})

		with pytest.raises(ConnectionError):
			asyncio.run(run())
		assert conn.log == ["begin", "execute", "commit failed", "rollback"]
		assert a.pool.released == [conn]
		assert conn._cur.executed[0][1] == [1, 2]

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

	def test_insert_many_stays_on_the_transaction_connection(self, adapter_module):
		a, conn = make_adapter(adapter_module)

		async def run():
			async with a.transaction() as tx:
				return await tx.insert_many("matches", [{"a": 1}, {"a": 2}], on_duplicate="ignore")
		assert asyncio.run(run()) == 2
		assert conn.log == ["begin", "executemany", "commit"]
		sql, rows = conn._cur.executed[0]
		assert sql.startswith("INSERT IGNORE INTO matches")
		assert rows == [[1], [2]]

	def test_insert_many_empty_input_issues_no_statement(self, adapter_module):
		a, conn = make_adapter(adapter_module)

		async def run():
			async with a.transaction() as tx:
				return await tx.insert_many("matches", [])
		assert asyncio.run(run()) == 0
		assert conn.log == ["begin", "commit"]


class TestLowCostPool:
	def test_idle_free_connections_are_cleared_without_a_query(self, adapter_module):
		a, _conn = make_adapter(adapter_module)
		a._last_activity = 10
		closed = asyncio.run(a.reap_idle_connections(idle_seconds=60, now=71))
		assert closed == 1
		assert a.pool.cleared == 1

	def test_active_or_recent_pool_is_not_cleared(self, adapter_module):
		a, _conn = make_adapter(adapter_module)
		a._last_activity = 10
		assert asyncio.run(a.reap_idle_connections(idle_seconds=60, now=69)) == 0
		assert a.pool.cleared == 0

	def test_connect_creates_a_zero_idle_two_connection_pool(self, adapter_module, monkeypatch):
		seen = {}

		class Pool:
			freesize = 0
			def close(self):
				pass
			async def wait_closed(self):
				pass

		async def create_pool(**kwargs):
			seen.update(kwargs)
			return Pool()

		monkeypatch.setattr(adapter_module.aiomysql, "create_pool", create_pool)
		a = adapter_module.Adapter("user:pass@host:3306/dbname")

		async def run():
			await a.connect()
			await a.close()
		asyncio.run(run())
		assert seen["minsize"] == 0
		assert seen["maxsize"] == 2
		assert seen["connect_timeout"] == 5

	def test_query_metrics_are_parameter_free_bounded_counters(self, adapter_module):
		a, _conn = make_adapter(adapter_module)

		async def run():
			with adapter_module.query_scope("quiz.jobs"):
				await a.fetchall("SELECT * FROM quiz_posts WHERE id=%s", [123])
		asyncio.run(run())
		rows = a.query_metrics_snapshot()
		assert rows[0]["source"] == "quiz.jobs"
		assert rows[0]["calls"] == 1
		assert rows[0]["rows"] == 1
		assert "123" not in rows[0]["fingerprint"]

	def test_a_sleeping_database_retries_only_before_sql_is_sent(
			self, adapter_module, monkeypatch):
		a, conn = make_adapter(adapter_module)
		attempts = {"n": 0}
		operational = adapter_module.mysqlErr.OperationalError("asleep")

		async def acquire():
			attempts["n"] += 1
			# Railway's observed cold wake exceeded the old three-attempt,
			# two-second window.  Prove a longer wake still retries acquisition
			# without ever replaying the SQL statement.
			if attempts["n"] < 5:
				raise operational
			return conn

		async def no_wait(_seconds):
			return None

		a.pool.acquire = acquire
		monkeypatch.setattr(adapter_module.asyncio, "sleep", no_wait)

		assert asyncio.run(a.fetchone("SELECT 1")) == {"balance": 500}
		assert attempts["n"] == 5
		assert conn.log == ["execute"], "connection retries must not retry SQL"

	def test_exhausted_connection_wake_is_translated_without_sending_sql(
			self, adapter_module, monkeypatch):
		a, conn = make_adapter(adapter_module)

		async def acquire():
			raise adapter_module.mysqlErr.OperationalError("still asleep")

		async def no_wait(_seconds):
			return None

		a.pool.acquire = acquire
		monkeypatch.setattr(adapter_module.asyncio, "sleep", no_wait)
		try:
			asyncio.run(a.execute("UPDATE t SET x=1"))
			assert False, "should have raised the adapter's OperationalError"
		except adapter_module.OperationalError:
			pass
		assert conn.log == [], "no statement exists to retry or duplicate"


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
