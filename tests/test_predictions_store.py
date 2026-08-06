"""The SQL bot/predictions/store.py actually issues.

Everything else in this package is tested through its decisions; these three
statements have no decisions in them, which is exactly why they were the ones
nothing defended:

* `close_betting` is a COMPARE-AND-SET. Without `AND status='open'` it stops
  being the thing that decides which of two racing sweeps owns a book, and
  without the transaction it cannot answer with a rowcount at all (the
  autocommit adapter's execute() returns lastrowid, which for an UPDATE says
  nothing).
* `unsettled_books` is the resume queue. Its JOIN to `matches` IS its
  definition of "stale" — a frozen post is normal for the whole length of a
  match — so a version of it that dropped the join would either resume nothing
  or settle books whose matches are still being played.
* the leaderboard union carries the `prediction_bets` half. Deleting it left
  1702 tests green while no gold bet counted toward prediction accuracy, which
  the design lists as a hard requirement.

Driven against a recorder rather than asserted as constants: what ships to
MySQL is the executed statement, not the source line.
"""
from __future__ import annotations

import asyncio

from bot.predictions import store


class FakeTx:
	def __init__(self, db):
		self.db = db

	async def execute(self, sql, args=None):
		self.db.calls.append(("execute", sql, list(args or [])))
		return self.db.rowcount

	async def fetchone(self, sql, args=None):
		self.db.calls.append(("fetchone", sql, list(args or [])))
		return self.db.rows[0] if self.db.rows else None


class FakeDb:
	def __init__(self, rows=(), rowcount=1):
		self.calls = []
		self.rows = list(rows)
		self.rowcount = rowcount
		self.committed = False

	def transaction(self):
		fake = self

		class _Ctx:
			async def __aenter__(self):
				return FakeTx(fake)

			async def __aexit__(self, exc_type, _exc, _tb):
				fake.committed = exc_type is None
				return False
		return _Ctx()

	async def fetchall(self, sql, args=None):
		self.calls.append(("fetchall", sql, list(args or [])))
		return list(self.rows)

	async def fetchone(self, sql, args=None):
		self.calls.append(("fetchone", sql, list(args or [])))
		return self.rows[0] if self.rows else None


def use_fake(monkeypatch, **kwargs):
	fake = FakeDb(**kwargs)
	monkeypatch.setattr(store, "db", fake)
	return fake


class TestCloseBetting:
	def test_it_is_a_compare_and_set_on_the_open_status(self, monkeypatch):
		fake = use_fake(monkeypatch)
		assert asyncio.run(store.close_betting(12)) is True

		kind, sql, args = fake.calls[0]
		assert kind == "execute"
		assert "UPDATE prediction_posts" in sql
		assert "status='frozen'" in sql
		assert "WHERE id=%s AND status='open'" in sql, (
			"an unconditional flip lets two sweeps both believe they own the book")
		assert args == [12]
		assert fake.committed

	def test_a_post_that_was_not_open_answers_false(self, monkeypatch):
		""" Zero rows matched = somebody else already closed it. The caller reads
		that as "not mine" and does nothing, which is what stops a double
		settlement and a duplicated betting report. """
		use_fake(monkeypatch, rowcount=0)
		assert asyncio.run(store.close_betting(12)) is False

	def test_it_runs_in_a_transaction_for_the_rowcount(self, monkeypatch):
		""" The autocommit adapter's execute() answers lastrowid; only the
		transaction handle answers affected rows, and the affected-row count is
		the entire return value here. """
		fake = use_fake(monkeypatch)
		asyncio.run(store.close_betting(12))
		assert [c[0] for c in fake.calls] == ["execute"], "no autocommit path"
		assert fake.committed


class TestClaimTerminal:
	""" The statement that stops a refunded book being paid out as well.

	It has to do BOTH things at once — close the book and stake the branch —
	because either half alone is the bug: a close without a claim is what
	shipped (and paid the pot twice), and a claim without the close would let
	a press land on a book already snapshotted for refund. """

	def test_the_claim_and_the_close_are_one_compare_and_set(self, monkeypatch):
		fake = use_fake(monkeypatch)
		assert asyncio.run(store.claim_terminal(12, "void")) == "void"

		assert len(fake.calls) == 1, "a second statement is a second chance to lose the race"
		kind, sql, args = fake.calls[0]
		assert kind == "execute"
		assert "status='frozen'" in sql, "the claim is also the close"
		assert "terminal_intent=%s" in sql
		assert "terminal_intent IS NULL" in sql, (
			"without this the second actor overwrites the first actor's branch")
		assert "status IN ('open','frozen')" in sql, (
			"a post that already went terminal must not be re-claimed")
		assert args == ["void", 12]
		assert fake.committed

	def test_losing_the_claim_answers_the_branch_that_won(self, monkeypatch):
		""" Zero rows matched: somebody else staked this post. The caller
		compares that answer with its own intent and stands down — which is the
		entire fix for the double payout. """
		fake = use_fake(monkeypatch, rowcount=0,
						rows=[{"status": "frozen", "terminal_intent": "void"}])
		assert asyncio.run(store.claim_terminal(12, "settle")) == "void"

		_kind, sql, args = fake.calls[1]
		assert "FOR UPDATE" in sql, (
			"a snapshot read here can still say NULL, and then both branches proceed")
		assert args == [12]

	def test_a_post_already_past_terminal_answers_nothing(self, monkeypatch):
		use_fake(monkeypatch, rowcount=0, rows=[{"status": "resolved", "terminal_intent": "settle"}])
		assert asyncio.run(store.claim_terminal(12, "void")) is None

	def test_a_deleted_post_answers_nothing(self, monkeypatch):
		use_fake(monkeypatch, rowcount=0, rows=[])
		assert asyncio.run(store.claim_terminal(12, "void")) is None

	def test_only_the_three_real_branches_can_be_staked(self, monkeypatch):
		""" The value is dispatched on by every resume path, so a typo'd intent
		would silently become "nothing is staked" and re-open the hole. """
		use_fake(monkeypatch)
		for bad in ("resolved", "voided", "", None):
			try:
				asyncio.run(store.claim_terminal(12, bad))
			except ValueError:
				continue
			raise AssertionError(f"{bad!r} was accepted as a terminal branch")
		assert set(store.TERMINAL_INTENTS) == {"settle", "void", "no_action"}


class TestUnsettledBooks:
	def test_the_queue_is_frozen_posts_whose_match_already_reported(self, monkeypatch):
		fake = use_fake(monkeypatch, rows=[{"id": 12, "match_winner": 1}])
		rows = asyncio.run(store.unsettled_books(1_700))

		assert rows == [{"id": 12, "match_winner": 1}]
		_kind, sql, args = fake.calls[0]
		assert "JOIN matches m ON m.match_id = p.match_id" in sql, (
			"the join is the definition of stale; without it every live match qualifies")
		assert "p.status='frozen'" in sql
		assert "m.reported_at<=%s" in sql
		assert "m.winner AS match_winner" in sql, "the resumer has no Match object left to ask"
		assert "LIMIT" in sql
		assert args == [1_700]

	def test_nothing_stranded_is_an_empty_list(self, monkeypatch):
		use_fake(monkeypatch, rows=[])
		assert asyncio.run(store.unsettled_books(1_700)) == []


class TestAbandonedBooks:
	def test_the_queue_is_frozen_posts_with_no_match_row_at_all(self, monkeypatch):
		fake = use_fake(monkeypatch, rows=[{"id": 12}])
		rows = asyncio.run(store.abandoned_books(1_700))

		assert rows == [{"id": 12}]
		_kind, sql, args = fake.calls[0]
		assert "LEFT JOIN matches m ON m.match_id = p.match_id" in sql
		assert "m.match_id IS NULL" in sql, (
			"an inner join here would refund every book whose match DID report")
		assert "p.status='frozen'" in sql and "p.freezes_at<=%s" in sql
		assert args == [1_700]


class TestMatchPlayerIds:
	def test_it_returns_bare_ids(self, monkeypatch):
		fake = use_fake(monkeypatch, rows=[{"user_id": 1}, {"user_id": 2}])
		assert asyncio.run(store.match_player_ids(77)) == [1, 2]
		assert fake.calls[0][2] == [77]


class TestLeaderboard:
	def test_gold_bets_count_toward_prediction_accuracy(self, monkeypatch):
		""" Placing a bet IS the user's prediction — the design's reason the
		accuracy leaderboard survived the cutover from free votes. Both halves
		of the union have to be in the statement that ships. """
		fake = use_fake(monkeypatch, rows=[])
		asyncio.run(store.leaderboard())

		sql = fake.calls[0][1]
		assert "prediction_bets" in sql, "no gold bet would ever count"
		assert "prediction_votes" in sql, "votes-era history still counts"
		assert "UNION ALL" in sql
		assert "b.side = p.winner_idx" in sql, "a bet grades against the recorded winner"
		assert sql.count("p.status='resolved'") == 2, "both halves ignore unsettled posts"

	def test_a_channel_filter_is_parameterised(self, monkeypatch):
		fake = use_fake(monkeypatch, rows=[])
		asyncio.run(store.leaderboard(900))
		_kind, sql, args = fake.calls[0]
		assert "WHERE channel_id=%s" in sql and args == [900]

	def test_user_stats_reads_the_same_union(self, monkeypatch):
		fake = use_fake(monkeypatch, rows=[{"correct": 3, "total": 4}])
		assert asyncio.run(store.user_stats(42, 900)) == (3, 4)
		_kind, sql, args = fake.calls[0]
		assert "prediction_bets" in sql and "prediction_votes" in sql
		assert args == [42, 900]
