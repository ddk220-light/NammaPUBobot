# -*- coding: utf-8 -*-
import asyncio
import re
import time
from collections import defaultdict
from contextvars import ContextVar
from contextlib import contextmanager
from contextlib import asynccontextmanager

import aiomysql
from pymysql import err as mysqlErr
from .common import *

from nammaoe2bot.runtime.console import log


_query_source = ContextVar("db_query_source", default="unattributed")
_sql_space = re.compile(r"\s+")
_sql_number = re.compile(r"\b\d+\b")
_sql_string = re.compile(r"'(?:''|[^'])*'")

# Railway may put the MySQL service to sleep after the bot releases its final
# socket.  Retrying *connection acquisition* is safe for reads and writes alike:
# no SQL has been sent yet, so this cannot duplicate a mutation.  Retrying after
# cur.execute() would not have that property and is deliberately not done.
# Railway's MySQL cold start can take longer than two seconds.  Keep retrying
# connection acquisition for up to ~8 seconds so the first command after an
# idle sleep wakes the database instead of surfacing a transient failure.  No
# SQL has been sent at this point, so every retry remains safe for writes.
_CONNECT_ATTEMPTS = 6
_CONNECT_RETRY_SECONDS = (0.5, 1.0, 1.5, 2.0, 3.0)


@contextmanager
def query_scope(source):
	"""Attach a fixed-cardinality caller label to queries in this context.

	Parameters are never recorded.  The tiny in-memory counters exist to find
	the next accidental polling loop without adding an APM agent (and its RAM)
	to this small deployment.
	"""
	token = _query_source.set(str(source or "unattributed")[:64])
	try:
		yield
	finally:
		_query_source.reset(token)


class Types:
	bool = "TINYINT(1)"
	int = "BIGINT"
	float = "FLOAT"
	str = "VARCHAR(191)"
	text = "VARCHAR(2000)"
	dict = "MEDIUMTEXT"


reference_options = dict(
	RESTRICT='RESTRICT',
	CASCADE='CASCADE',
	SET_NULL='SET NULL',
	SET_DEFAULT='SET DEFAULT'
)

# `unique_keys` is a list of (index_name, [column, ...]) — composite UNIQUE
# indexes, which the per-column `unique` flag cannot express. Honoured by
# create_table only, exactly like `primary_keys`: _ensure_table's job on an
# existing table is limited to adding missing COLUMNS, and changing a key on a
# populated table is a migration's decision (it can fail on the data), never
# something an import-time declaration should attempt behind an operator's back.
#
# `indexes` is the same list shape for NON-unique secondary indexes, and carries
# the same create_table-only rule for the same reason. It exists so a table's
# access paths are declared next to its columns rather than living only in a
# migration: a fresh install creates the table here and would otherwise get no
# index at all, because the migration that adds one to an EXISTING database runs
# before `import bot` and finds nothing to alter. The two halves are deliberate
# and complementary — declaration covers new databases, migration covers old
# ones — so an index added here must also be added as a migration, and vice
# versa.
table_blank = dict(tname=None, columns=[], primary_keys=[], foreign_keys=[], unique_keys=[], indexes=[])
column_blank = dict(cname=None, ctype=Types.str, notnull=False, unique=False, autoincrement=False, default=None)
fkey_blank = dict(cname=None, refTable=None, refColumn=None, on_delete=None, on_update=None)


class Transaction:
	"""Connection-bound handle yielded by Adapter.transaction(). Same query
	surface as the adapter, with ONE deliberate difference: execute()/insert()
	return the affected-row COUNT, not lastrowid — inside a transaction the
	caller's question is almost always "did that row apply?" (a conditional
	UPDATE that matched nothing, an INSERT IGNORE that hit its idem key), and
	rowcount is the only honest answer to it."""

	def __init__(self, adapter, cur):
		self._adapter = adapter
		self._cur = cur

	async def execute(self, *args):
		started = time.monotonic()
		try:
			await self._cur.execute(*args)
		except mysqlErr.Error as e:
			self._adapter._record_query(args[0], started, error=True)
			self._adapter.wrap_exc(e)
		self._adapter._record_query(args[0], started, rows=self._cur.rowcount)
		return self._cur.rowcount

	async def executemany(self, *args):
		started = time.monotonic()
		try:
			await self._cur.executemany(*args)
		except mysqlErr.Error as e:
			self._adapter._record_query(args[0], started, error=True)
			self._adapter.wrap_exc(e)
		self._adapter._record_query(args[0], started, rows=self._cur.rowcount)
		return self._cur.rowcount

	async def fetchone(self, *args):
		started = time.monotonic()
		try:
			await self._cur.execute(*args)
			row = await self._cur.fetchone()
		except mysqlErr.Error as e:
			self._adapter._record_query(args[0], started, error=True)
			self._adapter.wrap_exc(e)
		self._adapter._record_query(args[0], started, rows=int(row is not None))
		return row

	async def fetchall(self, *args):
		started = time.monotonic()
		try:
			await self._cur.execute(*args)
			rows = await self._cur.fetchall()
		except mysqlErr.Error as e:
			self._adapter._record_query(args[0], started, error=True)
			self._adapter.wrap_exc(e)
		self._adapter._record_query(args[0], started, rows=len(rows or ()))
		return rows

	async def insert(self, table, d, on_duplicate=None):
		request = self._adapter._mysql_insert(d.keys(), table, on_duplicate)
		return await self.execute(request, list(d.values()))

	async def insert_many(self, table, rows, on_duplicate=None):
		rows = list(rows)
		if not rows:
			return 0
		request = self._adapter._mysql_insert(rows[0].keys(), table, on_duplicate)
		return await self.executemany(request, [list(row.values()) for row in rows])

	async def update(self, table, d, keys=None):
		keys = keys or {}
		request = self._adapter._mysql_update(table, d.keys(), keys.keys())
		return await self.execute(request, list(d.values()) + list(keys.values()))


class Adapter:
	pool: aiomysql.Pool | None
	loop: asyncio.AbstractEventLoop
	types = Types
	errors = Errors

	def __init__(self, db_address):
		self.dbAddress = db_address
		self.pool = None
		self._idle_reaper_task = None
		self._last_activity = time.monotonic()
		self._query_metrics = defaultdict(lambda: {
			"calls": 0, "errors": 0, "rows": 0, "seconds": 0.0,
		})
		try:
			self.dbUser, db_address = db_address.split(':', 1)
			self.dbPassword, db_address = db_address.split('@', 1)
			self.dbHost, self.dbName = db_address.split('/', 1)
			if self.dbHost.find(':') > -1:
				self.dbHost, self.dbPort = self.dbHost.split(':')
			else:
				self.dbPort = '3306'
		except Exception:
			raise(ValueError('Bad database address string: ' + self.dbAddress))

	async def connect(self):
		if self.pool is not None:
			return
		self.loop = asyncio.get_running_loop()
		try:
			# minsize=0 is the cost-critical setting: constructing the adapter and
			# running an idle Discord process holds no MySQL socket.  A connection
			# is opened on the first real query and free connections are cleared
			# after a short idle window so Railway Serverless can sleep the DB.
			from nammaoe2bot.runtime.config import cfg
			self.pool = await aiomysql.create_pool(
				host=self.dbHost,
				port=int(self.dbPort),
				user=self.dbUser,
				password=self.dbPassword,
				db=self.dbName,
				charset='utf8mb4',
				autocommit=True,
				minsize=0,
				maxsize=min(8, max(1, int(getattr(cfg, "DB_POOL_MAX_SIZE", 2)))),
				connect_timeout=5,
				pool_recycle=3600,
				cursorclass=aiomysql.cursors.DictCursor)
			self._idle_reaper_task = asyncio.create_task(self._idle_reaper())

		except mysqlErr.Error as e:
			self.wrap_exc(e)

	@asynccontextmanager
	async def _connection(self):
		if self.pool is None:
			await self.connect()
		conn = None
		self._last_activity = time.monotonic()
		for attempt in range(_CONNECT_ATTEMPTS):
			try:
				conn = await self.pool.acquire()
				break
			except (mysqlErr.OperationalError, OSError, TimeoutError) as e:
				if attempt + 1 >= _CONNECT_ATTEMPTS:
					if isinstance(e, mysqlErr.Error):
						self.wrap_exc(e)
					raise OperationalError() from e
				await asyncio.sleep(_CONNECT_RETRY_SECONDS[attempt])
		try:
			yield conn
		finally:
			self.pool.release(conn)
			self._last_activity = time.monotonic()

	async def _idle_reaper(self):
		"""Close only FREE connections; borrowed transactions are untouched."""
		from nammaoe2bot.runtime.config import cfg
		interval = max(10, int(getattr(cfg, "DB_IDLE_CLOSE_SECONDS", 60)))
		try:
			while True:
				await asyncio.sleep(interval)
				await self.reap_idle_connections(interval)
		except asyncio.CancelledError:
			return

	async def reap_idle_connections(self, idle_seconds=None, now=None):
		"""Clear idle pooled sockets without issuing SQL. Returns sockets closed."""
		if self.pool is None:
			return 0
		if idle_seconds is None:
			from nammaoe2bot.runtime.config import cfg
			idle_seconds = max(10, int(getattr(cfg, "DB_IDLE_CLOSE_SECONDS", 60)))
		now = time.monotonic() if now is None else now
		free = int(getattr(self.pool, "freesize", 0) or 0)
		if free and now - self._last_activity >= idle_seconds:
			await self.pool.clear()
			metrics = self.query_metrics_snapshot(reset=True)
			if metrics:
				top = ", ".join(
					f"{row['source']}={row['calls']}"
					for row in metrics[:8])
				log.debug(
					f"Closed {free} idle MySQL connection(s); query calls by source: {top}")
			return free
		return 0

	@staticmethod
	def _fingerprint(sql):
		text = _sql_space.sub(" ", str(sql or "")).strip().lower()
		text = _sql_string.sub("?", text)
		text = _sql_number.sub("?", text)
		return text[:240]

	def _record_query(self, sql, started, rows=0, error=False):
		key = (_query_source.get(), self._fingerprint(sql))
		# Strict cap: diagnostics must never become the memory problem it measures.
		if key not in self._query_metrics and len(self._query_metrics) >= 128:
			key = ("other", "other")
		metric = self._query_metrics[key]
		metric["calls"] += 1
		metric["errors"] += int(bool(error))
		metric["rows"] += max(0, int(rows or 0))
		metric["seconds"] += max(0.0, time.monotonic() - started)

	def query_metrics_snapshot(self, reset=False):
		out = [dict(source=source, fingerprint=fingerprint, **values)
			for (source, fingerprint), values in self._query_metrics.items()]
		out.sort(key=lambda row: (-row["calls"], row["source"], row["fingerprint"]))
		if reset:
			self._query_metrics.clear()
		return out

	async def execute(self, *args):
		started = time.monotonic()
		async with self._connection() as conn:
			async with conn.cursor() as cur:
				try:
					await cur.execute(*args)
					lastrowid = cur.lastrowid
				except Exception as e:
					self._record_query(args[0], started, error=True)
					self.wrap_exc(e)
				self._record_query(args[0], started, rows=getattr(cur, "rowcount", 0))
				return lastrowid

	async def executemany(self, *args):
		started = time.monotonic()
		async with self._connection() as conn:
			async with conn.cursor() as cur:
				try:
					await cur.executemany(*args)
				except mysqlErr.Error as e:
					self._record_query(args[0], started, error=True)
					self.wrap_exc(e)
				self._record_query(args[0], started, rows=getattr(cur, "rowcount", 0))

	async def fetchone(self, *args):
		started = time.monotonic()
		async with self._connection() as conn:
			async with conn.cursor() as cur:
				try:
					await cur.execute(*args)
					row = await cur.fetchone()
				except mysqlErr.Error as e:
					self._record_query(args[0], started, error=True)
					self.wrap_exc(e)
				self._record_query(args[0], started, rows=int(row is not None))
				return row

	async def fetchall(self, *args):
		started = time.monotonic()
		async with self._connection() as conn:
			async with conn.cursor() as cur:
				try:
					await cur.execute(*args)
					rows = await cur.fetchall()
				except mysqlErr.Error as e:
					self._record_query(args[0], started, error=True)
					self.wrap_exc(e)
				self._record_query(args[0], started, rows=len(rows or ()))
				return rows

	@asynccontextmanager
	async def transaction(self):
		"""One pooled connection, BEGIN .. COMMIT, ROLLBACK on any exception
		(which propagates). The pool runs autocommit=True; conn.begin() opens an
		explicit transaction that suspends autocommit until commit/rollback, so
		nothing else on this connection leaks in."""
		async with self._connection() as conn:
			await conn.begin()
			try:
				async with conn.cursor() as cur:
					yield Transaction(self, cur)
				await conn.commit()
			except BaseException:
				try:
					await conn.rollback()
				except BaseException:
					conn.close()  # Never return an uncertain transaction to the pool.
				raise

	@staticmethod
	def _mysql_column(kwargs):
		return "`{cname}` {ctype}{notnull}{unique}{autoincrement}{default}".format(
			cname=kwargs['cname'],
			ctype=kwargs['ctype'],
			notnull=" NOT NULL" if kwargs['notnull'] else "",
			unique=" UNIQUE" if kwargs['unique'] else "",
			autoincrement=" AUTO_INCREMENT" if kwargs['autoincrement'] else "",
			default=" DEFAULT '{}'".format(kwargs['default']) if kwargs['default'] is not None else ""
		)

	@staticmethod
	def _mysql_fkey(kwargs):
		return "({cname}) REFERENCES {refTable}({refColumn}){on_delete}{on_update}".format(
			cname=kwargs['cname'],
			refTable=kwargs['refTable'],
			refColumn=kwargs['refColumn'],
			on_delete=" ON DELETE " + reference_options[kwargs['on_delete']] if kwargs['on_delete'] else '',
			on_update=" ON UPDATE " + reference_options[kwargs['on_update']] if kwargs['on_update'] else ''
		)

	@staticmethod
	def _mysql_insert(columns, table, on_duplicate):
		columns = list(columns)
		request = "{action}{ignore} INTO {table} ({columns}) VALUES({values})".format(
			action="REPLACE" if on_duplicate == 'replace' else "INSERT",
			ignore=" IGNORE" if on_duplicate == 'ignore' else "",
			table=table,
			columns=", ".join((f"`{i}`" for i in columns)),
			values=", ".join(('%s' for i in range(len(columns))))
		)
		if on_duplicate == "keep":
			# Acquire the existing row's exclusive lock without changing it.
			# Unlike IGNORE, other data errors still fail the transaction. With
			# our default client flags, a duplicate reports zero affected rows.
			column = columns[0]
			request += f" ON DUPLICATE KEY UPDATE `{column}`=`{column}`"
		return request

	@staticmethod
	def _mysql_update(table, columns, keys):
		where = " WHERE {}".format(" AND ".join(["`{}`=%s".format(i) for i in keys])) if len(keys) else ""
		return "UPDATE {table} SET {columns}{where}".format(
			table=table,
			columns=",".join(["`{}`=%s".format(i) for i in columns]),
			where=where
		)

	async def create_table(self, table):
		table = {**table_blank, **table}

		columns = [self._mysql_column({**column_blank, **col}) for col in table['columns']]
		fkeys = ["FOREIGN KEY " + self._mysql_fkey({**fkey_blank, **fkey}) for fkey in table['foreign_keys']]
		pkeys = ", PRIMARY KEY(" + ", ".join("`{}`".format(k) for k in table['primary_keys']) + ')' if len(table['primary_keys']) else ''
		ukeys = "".join(
			", UNIQUE KEY `{}` ({})".format(name, ", ".join("`{}`".format(c) for c in cols))
			for name, cols in table['unique_keys']
		)
		ikeys = "".join(
			", INDEX `{}` ({})".format(name, ", ".join("`{}`".format(c) for c in cols))
			for name, cols in table['indexes']
		)

		request = "CREATE TABLE {tname} ({tdeskr})".format(
			tname=table['tname'],
			tdeskr=", ".join((columns + fkeys)) + pkeys + ukeys + ikeys
		)

		await self.execute(request)

	def ensure_table(self, table):
		self.loop.run_until_complete(self._ensure_table(table))

	async def _ensure_table(self, table):
		table = {**table_blank, **table}
		columns = await self.fetchall("\n".join((
			"SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS",
			"WHERE TABLE_NAME = '{}' AND TABLE_SCHEMA = '{}'".format(table['tname'], self.dbName)
		)))
		columns = {i['COLUMN_NAME']: i['DATA_TYPE'] for i in columns}

		# Create table if not exist
		if not len(columns):
			await self.create_table(table)
			return

		# Create columns if not exist
		for col in table['columns']:
			col = {**column_blank, **col}
			if col['cname'] not in columns.keys():
				await self.execute("ALTER TABLE {tname} ADD COLUMN {column_sql}".format(
					tname=table['tname'],
					column_sql=self._mysql_column({**column_blank, **col})
				))
				# Add a foreign key if needed
				for fkey in (fkey for fkey in table['foreign_keys'] if fkey['cname'] == col['cname']):
					await self.execute("ALTER TABLE {tname} ADD FOREIGN KEY {fkey_sql}".format(
						tname=table['tname'],
						fkey_sql=self._mysql_fkey({**fkey_blank, **fkey})
					))
			elif not col['ctype'].lower().startswith(columns[col['cname']]):
				raise(TypeError(
					"Column '{}' types are mismatching, {} and {}".format(col['cname'], col['ctype'], columns[col['cname']])
				))

	async def select(self, columns, table, where=None, order_by=None, order_asc=False, limit=None, one=False):
		conditions = " WHERE " + " AND ".join(("`{}`=%s".format(k) for k in where.keys())) if where else ''
		args = list(where.values()) if where else ()

		# fix queries where there are some restricted words, for example in MySQL 8 'rank' is restricted
		sql_restricted_words = [
				'rank',
				'role',
		]
		columns = [f"`{col}`" if col in sql_restricted_words else col for col in columns]

		request = "SELECT {columns} FROM `{table}`{where}{order}{limit}".format(
			columns=', '.join(columns),
			table=table,
			where=conditions,
			order=" ORDER BY "+order_by+(" ASC" if order_asc else " DESC") if order_by else "",
			limit=(" LIMIT " + str(limit)) if limit else ""
		)

		if one:
			return await self.fetchone(request, args)
		else:
			return await self.fetchall(request, args)

	async def select_one(self, *args, **kwargs):
		return await self.select(*args, **kwargs, one=True)

	async def delete(self, table, where=None):
		conditions = " WHERE " + " AND ".join(("`{}`=%s".format(k) for k in where.keys())) if where else ''
		args = list(where.values()) if where else ()
		await self.execute("DELETE FROM {}{}".format(table, conditions), args)

	async def insert(self, table, d, on_duplicate=None):
		request = self._mysql_insert(d.keys(), table, on_duplicate)
		return await self.execute(request, list(d.values()))

	async def update(self, table, d, keys=None):
		keys = keys or {}
		request = self._mysql_update(table, d.keys(), keys.keys())
		await self.execute(request, list(d.values()) + list(keys.values()))

	async def insert_many(self, table, it, on_duplicate=None):
		try:
			first, it = peek(iter(it))
		except StopIteration:
			return

		request = self._mysql_insert(first.keys(), table, on_duplicate)
		await self.executemany(request, (list(d.values()) for d in it))

	async def close(self):
		if self._idle_reaper_task is not None:
			self._idle_reaper_task.cancel()
			try:
				await self._idle_reaper_task
			except asyncio.CancelledError:
				pass
			self._idle_reaper_task = None
		if self.pool is not None:
			self.pool.close()
			await self.pool.wait_closed()
			self.pool = None

	@staticmethod
	def wrap_exc(e):
		if e.__class__ in [mysqlErr.InternalError, mysqlErr.OperationalError]:
			raise OperationalError() from e

		elif e.__class__ == mysqlErr.DataError:
			raise DataError() from e

		elif e.__class__ == mysqlErr.IntegrityError:
			raise IntegrityError() from e

		elif e.__class__ == mysqlErr.ProgrammingError:
			raise ProgrammingError() from e

		else:
			raise DatabaseError() from e
