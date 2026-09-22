"""Real report/rating code over a disposable transactional SQL database.

SQLite verifies rollback/data outcomes without production credentials. Its SQL
bridge strips MySQL row locks; MySQL locking/driver contracts are tested in the
adapter suite, not claimed to be simulated by SQLite.
"""
import asyncio
import ast
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from nammaoe2bot.app import Application
from nammaoe2bot.exceptions import Exceptions as Exc
from nammaoe2bot.pickup import stats
from nammaoe2bot.runtime.paths import REPO_ROOT
from nammaoe2bot.runtime.utils import find
from tests.test_match_lifecycle_e2e import FakeCtx, build_match


def _rating_class():
	# Execute the production classes intact, without importing the unrelated
	# glicko2/trueskill engines (CI intentionally installs pytest only).
	path = Path(REPO_ROOT) / "nammaoe2bot/pickup/rating.py"
	tree = ast.parse(path.read_text())
	classes = [node for node in tree.body if isinstance(node, ast.ClassDef)
		and node.name in ("BaseRating", "AoE2Rating")]
	namespace = {"find": find}
	exec(compile(ast.Module(body=classes, type_ignores=[]), str(path), "exec"), namespace)
	return namespace["AoE2Rating"]


class SQLDatabase:

	def __init__(self):
		self.conn = sqlite3.connect(":memory:", isolation_level=None)
		self.conn.row_factory = sqlite3.Row
		self.conn.executescript("""
			CREATE TABLE matches (match_id INTEGER PRIMARY KEY, channel_id, queue_id,
			 queue_name, alpha_name, beta_name, reported_at, ranked, winner, alpha_score, beta_score, maps);
			CREATE TABLE match_players (match_id, channel_id, user_id, nick, team,
			 PRIMARY KEY(match_id, user_id));
			CREATE TABLE player_ratings (channel_id, user_id, nick, rating, deviation,
			 wins DEFAULT 0, losses DEFAULT 0, draws DEFAULT 0, streak DEFAULT 0,
			 last_ranked_match_at, PRIMARY KEY(user_id, channel_id));
			CREATE TABLE rating_history (channel_id, user_id, at, rating_before, rating_change,
			 deviation_before, deviation_change, match_id, reason);
		""")
		self.fail_at = None
		self.writes = 0
		self.lose_ack = False
		self.lock = asyncio.Lock()
		self.in_transaction = False

	@asynccontextmanager
	async def transaction(self):
		async with self.lock:
			self.in_transaction = True
			self.conn.execute("BEGIN")
			try:
				yield self
			except BaseException:
				self.conn.rollback()
				raise
			else:
				self.conn.commit()
			finally:
				self.in_transaction = False
			if self.lose_ack:
				self.lose_ack = False
				raise ConnectionError("commit succeeded, acknowledgement lost")

	async def fetchall(self, sql, args=()):
		return [dict(row) for row in self.conn.execute(sql.replace("%s", "?").replace(" FOR UPDATE", ""), args)]

	async def fetchone(self, sql, args=()):
		rows = await self.fetchall(sql, args)
		return rows[0] if rows else None

	def _write(self, sql, args):
		assert self.in_transaction, "a report write escaped its transaction"
		self.writes += 1
		if self.writes == self.fail_at:
			raise ConnectionError("injected write failure")
		return self.conn.execute(sql, args).rowcount

	async def insert(self, table, row, on_duplicate=None):
		mode = " OR IGNORE" if on_duplicate in ("ignore", "keep") else ""
		return self._write(f"INSERT{mode} INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", list(row.values()))

	async def insert_many(self, table, rows, on_duplicate=None):
		for row in rows:
			await self.insert(table, row, on_duplicate)

	async def update(self, table, row, keys):
		return self._write(f"UPDATE {table} SET " + ",".join(f"{key}=?" for key in row)
			+ " WHERE " + " AND ".join(f"{key}=?" for key in keys), [*row.values(), *keys.values()])

	def rows(self, table):
		return [dict(row) for row in self.conn.execute(f"SELECT * FROM {table}")]


def setup_report(monkeypatch, ranked=True, rating_channel=900):
	database = SQLDatabase()
	monkeypatch.setattr(stats, "db", database)
	app = Application(client=None)
	match = build_match(app, ranked=ranked)
	match.qc.rating = _rating_class()(channel_id=rating_channel)
	match.state = match.WAITING_REPORT
	return database, app, match, FakeCtx(match.qc)


@pytest.mark.parametrize("ranked,write_count", [(True, 17), (False, 13)])
def test_failure_at_every_write_rolls_back_and_keeps_match_retryable(monkeypatch, ranked, write_count):
	for fail_at in range(1, write_count + 1):
		database, app, match, ctx = setup_report(monkeypatch, ranked)
		database.fail_at = fail_at
		with pytest.raises(ConnectionError):
			asyncio.run(match.report_scores(ctx, [1, 0]))
		assert match in app.active_matches
		assert not match._result_committed
		assert match.scores == [0, 0]
		for table in ("matches", "match_players", "player_ratings", "rating_history"):
			assert database.rows(table) == [], (fail_at, table)
		database.fail_at = None
		asyncio.run(match.report_scores(ctx, [1, 0]))
		assert match not in app.active_matches
		assert len(database.rows("matches")) == 1
		assert len(database.rows("match_players")) == 4
		assert len(database.rows("rating_history")) == (4 if ranked else 0)


def test_lost_commit_acknowledgement_is_safe_to_retry(monkeypatch):
	database, app, match, ctx = setup_report(monkeypatch)
	database.lose_ack = True
	with pytest.raises(ConnectionError):
		asyncio.run(match.report_scores(ctx, [1, 0]))
	assert match in app.active_matches
	before = database.rows("player_ratings")
	write_count = database.writes
	asyncio.run(match.report_scores(ctx, [1, 0]))
	assert match not in app.active_matches
	assert database.rows("player_ratings") == before
	assert database.writes == write_count + 1  # Only the no-op match claim; no rating writes.
	assert len(database.rows("rating_history")) == 4


def test_conflicting_retry_does_not_change_the_committed_result(monkeypatch):
	database, app, match, ctx = setup_report(monkeypatch)
	database.lose_ack = True
	with pytest.raises(ConnectionError):
		asyncio.run(match.report_scores(ctx, [1, 0]))
	before = database.rows("player_ratings")
	with pytest.raises(Exc.ValueError, match="different recorded result"):
		asyncio.run(match.report_scores(ctx, [0, 1]))
	assert database.rows("player_ratings") == before
	assert match in app.active_matches


def test_notification_failures_do_not_undo_or_repeat_ratings(monkeypatch):
	database, app, match, ctx = setup_report(monkeypatch)
	events = []

	async def fail(*_args):
		assert not database.in_transaction
		raise RuntimeError("Discord unavailable")

	async def finished(*_args):
		events.append("finished")

	monkeypatch.setattr(match.qc, "update_rating_roles", fail)
	monkeypatch.setattr(match, "print_rating_results", fail)
	app.match_events.on("finished", finished)
	asyncio.run(match.report_scores(ctx, [1, 0]))
	before = database.rows("player_ratings")
	asyncio.run(match.report_scores(ctx, [0, 1]))
	assert database.rows("player_ratings") == before
	assert match not in app.active_matches
	assert events == ["finished"]


def test_shared_rating_channel_and_multigame_score_keep_algorithm(monkeypatch):
	database, _app, match, ctx = setup_report(monkeypatch, rating_channel=901)
	asyncio.run(match.report_scores(ctx, [2, 1]))
	assert len(database.rows("player_ratings")) == 8
	assert {p["channel_id"] for p in database.rows("rating_history")} == {901}
	assert {p["channel_id"] for p in database.rows("match_players")} == {900}
	for p in database.rows("player_ratings"):
		if p["channel_id"] == 901:
			assert p["wins"] + p["losses"] == 3
		else:
			assert p["rating"] is None


def test_concurrent_commands_and_cancel_cannot_mutate_a_pending_report(monkeypatch):
	database, app, match, ctx = setup_report(monkeypatch)
	original = database.insert

	async def run():
		entered, release = asyncio.Event(), asyncio.Event()

		async def pause(table, *args, **kwargs):
			if table == "matches":
				entered.set()
				await release.wait()
			return await original(table, *args, **kwargs)

		monkeypatch.setattr(database, "insert", pause)
		first = asyncio.create_task(match.report_scores(ctx, [1, 0]))
		await entered.wait()
		assert match in app.active_matches
		with pytest.raises(Exc.MatchStateError):
			await match.report_scores(ctx, [0, 1])
		with pytest.raises(Exc.MatchStateError):
			await match.cancel(ctx)
		with pytest.raises(Exc.MatchStateError):
			await match.draft.put(ctx, match.players[0], "Beta")
		release.set()
		await first

	asyncio.run(run())
	assert database.rows("matches")[0]["alpha_score"] == 1
	assert match not in app.active_matches


def test_cancellation_rolls_back_and_leaves_a_retryable_match(monkeypatch):
	database, app, match, ctx = setup_report(monkeypatch)
	original = database.insert

	async def run():
		entered = asyncio.Event()

		async def pause(table, *args, **kwargs):
			if table == "rating_history":
				entered.set()
				await asyncio.Event().wait()
			return await original(table, *args, **kwargs)

		monkeypatch.setattr(database, "insert", pause)
		task = asyncio.create_task(match.report_scores(ctx, [1, 0]))
		await entered.wait()
		task.cancel()
		with pytest.raises(asyncio.CancelledError):
			await task

	asyncio.run(run())
	assert match in app.active_matches
	assert not match._reporting and not match._finishing
	assert database.rows("matches") == database.rows("player_ratings") == []


def test_report_cannot_race_a_cancellation_already_in_progress(monkeypatch):
	database, app, match, ctx = setup_report(monkeypatch)

	async def run():
		entered, release = asyncio.Event(), asyncio.Event()

		async def notice(*_args):
			entered.set()
			await release.wait()

		monkeypatch.setattr(ctx, "notice", notice)
		task = asyncio.create_task(match.cancel(ctx))
		await entered.wait()
		with pytest.raises(Exc.MatchStateError):
			await match.report_scores(ctx, [1, 0])
		release.set()
		await task
		with pytest.raises(Exc.MatchStateError):
			await match.report_scores(ctx, [1, 0])

	asyncio.run(run())
	assert match not in app.active_matches
	assert database.rows("matches") == []


def test_failed_unranked_completion_stays_reportable_without_tick_retries(monkeypatch):
	database, app, match, ctx = setup_report(monkeypatch, ranked=False)
	match.state = match.CHECK_IN
	database.fail_at = 1
	with pytest.raises(ConnectionError):
		asyncio.run(match.finish_match(ctx))
	assert match.state == match.WAITING_REPORT
	assert match in app.active_matches
	asyncio.run(match.think(match.start_time + 1))
	assert database.writes == 1
	database.fail_at = None
	asyncio.run(match.report_scores(ctx, [0, 0]))
	assert match not in app.active_matches


def test_ranked_draw_records_each_player_once(monkeypatch):
	database, _app, match, ctx = setup_report(monkeypatch)
	asyncio.run(match.report_scores(ctx, [0, 0]))
	assert database.rows("matches")[0]["winner"] is None
	assert all(row["draws"] == 1 for row in database.rows("player_ratings"))
	assert len(database.rows("rating_history")) == 4
