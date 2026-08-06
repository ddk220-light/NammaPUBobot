"""The gold bank's control flow, driven by a scripted fake db.

What matters here is not SQL syntax but the DECISIONS: which paths roll the
transaction back, which return which status, and that an idempotent skip
(INSERT IGNORE rowcount 0) short-circuits without touching a balance."""
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


class FakeDb:
	def __init__(self):
		self.calls = []
		self.rowcounts = []          # consumed left-to-right by execute/insert
		self.fetchone_result = {"balance": 440, "side": 1}
		self.raise_integrity_on = None
		self.errors = _Errors
		self.rolled_back = False

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
		fake.fetchone_result = {"balance": 30}
		status, value = asyncio.run(gold.place_bet(5, 42, 12, 0, 50, "nick", 1000))
		assert status == "insufficient" and value == 30
		assert fake.rolled_back

	def test_other_side_press_is_side_locked(self, monkeypatch):
		fake = use_fake(monkeypatch)
		# balance UPDATE hits; bets same-side UPDATE misses; INSERT hits the PK
		fake.rowcounts = [1, 0]
		fake.raise_integrity_on = "prediction_bets"
		fake.fetchone_result = {"side": 1}
		status, value = asyncio.run(gold.place_bet(5, 42, 12, 0, 50, "nick", 1000))
		assert status == "side_locked" and value == 1
		assert fake.rolled_back          # the stake deduction must not survive

	def test_forged_stake_is_rejected_outright(self, monkeypatch):
		use_fake(monkeypatch)
		try:
			asyncio.run(gold.place_bet(5, 42, 12, 0, 9999, "nick", 1000))
			assert False, "should have raised"
		except ValueError:
			pass


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
