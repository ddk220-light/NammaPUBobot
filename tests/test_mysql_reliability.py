"""Opt-in real MySQL 9.4 checks; only the fixed disposable CI database is used.

The ordinary suite needs no SQL driver/server. Enable with NAMMA_TEST_MYSQL=1
and the localhost service defined in ci.yml. Never reads config.cfg or Railway
credentials. Discord is fake; schema, adapter, transactions, SQL, and ratings
are production code.
"""
import ast
import asyncio
import importlib
import os
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from nammaoe2bot.app import Application
from nammaoe2bot.pickup import stats
from nammaoe2bot.runtime.paths import REPO_ROOT
from tests.test_match_lifecycle_e2e import FakeCtx, build_match
from tests.test_match_reporting_atomic import _rating_class
from tests.test_state_persistence import state


pytestmark = pytest.mark.skipif(os.environ.get("NAMMA_TEST_MYSQL") != "1", reason="requires disposable MySQL CI service")


@asynccontextmanager
async def live_database(monkeypatch):
	# This fixed endpoint and dedicated user deliberately cannot use production
	# credentials accidentally. The CI service is destroyed with its job.
	module = importlib.import_module("nammaoe2bot.runtime.database.mysql")
	database = module.Adapter("reliability_test:integration-only@127.0.0.1:3306/namma_reliability_test")
	await database.connect()
	try:
		version = await database.fetchone("SELECT VERSION() AS version, DATABASE() AS db")
		assert version["version"].startswith("9.4.")
		assert version["db"] == "namma_reliability_test"
		print(f"Real MySQL {version['version']}; pool maximum {database.pool.maxsize}")
		assert database.pool.maxsize == 2
		# Reuse the real table declarations instead of maintaining an approximate
		# schema in this integration test. No bootstrap/ensure_table side effects.
		class Schema:
			types = database.types

			def __init__(self):
				self.tables = []

			def ensure_table(self, table):
				self.tables.append(table)

		schema = Schema()
		for relative in ("nammaoe2bot/pickup/stats.py", "nammaoe2bot/state.py"):
			path = Path(REPO_ROOT) / relative
			tree = ast.parse(path.read_text())
			declarations = [node for node in tree.body if isinstance(node, ast.Expr)
				and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
				and node.value.func.attr == "ensure_table"]
			exec(compile(ast.Module(body=declarations, type_ignores=[]), str(path), "exec"), {"db": schema})
		for table in schema.tables:
			await database.execute(f"DROP TABLE IF EXISTS `{table['tname']}`")
			await database.create_table(table)
		engines = await database.fetchall("SELECT ENGINE FROM information_schema.tables WHERE table_schema=DATABASE()")
		assert all(row["ENGINE"] == "InnoDB" for row in engines)
		monkeypatch.setattr(stats, "db", database)
		yield database, module
	finally:
		await database.close()


def make_match(mid=81, *, ranked=True, players_offset=0):
	app = Application(client=None)
	match = build_match(app, ranked=ranked)
	match.id = mid
	for player in match.players:
		player.id += players_offset
	match.qc.rating = _rating_class()(channel_id=900)
	match.state = match.WAITING_REPORT
	return app, match, FakeCtx(match.qc)


@pytest.mark.parametrize("ranked", [True, False])
def test_mysql_rollback_at_each_write_and_retry(monkeypatch, ranked):
	async def run():
		async with live_database(monkeypatch) as (database, module):
			original_execute = module.Transaction.execute
			original_many = module.Transaction.executemany
			control = dict(count=0, fail_at=0)

			async def execute(tx, *args):
				control["count"] += 1
				if control["count"] == control["fail_at"]:
					raise ConnectionError("injected write failure")
				return await original_execute(tx, *args)

			async def many(tx, *args):
				control["count"] += 1
				if control["count"] == control["fail_at"]:
					raise ConnectionError("injected batch failure")
				return await original_many(tx, *args)

			monkeypatch.setattr(module.Transaction, "execute", execute)
			monkeypatch.setattr(module.Transaction, "executemany", many)
			for fail_at in range(1, (14 if ranked else 13) + 1):
				for table in ("matches", "match_players", "rating_history", "player_ratings"):
					await database.execute(f"TRUNCATE TABLE {table}")
				control.update(count=0, fail_at=fail_at)
				app, match, ctx = make_match(ranked=ranked)
				with pytest.raises(ConnectionError):
					await match.report_scores(ctx, [1, 0])
				assert match in app.active_matches and not match._result_committed
				for table in ("matches", "match_players", "rating_history", "player_ratings"):
					assert not await database.fetchall(f"SELECT * FROM {table}")
				control["fail_at"] = 0
				await match.report_scores(ctx, [1, 0])
				assert match not in app.active_matches
				assert len(await database.fetchall("SELECT * FROM match_players")) == 4
				assert len(await database.fetchall("SELECT * FROM rating_history")) == (4 if ranked else 0)
	asyncio.run(run())


def test_mysql_lost_ack_retry_and_stale_snapshot_recovery(monkeypatch, tmp_path):
	async def run():
		async with live_database(monkeypatch) as (database, _module):
			app, match, ctx = make_match()
			app.state_restored = True
			monkeypatch.setattr(state, "db", database)
			monkeypatch.setattr(state, "STATE_PATH", str(tmp_path / "state.json"))
			monkeypatch.setattr(state, "_last_db_payload", None)
			monkeypatch.setattr(state, "_last_local_payload", None)
			await state.save_state_if_changed(app)
			transaction = database.transaction

			@asynccontextmanager
			async def lose_ack():
				async with transaction() as tx:
					yield tx
				raise ConnectionError("lost commit acknowledgement")

			monkeypatch.setattr(database, "transaction", lose_ack)
			with pytest.raises(ConnectionError):
				await match.report_scores(ctx, [1, 0])
			monkeypatch.setattr(database, "transaction", transaction)
			before = await database.fetchall("SELECT * FROM player_ratings ORDER BY user_id")
			await match.report_scores(ctx, [1, 0])
			assert await database.fetchall("SELECT * FROM player_ratings ORDER BY user_id") == before
			assert len(await database.fetchall("SELECT * FROM rating_history")) == 4
			# The saved blob predates the committed result. A fresh application
			# must skip that match and keep its original ID reserved.
			fresh = Application(client=None)
			async def load_expire(_rows):
				pass
			monkeypatch.setattr(state.expire, "load_json", load_expire, raising=False)
			await state.load_state(fresh)
			assert fresh.state_restored and fresh.active_matches == []
			assert await stats.next_match() == 82
	asyncio.run(run())


@pytest.mark.parametrize("shared_players", [False, True])
def test_mysql_concurrent_reports_commit_without_lost_updates(monkeypatch, shared_players):
	async def run():
		async with live_database(monkeypatch) as (database, module):
			# Force the two transactions to read the absent result before either
			# inserts. This exposes MySQL gap-lock behavior hidden by SQLite.
			original = module.Transaction.fetchone
			arrived, release = 0, asyncio.Event()

			async def synchronize(tx, sql, args=None):
				nonlocal arrived
				row = await original(tx, sql, args)
				if "FROM matches WHERE match_id=" in sql:
					arrived += 1
					if arrived == 2:
						release.set()
					await asyncio.wait_for(release.wait(), timeout=5)
				return row

			monkeypatch.setattr(module.Transaction, "fetchone", synchronize)
			_app1, first, ctx1 = make_match(81)
			_app2, second, ctx2 = make_match(82, players_offset=0 if shared_players else 10)
			await asyncio.wait_for(asyncio.gather(
				first.report_scores(ctx1, [1, 0]), second.report_scores(ctx2, [1, 0])), timeout=15)
			assert len(await database.fetchall("SELECT * FROM matches")) == 2
			assert len(await database.fetchall("SELECT * FROM rating_history")) == 8
			players = await database.fetchall("SELECT * FROM player_ratings")
			assert all(p["wins"] + p["losses"] == (2 if shared_players else 1) for p in players)
	asyncio.run(run())


def test_mysql_cancelled_query_rolls_back_and_pool_remains_usable(monkeypatch):
	async def run():
		async with live_database(monkeypatch) as (database, _module):
			entered = asyncio.Event()

			async def write_then_wait():
				async with database.transaction() as tx:
					await tx.insert("match_counter", dict(next_id=999))
					entered.set()
					await tx.fetchone("SELECT SLEEP(30)")

			task = asyncio.create_task(write_then_wait())
			await entered.wait()
			await asyncio.sleep(0.1)
			task.cancel()
			with pytest.raises(asyncio.CancelledError):
				await task
			assert not await database.fetchall("SELECT * FROM match_counter")
			await stats.check_match_id_counter()
			assert await stats.next_match() == 0
	asyncio.run(run())
