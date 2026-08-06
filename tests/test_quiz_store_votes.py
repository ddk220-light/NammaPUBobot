# -*- coding: utf-8 -*-
"""Pins bot.quiz.store.create_post's contract for the poll re-render: every field
the card displays has to survive the round trip through quiz_posts, and this is
where difficulty and closes_at get pinned.

No pytest-asyncio in this repo (see tests/test_civ_stats.py's note), so the async
store calls are driven with asyncio.run() from plain `def test_...` -- an
`async def test_...` here would be silently skipped and falsely report as passing.

FakeDb mirrors tests/test_predictions_store.py's fake (a recorder monkeypatched
onto `store.db`), sized to what create_post actually calls: db.insert(table, d).
`inserted()` hands back the dicts recorded for a table, which is what this file's
assertions care about -- not the SQL, since db.insert's INSERT statement is
generated from the dict's own keys and has no decision in it to pin.

WHAT THE FAKE GREW FOR THE VOTE RACE. record_vote/record_vote_multi are no
longer bare upserts: they open a db.transaction(), re-read the post row `FOR
UPDATE`, re-check open/deadline under that lock and only then write. A fake that
ignored any of that would make the race tests below meaningless, so it models
three things it did not before -- `db.transaction()` (the FakeTx handle, same
shape as tests/test_predictions_gold.py's), a real `quiz_posts` table the lock
reads back and the clamp mutates, and an `on_lock` hook that fires WHILE a `FOR
UPDATE` read is being answered. That last one is the whole point: it is how the
fake represents "the row changed between the router's pre-read and this
transaction", which is exactly the interleaving the row lock exists to make
impossible to get away with."""
from __future__ import annotations

import asyncio

import pytest

from bot.quiz import store


class FakeTx:
	""" The connection-bound handle db.transaction() yields (see
	core/DBAdapters/mysql.py::Transaction): the same query surface as the
	adapter, sharing its tables, with execute/insert returning ROWCOUNTS. """

	def __init__(self, db):
		self.db = db

	async def fetchone(self, sql, args=None):
		return self.db._fetchone(sql, list(args or []))

	async def execute(self, sql, args=None):
		return await self.db.execute(sql, args)

	async def insert(self, table, d, on_duplicate=None):
		return await self.db.insert(table, d, on_duplicate)


class FakeDb:
	""" `calls` keeps the original (table, dict) shape `inserted()` relies on, so
	the create_post tests above are untouched. `_rows` is a real table keyed on
	(post_id, user_id), so a "replace" insert genuinely overwrites the prior row
	(mirroring MySQL's REPLACE INTO) instead of just accumulating history the
	way a bare append would -- which is what the changed-her-mind test needs to
	be able to fail. `posts` is quiz_posts, which the vote gate locks and reads
	and the clamp writes. `trace` is one ordered log of every statement, so
	"the lock came before the write" is assertable at all. """
	def __init__(self):
		self.calls = []  # [(table, dict)]
		self.executed = []  # [(sql, args)]
		self.trace = []  # ordered ("fetchone"|"execute"|"insert", sql|table, args|dict)
		self._rows = {}  # table -> {(post_id, user_id): dict}
		self.posts = {}  # quiz_posts, keyed on id
		self.on_lock = None  # fires once, inside the next FOR UPDATE read
		self.rolled_back = False

	# — the tables —
	def open_post(self, post_id, closes_at=9_000, status="open"):
		self.posts[post_id] = dict(id=post_id, status=status, closes_at=closes_at)
		return self.posts[post_id]

	async def insert(self, table, d, on_duplicate=None):
		self.calls.append((table, dict(d)))
		self.trace.append(("insert", table, dict(d)))
		if "post_id" in d and "user_id" in d:
			table_rows = self._rows.setdefault(table, {})
			key = (d["post_id"], d["user_id"])
			if on_duplicate == "ignore" and key in table_rows:
				pass  # existing row wins, mirrors INSERT IGNORE
			else:
				table_rows[key] = dict(d)
		return len(self.calls)

	async def execute(self, sql, args=None):
		args = list(args or [])
		self.executed.append((sql, args))
		self.trace.append(("execute", sql, args))
		if "UPDATE quiz_answers SET is_correct=" in sql:
			is_correct, post_id, user_id = args
			row = self._rows.get("quiz_answers", {}).get((post_id, user_id))
			if row is not None:
				row["is_correct"] = is_correct
		elif "UPDATE quiz_posts SET closes_at=LEAST" in sql:
			now, post_id = args
			row = self.posts.get(post_id)
			if row is not None:
				row["closes_at"] = min(int(row["closes_at"]), int(now))
		return 1

	def _fetchone(self, sql, args):
		self.trace.append(("fetchone", sql, args))
		if "FROM quiz_posts" not in sql:
			return None
		if self.on_lock is not None and "FOR UPDATE" in sql:
			# The other transaction committed while this one waited for the row.
			hook, self.on_lock = self.on_lock, None
			hook(self.posts)
		row = self.posts.get(args[0])
		return dict(row) if row is not None else None

	async def fetchone(self, sql, args=None):
		return self._fetchone(sql, list(args or []))

	async def select_one(self, _columns, table, where):
		"""What store.get_post -- the router's cheap pre-read -- goes through."""
		if table != "quiz_posts":
			return None
		row = self.posts.get(where["id"])
		return dict(row) if row is not None else None

	def transaction(self):
		fake = self

		class _Ctx:
			async def __aenter__(self):
				return FakeTx(fake)

			async def __aexit__(self, exc_type, exc, tb):
				if exc_type is not None:
					fake.rolled_back = True
				return False

		return _Ctx()

	# — the assertions —
	def inserted(self, table):
		return [d for t, d in self.calls if t == table]

	def row(self, table, **keys):
		return self._rows.get(table, {}).get((keys["post_id"], keys["user_id"]))

	def sql(self, fragment):
		"""Every recorded statement whose SQL contains `fragment`."""
		return [c for c in self.trace if c[0] in ("fetchone", "execute") and fragment in c[1]]


@pytest.fixture
def fake_db(monkeypatch):
	fake = FakeDb()
	monkeypatch.setattr(store, "db", fake)
	return fake


def _question(**overrides):
	q = dict(id="q1", category="combat", difficulty="medium", prompt="p",
			options=["a", "b"], correct_index=0, correct_indices=[0],
			explanation="e", seq=1, week=1, day=1, source="game")
	q.update(overrides)
	return q


def test_create_post_stores_difficulty(fake_db):
	q = _question(difficulty="medium")
	asyncio.run(store.create_post(123, q, 1000, 87400))

	row = fake_db.inserted("quiz_posts")[-1]
	assert row["difficulty"] == "medium"


def test_create_post_stores_closes_at_as_passed(fake_db):
	""" closes_at is the 24-hour lock deadline a later task gates voting on --
	it has to land in the row exactly as the caller computed it, not be
	recomputed or defaulted here. """
	q = _question()
	asyncio.run(store.create_post(123, q, 1000, 87400))

	row = fake_db.inserted("quiz_posts")[-1]
	assert row["closes_at"] == 87400


def test_create_post_stores_none_when_difficulty_is_absent(fake_db):
	""" A live-generated player question (bot/quiz/player_bank.py) carries no
	difficulty key at all -- create_post must read it with .get, not [], or
	every player-day quiz crashes at post time instead of storing NULL. """
	q = _question()
	del q["difficulty"]
	asyncio.run(store.create_post(123, q, 1000, 87400))

	row = fake_db.inserted("quiz_posts")[-1]
	assert row["difficulty"] is None


def test_record_vote_is_a_replace_upsert(fake_db):
	""" The PK (post_id, user_id) is the one-vote rule; REPLACE is what makes a
	changed mind overwrite the row instead of erroring or piling up a second
	one. Changing a vote must keep working for as long as the poll is genuinely
	open -- the transactional gate refuses late presses, never legitimate ones. """
	fake_db.open_post(9, closes_at=9_000)
	assert asyncio.run(store.record_vote(9, 1, "Ann", 2, 1000)) is True
	assert asyncio.run(store.record_vote(9, 1, "Ann", 0, 1001)) is True   # changed her mind
	row = fake_db.row("quiz_answers", post_id=9, user_id=1)
	assert row["choice_index"] == 0
	assert row["choice_indices"] is None
	assert row["is_correct"] is None                       # graded at lock, not at press
	assert row["answered_at"] == 1001
	assert row["revealed_at"] is None and row["response_ms"] is None


def test_record_vote_multi_stores_sorted_set(fake_db):
	fake_db.open_post(9, closes_at=9_000)
	assert asyncio.run(store.record_vote_multi(9, 1, "Ann", [2, 0], 1000)) is True
	row = fake_db.row("quiz_answers", post_id=9, user_id=1)
	assert row["choice_indices"] == "[0, 2]"
	assert row["choice_index"] is None


def test_write_grade(fake_db):
	fake_db.open_post(9, closes_at=9_000)
	asyncio.run(store.record_vote(9, 1, "Ann", 0, 1000))
	asyncio.run(store.write_grade(9, 1, True))
	assert fake_db.row("quiz_answers", post_id=9, user_id=1)["is_correct"] == 1


def test_write_grade_records_a_wrong_answer_as_wrong(fake_db):
	# Both verdicts, because write_grade decides the 50-vs-10 gold split: a
	# version that hardcoded "correct" passed the entire suite until this test
	# existed, and would have paid every voter the winning rate.
	fake_db.open_post(9, closes_at=9_000)
	asyncio.run(store.record_vote(9, 2, "Bob", 1, 1000))
	asyncio.run(store.write_grade(9, 2, False))
	assert fake_db.row("quiz_answers", post_id=9, user_id=2)["is_correct"] == 0


# ── the vote write re-checks the post under a row lock ───────────────────
# THE RESIDUAL RACE, and why a boolean return exists at all.
#
# The vote path used to be read-then-write across two round-trips:
# bot/quiz/interactions.py called store.get_post, checked `status == 'open' and
# now < closes_at`, and wrote later. A press whose get_post returned
# microseconds before bot/quiz/jobs.py::_reveal clamped the deadline passed that
# check and committed AFTER _reveal's vote snapshot. Reproduced timeline: press
# at T, get_post returns at T+2ms with the old closes_at, the clamp lands at
# T+3ms, the snapshot at T+5ms, the REPLACE commits at T+6ms. That vote is never
# graded (is_correct stays NULL), never paid its 10-or-50 gold, and close_post
# then puts the post beyond due_to_close forever -- with the ledger and the
# balance cache still in perfect agreement, so no reconciliation can ever see
# it. The change flavour is worse: the REPLACE resets is_correct to NULL after
# write_grade ran and after gold was paid on that verdict, so scoring.tally
# scores the user differently from what the payout assumed.
#
# The fix is the one bot/predictions/gold.py::place_bet already uses for money:
# gate and write are ONE transaction, the post row is re-read FOR UPDATE, and
# the open/deadline check is redone under that lock. Every test below fails if
# the in-transaction re-check is dropped.
def test_a_vote_on_a_genuinely_open_poll_still_lands(fake_db):
	fake_db.open_post(9, closes_at=9_000)
	assert asyncio.run(store.record_vote(9, 1, "Ann", 2, 1_000)) is True
	assert fake_db.row("quiz_answers", post_id=9, user_id=1)["choice_index"] == 2


def test_a_vote_whose_transaction_runs_after_the_clamp_is_refused(fake_db):
	""" The pre-read saw an open poll; the resolve clamped before the write. """
	fake_db.open_post(9, closes_at=9_000)
	pre = asyncio.run(store.get_post(9))                     # the router's cheap fast path
	assert pre["status"] == "open" and int(pre["closes_at"]) > 1_000, "the pre-read passed"

	asyncio.run(store.clamp_closes_at(9, 1_000))             # _reveal, in between

	assert asyncio.run(store.record_vote(9, 1, "Ann", 2, 1_000)) is False
	assert fake_db.row("quiz_answers", post_id=9, user_id=1) is None
	assert fake_db.inserted("quiz_answers") == [], "no row may be written at all"


def test_a_clamp_that_wins_the_row_lock_refuses_the_waiting_press(fake_db):
	""" The same race at its tightest: the clamp commits WHILE this transaction
	is waiting for the row, so the press's own locked read is the first thing
	that can see it. `on_lock` fires inside the FOR UPDATE read, which is the
	only honest way for a single-threaded fake to represent that interleaving --
	and a version that read the row before locking it, or did not re-read at
	all, cannot survive it. """
	fake_db.open_post(9, closes_at=9_000)
	assert asyncio.run(store.get_post(9))["status"] == "open"
	fake_db.on_lock = lambda posts: posts[9].update(closes_at=1_000)

	assert asyncio.run(store.record_vote(9, 1, "Ann", 2, 1_000)) is False
	assert fake_db.row("quiz_answers", post_id=9, user_id=1) is None


def test_a_multi_vote_whose_transaction_runs_after_the_clamp_is_refused(fake_db):
	fake_db.open_post(9, closes_at=9_000)
	pre = asyncio.run(store.get_post(9))
	assert pre["status"] == "open" and int(pre["closes_at"]) > 1_000

	asyncio.run(store.clamp_closes_at(9, 1_000))

	assert asyncio.run(store.record_vote_multi(9, 1, "Ann", [2, 0], 1_000)) is False
	assert fake_db.row("quiz_answers", post_id=9, user_id=1) is None
	assert fake_db.inserted("quiz_answers") == []


def test_a_vote_change_after_the_clamp_cannot_undo_the_grade(fake_db):
	""" THE SECOND FLAVOUR, and the one no "did a row appear?" assertion catches:
	the voter already has a row, it has already been graded, and gold has
	already been paid on that verdict. An accepted REPLACE here would silently
	reset is_correct to NULL and rewrite the choice, leaving the weekly
	scoring.tally scoring them differently from what the payout assumed. """
	fake_db.open_post(9, closes_at=9_000)
	assert asyncio.run(store.record_vote(9, 1, "Ann", 2, 1_000)) is True
	asyncio.run(store.write_grade(9, 1, True))               # graded, and paid 50 on this
	asyncio.run(store.clamp_closes_at(9, 1_500))             # the resolve shuts the door

	assert asyncio.run(store.record_vote(9, 1, "Ann", 0, 1_500)) is False

	row = fake_db.row("quiz_answers", post_id=9, user_id=1)
	assert row["choice_index"] == 2, "the graded choice must survive untouched"
	assert row["is_correct"] == 1, "the verdict gold was paid on must not be reset"
	assert row["answered_at"] == 1_000


def test_a_vote_change_refused_on_the_multi_path_too(fake_db):
	fake_db.open_post(9, closes_at=9_000, status="closed")
	fake_db._rows.setdefault("quiz_answers", {})[(9, 1)] = dict(
		post_id=9, user_id=1, choice_index=None, choice_indices="[0, 2]",
		is_correct=1, answered_at=1_000)

	assert asyncio.run(store.record_vote_multi(9, 1, "Ann", [1], 1_100)) is False

	row = fake_db.row("quiz_answers", post_id=9, user_id=1)
	assert row["choice_indices"] == "[0, 2]" and row["is_correct"] == 1


def test_a_press_on_a_closed_post_is_refused_by_the_status_too(fake_db):
	# The deadline is the load-bearing half (status flips last), but a post the
	# resolve already closed must be refused on the status alone.
	fake_db.open_post(9, closes_at=9_000, status="closed")
	assert asyncio.run(store.record_vote(9, 1, "Ann", 2, 1_000)) is False
	assert fake_db.inserted("quiz_answers") == []


def test_a_vote_on_a_post_that_does_not_exist_is_refused(fake_db):
	assert asyncio.run(store.record_vote(404, 1, "Ann", 2, 1_000)) is False
	assert fake_db.inserted("quiz_answers") == []


def test_the_vote_gate_locks_the_post_row_before_it_writes(fake_db):
	""" `FOR UPDATE` is what serialises the press against clamp_closes_at's
	write to the same row. A plain SELECT reads a snapshot and loses exactly the
	race it is here to win -- and nothing else in this suite can tell the two
	apart, because a single-threaded fake answers both identically. Pinned on
	the SQL text, the way tests/test_predictions_gold.py pins place_bet's. """
	fake_db.open_post(9, closes_at=9_000)
	asyncio.run(store.record_vote(9, 1, "Ann", 2, 1_000))

	locks = fake_db.sql("FOR UPDATE")
	assert len(locks) == 1
	_kind, sql, args = locks[0]
	assert "quiz_posts" in sql and "status" in sql and "closes_at" in sql
	assert args == [9]

	writes = [n for n, c in enumerate(fake_db.trace) if c[0] == "insert"]
	assert writes and min(writes) > fake_db.trace.index(locks[0])


def test_the_multi_vote_gate_locks_the_post_row_too(fake_db):
	fake_db.open_post(9, closes_at=9_000)
	asyncio.run(store.record_vote_multi(9, 1, "Ann", [0], 1_000))

	locks = fake_db.sql("FOR UPDATE")
	assert len(locks) == 1 and "quiz_posts" in locks[0][1] and locks[0][2] == [9]
	writes = [n for n, c in enumerate(fake_db.trace) if c[0] == "insert"]
	assert writes and min(writes) > fake_db.trace.index(locks[0])


# ── the clamp takes the same lock ───────────────────────────────────────
def test_the_clamp_takes_the_same_row_lock_before_it_moves_the_deadline(fake_db):
	""" Both halves of the mechanism have to lock the SAME row or they do not
	serialise at all: the clamp would commit under a press that is mid-flight,
	and _reveal's snapshot -- taken after the clamp returns -- could still miss
	a vote that lands afterwards. """
	fake_db.open_post(9, closes_at=9_000)
	asyncio.run(store.clamp_closes_at(9, 1_000))

	locks = fake_db.sql("FOR UPDATE")
	assert len(locks) == 1 and "quiz_posts" in locks[0][1] and locks[0][2] == [9]
	clamp = fake_db.sql("SET closes_at=LEAST")
	assert len(clamp) == 1
	assert fake_db.trace.index(locks[0]) < fake_db.trace.index(clamp[0])
	assert fake_db.posts[9]["closes_at"] == 1_000


def test_the_clamp_never_pushes_a_deadline_forward(fake_db):
	# LEAST(closes_at, now), not an assignment: _close_due re-enters _reveal for
	# posts already days past their deadline, and moving closes_at up to now
	# would re-open a poll that has been shut for a week.
	fake_db.open_post(9, closes_at=1_000)
	asyncio.run(store.clamp_closes_at(9, 9_000))
	assert fake_db.posts[9]["closes_at"] == 1_000
