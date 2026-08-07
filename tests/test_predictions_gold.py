"""The gold bank's control flow, driven by a scripted fake db.

Most of what matters here is the DECISIONS: which paths roll the transaction
back, which return which status, and that an idempotent skip (INSERT IGNORE
rowcount 0) short-circuits without touching a balance.

But some of it IS the SQL, and pretending otherwise left the money rules
undefended. The fake below has always recorded every statement and every
`on_duplicate`; nothing asserted on them, and seven separate mutations of this
package's most load-bearing predicates — dropping `AND balance>=%s`, dropping
`AND side=%s`, hardwiring the inserted side to 0, removing `on_duplicate
="ignore"` from each of the three idempotent inserts — left the whole suite
green. Each one silently breaks an invariant the design calls binding: no
overdraft, no side switching, no double application. TestTheMoneyPredicates and
TestIdempotentInserts below are the tests that fail when they are removed.

AND SOME OF IT IS THE TRANSACTION, which is a different claim from the SQL. A
`SELECT ... FOR UPDATE` means nothing on its own: on the bare adapter it checks
out its own pooled connection, autocommits, and drops the row lock before the
next statement is even sent. `tx.fetchone` and `db.fetchone` are therefore
completely different instructions, and swapping place_bet's book re-read from
one to the other — deleting the only thing standing between a press and a
snapshot that has already refunded or paid the book — left the whole suite
green, because `sql()` matched both and nothing asserted the scope. Every
statement now records WHICH transaction issued it (`"tx#n.*"`) or that it went
to the bare adapter (`"db.*"`), `sql()` reports tx-scoped statements only, and
a bare statement issued while one of the module's own transactions is open is
refused outright — in production that is a second connection outside the
transaction, and there is no legitimate reason to do it."""
from __future__ import annotations

import asyncio

from nammaoe2bot.features.betting import gold


class OutsideTransaction(AssertionError):
	"""A statement went to the bare adapter while the code under test had a
	transaction of its own open. In production that runs on a different pooled
	connection: outside the transaction, autocommitted, and any `FOR UPDATE` in
	it takes a lock released the moment the statement returns."""


class FakeTx:
	"""The connection-bound handle db.transaction() yields — and it has an
	IDENTITY, `scope` (e.g. "tx#1"), stamped on everything it records. Two
	statements carrying the same scope were issued on the same transaction; a
	statement tagged "db.*" was in none at all. Without that the fake could not
	tell the two apart, and "the book is re-read under a lock inside the
	transaction that spends the stake" was a comment rather than a test."""

	def __init__(self, db, scope):
		self.db = db
		self.scope = scope

	async def execute(self, sql, args=None):
		self.db.calls.append((f"{self.scope}.execute", sql, list(args or [])))
		return self.db.rowcounts.pop(0) if self.db.rowcounts else 1

	async def insert(self, table, d, on_duplicate=None):
		self.db.calls.append((f"{self.scope}.insert", table, dict(d), on_duplicate))
		if self.db.raise_integrity_on == table:
			self.db.raise_integrity_on = None
			raise self.db.errors.IntegrityError()
		return self.db.rowcounts.pop(0) if self.db.rowcounts else 1

	async def fetchone(self, sql, args=None):
		self.db.calls.append((f"{self.scope}.fetchone", sql, list(args or [])))
		return self.db.next_row()


class _Errors:
	class IntegrityError(Exception):
		pass


def _kind(call):
	""""tx#2.insert" -> "insert". The statement, with its scope stripped."""
	return call[0].rsplit(".", 1)[1]


def _scope(call):
	""""tx#2.insert" -> "tx#2"; "db.fetchone" -> "db". WHICH transaction issued
	the statement, or "db" for none at all."""
	return call[0].rsplit(".", 1)[0]


# One row answers every fetchone the bank makes — the book it locks FOR UPDATE,
# the balance it reads back, the side it reports on a locked press — because the
# fake has one slot for all of them. `status`/`freezes_at` belong to the book:
# place_bet re-reads the post inside its own transaction, so a fake that omitted
# them would KeyError before any money moved. Where one slot genuinely cannot
# say what a test means — cancel_bet reads an open book and then a bets row that
# is NOT there — `answers()` scripts the fetchones one at a time instead.
ROW = {"balance": 440, "side": 1, "status": "open", "freezes_at": 9_999_999_999}


class FakeDb:
	def __init__(self):
		self.calls = []
		self.rowcounts = []          # consumed left-to-right by execute/insert
		self.fetchone_result = dict(ROW)
		self.fetchone_queue = []     # consumed left-to-right by fetchone, if set
		self.fetchall_result = []    # answered verbatim by db.fetchall
		self.raise_integrity_on = None
		self.errors = _Errors
		self.rolled_back = False
		self._open_tx = 0
		self._tx_seq = 0

	def answer(self, **overrides):
		"""Override single columns without dropping the rest of the row."""
		self.fetchone_result = {**ROW, **overrides}
		return self

	def answers(self, *rows):
		"""Answer successive fetchones with these rows in order, falling back to
		the single-slot answer once they run out. Each entry is either a dict of
		column overrides or None, meaning THE ROW DOES NOT EXIST — the only
		honest way to say "the book is there, the bet row is not", which one
		shared slot cannot express, and which cancel_bet has to distinguish."""
		self.fetchone_queue = [None if r is None else {**ROW, **r} for r in rows]
		return self

	def next_row(self):
		return self.fetchone_queue.pop(0) if self.fetchone_queue else self.fetchone_result

	def sql(self, fragment):
		"""Every TRANSACTION-SCOPED statement containing `fragment`.

		Deliberately not the bare ones. A `FOR UPDATE` on the bare adapter
		holds no lock anybody else can wait on, so counting it here would let
		`tx.fetchone -> db.fetchone` pass every lock-ordering assertion in this
		file — which it did. `bare_sql` reports the other half."""
		return [c for c in self.calls
				if c[0].startswith("tx#") and c[0].rsplit(".", 1)[1] in ("execute", "fetchone")
				and fragment in c[1]]

	def inserts(self):
		"""Every insert the bank made INSIDE a transaction — which is every
		insert the bank makes at all. Asserting on this rather than on the raw
		call log is what keeps that true: a ledger row written on the bare
		adapter is a row that commits on its own, separately from the balance
		update it is supposed to be atomic with, and it would not appear here."""
		return [c for c in self.calls if _kind(c) == "insert" and _scope(c).startswith("tx#")]

	def bare_sql(self, fragment):
		"""Statements issued OUTSIDE any transaction (gold.balance,
		quiz_paid_total, the balance read-back after place_bet commits)."""
		return [c for c in self.calls
				if c[0] in ("db.fetchone", "db.fetchall", "db.execute") and fragment in c[1]]

	def _refuse_bare(self, kind, target):
		if self._open_tx:
			raise OutsideTransaction(
				f"{kind} {target!r} went to the bare adapter while a transaction was open — "
				"in production that is a different pooled connection, outside the "
				"transaction, and any FOR UPDATE in it locks nothing")

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

	async def fetchone(self, sql, args=None):
		self._refuse_bare("fetchone", sql)
		self.calls.append(("db.fetchone", sql, list(args or [])))
		return self.next_row()

	async def fetchall(self, sql, args=None):
		self._refuse_bare("fetchall", sql)
		self.calls.append(("db.fetchall", sql, list(args or [])))
		return self.fetchall_result

	# The bank never issues either of these on the bare adapter. They exist so
	# that a mutation moving one OUT of its transaction fails on the invariant
	# it broke, loudly, rather than on an AttributeError that reads like a
	# missing test fixture.
	async def execute(self, sql, args=None):
		self._refuse_bare("execute", sql)
		self.calls.append(("db.execute", sql, list(args or [])))
		return self.rowcounts.pop(0) if self.rowcounts else 1

	async def insert(self, table, d, on_duplicate=None):
		self._refuse_bare("insert", table)
		self.calls.append(("db.insert", table, dict(d), on_duplicate))
		return self.rowcounts.pop(0) if self.rowcounts else 1


def use_fake(monkeypatch):
	fake = FakeDb()
	monkeypatch.setattr(gold, "db", fake)
	return fake


class TestPlaceBet:
	def test_ok_path_spends_bets_and_ledgers(self, monkeypatch):
		fake = use_fake(monkeypatch)
		# balance UPDATE hits, bets UPDATE hits; then ledger insert, then SELECT balance
		fake.rowcounts = [1, 1, 1]
		status, value = asyncio.run(gold.place_bet(5, 42, 12, 0, 50, "nick", 1000))
		assert status == "ok" and value == 440
		tables = [c[1] for c in fake.inserts()]
		assert "gold_ledger" in tables
		ledger = next(c[2] for c in fake.inserts() if c[1] == "gold_ledger")
		assert ledger["amount"] == -50 and ledger["entry_type"] == "bet"
		assert ledger.get("idem_key") is None or "idem_key" not in ledger

	def test_insufficient_rolls_back_and_reports_balance(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.rowcounts = [0]          # balance UPDATE matches nothing
		fake.answer(balance=30)
		status, value = asyncio.run(gold.place_bet(5, 42, 12, 0, 50, "nick", 1000))
		assert status == "insufficient" and value == 30
		assert fake.rolled_back

	def test_other_side_press_is_side_locked(self, monkeypatch):
		fake = use_fake(monkeypatch)
		# balance UPDATE hits; bets same-side UPDATE misses; INSERT hits the PK
		fake.rowcounts = [1, 0]
		fake.raise_integrity_on = "prediction_bets"
		fake.answer(side=1)
		status, value = asyncio.run(gold.place_bet(5, 42, 12, 0, 50, "nick", 1000))
		assert status == "side_locked" and value == 1
		assert fake.rolled_back          # the stake deduction must not survive

	def test_is_player_is_written_on_the_bets_row(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 0, 1, 1]      # balance ok, same-side UPDATE misses -> INSERT
		asyncio.run(gold.place_bet(5, 42, 12, 0, 50, "nick", 1000, is_player=True))
		row = next(c[2] for c in fake.inserts() if c[1] == "prediction_bets")
		assert row["is_player"] == 1

	def test_is_player_defaults_to_false_for_spectators(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 0, 1, 1]
		asyncio.run(gold.place_bet(5, 42, 12, 0, 50, "nick", 1000))
		row = next(c[2] for c in fake.inserts() if c[1] == "prediction_bets")
		assert row["is_player"] == 0

	def test_a_later_press_on_the_same_side_re_asserts_is_player(self, monkeypatch):
		""" The additive path writes no INSERT at all, so without is_player in
		the same-side UPDATE's SET list a player's second press would leave the
		flag at whatever the first press happened to write. """
		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 1, 1]         # balance ok, same-side UPDATE hits
		asyncio.run(gold.place_bet(5, 42, 12, 0, 50, "nick", 1000, is_player=True))
		update = fake.sql("UPDATE prediction_bets")[0]
		assert "is_player=%s" in update[1]
		assert 1 in update[2]

	def test_forged_stake_is_rejected_outright(self, monkeypatch):
		use_fake(monkeypatch)
		try:
			asyncio.run(gold.place_bet(5, 42, 12, 0, 9999, "nick", 1000))
			assert False, "should have raised"
		except ValueError:
			pass


class TestTheBookIsCheckedInsideTheTransaction:
	""" THE DESTROYED-GOLD RACE. The handler gates a press on a post row it read
	before any of this, and a freeze/void/settle sweep can flip that row in
	between. Such a press commits its stake into a book that has already been
	snapshotted for refund or payout: nobody refunds it, nobody pays it, and
	reconcile() cannot see it because the ledger and the balance cache agree —
	the gold is just gone. The re-read here is the only thing standing between
	that and a normal /subauto. """

	def test_a_closed_book_refuses_the_press_and_moves_no_money(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.answer(status="frozen")
		status, value = asyncio.run(gold.place_bet(5, 42, 12, 0, 50, "nick", 1000))
		assert (status, value) == ("closed", None)
		assert fake.sql("gold_balances SET balance=balance-") == [], "no stake was taken"
		assert not fake.inserts()
		assert fake.rolled_back

	def test_a_book_past_its_freeze_time_refuses_too(self, monkeypatch):
		""" The status flip lags the clock by up to one 15s sweep; the deadline
		is authoritative in between. """
		fake = use_fake(monkeypatch)
		fake.answer(freezes_at=999)
		assert asyncio.run(gold.place_bet(5, 42, 12, 0, 50, "nick", 1000))[0] == "closed"
		assert fake.sql("gold_balances SET balance=balance-") == []

	def test_a_deleted_post_refuses(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.fetchone_result = None
		assert asyncio.run(gold.place_bet(5, 42, 12, 0, 50, "nick", 1000))[0] == "closed"

	def test_the_book_row_is_locked_for_update_before_the_stake_is_taken(self, monkeypatch):
		""" SELECT ... FOR UPDATE is what serialises the press against
		store.close_betting's compare-and-set on the same row. A plain SELECT
		reads a snapshot and loses the race it is here to win. """
		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 1, 1]
		asyncio.run(gold.place_bet(5, 42, 12, 0, 50, "nick", 1000))

		locks = fake.sql("FOR UPDATE")
		assert len(locks) == 1
		sql, args = locks[0][1], locks[0][2]
		assert "prediction_posts" in sql and "status" in sql and "freezes_at" in sql
		assert args == [12]
		spend = fake.sql("gold_balances SET balance=balance-")
		assert fake.calls.index(locks[0]) < fake.calls.index(spend[0])

	def test_the_book_re_read_runs_inside_the_transaction_that_spends(self, monkeypatch):
		""" `FOR UPDATE` IS ONLY A LOCK IF IT IS IN A TRANSACTION. On the bare
		adapter that statement takes its own pooled connection and autocommits:
		the row lock is released before the next statement is even sent, so the
		sweep it is supposed to serialise against can flip the book between the
		re-read and the spend, and the press's stake lands in a book already
		snapshotted for refund or payout — gone, with reconcile() unable to see
		it because the ledger and the balance cache still agree.

		Nothing else in this file could tell the two apart (`sql()` matched
		both, and FakeTx used to delegate straight to FakeDb), and
		`tx.fetchone -> db.fetchone` here passed all 1890 tests. The scope tag
		is the fix: the re-read, the conditional spend and the ledger row must
		all carry the SAME transaction's identity, and none of them "db". """
		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 1, 1]
		asyncio.run(gold.place_bet(5, 42, 12, 0, 50, "nick", 1000))

		lock = fake.sql("FOR UPDATE")[0]
		spend = fake.sql("gold_balances SET balance=balance-")[0]
		ledger = next(c for c in fake.inserts() if c[1] == "gold_ledger")
		assert _scope(lock) != "db", "the book re-read left the transaction"
		assert _scope(lock) == _scope(spend) == _scope(ledger), \
			"the re-read, the spend and the ledger row are not one transaction"

	def test_the_book_lock_comes_before_every_bets_row_write(self, monkeypatch):
		""" THE INVARIANT cancel_bet's docstring leans on when it calls its own
		second row lock belt-and-braces: prediction_bets has exactly two
		writers, and BOTH take the post row lock before touching it. That is
		what makes a bet row unable to change under either of them — and a
		third writer added without it would silently undo the reasoning. """
		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 1, 1]
		asyncio.run(gold.place_bet(5, 42, 12, 0, 50, "nick", 1000))

		lock_at = fake.calls.index(fake.sql("FOR UPDATE")[0])
		touches = [n for n, c in enumerate(fake.calls)
				   if "prediction_bets" in (c[1] or "")]
		assert touches, "place_bet did not touch prediction_bets at all"
		assert min(touches) > lock_at


class TestTheMoneyPredicates:
	""" The four SQL fragments the economy actually rests on. Each was mutated
	away against the full suite and every test still passed. """

	def run_ok(self, monkeypatch, side=0, stake=50):
		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 1, 1]
		asyncio.run(gold.place_bet(5, 42, 12, side, stake, "nick", 1000))
		return fake

	def test_the_spend_cannot_overdraw(self, monkeypatch):
		""" `AND balance>=%s` is the ONLY thing between the economy and negative
		balances: place_bet reads "zero rows matched" as "not enough gold", and
		an unconditional UPDATE always matches. """
		fake = self.run_ok(monkeypatch)
		spends = fake.sql("gold_balances SET balance=balance-")
		assert len(spends) == 1
		sql, args = spends[0][1], spends[0][2]
		assert "AND balance>=%s" in sql
		assert args[-1] == 50, "the guard compares against the stake being taken"

	def test_the_additive_update_is_pinned_to_the_side_already_staked(self, monkeypatch):
		""" Half of the side lock. Without `AND side=%s` the UPDATE matches the
		user's Alpha row when they press Bravo, and their whole accumulated
		stake silently changes sides — the exact switch the design forbids. """
		fake = self.run_ok(monkeypatch, side=1)
		adds = fake.sql("UPDATE prediction_bets SET stake=stake+")
		assert len(adds) == 1
		sql, args = adds[0][1], adds[0][2]
		assert "side=%s" in sql
		assert args[-1] == 1, "matched against the side that was pressed"

	def test_the_first_press_records_the_side_that_was_pressed(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 0, 1, 1]      # spend hits, additive UPDATE misses -> INSERT
		asyncio.run(gold.place_bet(5, 42, 12, 1, 100, "nick", 1000))
		row = next(c[2] for c in fake.inserts() if c[1] == "prediction_bets")
		assert row["side"] == 1 and row["stake"] == 100 and row["post_id"] == 12

	def test_the_bet_ledger_row_is_a_plain_insert(self, monkeypatch):
		""" Bet rows carry idem_key=NULL by design (many presses per user per
		post), so this is the one movement that must NOT be an INSERT IGNORE —
		a swallowed failure here is a stake taken with no ledger row. """
		fake = self.run_ok(monkeypatch)
		ledger = next(c for c in fake.inserts() if c[1] == "gold_ledger")
		assert ledger[3] is None, "on_duplicate must stay off the bet row"


class TestIdempotentInserts:
	""" `on_duplicate="ignore"` is the single mechanism the entire idempotency
	guarantee rests on. Without it a re-run raises IntegrityError out of
	tx.insert instead of skipping, which aborts the rest of the sweep — so the
	crash-resume path would strand exactly the bettors it exists to pay. """

	def test_the_seed_is_an_insert_ignore(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 1]
		asyncio.run(gold.ensure_seeded(5, 42, 1000))
		assert next(c[3] for c in fake.inserts()) == "ignore"

	def test_refunds_and_payouts_are_insert_ignores(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 1]
		asyncio.run(gold.pay_post(5, {1: 225}, 12, 1000))
		assert next(c[3] for c in fake.inserts()) == "ignore"

		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 1]
		asyncio.run(gold.refund_post(5, [dict(user_id=1, side=0, stake=60)], 12, 1000))
		assert next(c[3] for c in fake.inserts()) == "ignore"

	def test_the_match_reward_is_an_insert_ignore(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.answer(balance=100)
		fake.rowcounts = [1, 1]
		asyncio.run(gold.grant_match_reward(5, 42, 900, 1000))
		assert next(c[3] for c in fake.inserts()) == "ignore"


class TestEnsureSeeded:
	def test_first_touch_seeds(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 1]
		assert asyncio.run(gold.ensure_seeded(5, 42, 1000)) is True
		ledger = next(c[2] for c in fake.inserts())
		assert ledger["idem_key"] == "seed:5:42" and ledger["amount"] == 500

	def test_second_touch_is_a_noop(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.rowcounts = [0]          # idem key already there
		assert asyncio.run(gold.ensure_seeded(5, 42, 1000)) is False
		# and no balance write happened after the skip
		assert not [c for c in fake.calls if _kind(c) == "execute" and "gold_balances" in c[1]]


class TestCancelBet:
	""" Cancel is the one credit with no idem_key: the bets ROW is the refund
	token, and the DELETE's rowcount is the exactly-once guard. Both halves of
	that — the book lock and the rowcount — are money guards, and each is
	defended by its own test below. """

	OPEN = dict(status="open", freezes_at=9999)

	def test_cancelling_returns_the_whole_stake_and_ledgers_it(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.answer(**self.OPEN, stake=60)
		fake.rowcounts = [1, 1, 1]      # DELETE hits, ledger insert, balance bump
		status, amount = asyncio.run(gold.cancel_bet(5, 42, 12, 1000))
		assert (status, amount) == ("ok", 60)
		row = next(c[2] for c in fake.inserts() if c[1] == "gold_ledger")
		assert row["entry_type"] == "cancel" and row["amount"] == 60
		bumps = fake.sql("gold_balances SET balance=balance+")
		assert len(bumps) == 1 and bumps[0][2][0] == 60

	def test_the_cancel_ledger_row_carries_no_idem_key(self, monkeypatch):
		""" A user may bet -> cancel -> bet -> cancel on one post, so a
		cancel:{post}:{user} key would swallow the second cancel and silently
		keep their gold. The DELETE's rowcount is the guard instead. """
		fake = use_fake(monkeypatch)
		fake.answer(**self.OPEN, stake=60)
		fake.rowcounts = [1, 1, 1]
		asyncio.run(gold.cancel_bet(5, 42, 12, 1000))
		row = next(c for c in fake.inserts() if c[1] == "gold_ledger")
		assert row[2].get("idem_key") is None
		assert row[3] is None, "on_duplicate must stay off the cancel row"

	def test_a_second_cancel_refunds_nothing(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.answers(self.OPEN, None)            # the book is open; the bets row is gone
		status, amount = asyncio.run(gold.cancel_bet(5, 42, 12, 1000))
		assert (status, amount) == ("nothing", 0)
		assert not fake.inserts()
		assert not fake.sql("DELETE FROM prediction_bets")

	def test_a_delete_that_matches_nothing_refunds_nothing(self, monkeypatch):
		""" Two presses both read the row; one DELETEs it first. The loser's
		DELETE affects 0 rows and must not pay out. """
		fake = use_fake(monkeypatch)
		fake.answer(**self.OPEN, stake=60)
		fake.rowcounts = [0]             # DELETE matched nothing
		status, amount = asyncio.run(gold.cancel_bet(5, 42, 12, 1000))
		assert (status, amount) == ("nothing", 0)
		assert not fake.inserts()
		assert not fake.sql("gold_balances SET balance=balance+")

	def test_a_frozen_book_refuses_the_cancel_under_the_row_lock(self, monkeypatch):
		""" The mirror of place_bet's race. A cancel that slipped past a sweep's
		snapshot would be refunded twice — once here, once by the sweep's
		idempotent refund row — and reconcile() could never see it. The stake and
		rowcounts are supplied so that dropping the book check pays out rather
		than merely erroring: the mutation has to fail for the money reason. """
		fake = use_fake(monkeypatch)
		fake.answer(status="frozen", freezes_at=9999, stake=60)
		fake.rowcounts = [1, 1, 1]
		status, amount = asyncio.run(gold.cancel_bet(5, 42, 12, 1000))
		assert (status, amount) == ("closed", 0)
		assert not fake.inserts()
		assert not fake.sql("DELETE FROM prediction_bets")

	def test_a_cancel_after_the_deadline_is_refused_even_while_open(self, monkeypatch):
		""" The status flip lags the clock by up to one 15s sweep; the deadline is
		authoritative in between, exactly as it is for a press. """
		fake = use_fake(monkeypatch)
		fake.answer(status="open", freezes_at=500, stake=60)   # now=1000 is past it
		fake.rowcounts = [1, 1, 1]
		status, amount = asyncio.run(gold.cancel_bet(5, 42, 12, 1000))
		assert (status, amount) == ("closed", 0)
		assert not fake.inserts()
		assert not fake.sql("DELETE FROM prediction_bets")

	def test_a_deleted_post_refuses(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.fetchone_result = None
		assert asyncio.run(gold.cancel_bet(5, 42, 12, 1000)) == ("closed", 0)

	def test_the_book_is_locked_before_the_bet_row_is_read(self, monkeypatch):
		""" Ordering is the guarantee: lock the post, THEN touch the money.

		This is also the whole of why cancel_bet's SECOND `FOR UPDATE` — the
		one on the bets row — is belt and braces rather than load-bearing.
		prediction_bets has exactly two writers, this function and place_bet,
		and both reach it only after the post row is locked (see
		TestPlaceBet.test_the_book_lock_comes_before_every_bets_row_write), so
		no other transaction can change a bet row between the read and the
		DELETE. The DELETE's rowcount is the exactly-once guard on top of
		that. Pinning THIS ordering is worth doing; a test asserting the
		redundant lock's presence would only assert that a line exists. """
		fake = use_fake(monkeypatch)
		fake.answer(status="open", freezes_at=9999, stake=60)
		fake.rowcounts = [1, 1, 1]
		asyncio.run(gold.cancel_bet(5, 42, 12, 1000))
		reads = [c[1] for c in fake.calls if _kind(c) == "fetchone"]
		assert "prediction_posts" in reads[0] and "FOR UPDATE" in reads[0]
		assert "prediction_bets" in reads[1]
		delete = fake.sql("DELETE FROM prediction_bets")
		assert len(delete) == 1 and delete[0][2] == [12, 42]
		# Every prediction_bets statement, not merely the first read.
		lock_at = fake.calls.index(fake.sql("prediction_posts")[0])
		touches = [n for n, c in enumerate(fake.calls) if "prediction_bets" in (c[1] or "")]
		assert len(touches) == 2 and min(touches) > lock_at

	def test_a_missing_balance_row_rolls_the_refund_back(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.answer(**self.OPEN, stake=60)
		fake.rowcounts = [1, 1, 0]       # DELETE ok, ledger ok, balance UPDATE misses
		try:
			asyncio.run(gold.cancel_bet(5, 42, 12, 1000))
			assert False, "should have raised"
		except RuntimeError:
			pass
		assert fake.rolled_back


class TestCreditPaths:
	def test_refund_post_credits_each_bettor_idempotently(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 1, 0]     # first refund applies (+balance), second skips
		bets = [dict(user_id=1, side=0, stake=60), dict(user_id=2, side=1, stake=10)]
		done = asyncio.run(gold.refund_post(5, bets, 12, 1000))
		assert done == 1
		keys = [c[2]["idem_key"] for c in fake.inserts()]
		assert keys == ["refund:12:1", "refund:12:2"]

	def test_pay_post_uses_payout_keys(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 1]
		done = asyncio.run(gold.pay_post(5, {1: 225}, 12, 1000))
		assert done == 1
		ledger = next(c[2] for c in fake.inserts())
		assert ledger["idem_key"] == "payout:12:1" and ledger["amount"] == 225

	def test_missing_balance_row_rolls_the_credit_back(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 0]        # ledger applies, balance UPDATE matches nothing
		try:
			asyncio.run(gold.pay_post(5, {1: 225}, 12, 1000))
			assert False, "should have raised"
		except RuntimeError:
			pass
		assert fake.rolled_back


class TestGrantMatchReward:
	def test_grants_the_topup_below_ceiling(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.fetchone_result = {"balance": 496}
		fake.rowcounts = [1, 1]
		assert asyncio.run(gold.grant_match_reward(5, 42, 900, 1000)) == 4
		ledger = next(c[2] for c in fake.inserts())
		assert ledger["idem_key"] == "reward:900:42" and ledger["amount"] == 4

	def test_at_ceiling_grants_nothing_and_writes_nothing(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.fetchone_result = {"balance": 500}
		assert asyncio.run(gold.grant_match_reward(5, 42, 900, 1000)) == 0
		assert not fake.inserts()

	def test_already_granted_is_zero(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.fetchone_result = {"balance": 100}
		fake.rowcounts = [0]           # idem key already there
		assert asyncio.run(gold.grant_match_reward(5, 42, 900, 1000)) == 0

	def test_the_balance_is_locked_for_update_inside_the_grant(self, monkeypatch):
		""" THE CEILING IS A READ-THEN-WRITE, so it is only a ceiling under a
		lock. reward_amount() is computed from the balance this SELECT returns
		and the UPDATE then adds it — two statements, and between them a
		concurrent grant, payout or bet settlement on the same user can move
		that balance. Without `FOR UPDATE` two rewards racing on a 495 balance
		both read 495, both compute 5, and the user ends on 505: past a ceiling
		the whole faucet exists to enforce, with a ledger that agrees perfectly
		so reconcile() reports nothing.

		Dropping the fragment left the entire suite green, as did moving the
		read out of the transaction — where the lock would be released before
		the UPDATE anyway. Both are pinned here. """
		fake = use_fake(monkeypatch)
		fake.fetchone_result = {"balance": 100}
		fake.rowcounts = [1, 1]
		asyncio.run(gold.grant_match_reward(5, 42, 900, 1000))

		locks = fake.sql("FOR UPDATE")
		assert len(locks) == 1, "the balance read is not a locking read in a transaction"
		assert "gold_balances" in locks[0][1] and locks[0][2] == [5, 42]
		bump = fake.sql("gold_balances SET balance=balance+")[0]
		assert _scope(locks[0]) == _scope(bump) != "db", \
			"the locked read and the balance bump are not one transaction"
		assert fake.calls.index(locks[0]) < fake.calls.index(bump)


class TestGrantQuizReward:
	""" Mirrors TestGrantMatchReward exactly: same shape, same ceiling, same
	idempotency mechanism — the only differences are the amount (correctness-
	dependent) and the idem_key, which is keyed on (quiz post, user) rather
	than entry_type so a correct-then-replayed grant can't double-pay. """

	def test_correct_pays_50_with_entry_type(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.fetchone_result = {"balance": 100}
		fake.rowcounts = [1, 1]
		assert asyncio.run(gold.grant_quiz_reward(5, 42, 77, True, 1000)) == 50
		ledger = next(c[2] for c in fake.inserts())
		assert ledger["entry_type"] == "quiz_correct"
		assert ledger["idem_key"] == "quiz:77:42"
		# post_id references prediction_posts, not quiz posts — a quiz post id
		# in it would be a foreign lie, so both stay unset (NULL).
		assert ledger.get("post_id") is None and ledger.get("match_id") is None

	def test_played_pays_10(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.fetchone_result = {"balance": 100}
		fake.rowcounts = [1, 1]
		assert asyncio.run(gold.grant_quiz_reward(5, 42, 77, False, 1000)) == 10
		ledger = next(c[2] for c in fake.inserts())
		assert ledger["entry_type"] == "quiz_played"
		assert ledger["idem_key"] == "quiz:77:42"

	def test_at_ceiling_grants_nothing_and_writes_nothing(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.fetchone_result = {"balance": 500}
		assert asyncio.run(gold.grant_quiz_reward(5, 42, 77, True, 1000)) == 0
		assert not fake.inserts()

	def test_ceiling_clamps_a_partial_topup(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.fetchone_result = {"balance": 460}
		fake.rowcounts = [1, 1]
		assert asyncio.run(gold.grant_quiz_reward(5, 42, 78, True, 1000)) == 40

	def test_already_granted_is_zero(self, monkeypatch):
		""" THE ONE THAT MATTERS: if grant_quiz_reward stopped consulting the
		INSERT IGNORE's rowcount, this would fall through to the balance
		UPDATE — and the fake's rowcounts queue is empty by then, so a plain
		`return 1` default would silently bump the balance a second time. """
		fake = use_fake(monkeypatch)
		fake.fetchone_result = {"balance": 100}
		fake.rowcounts = [0]           # idem key already there
		assert asyncio.run(gold.grant_quiz_reward(5, 42, 77, True, 1000)) == 0
		assert not [c for c in fake.calls if _kind(c) == "execute" and "gold_balances" in c[1]]

	def test_missing_balance_row_raises(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.fetchone_result = None    # no gold_balances row for this user
		fake.rowcounts = [1, 0]        # ledger applies, balance UPDATE matches nothing
		try:
			asyncio.run(gold.grant_quiz_reward(5, 999999, 77, True, 1000))
			assert False, "should have raised"
		except RuntimeError:
			pass

	def test_the_balance_is_locked_for_update_inside_the_grant(self, monkeypatch):
		""" Same read-then-write ceiling as the match faucet, and the same lock
		— but the quiz pays 50, not a 10-gold top-up, so a lost race overshoots
		by ten times as much. A whole channel is resolved in one loop here: the
		grants for every voter run back to back, and a prediction settling or a
		bet landing on any of them in that window is exactly the concurrency
		this SELECT has to hold still. Dropping `FOR UPDATE`, or moving the
		read out of the transaction, left the entire suite green. """
		fake = use_fake(monkeypatch)
		fake.fetchone_result = {"balance": 100}
		fake.rowcounts = [1, 1]
		asyncio.run(gold.grant_quiz_reward(5, 42, 77, True, 1000))

		locks = fake.sql("FOR UPDATE")
		assert len(locks) == 1, "the balance read is not a locking read in a transaction"
		assert "gold_balances" in locks[0][1] and locks[0][2] == [5, 42]
		bump = fake.sql("gold_balances SET balance=balance+")[0]
		assert _scope(locks[0]) == _scope(bump) != "db", \
			"the locked read and the balance bump are not one transaction"
		assert fake.calls.index(locks[0]) < fake.calls.index(bump)


class TestQuizPaidTotal:
	def test_reads_the_ledger_sum_for_this_post_only(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.fetchall_result = [{"s": 60}]
		assert asyncio.run(gold.quiz_paid_total(77)) == 60
		call = next(c for c in fake.calls if c[0] == "db.fetchall")
		assert "idem_key LIKE" in call[1]
		assert call[2] == ["quiz:77:%"]

	def test_no_rows_is_zero(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.fetchall_result = []
		assert asyncio.run(gold.quiz_paid_total(77)) == 0
