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
TestIdempotentInserts below are the tests that fail when they are removed."""
from __future__ import annotations

import asyncio

from bot.predictions import gold


class FakeTx:
	def __init__(self, db):
		self.db = db

	async def execute(self, sql, args=None):
		self.db.calls.append(("execute", sql, list(args or [])))
		return self.db.rowcounts.pop(0) if self.db.rowcounts else 1

	async def insert(self, table, d, on_duplicate=None):
		self.db.calls.append(("insert", table, dict(d), on_duplicate))
		if self.db.raise_integrity_on == table:
			self.db.raise_integrity_on = None
			raise self.db.errors.IntegrityError()
		return self.db.rowcounts.pop(0) if self.db.rowcounts else 1

	async def fetchone(self, sql, args=None):
		self.db.calls.append(("fetchone", sql, list(args or [])))
		return self.db.fetchone_result


class _Errors:
	class IntegrityError(Exception):
		pass


# One row answers every fetchone the bank makes — the book it locks FOR UPDATE,
# the balance it reads back, the side it reports on a locked press — because the
# fake has one slot for all of them. `status`/`freezes_at` belong to the book:
# place_bet re-reads the post inside its own transaction, so a fake that omitted
# them would KeyError before any money moved.
ROW = {"balance": 440, "side": 1, "status": "open", "freezes_at": 9_999_999_999}


class FakeDb:
	def __init__(self):
		self.calls = []
		self.rowcounts = []          # consumed left-to-right by execute/insert
		self.fetchone_result = dict(ROW)
		self.raise_integrity_on = None
		self.errors = _Errors
		self.rolled_back = False

	def answer(self, **overrides):
		"""Override single columns without dropping the rest of the row."""
		self.fetchone_result = {**ROW, **overrides}
		return self

	def sql(self, fragment):
		"""Every recorded statement containing `fragment`."""
		return [c for c in self.calls if c[0] in ("execute", "fetchone", "db.fetchone")
				and fragment in c[1]]

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

	async def fetchone(self, sql, args=None):
		self.calls.append(("db.fetchone", sql, list(args or [])))
		return self.fetchone_result

	async def fetchall(self, sql, args=None):
		self.calls.append(("db.fetchall", sql, list(args or [])))
		return []


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
		tables = [c[1] for c in fake.calls if c[0] == "insert"]
		assert "gold_ledger" in tables
		ledger = next(c[2] for c in fake.calls if c[0] == "insert" and c[1] == "gold_ledger")
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
		row = next(c[2] for c in fake.calls if c[0] == "insert" and c[1] == "prediction_bets")
		assert row["is_player"] == 1

	def test_is_player_defaults_to_false_for_spectators(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 0, 1, 1]
		asyncio.run(gold.place_bet(5, 42, 12, 0, 50, "nick", 1000))
		row = next(c[2] for c in fake.calls if c[0] == "insert" and c[1] == "prediction_bets")
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
		assert not [c for c in fake.calls if c[0] == "insert"]
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
		row = next(c[2] for c in fake.calls if c[0] == "insert" and c[1] == "prediction_bets")
		assert row["side"] == 1 and row["stake"] == 100 and row["post_id"] == 12

	def test_the_bet_ledger_row_is_a_plain_insert(self, monkeypatch):
		""" Bet rows carry idem_key=NULL by design (many presses per user per
		post), so this is the one movement that must NOT be an INSERT IGNORE —
		a swallowed failure here is a stake taken with no ledger row. """
		fake = self.run_ok(monkeypatch)
		ledger = next(c for c in fake.calls if c[0] == "insert" and c[1] == "gold_ledger")
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
		assert next(c[3] for c in fake.calls if c[0] == "insert") == "ignore"

	def test_refunds_and_payouts_are_insert_ignores(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 1]
		asyncio.run(gold.pay_post(5, {1: 225}, 12, 1000))
		assert next(c[3] for c in fake.calls if c[0] == "insert") == "ignore"

		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 1]
		asyncio.run(gold.refund_post(5, [dict(user_id=1, side=0, stake=60)], 12, 1000))
		assert next(c[3] for c in fake.calls if c[0] == "insert") == "ignore"

	def test_the_match_reward_is_an_insert_ignore(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.answer(balance=100)
		fake.rowcounts = [1, 1]
		asyncio.run(gold.grant_match_reward(5, 42, 900, 1000))
		assert next(c[3] for c in fake.calls if c[0] == "insert") == "ignore"


class TestEnsureSeeded:
	def test_first_touch_seeds(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 1]
		assert asyncio.run(gold.ensure_seeded(5, 42, 1000)) is True
		ledger = next(c[2] for c in fake.calls if c[0] == "insert")
		assert ledger["idem_key"] == "seed:5:42" and ledger["amount"] == 500

	def test_second_touch_is_a_noop(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.rowcounts = [0]          # idem key already there
		assert asyncio.run(gold.ensure_seeded(5, 42, 1000)) is False
		# and no balance write happened after the skip
		assert not [c for c in fake.calls if c[0] == "execute" and "gold_balances" in c[1]]


class TestCreditPaths:
	def test_refund_post_credits_each_bettor_idempotently(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 1, 0]     # first refund applies (+balance), second skips
		bets = [dict(user_id=1, side=0, stake=60), dict(user_id=2, side=1, stake=10)]
		done = asyncio.run(gold.refund_post(5, bets, 12, 1000))
		assert done == 1
		keys = [c[2]["idem_key"] for c in fake.calls if c[0] == "insert"]
		assert keys == ["refund:12:1", "refund:12:2"]

	def test_pay_post_uses_payout_keys(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.rowcounts = [1, 1]
		done = asyncio.run(gold.pay_post(5, {1: 225}, 12, 1000))
		assert done == 1
		ledger = next(c[2] for c in fake.calls if c[0] == "insert")
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
		ledger = next(c[2] for c in fake.calls if c[0] == "insert")
		assert ledger["idem_key"] == "reward:900:42" and ledger["amount"] == 4

	def test_at_ceiling_grants_nothing_and_writes_nothing(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.fetchone_result = {"balance": 500}
		assert asyncio.run(gold.grant_match_reward(5, 42, 900, 1000)) == 0
		assert not [c for c in fake.calls if c[0] == "insert"]

	def test_already_granted_is_zero(self, monkeypatch):
		fake = use_fake(monkeypatch)
		fake.fetchone_result = {"balance": 100}
		fake.rowcounts = [0]           # idem key already there
		assert asyncio.run(gold.grant_match_reward(5, 42, 900, 1000)) == 0
