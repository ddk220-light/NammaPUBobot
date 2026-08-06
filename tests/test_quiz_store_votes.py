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
UPDATE`, re-evaluate the gate under that lock AGAINST A CLOCK THEY READ
THEMSELVES, and only then write. A fake that ignored any of that would make the
race tests below meaningless, so it models four things it did not before:

  * `db.transaction()` (the FakeTx handle, same shape as
    tests/test_predictions_gold.py's);
  * a real `quiz_posts` table the lock reads back and the clamp mutates;
  * a WALL CLOCK (`fake.clock`) that `bot.quiz.store` reads through `time.time`,
    and that the tests move forward -- because the bug this file exists to
    catch is entirely about WHEN the clock is read, and a frozen clock cannot
    express it;
  * an `on_lock` hook that fires WHILE a `FOR UPDATE` read is being answered.
    That last one is the whole point: it is how the fake represents "the row
    changed, and time passed, while this transaction waited for the row",
    which is exactly the interleaving the row lock exists to make impossible to
    get away with.

AND WHY THE TRACE IS SCOPED. Every recorded statement carries the scope it was
issued on -- `"tx#n"` for a transaction-bound handle, `"db"` for the bare
adapter. Without that, this fake could not tell the two apart at all (FakeTx
used to just delegate to FakeDb), and swapping any `tx.fetchone`/`tx.insert`
for the bare `db.*` equivalent -- which in production means LEAVING THE
TRANSACTION, running on a different pooled connection where `FOR UPDATE` locks
nothing anybody else will ever wait on -- left the entire suite green. The
scope tag makes "this statement was inside the transaction" assertable, and
`_refuse_bare` makes a bare call issued while one of the module's own
transactions is open an outright error, since that is never anything but a bug.
"""
from __future__ import annotations

import asyncio
import inspect
import types

import pytest

from bot.quiz import store


class OutsideTransaction(AssertionError):
	"""A statement went to the bare adapter while the code under test had a
	transaction of its own open.

	In production `db.fetchone` / `db.execute` / `db.insert` check out their
	own connection from the aiomysql pool, so such a statement is not merely
	untidy -- it runs OUTSIDE the transaction, autocommitted, and any `FOR
	UPDATE` in it takes a lock that is released the instant the statement
	returns. Every guarantee the surrounding `async with db.transaction()`
	claims is void. There is no legitimate reason to do it, so the fake
	refuses rather than recording it."""


class Clock:
	"""The wall clock `bot.quiz.store` reads -- deliberately NOT the caller's.

	Held by the fake DB because it belongs to the same fake world the rows do:
	a press's timeline (arrived, queued for the row, granted the row) is a
	sequence of clock readings interleaved with statements, and the two have to
	be scriptable together. `at()` moves it FORWARD; nothing here ever rewinds
	it, because nothing in production can."""

	def __init__(self, t=1_000):
		self.t = int(t)

	def __call__(self):
		return self.t

	def at(self, t):
		assert int(t) >= self.t, "the wall clock does not run backwards"
		self.t = int(t)
		return self.t


class FakeTx:
	""" The connection-bound handle db.transaction() yields (see
	core/DBAdapters/mysql.py::Transaction): the same query surface as the
	adapter, sharing its tables, with execute/insert returning ROWCOUNTS.

	It has an IDENTITY -- `scope`, e.g. "tx#1" -- and stamps it on everything
	it records. Two statements carrying the same scope were issued on the same
	transaction; a statement carrying "db" was not in one at all. That
	distinction is the only thing standing between "the gate and the write are
	atomic" and a comment claiming they are. """

	def __init__(self, db, scope):
		self.db = db
		self.scope = scope

	async def fetchone(self, sql, args=None):
		return self.db._fetchone(self.scope, sql, list(args or []))

	async def execute(self, sql, args=None):
		return self.db._execute(self.scope, sql, list(args or []))

	async def insert(self, table, d, on_duplicate=None):
		return self.db._insert(self.scope, table, d, on_duplicate)


class FakeDb:
	""" `calls` keeps the original (table, dict) shape `inserted()` relies on, so
	the create_post tests above are untouched. `_rows` is a real table keyed on
	(post_id, user_id), so a "replace" insert genuinely overwrites the prior row
	(mirroring MySQL's REPLACE INTO) instead of just accumulating history the
	way a bare append would -- which is what the changed-her-mind test needs to
	be able to fail. `posts` is quiz_posts, which the vote gate locks and reads
	and the clamp writes. `trace` is one ordered log of every statement AND THE
	SCOPE IT RAN ON, so both "the lock came before the write" and "the write was
	inside the transaction" are assertable at all. """

	def __init__(self):
		self.calls = []  # [(table, dict)]
		self.executed = []  # [(sql, args)]
		self.trace = []  # ordered (scope, "fetchone"|"execute"|"insert"|"fetchall", target, payload)
		self._rows = {}  # table -> {(post_id, user_id): dict}
		self.posts = {}  # quiz_posts, keyed on id
		self.on_lock = None  # fires once, inside the next FOR UPDATE read
		self.rolled_back = False
		self.clock = Clock()
		self._open_tx = 0
		self._tx_seq = 0

	# — the tables —
	def open_post(self, post_id, closes_at=9_000, status="open"):
		self.posts[post_id] = dict(id=post_id, status=status, closes_at=closes_at)
		return self.posts[post_id]

	def answer_row(self, **row):
		"""Put a quiz_answers row in place directly (a vote from a previous run,
		or a reveal-era ghost with answered_at NULL)."""
		self._rows.setdefault("quiz_answers", {})[(row["post_id"], row["user_id"])] = dict(row)
		return row

	def clamp_wins_the_lock(self, post_id, closes_at, granted_at):
		"""Script THE interleaving, the one the whole mechanism exists for.

		The press's transaction is already open and queued behind the resolve.
		The clamp commits -- pulling the post's deadline back to `closes_at` --
		and only then is the press granted the row, at `granted_at`, a strictly
		LATER instant because it spent the wait blocked.

		The two timestamps differing, and in that direction, is the entire
		test. A gate judging with a clock read before the wait (the router's
		`now`, or any advisory parameter carrying it down) compares the press's
		ARRIVAL against a deadline set after it, finds `now < closes_at`, and
		writes anyway -- after the resolve's vote snapshot, never graded, never
		paid, and invisible to reconcile(). Equal timestamps hide this
		completely: `now < closes_at` is false by equality, so the broken gate
		refuses too and the test passes for the wrong reason."""

		def _hook(posts):
			posts[post_id]["closes_at"] = closes_at
			self.clock.at(granted_at)

		self.on_lock = _hook

	# — the scope guard —
	def _refuse_bare(self, kind, target):
		if self._open_tx:
			raise OutsideTransaction(
				f"{kind} {target!r} went to the bare adapter while a transaction was open — "
				"in production that is a different pooled connection, outside the "
				"transaction, and any FOR UPDATE in it locks nothing")

	# — the statements (scope-tagged; FakeTx and the bare adapter share them) —
	def _insert(self, scope, table, d, on_duplicate=None):
		self.calls.append((table, dict(d)))
		self.trace.append((scope, "insert", table, dict(d)))
		if "post_id" in d and "user_id" in d:
			table_rows = self._rows.setdefault(table, {})
			key = (d["post_id"], d["user_id"])
			if on_duplicate == "ignore" and key in table_rows:
				pass  # existing row wins, mirrors INSERT IGNORE
			else:
				table_rows[key] = dict(d)
		return len(self.calls)

	def _execute(self, scope, sql, args):
		self.executed.append((sql, args))
		self.trace.append((scope, "execute", sql, args))
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

	def _fetchone(self, scope, sql, args):
		self.trace.append((scope, "fetchone", sql, args))
		if "FROM quiz_posts" not in sql:
			return None
		if self.on_lock is not None and "FOR UPDATE" in sql:
			# The other transaction committed, and time passed, while this one
			# waited for the row.
			hook, self.on_lock = self.on_lock, None
			hook(self.posts)
		row = self.posts.get(args[0])
		return dict(row) if row is not None else None

	def _fetchall(self, scope, sql, args):
		"""Answers quiz_answers reads BY OBEYING THE SQL IT WAS GIVEN, not by
		re-implementing the query's intent.

		`answered_at IS NOT NULL` is applied if and only if the statement asks
		for it. That is the difference between a test that pins the filter and
		one that merely agrees with it: every consumer-side fake in this suite
		re-applies the filter itself, so deleting it from the real query
		changes nothing they can see."""
		self.trace.append((scope, "fetchall", sql, args))
		if "FROM quiz_answers" not in sql:
			return []
		rows = [dict(r) for r in self._rows.get("quiz_answers", {}).values()
				if r.get("post_id") == args[0]]
		if "answered_at IS NOT NULL" in sql:
			rows = [r for r in rows if r.get("answered_at") is not None]
		return rows

	# — the bare adapter surface —
	async def insert(self, table, d, on_duplicate=None):
		self._refuse_bare("insert", table)
		return self._insert("db", table, d, on_duplicate)

	async def execute(self, sql, args=None):
		self._refuse_bare("execute", sql)
		return self._execute("db", sql, list(args or []))

	async def fetchone(self, sql, args=None):
		self._refuse_bare("fetchone", sql)
		return self._fetchone("db", sql, list(args or []))

	async def fetchall(self, sql, args=None):
		self._refuse_bare("fetchall", sql)
		return self._fetchall("db", sql, list(args or []))

	async def select_one(self, _columns, table, where):
		"""What store.get_post -- the router's cheap pre-read -- goes through."""
		self._refuse_bare("select_one", table)
		if table != "quiz_posts":
			return None
		row = self.posts.get(where["id"])
		return dict(row) if row is not None else None

	def transaction(self):
		fake = self
		fake._tx_seq += 1
		scope = f"tx#{fake._tx_seq}"

		class _Ctx:
			async def __aenter__(self):
				fake._open_tx += 1
				return FakeTx(fake, scope)

			async def __aexit__(self, exc_type, exc, tb):
				fake._open_tx -= 1
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
		return [c for c in self.trace
				if c[1] in ("fetchone", "execute", "fetchall") and fragment in c[2]]

	def writes(self, table):
		"""Every recorded insert into `table`, as trace entries (so the scope
		and the ordering come with them)."""
		return [c for c in self.trace if c[1] == "insert" and c[2] == table]


@pytest.fixture
def fake_db(monkeypatch):
	fake = FakeDb()
	monkeypatch.setattr(store, "db", fake)
	# The store reads `time.time()` for itself now — that is the fix this file
	# defends — so the fake world has to own that clock too. Patching the
	# module attribute rather than the stdlib keeps the real clock untouched.
	monkeypatch.setattr(store, "time", types.SimpleNamespace(time=fake.clock))
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
	fake_db.clock.at(1_000)
	assert asyncio.run(store.record_vote(9, 1, "Ann", 2)) is True
	fake_db.clock.at(1_001)
	assert asyncio.run(store.record_vote(9, 1, "Ann", 0)) is True   # changed her mind
	row = fake_db.row("quiz_answers", post_id=9, user_id=1)
	assert row["choice_index"] == 0
	assert row["choice_indices"] is None
	assert row["is_correct"] is None                       # graded at lock, not at press
	assert row["answered_at"] == 1001
	assert row["revealed_at"] is None and row["response_ms"] is None


def test_record_vote_multi_stores_sorted_set(fake_db):
	fake_db.open_post(9, closes_at=9_000)
	assert asyncio.run(store.record_vote_multi(9, 1, "Ann", [2, 0])) is True
	row = fake_db.row("quiz_answers", post_id=9, user_id=1)
	assert row["choice_indices"] == "[0, 2]"
	assert row["choice_index"] is None


def test_write_grade(fake_db):
	fake_db.open_post(9, closes_at=9_000)
	asyncio.run(store.record_vote(9, 1, "Ann", 0))
	asyncio.run(store.write_grade(9, 1, True))
	assert fake_db.row("quiz_answers", post_id=9, user_id=1)["is_correct"] == 1


def test_write_grade_records_a_wrong_answer_as_wrong(fake_db):
	# Both verdicts, because write_grade decides the 50-vs-10 gold split: a
	# version that hardcoded "correct" passed the entire suite until this test
	# existed, and would have paid every voter the winning rate.
	fake_db.open_post(9, closes_at=9_000)
	asyncio.run(store.record_vote(9, 2, "Bob", 1))
	asyncio.run(store.write_grade(9, 2, False))
	assert fake_db.row("quiz_answers", post_id=9, user_id=2)["is_correct"] == 0


# ── the vote write re-checks the post under a row lock, against its own clock ──
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
# THE FIRST FIX WAS HALF A FIX. Gate and write became ONE transaction and the
# post row was re-read `FOR UPDATE` -- the ordering half, and correct -- but the
# gate still judged that freshly-locked row against a `now` the ROUTER had
# captured before any of it. Serialising a press against the clamp and then
# comparing the clamped deadline to a clock reading from before the wait refuses
# nothing: `:04 < :05` is true, and the write lands exactly where it always did.
# So the gate reads the clock ITSELF, after its own `FOR UPDATE` returns, and
# takes no timestamp from anybody -- a reading that cannot predate the lock
# cannot predate the clamp that held it. Every test below scripts the press's
# arrival STRICTLY EARLIER than the clamp, which is the only arrangement that
# can tell the two versions apart.
def test_a_vote_on_a_genuinely_open_poll_still_lands(fake_db):
	fake_db.open_post(9, closes_at=9_000)
	assert asyncio.run(store.record_vote(9, 1, "Ann", 2)) is True
	assert fake_db.row("quiz_answers", post_id=9, user_id=1)["choice_index"] == 2


def test_a_vote_whose_transaction_runs_after_the_clamp_is_refused(fake_db):
	""" The pre-read saw an open poll; the resolve clamped before the write. """
	fake_db.open_post(9, closes_at=9_000)
	fake_db.clock.at(1_000)
	pre = asyncio.run(store.get_post(9))                     # the router's cheap fast path
	assert pre["status"] == "open" and int(pre["closes_at"]) > 1_000, "the pre-read passed"

	# The press opens its transaction at 1_000 and queues behind the resolve;
	# the clamp lands at 1_001; the row is granted at 1_002.
	fake_db.clamp_wins_the_lock(9, closes_at=1_001, granted_at=1_002)

	assert asyncio.run(store.record_vote(9, 1, "Ann", 2)) is False
	assert fake_db.row("quiz_answers", post_id=9, user_id=1) is None
	assert fake_db.inserted("quiz_answers") == [], "no row may be written at all"


def test_a_clamp_that_wins_the_row_lock_refuses_the_waiting_press(fake_db):
	""" The same race at its tightest: the clamp commits WHILE this transaction
	is waiting for the row, so the press's own locked read is the first thing
	that can see it. `on_lock` fires inside the FOR UPDATE read, which is the
	only honest way for a single-threaded fake to represent that interleaving --
	and a version that read the row before locking it, did not re-read at all,
	or judged the re-read against a clock from before the wait, cannot survive
	it. """
	fake_db.open_post(9, closes_at=9_000)
	fake_db.clock.at(1_000)
	assert asyncio.run(store.get_post(9))["status"] == "open"
	fake_db.clamp_wins_the_lock(9, closes_at=1_001, granted_at=1_002)

	assert asyncio.run(store.record_vote(9, 1, "Ann", 2)) is False
	assert fake_db.row("quiz_answers", post_id=9, user_id=1) is None


def test_a_multi_vote_whose_transaction_runs_after_the_clamp_is_refused(fake_db):
	fake_db.open_post(9, closes_at=9_000)
	fake_db.clock.at(1_000)
	pre = asyncio.run(store.get_post(9))
	assert pre["status"] == "open" and int(pre["closes_at"]) > 1_000

	fake_db.clamp_wins_the_lock(9, closes_at=1_001, granted_at=1_002)

	assert asyncio.run(store.record_vote_multi(9, 1, "Ann", [2, 0])) is False
	assert fake_db.row("quiz_answers", post_id=9, user_id=1) is None
	assert fake_db.inserted("quiz_answers") == []


def test_a_vote_change_after_the_clamp_cannot_undo_the_grade(fake_db):
	""" THE SECOND FLAVOUR, and the one no "did a row appear?" assertion catches:
	the voter already has a row, it has already been graded, and gold has
	already been paid on that verdict. An accepted REPLACE here would silently
	reset is_correct to NULL and rewrite the choice, leaving the weekly
	scoring.tally scoring them differently from what the payout assumed. """
	fake_db.open_post(9, closes_at=9_000)
	fake_db.clock.at(1_000)
	assert asyncio.run(store.record_vote(9, 1, "Ann", 2)) is True
	asyncio.run(store.write_grade(9, 1, True))               # graded, and paid 50 on this

	# She changes her mind at 1_400 — before the resolve clamps at 1_500.
	fake_db.clock.at(1_400)
	fake_db.clamp_wins_the_lock(9, closes_at=1_500, granted_at=1_502)

	assert asyncio.run(store.record_vote(9, 1, "Ann", 0)) is False

	row = fake_db.row("quiz_answers", post_id=9, user_id=1)
	assert row["choice_index"] == 2, "the graded choice must survive untouched"
	assert row["is_correct"] == 1, "the verdict gold was paid on must not be reset"
	assert row["answered_at"] == 1_000


def test_a_vote_change_refused_on_the_multi_path_too(fake_db):
	fake_db.open_post(9, closes_at=9_000, status="closed")
	fake_db.answer_row(post_id=9, user_id=1, choice_index=None, choice_indices="[0, 2]",
			is_correct=1, answered_at=1_000)

	assert asyncio.run(store.record_vote_multi(9, 1, "Ann", [1])) is False

	row = fake_db.row("quiz_answers", post_id=9, user_id=1)
	assert row["choice_indices"] == "[0, 2]" and row["is_correct"] == 1


def test_a_press_on_a_closed_post_is_refused_by_the_status_too(fake_db):
	# The deadline is the load-bearing half (status flips last), but a post the
	# resolve already closed must be refused on the status alone.
	fake_db.open_post(9, closes_at=9_000, status="closed")
	assert asyncio.run(store.record_vote(9, 1, "Ann", 2)) is False
	assert fake_db.inserted("quiz_answers") == []


def test_a_vote_on_a_post_that_does_not_exist_is_refused(fake_db):
	assert asyncio.run(store.record_vote(404, 1, "Ann", 2)) is False
	assert fake_db.inserted("quiz_answers") == []


# ── the store, not the caller, is the clock ─────────────────────────────
def test_the_gate_reads_the_clock_under_the_lock_not_before_it(fake_db):
	""" THE PROPERTY, pinned on its own, with no clamp anywhere near it.

	The press opens its transaction at 1_000 and is granted the post row at
	1_002 — two seconds queued behind whatever held it. `answered_at` is the
	gate's own reading, so it must be 1_002: the instant the vote was ACCEPTED,
	which is the instant the deadline was judged against. Any version that
	reads the clock before the lock — an advisory `now` parameter, a
	`time.time()` hoisted above the `FOR UPDATE`, the router's press timestamp
	carried down — stamps 1_000 here, and 1_000 is precisely the reading that
	cannot refuse a deadline clamped at 1_001. This test does not need a clamp
	to fail those versions; it fails them on the timestamp alone. """
	fake_db.open_post(9, closes_at=9_000)
	fake_db.clock.at(1_000)
	fake_db.on_lock = lambda _posts: fake_db.clock.at(1_002)

	assert asyncio.run(store.record_vote(9, 1, "Ann", 2)) is True
	assert fake_db.row("quiz_answers", post_id=9, user_id=1)["answered_at"] == 1_002


def test_the_multi_gate_reads_the_clock_under_the_lock_too(fake_db):
	fake_db.open_post(9, closes_at=9_000)
	fake_db.clock.at(1_000)
	fake_db.on_lock = lambda _posts: fake_db.clock.at(1_002)

	assert asyncio.run(store.record_vote_multi(9, 1, "Ann", [1])) is True
	assert fake_db.row("quiz_answers", post_id=9, user_id=1)["answered_at"] == 1_002


def test_the_vote_writers_take_no_timestamp_from_their_caller(fake_db):
	""" NOT a style rule. An advisory `now` parameter is exactly how this race
	survived its first fix: the store held the lock, the caller held the clock,
	and the two together refused nothing. A parameter a caller CAN get wrong on
	a money path is a parameter a caller WILL get wrong, so there is none to
	pass — the authority owns both halves or it is not the authority. """
	for fn in (store.record_vote, store.record_vote_multi, store._open_for_voting):
		names = list(inspect.signature(fn).parameters)
		assert not [n for n in names if n in ("now", "now_ts", "timestamp", "answered_at")], \
			f"{fn.__name__} still takes a caller-supplied clock: {names}"


# ── the gate and the write are ONE transaction ──────────────────────────
def test_the_vote_gate_locks_the_post_row_before_it_writes(fake_db):
	""" `FOR UPDATE` is what serialises the press against clamp_closes_at's
	write to the same row. A plain SELECT reads a snapshot and loses exactly the
	race it is here to win. Pinned on the SQL text, the way
	tests/test_predictions_gold.py pins place_bet's. """
	fake_db.open_post(9, closes_at=9_000)
	asyncio.run(store.record_vote(9, 1, "Ann", 2))

	locks = fake_db.sql("FOR UPDATE")
	assert len(locks) == 1
	_scope, _kind, sql, args = locks[0]
	assert "quiz_posts" in sql and "status" in sql and "closes_at" in sql
	assert args == [9]

	writes = fake_db.writes("quiz_answers")
	assert writes and fake_db.trace.index(writes[0]) > fake_db.trace.index(locks[0])


def test_the_vote_gate_and_the_vote_write_are_the_same_transaction(fake_db):
	""" `FOR UPDATE` on the bare adapter is theatre: that statement runs on its
	own pooled connection and autocommits, so the lock is released the instant
	it returns and the write that follows is unprotected. Nothing else in this
	suite could see the difference — FakeTx used to delegate straight to FakeDb
	— and swapping either call for its `db.*` equivalent left all 1890 tests
	green. The scope tag is what closes that: both statements have to carry the
	SAME transaction's identity, and neither may be "db". """
	fake_db.open_post(9, closes_at=9_000)
	asyncio.run(store.record_vote(9, 1, "Ann", 2))

	lock = fake_db.sql("FOR UPDATE")[0]
	write = fake_db.writes("quiz_answers")[0]
	assert lock[0] != "db", "the gate's locked read left the transaction"
	assert write[0] != "db", "the vote write left the transaction"
	assert lock[0] == write[0], "gate and write ran on different transactions"


def test_the_multi_vote_gate_locks_the_post_row_too(fake_db):
	fake_db.open_post(9, closes_at=9_000)
	asyncio.run(store.record_vote_multi(9, 1, "Ann", [0]))

	locks = fake_db.sql("FOR UPDATE")
	assert len(locks) == 1 and "quiz_posts" in locks[0][2] and locks[0][3] == [9]
	write = fake_db.writes("quiz_answers")[0]
	assert fake_db.trace.index(write) > fake_db.trace.index(locks[0])
	assert locks[0][0] == write[0] != "db"


# ── the clamp takes the same lock, in its own transaction ───────────────
def test_the_clamp_takes_the_same_row_lock_before_it_moves_the_deadline(fake_db):
	""" Both halves of the mechanism have to lock the SAME row or they do not
	serialise at all: the clamp would commit under a press that is mid-flight,
	and _reveal's snapshot -- taken after the clamp returns -- could still miss
	a vote that lands afterwards. """
	fake_db.open_post(9, closes_at=9_000)
	asyncio.run(store.clamp_closes_at(9, 1_000))

	locks = fake_db.sql("FOR UPDATE")
	assert len(locks) == 1 and "quiz_posts" in locks[0][2] and locks[0][3] == [9]
	clamp = fake_db.sql("SET closes_at=LEAST")
	assert len(clamp) == 1
	assert fake_db.trace.index(locks[0]) < fake_db.trace.index(clamp[0])
	assert fake_db.posts[9]["closes_at"] == 1_000


def test_the_clamp_holds_its_lock_over_the_update_in_one_transaction(fake_db):
	""" The lock and the UPDATE in SEPARATE statements only serialise anything
	if they are in the same transaction: a bare `SELECT ... FOR UPDATE`
	autocommits and releases the row before the UPDATE is even sent, which
	re-opens the exact gap between the clamp and the snapshot that a press can
	slip through. Rewriting this function as a bare fetchone plus a bare
	execute left the whole suite green, because the fake could not tell a
	transaction from a connection. """
	fake_db.open_post(9, closes_at=9_000)
	asyncio.run(store.clamp_closes_at(9, 1_000))

	lock = fake_db.sql("FOR UPDATE")[0]
	update = fake_db.sql("SET closes_at=LEAST")[0]
	assert lock[0] != "db", "the clamp's locked read is not in a transaction"
	assert update[0] != "db", "the clamp's UPDATE is not in a transaction"
	assert lock[0] == update[0], "the clamp's lock and UPDATE are separate transactions"


def test_the_clamp_never_pushes_a_deadline_forward(fake_db):
	# LEAST(closes_at, now), not an assignment: _close_due re-enters _reveal for
	# posts already days past their deadline, and moving closes_at up to now
	# would re-open a poll that has been shut for a week.
	fake_db.open_post(9, closes_at=1_000)
	asyncio.run(store.clamp_closes_at(9, 9_000))
	assert fake_db.posts[9]["closes_at"] == 1_000


# ── answers_for_post's cast-a-vote filter ───────────────────────────────
# `AND answered_at IS NOT NULL` is a PAYROLL predicate. bot/quiz/jobs.py::_reveal
# grades and pays every row this returns, so a reveal-era ghost — a user who
# pressed the old "Reveal & start" button and never answered — that leaks
# through is graded wrong and paid 10 gold it never earned, with real ledger
# rows behind it and nothing to distinguish them from honest ones.
#
# Deleting the fragment from the real SQL used to leave the whole suite green:
# every consumer test (tests/test_quiz_jobs_resolve.py, tests/test_quiz_
# interactions.py) re-implements the filter inside its own fake and therefore
# asserts on the fake. These two drive the REAL query against a fake that
# applies the filter only if the statement asks for it.
def test_answers_for_post_excludes_reveal_era_ghost_rows(fake_db):
	fake_db.answer_row(post_id=9, user_id=1, nick="Ann", choice_index=0,
			choice_indices=None, is_correct=None, answered_at=1_500)
	fake_db.answer_row(post_id=9, user_id=3, nick="Ghost", choice_index=None,
			choice_indices=None, is_correct=None, answered_at=None)

	rows = asyncio.run(store.answers_for_post(9))

	assert [r["user_id"] for r in rows] == [1]
	assert "Ghost" not in [r["nick"] for r in rows], \
		"a row that never cast a vote would be graded wrong and paid 10 gold"


def test_answers_for_post_is_scoped_to_the_post(fake_db):
	fake_db.answer_row(post_id=9, user_id=1, nick="Ann", answered_at=1_500)
	fake_db.answer_row(post_id=10, user_id=2, nick="Bob", answered_at=1_500)

	assert [r["user_id"] for r in asyncio.run(store.answers_for_post(9))] == [1]
