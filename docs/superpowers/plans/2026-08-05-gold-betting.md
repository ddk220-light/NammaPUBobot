# Gold Betting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace free prediction votes with pari-mutuel gold betting: six buttons on the match card, an append-only gold ledger, refunds/payouts that can never double-apply, and a post-match betting report.

**Architecture:** A new `bot/predictions/gold.py` bank owns two new tables (`gold_ledger` append-only + `gold_balances` cache) and performs every gold movement inside a new adapter-level `transaction()`. Bets live in a third new table `prediction_bets` (one row per bettor per post, side-locked by its primary key). The existing `open → freeze → resolve/void` lifecycle in `bot/predictions/flow.py` is rewired from reactions to bets; button clicks route through the global `on_interaction` handler exactly like the quiz (DB-resolvable `custom_id`s, redeploy-safe, no persistent View re-registration).

**Tech Stack:** Python 3.11, nextcord, aiomysql, MySQL 8, pytest (pure-function tests only — CI has no MySQL; conftest stubs `core.*`).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-05-gold-betting-design.md`. Read it before starting.
- Stakes are exactly `(10, 50, 100)`. Seed is `500`. Match reward is `min(10, max(0, 500 − balance))` — playing never lifts a balance above 500.
- Spectators only: anyone in the match roster cannot bet.
- First press locks the side; presses are additive; no cancels, no switching.
- One-sided pool at freeze → status `no_action`, immediate full refund.
- Payout: `floor(stake_i × total_pool / winning_pool)`; the remainder is burned.
- Every seed/reward/refund/payout is an idempotent `INSERT IGNORE` keyed by `idem_key`; `bet` rows have `idem_key = NULL`.
- `bot/` and `core/` files use **tabs** for indentation (so do `tests/`). Match exactly.
- Registry: every new `ensure_table` needs a `core/data_registry.py` entry or `tests/test_data_registry.py` fails.
- After every task: `ruff check .` and `pytest tests/` must pass.
- Commit format: `feat(betting): …` / `test(betting): …`, ending with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Adapter `transaction()`

**Files:**
- Modify: `core/DBAdapters/mysql.py` (add `Transaction` class + `Adapter.transaction()`; nothing existing changes)
- Test: `tests/test_db_adapter_transaction.py` (new)

**Interfaces:**
- Produces: `async with db.transaction() as tx:` where `tx` has:
  - `await tx.execute(sql, args) -> int` — **rowcount** (NOT lastrowid; documented difference from `Adapter.execute`)
  - `await tx.fetchone(sql, args) -> dict | None`
  - `await tx.fetchall(sql, args) -> list[dict]`
  - `await tx.insert(table, d, on_duplicate=None) -> int` — rowcount; `on_duplicate="ignore"` builds `INSERT IGNORE`, duplicate → returns 0
- Commit on clean exit, rollback on any exception (which propagates). MySQL errors are wrapped via `wrap_exc` like every other adapter method.

- [ ] **Step 1: Write the failing test**

Create `tests/test_db_adapter_transaction.py`. The adapter is import-safe without a DB (constructor only parses the address string); drive it with fakes. Note `conftest.py` stubs `core.*` modules but NOT `core.DBAdapters` — import the real module directly.

```python
"""Transaction context manager on the MySQL adapter — begin/commit/rollback
ordering and the rowcount-returning Transaction handle, driven by fakes.
No MySQL involved: the fakes record the calls the pool/connection receive."""
from __future__ import annotations

import asyncio

from core.DBAdapters.mysql import Adapter


class FakeCursor:
	def __init__(self, conn):
		self.conn = conn
		self.rowcount = 0
		self.executed = []

	async def execute(self, sql, args=None):
		self.executed.append((sql, list(args) if args else []))
		self.conn.log.append("execute")
		# INSERT IGNORE hitting a duplicate reports 0 affected rows
		self.rowcount = 0 if getattr(self.conn, "duplicate_next", False) else 1
		self.conn.duplicate_next = False

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

	def acquire(self):
		pool = self

		class _Ctx:
			async def __aenter__(self):
				return pool._conn

			async def __aexit__(self, *exc):
				return False

		return _Ctx()


def make_adapter():
	a = Adapter("user:pass@host:3306/dbname")
	conn = FakeConn()
	a.pool = FakePool(conn)
	return a, conn


class TestTransaction:
	def test_commits_on_clean_exit(self):
		a, conn = make_adapter()

		async def run():
			async with a.transaction() as tx:
				await tx.execute("UPDATE t SET x=1")
		asyncio.run(run())
		assert conn.log == ["begin", "execute", "commit"]

	def test_rolls_back_and_reraises_on_exception(self):
		a, conn = make_adapter()

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

	def test_execute_returns_rowcount(self):
		a, conn = make_adapter()

		async def run():
			async with a.transaction() as tx:
				return await tx.execute("UPDATE t SET x=1 WHERE y=%s", [2])
		assert asyncio.run(run()) == 1

	def test_insert_ignore_duplicate_returns_zero(self):
		a, conn = make_adapter()

		async def run():
			async with a.transaction() as tx:
				conn.duplicate_next = True
				return await tx.insert("gold_ledger", {"a": 1}, on_duplicate="ignore")
		assert asyncio.run(run()) == 0

	def test_insert_builds_insert_ignore_sql(self):
		a, conn = make_adapter()

		async def run():
			async with a.transaction() as tx:
				await tx.insert("gold_ledger", {"a": 1, "b": 2}, on_duplicate="ignore")
		asyncio.run(run())
		sql, args = conn._cur.executed[0]
		assert sql.startswith("INSERT IGNORE INTO gold_ledger")
		assert args == [1, 2]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_db_adapter_transaction.py -v`
Expected: FAIL / ERROR with `AttributeError: 'Adapter' object has no attribute 'transaction'`

- [ ] **Step 3: Implement**

In `core/DBAdapters/mysql.py`, add at the top (after existing imports):

```python
from contextlib import asynccontextmanager
```

Add before `class Adapter`:

```python
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
		try:
			await self._cur.execute(*args)
		except mysqlErr.Error as e:
			self._adapter.wrap_exc(e)
		return self._cur.rowcount

	async def fetchone(self, *args):
		try:
			await self._cur.execute(*args)
			return await self._cur.fetchone()
		except mysqlErr.Error as e:
			self._adapter.wrap_exc(e)

	async def fetchall(self, *args):
		try:
			await self._cur.execute(*args)
			return await self._cur.fetchall()
		except mysqlErr.Error as e:
			self._adapter.wrap_exc(e)

	async def insert(self, table, d, on_duplicate=None):
		request = self._adapter._mysql_insert(d.keys(), table, on_duplicate)
		return await self.execute(request, list(d.values()))
```

Add to `class Adapter` (after `fetchall`):

```python
	@asynccontextmanager
	async def transaction(self):
		"""One pooled connection, BEGIN .. COMMIT, ROLLBACK on any exception
		(which propagates). The pool runs autocommit=True; conn.begin() opens an
		explicit transaction that suspends autocommit until commit/rollback, so
		nothing else on this connection leaks in."""
		async with self.pool.acquire() as conn:
			await conn.begin()
			try:
				async with conn.cursor() as cur:
					yield Transaction(self, cur)
			except BaseException:
				await conn.rollback()
				raise
			else:
				await conn.commit()
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_db_adapter_transaction.py -v`
Expected: 5 PASS

- [ ] **Step 5: Full check + commit**

Run: `ruff check . && pytest tests/ -q`
Expected: clean, all pass.

```bash
git add core/DBAdapters/mysql.py tests/test_db_adapter_transaction.py
git commit -m "feat(db): transaction() on the MySQL adapter — begin/commit/rollback with a rowcount-returning handle

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Pure betting math + custom_id routing in `scoring.py`

**Files:**
- Modify: `bot/predictions/scoring.py` (add constants + five functions; delete `resolve_ballots` and `tally` — the reaction model is going away, and `grade` goes too: correctness is computed in SQL from `side = winner_idx`)
- Modify: `tests/test_predictions.py` (drop `TestResolveBallots`, `TestTally`, `TestGrade`; add the new classes below — keep `TestSplitPct` and all view tests for now, view changes come in Task 6)

**Interfaces:**
- Produces (all pure, no imports beyond stdlib):
  - `STAKES = (10, 50, 100)`, `SEED_AMOUNT = 500`, `MATCH_REWARD = 10`, `REWARD_CEILING = 500`
  - `parse_bet_custom_id(cid) -> (post_id, side, stake) | None` — `'bet:{post_id}:{side}:{stake}'`; side must be 0/1, stake must be in `STAKES` (custom_ids arrive from the client — a forged stake must parse to None)
  - `pools(bets) -> (pool0, pool1)` — `bets` is an iterable of dicts with `side`, `stake`
  - `payouts(bets, winner_idx) -> (paid, burned)` — `paid` is `{user_id: payout}`; returns `({}, 0)` when either pool is empty (caller refunds instead)
  - `reward_amount(balance) -> int`
  - `multiplier(pool_side, pool_other) -> float | None` — what a winning stake on this side multiplies by; None when the side's pool is empty
- `split_pct` stays (frozen embed still shows a percentage split of bettors).

- [ ] **Step 1: Write the failing tests**

In `tests/test_predictions.py`, delete `TestResolveBallots`, `TestTally`, `TestGrade` and add (module docstring: update its first line to say "betting math" rather than reaction ballots):

```python
class TestParseBetCustomId:
	def test_parses_a_valid_bet(self):
		assert scoring.parse_bet_custom_id("bet:12:0:50") == (12, 0, 50)

	def test_side_one(self):
		assert scoring.parse_bet_custom_id("bet:7:1:100") == (7, 1, 100)

	def test_foreign_prefix_is_none(self):
		assert scoring.parse_bet_custom_id("quiz:12:reveal") is None

	def test_forged_stake_is_none(self):
		# custom_ids come from the client; only the three tiers are money.
		assert scoring.parse_bet_custom_id("bet:12:0:9999") is None

	def test_forged_side_is_none(self):
		assert scoring.parse_bet_custom_id("bet:12:2:50") is None

	def test_garbage_is_none(self):
		assert scoring.parse_bet_custom_id("bet:x:y:z") is None
		assert scoring.parse_bet_custom_id("") is None


class TestPools:
	def test_sums_each_side(self):
		bets = [dict(user_id=1, side=0, stake=150), dict(user_id=2, side=0, stake=50),
				dict(user_id=3, side=1, stake=100)]
		assert scoring.pools(bets) == (200, 100)

	def test_empty(self):
		assert scoring.pools([]) == (0, 0)


class TestPayouts:
	def test_winners_split_the_whole_pot_proportionally(self):
		# The spec's worked example: 150+50 vs 100, side 0 wins.
		bets = [dict(user_id=1, side=0, stake=150), dict(user_id=2, side=0, stake=50),
				dict(user_id=3, side=1, stake=100)]
		paid, burned = scoring.payouts(bets, 0)
		assert paid == {1: 225, 2: 75}
		assert burned == 0

	def test_flooring_burns_the_remainder(self):
		# total=25, win_pool=20: 10*25//20=12, 10*25//20=12, burned 1.
		bets = [dict(user_id=1, side=0, stake=10), dict(user_id=2, side=0, stake=10),
				dict(user_id=3, side=1, stake=5)]
		paid, burned = scoring.payouts(bets, 0)
		assert paid == {1: 12, 2: 12}
		assert burned == 1

	def test_every_winner_gets_at_least_their_stake_back(self):
		bets = [dict(user_id=1, side=0, stake=10), dict(user_id=2, side=0, stake=100),
				dict(user_id=3, side=1, stake=10)]
		paid, _ = scoring.payouts(bets, 0)
		assert paid[1] >= 10 and paid[2] >= 100

	def test_empty_losing_pool_signals_refund(self):
		bets = [dict(user_id=1, side=0, stake=10)]
		assert scoring.payouts(bets, 0) == ({}, 0)

	def test_empty_winning_pool_signals_refund(self):
		bets = [dict(user_id=1, side=0, stake=10)]
		assert scoring.payouts(bets, 1) == ({}, 0)

	def test_no_bets(self):
		assert scoring.payouts([], 0) == ({}, 0)


class TestRewardAmount:
	def test_full_reward_below_the_ceiling(self):
		assert scoring.reward_amount(480) == 10

	def test_partial_reward_tops_up_to_exactly_500(self):
		assert scoring.reward_amount(496) == 4

	def test_nothing_at_the_ceiling(self):
		assert scoring.reward_amount(500) == 0

	def test_nothing_above_the_ceiling(self):
		assert scoring.reward_amount(620) == 0

	def test_zero_balance_gets_the_full_reward(self):
		assert scoring.reward_amount(0) == 10


class TestMultiplier:
	def test_underdog_pays_more(self):
		assert scoring.multiplier(100, 200) == 3.0
		assert scoring.multiplier(200, 100) == 1.5

	def test_empty_side_is_none(self):
		assert scoring.multiplier(0, 100) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_predictions.py -v -k "Parse or Pools or Payouts or Reward or Multiplier"`
Expected: FAIL with `AttributeError: module 'bot.predictions.scoring' has no attribute 'parse_bet_custom_id'`

- [ ] **Step 3: Implement in `bot/predictions/scoring.py`**

Replace the module docstring's ballot description with betting math, delete `resolve_ballots`, `tally` and `grade`, keep `split_pct`, and add:

```python
# The whole economy in four numbers. STAKES is also the input-validation
# whitelist: custom_ids arrive from the client, so any stake not in this
# tuple is a forgery, not a feature request.
STAKES = (10, 50, 100)
SEED_AMOUNT = 500
MATCH_REWARD = 10
REWARD_CEILING = 500


def parse_bet_custom_id(cid):
	"""Route a component custom_id. 'bet:{post_id}:{side}:{stake}' ->
	(post_id, side, stake); anything else — foreign prefix, non-int parts,
	side not 0/1, stake not a real tier — is None."""
	if not cid or not cid.startswith("bet:"):
		return None
	parts = cid.split(":")
	if len(parts) != 4:
		return None
	try:
		post_id, side, stake = int(parts[1]), int(parts[2]), int(parts[3])
	except ValueError:
		return None
	if side not in (0, 1) or stake not in STAKES:
		return None
	return post_id, side, stake


def pools(bets):
	"""[{side, stake}, ...] -> (pool0, pool1)."""
	pool0 = sum(b["stake"] for b in bets if b["side"] == 0)
	pool1 = sum(b["stake"] for b in bets if b["side"] == 1)
	return pool0, pool1


def payouts(bets, winner_idx):
	"""Pari-mutuel split: winners share the WHOLE pot in proportion to stake.

	[{user_id, side, stake}, ...] -> ({user_id: payout}, burned).
	floor() keeps gold integral; the crumbs are burned, never minted back.
	Either pool empty -> ({}, 0): a one-sided book has no odds, the caller
	refunds instead (the freeze no_action rule makes this unreachable at
	resolve, but the math must not invent an answer if it ever isn't).
	"""
	total = sum(b["stake"] for b in bets)
	win_pool = sum(b["stake"] for b in bets if b["side"] == winner_idx)
	if not win_pool or win_pool == total:
		return {}, 0
	paid = {b["user_id"]: b["stake"] * total // win_pool
			for b in bets if b["side"] == winner_idx}
	return paid, total - sum(paid.values())


def reward_amount(balance):
	"""Playing regenerates a depleted balance toward REWARD_CEILING and does
	nothing at or above it — the faucet is a lifeline, not an income."""
	return min(MATCH_REWARD, max(0, REWARD_CEILING - balance))


def multiplier(pool_side, pool_other):
	"""What a winning stake on this side multiplies by right now, or None
	when nobody has bet the side yet (no odds without a book)."""
	if not pool_side:
		return None
	return (pool_side + pool_other) / pool_side
```

- [ ] **Step 4: Run the module's tests**

Run: `pytest tests/test_predictions.py -v`
Expected: new classes PASS; view tests still PASS (view.py untouched so far).

- [ ] **Step 5: Check nothing else imported the deleted functions**

Run: `grep -rn "resolve_ballots\|scoring.tally\|scoring.grade" bot/ tests/`
Expected: only hits inside `bot/predictions/flow.py` (`_freeze` uses `resolve_ballots`/`tally`, `resolve_for_match` uses `grade`) — those call sites are rewritten in Task 8 and flow.py is not imported by the test suite. If anything ELSE hits, stop and fix it now.
Note: `pytest tests/ -q` must still pass (flow.py is never imported under the conftest stubs); `ruff check .` must pass.
Sequencing note: from this task until Task 8 lands, `flow.py` still references the deleted functions at runtime — the branch is NOT deployable mid-way. That is fine on a feature branch; do not cherry-pick tasks 2–7 to main alone.

- [ ] **Step 6: Commit**

```bash
git add bot/predictions/scoring.py tests/test_predictions.py
git commit -m "feat(betting): pari-mutuel math, faucet rule and bet custom_id routing — the votes-era ballot logic retires

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Schema + registry

**Files:**
- Modify: `bot/predictions/__init__.py` (three new `ensure_table` blocks after the existing two)
- Modify: `core/data_registry.py` (three new entries next to the prediction_* ones)

**Interfaces:**
- Produces tables: `gold_ledger`, `gold_balances`, `prediction_bets` (columns below — Tasks 4/5 write to exactly these names).

- [ ] **Step 1: Declare the tables**

In `bot/predictions/__init__.py`, after the `prediction_votes` block, add:

```python
# ── gold betting (stage: gold-betting spec, 2026-08-05) ─────────────────
# gold_ledger is APPEND-ONLY: no UPDATE, no DELETE, ever. idem_key makes
# every non-bet movement impossible to apply twice at the schema level
# (unique index; MySQL unique ignores NULLs, and bet rows are NULL because
# a user may press the buttons many times). Balance truth is
# SUM(amount) per (community_id, user_id); gold_balances is the spendable
# cache written in the same transaction — see bot/predictions/gold.py.
db.ensure_table(dict(
	tname="gold_ledger",
	columns=[
		dict(cname="id", ctype=db.types.int, autoincrement=True),
		dict(cname="community_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		# seed | match_reward | bet | refund | payout | admin_adjust
		dict(cname="entry_type", ctype=db.types.str),
		dict(cname="amount", ctype=db.types.int),          # signed; negative only for 'bet'
		dict(cname="match_id", ctype=db.types.int, notnull=False),
		dict(cname="post_id", ctype=db.types.int, notnull=False),
		dict(cname="created_at", ctype=db.types.int),
		dict(cname="idem_key", ctype=db.types.str, notnull=False, unique=True),
	],
	primary_keys=["id"],
	indexes=[("ix_gold_ledger_holder", ["community_id", "user_id"])],
))

db.ensure_table(dict(
	tname="gold_balances",
	columns=[
		dict(cname="community_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="balance", ctype=db.types.int),
		dict(cname="updated_at", ctype=db.types.int),
	],
	primary_keys=["community_id", "user_id"],
))

# One row per bettor per post — the composite PK IS the side lock: a second
# side for the same (post, user) has nowhere to live. `stake` accumulates
# across presses; `nick` is display-only, captured at press time for the
# betting report and the leaderboard (the prediction_votes pattern).
db.ensure_table(dict(
	tname="prediction_bets",
	columns=[
		dict(cname="post_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="nick", ctype=db.types.str),
		dict(cname="side", ctype=db.types.int),
		dict(cname="stake", ctype=db.types.int),
		dict(cname="updated_at", ctype=db.types.int),
	],
	primary_keys=["post_id", "user_id"],
))
```

Also update the module docstring's first paragraph: predictions are now bet with gold via buttons, not called with reactions (keep the flow.py/jobs naming warning untouched — it is load-bearing).

- [ ] **Step 2: Run the registry test to see it fail**

Run: `pytest tests/test_data_registry.py -v`
Expected: FAIL — three declared tables missing from the registry.

- [ ] **Step 3: Register them**

In `core/data_registry.py`, directly after the `prediction_votes` entry:

```python
	# prediction_votes stopped being written when gold betting replaced free
	# votes (gold-betting spec, 2026-08-05); rows stay for leaderboard history.
	"prediction_bets": dict(
		layer="core", tenancy="channel", writers=("bot/predictions/gold.py",), retention="forever"
	),
	# Append-only money trail — the ONLY module allowed to write gold is
	# bot/predictions/gold.py, and gold_ledger rows are never updated/deleted.
	"gold_ledger": dict(
		layer="core", tenancy="community", writers=("bot/predictions/gold.py",), retention="forever"
	),
	"gold_balances": dict(
		layer="core", tenancy="community", writers=("bot/predictions/gold.py",), retention="forever"
	),
```

Also update the existing `prediction_votes` entry's comment (or add one) noting its writer is historical-only after this feature (leave `writers` as-is — the registry records today's code, and `store.py` still contains no new vote writes after Task 5 deletes them; revisit in Task 5).

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_data_registry.py tests/ -q && ruff check .`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bot/predictions/__init__.py core/data_registry.py
git commit -m "feat(betting): declare gold_ledger, gold_balances and prediction_bets

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: The gold bank (`bot/predictions/gold.py`)

**Files:**
- Create: `bot/predictions/gold.py`
- Test: `tests/test_predictions_gold.py` (new; drives the bank with a scripted fake `db`)

**Interfaces:**
- Consumes: `db.transaction()` (Task 1), `scoring.SEED_AMOUNT / reward_amount / STAKES` (Task 2), tables (Task 3).
- Produces (all `async`, all take `now` as int epoch seconds — never call `time.time()` inside, callers pass it):
  - `balance(community_id, user_id) -> int` (0 when never seeded)
  - `ensure_seeded(community_id, user_id, now) -> bool` (True only when the seed applied now)
  - `bulk_seed(now) -> int` (idempotent, every boot; count newly seeded)
  - `place_bet(community_id, user_id, post_id, side, stake, nick, now) -> (status, value)` where status ∈ `'ok'` (value = new balance), `'insufficient'` (value = current balance), `'side_locked'` (value = the locked side 0/1)
  - `refund_post(community_id, bets, post_id, now) -> int` (bets = `store.bets_for()` rows; count refunded)
  - `pay_post(community_id, paid, post_id, now) -> int` (paid = `scoring.payouts()[0]`)
  - `grant_match_reward(community_id, user_id, match_id, now) -> int` (gold granted, 0 at/above ceiling or already granted)
  - `top_balances(community_id, limit=200) -> [{user_id, balance}]`
  - `recent_entries(community_id, user_id, limit=8) -> [{entry_type, amount, match_id, post_id, created_at}]`
  - `reconcile() -> [rows]` (empty = every balance equals its ledger sum)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_predictions_gold.py`. The fake `db` scripts rowcounts per statement and records SQL, letting the money-flow control logic (rollback paths, idempotent skips, status returns) be tested without MySQL:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_predictions_gold.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bot.predictions.gold'` (or ImportError).

- [ ] **Step 3: Implement `bot/predictions/gold.py`** (tabs)

```python
# -*- coding: utf-8 -*-
"""The gold bank — the ONLY module that moves gold.

Every movement is one transaction: an append-only gold_ledger row plus the
matching gold_balances update, committed together or not at all. Non-bet
movements carry an idem_key with a unique index, so seeds, rewards, refunds
and payouts are impossible to apply twice — re-running a half-finished sweep
skips the rows that already exist (INSERT IGNORE, rowcount 0) and applies the
rest. Balance truth is SUM(gold_ledger.amount); gold_balances is the
spendable cache, and reconcile() can prove the two agree.

No nextcord and no time.time() in here: callers pass `now`, and the module
stays importable (and its control flow testable) under the conftest stubs."""
from core.console import log
from core.database import db

from . import scoring


class _Insufficient(Exception):
	pass


class _SideLocked(Exception):
	pass


async def balance(community_id, user_id):
	"""Spendable gold; 0 for a user who has never been seeded."""
	row = await db.fetchone(
		"SELECT balance FROM gold_balances WHERE community_id=%s AND user_id=%s",
		[community_id, user_id])
	return int(row["balance"]) if row else 0


async def ensure_seeded(community_id, user_id, now):
	"""Grant the one-time starting gold. True only when the seed applied NOW —
	the caller can greet a first-time bettor. Safe to call on every touch."""
	async with db.transaction() as tx:
		applied = await tx.insert("gold_ledger", dict(
			community_id=community_id, user_id=user_id, entry_type="seed",
			amount=scoring.SEED_AMOUNT, created_at=now,
			idem_key=f"seed:{community_id}:{user_id}"), on_duplicate="ignore")
		if not applied:
			return False
		# Upsert-with-increment: correct even if a balances row somehow already
		# exists, and creates it when (normally) it does not.
		await tx.execute(
			"INSERT INTO gold_balances (community_id, user_id, balance, updated_at) "
			"VALUES (%s, %s, %s, %s) "
			"ON DUPLICATE KEY UPDATE balance=balance+%s, updated_at=%s",
			[community_id, user_id, scoring.SEED_AMOUNT, now, scoring.SEED_AMOUNT, now])
		return True


async def bulk_seed(now):
	"""Seed every known player in every community. Idempotent per
	(community, user) via the seed idem_key, so running this on every boot is
	safe — after the first pass it inserts nothing. Returns newly seeded."""
	rows = await db.fetchall(
		"SELECT DISTINCT cc.community_id, pr.user_id "
		"FROM player_ratings pr "
		"JOIN community_channels cc ON cc.channel_id = pr.channel_id") or []
	seeded = 0
	for r in rows:
		try:
			if await ensure_seeded(r["community_id"], r["user_id"], now):
				seeded += 1
		except Exception as e:
			log.error(f"Gold seed failed for {r['community_id']}/{r['user_id']}: {e}")
	return seeded


async def place_bet(community_id, user_id, post_id, side, stake, nick, now):
	"""One press of a bet button, atomically.

	-> ('ok', new_balance) | ('insufficient', balance) | ('side_locked', locked_side)

	Inside one transaction: conditional balance decrement (matching zero rows
	means not enough gold), the prediction_bets upsert whose composite PK IS
	the side lock (same-side UPDATE, else INSERT; a duplicate-key error means
	the user is on the other side), and the ledger row. Any rejection raises,
	so the stake deduction can never survive a refused bet."""
	if stake not in scoring.STAKES:
		raise ValueError(f"stake {stake} is not one of {scoring.STAKES}")
	try:
		async with db.transaction() as tx:
			spent = await tx.execute(
				"UPDATE gold_balances SET balance=balance-%s, updated_at=%s "
				"WHERE community_id=%s AND user_id=%s AND balance>=%s",
				[stake, now, community_id, user_id, stake])
			if not spent:
				raise _Insufficient()
			added = await tx.execute(
				"UPDATE prediction_bets SET stake=stake+%s, nick=%s, updated_at=%s "
				"WHERE post_id=%s AND user_id=%s AND side=%s",
				[stake, nick, now, post_id, user_id, side])
			if not added:
				try:
					await tx.insert("prediction_bets", dict(
						post_id=post_id, user_id=user_id, nick=nick,
						side=side, stake=stake, updated_at=now))
				except db.errors.IntegrityError:
					raise _SideLocked() from None
			await tx.insert("gold_ledger", dict(
				community_id=community_id, user_id=user_id, entry_type="bet",
				amount=-stake, post_id=post_id, created_at=now))
			row = await tx.fetchone(
				"SELECT balance FROM gold_balances WHERE community_id=%s AND user_id=%s",
				[community_id, user_id])
			return "ok", int(row["balance"])
	except _Insufficient:
		return "insufficient", await balance(community_id, user_id)
	except _SideLocked:
		row = await db.fetchone(
			"SELECT side FROM prediction_bets WHERE post_id=%s AND user_id=%s",
			[post_id, user_id])
		return "side_locked", int(row["side"]) if row else side


async def _credit(community_id, user_id, entry_type, amount, idem_key, now,
				  match_id=None, post_id=None):
	"""Apply one positive movement exactly once. False when idem_key already
	applied. A missing balances row aborts the whole transaction — the ledger
	and the cache move together or not at all."""
	if amount <= 0:
		return False
	async with db.transaction() as tx:
		applied = await tx.insert("gold_ledger", dict(
			community_id=community_id, user_id=user_id, entry_type=entry_type,
			amount=amount, match_id=match_id, post_id=post_id,
			created_at=now, idem_key=idem_key), on_duplicate="ignore")
		if not applied:
			return False
		bumped = await tx.execute(
			"UPDATE gold_balances SET balance=balance+%s, updated_at=%s "
			"WHERE community_id=%s AND user_id=%s",
			[amount, now, community_id, user_id])
		if not bumped:
			raise RuntimeError(
				f"gold_balances row missing for {community_id}/{user_id} "
				f"({entry_type} {idem_key})")
		return True


async def refund_post(community_id, bets, post_id, now):
	"""Give every bettor their full stake back, exactly once each.
	`bets` is store.bets_for(post_id). Returns how many applied now."""
	done = 0
	for b in bets:
		if await _credit(community_id, b["user_id"], "refund", b["stake"],
						 f"refund:{post_id}:{b['user_id']}", now, post_id=post_id):
			done += 1
	return done


async def pay_post(community_id, paid, post_id, now):
	"""Apply scoring.payouts() to the bank, exactly once per winner."""
	done = 0
	for user_id, amount in paid.items():
		if await _credit(community_id, user_id, "payout", amount,
						 f"payout:{post_id}:{user_id}", now, post_id=post_id):
			done += 1
	return done


async def grant_match_reward(community_id, user_id, match_id, now):
	"""The playing faucet: top the balance up toward the ceiling, never above.
	Returns the gold granted (0 at/above ceiling, or when already granted)."""
	async with db.transaction() as tx:
		row = await tx.fetchone(
			"SELECT balance FROM gold_balances "
			"WHERE community_id=%s AND user_id=%s FOR UPDATE",
			[community_id, user_id])
		amount = scoring.reward_amount(int(row["balance"]) if row else 0)
		if not amount:
			return 0
		applied = await tx.insert("gold_ledger", dict(
			community_id=community_id, user_id=user_id, entry_type="match_reward",
			amount=amount, match_id=match_id, created_at=now,
			idem_key=f"reward:{match_id}:{user_id}"), on_duplicate="ignore")
		if not applied:
			return 0
		bumped = await tx.execute(
			"UPDATE gold_balances SET balance=balance+%s, updated_at=%s "
			"WHERE community_id=%s AND user_id=%s",
			[amount, now, community_id, user_id])
		if not bumped:
			raise RuntimeError(f"gold_balances row missing for {community_id}/{user_id} (reward)")
		return amount


async def top_balances(community_id, limit=200):
	return await db.fetchall(
		"SELECT user_id, balance FROM gold_balances "
		"WHERE community_id=%s ORDER BY balance DESC, user_id ASC LIMIT " + str(int(limit)),
		[community_id]) or []


async def recent_entries(community_id, user_id, limit=8):
	return await db.fetchall(
		"SELECT entry_type, amount, match_id, post_id, created_at FROM gold_ledger "
		"WHERE community_id=%s AND user_id=%s ORDER BY id DESC LIMIT " + str(int(limit)),
		[community_id, user_id]) or []


async def reconcile():
	"""Every (community, user) whose cached balance disagrees with its ledger
	sum. Empty means the invariant holds everywhere. Any row here is a bug."""
	return await db.fetchall(
		"SELECT b.community_id, b.user_id, b.balance, "
		"       COALESCE(SUM(l.amount), 0) AS ledger_sum "
		"FROM gold_balances b "
		"LEFT JOIN gold_ledger l "
		"  ON l.community_id=b.community_id AND l.user_id=b.user_id "
		"GROUP BY b.community_id, b.user_id, b.balance "
		"HAVING b.balance <> ledger_sum") or []
```

Note for the implementer: `place_bet`'s conditional UPDATEs rely on rowcount = *changed* rows (aiomysql default, no `FOUND_ROWS` flag); every UPDATE here changes `balance` or `stake` by a non-zero amount, so a match is always a change. `grant_match_reward` reads the balance `FOR UPDATE` inside the transaction so a concurrent bet can't slip between the read and the grant. Also note the ensure_seeded call ordering used by later tasks: bettors and reward recipients are always `ensure_seeded` first, so `_credit`'s missing-row RuntimeError genuinely means corruption, not a new user.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_predictions_gold.py tests/test_predictions.py -v`
Expected: all PASS.

- [ ] **Step 5: Full check + commit**

Run: `ruff check . && pytest tests/ -q`

```bash
git add bot/predictions/gold.py tests/test_predictions_gold.py
git commit -m "feat(betting): the gold bank — transactional ledger + balance cache with idempotent movements

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Store — bets accessors, post lookup, leaderboard union

**Files:**
- Modify: `bot/predictions/store.py`

**Interfaces:**
- Consumes: tables from Task 3.
- Produces:
  - `get_post(post_id) -> dict | None`
  - `bets_for(post_id) -> [{user_id, nick, side, stake}]` ordered stake DESC then user_id ASC
  - `no_action(post_id, now)` — terminal status for a one-sided book
  - `leaderboard(channel_id=None)` and `user_stats(user_id, channel_id=None)` — same shapes as today, now a UNION of historical votes and new bets
- Deletes: `save_ballots`, `ballots_for`, `mark_correct` (the reaction-tally trio — flow.py stops calling them in Task 8; they must not survive as dead code).

- [ ] **Step 1: Implement**

In `bot/predictions/store.py`:

1. Add after `set_message_id`:

```python
async def get_post(post_id):
	return await db.fetchone("SELECT * FROM prediction_posts WHERE id=%s", [post_id])
```

2. Replace the whole `# ── votes ──` section (deleting `save_ballots`, `ballots_for`, `mark_correct`) with:

```python
# ── bets ─────────────────────────────────────────────────────────────────
# All WRITES to prediction_bets live in bot/predictions/gold.py (inside the
# same transaction that moves the gold); the store only reads them.
async def bets_for(post_id):
	"""[{user_id, nick, side, stake}] biggest stake first — the order every
	report and payout roll-call presents."""
	return await db.fetchall(
		"SELECT user_id, nick, side, stake FROM prediction_bets "
		"WHERE post_id=%s ORDER BY stake DESC, user_id ASC", [post_id]) or []


async def no_action(post_id, now):
	"""Terminal status for a one-sided book: nobody on the other side, every
	stake refunded at freeze time. Distinct from void so the card and the
	audit trail say what actually happened."""
	await db.update("prediction_posts", {"status": "no_action", "resolved_at": now}, {"id": post_id})
```

3. Replace `_LB_SQL` and both aggregate functions. Correctness on bets is computed in SQL (`b.side = p.winner_idx`) — there is no `is_correct` column on bets, and votes keep using their frozen `is_correct`:

```python
# ── aggregates ───────────────────────────────────────────────────────────
# One prediction = one row of `u`: historical reaction votes (frozen
# is_correct) UNION ALL gold bets (side graded against winner_idx at read
# time). A user appears once per predicted match either way, so the
# accuracy leaderboard carried straight over when betting replaced votes.
_LB_UNION = (
	"SELECT v.user_id, v.nick, COALESCE(v.is_correct, 0) AS correct, p.channel_id "
	"FROM prediction_votes v JOIN prediction_posts p ON p.id = v.post_id "
	"WHERE p.status='resolved' "
	"UNION ALL "
	"SELECT b.user_id, b.nick, (b.side = p.winner_idx) AS correct, p.channel_id "
	"FROM prediction_bets b JOIN prediction_posts p ON p.id = b.post_id "
	"WHERE p.status='resolved'"
)

_LB_SQL = (
	"SELECT user_id, MAX(nick) AS nick, "
	"       COALESCE(SUM(correct), 0) AS correct, COUNT(*) AS total "
	"FROM (" + _LB_UNION + ") u "
	"{channel}"
	"GROUP BY user_id "
	"ORDER BY correct DESC, total ASC"
)


async def leaderboard(channel_id=None):
	"""[{user_id, nick, correct, total}] best-first across resolved posts."""
	if channel_id is None:
		return await db.fetchall(_LB_SQL.format(channel="")) or []
	return await db.fetchall(_LB_SQL.format(channel="WHERE channel_id=%s "), [channel_id]) or []


async def user_stats(user_id, channel_id=None):
	"""(correct, total) for one user across resolved posts."""
	sql = (
		"SELECT COALESCE(SUM(correct), 0) AS correct, COUNT(*) AS total "
		"FROM (" + _LB_UNION + ") u WHERE user_id=%s"
	)
	params = [user_id]
	if channel_id is not None:
		sql += " AND channel_id=%s"
		params.append(channel_id)
	rows = await db.fetchall(sql, params)
	if not rows:
		return 0, 0
	return int(rows[0]["correct"] or 0), int(rows[0]["total"] or 0)
```

4. Update the module docstring: persistence for posts and read-side of bets; gold writes live in gold.py.

- [ ] **Step 2: Check call sites of the deleted functions**

Run: `grep -rn "save_ballots\|ballots_for\|mark_correct" bot/ tests/`
Expected: only `bot/predictions/flow.py` (rewritten in Task 8). Anything else: stop and fix.

- [ ] **Step 3: Run checks**

Run: `ruff check . && pytest tests/ -q`
Expected: pass (store.py is only imported through the stubs; syntax/lint is the check here).

- [ ] **Step 4: Commit**

```bash
git add bot/predictions/store.py
git commit -m "feat(betting): store reads bets, posts get no_action, the leaderboard unions votes-era history with bets

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Betting UI — pure lines + embeds/buttons

**Files:**
- Modify: `bot/predictions/view.py` (rewrite open/frozen/result lines for pools; add confirm/no-action/report/gold lines)
- Modify: `bot/predictions/embeds.py` (bet_view buttons; embed wrappers for the new lines)
- Modify: `tests/test_predictions.py` (view tests — rewrite the classes for the changed signatures, add new ones)

**Interfaces:**
- Consumes: `scoring.pools`, `scoring.multiplier`, `scoring.STAKES`, `TEAM_EMOJIS`.
- Produces (view.py, all pure):
  - `open_lines(team0, team1, minutes, match_id, pool0=0, pool1=0)`
  - `frozen_lines(team0, team1, pool0, pool1, bettors0, bettors1)`
  - `no_action_lines(team0, team1)`
  - `report_lines(team0, team1, winner_idx, bets, paid, max_named=25)` — the post-match betting report: pot + per-side pools, winners with stake → payout (+net), losers with stake, both capped with "+N more"
  - `bet_confirm_lines(team_name, stake, total_stake, pool0, pool1, balance_after)`
  - `voided_lines(reason)` (trivial passthrough, keeps flow.py free of copy)
  - `gold_lines(balance_amount, entries)` — `/gold`; entry labels: seed "Starting gold", match_reward "Match played", bet "Bet placed", refund "Refund", payout "Winnings", admin_adjust "Adjustment"
  - `gold_top_lines(rows, page=1, per_page=10)` — rows `[{nick, balance}]` sorted best-first
  - `leaderboard_lines` and `rank_field` stay as they are.
- Produces (embeds.py):
  - `bet_view(post_id)` — nextcord View, six buttons, custom_id `bet:{post_id}:{side}:{stake}`, side 0 row 0 `ButtonStyle.primary`, side 1 row 1 `ButtonStyle.danger`, `emoji=TEAM_EMOJIS[side]`, `label=str(stake)`, `timeout=None, auto_defer=False` (the quiz card_view pattern — routed by the global on_interaction handler, so it survives redeploys)
  - `open_embed(team0, team1, minutes, match_id, pool0=0, pool1=0)`, `frozen_embed(team0, team1, pool0, pool1, bettors0, bettors1)`, `no_action_embed(team0, team1)`, `report_embed(team0, team1, winner_idx, bets, paid)`, `gold_embed(balance_amount, entries, seeded_now=False)`, `gold_top_embed(rows, page=1)`; `leaderboard_embed` stays.

- [ ] **Step 1: Write the failing view tests**

In `tests/test_predictions.py`, replace the existing view test classes (`TestOpenLines`/`TestFrozenLines`/`TestResultLines` or however they are currently named — read the file first) with:

```python
class TestOpenLines:
	def test_shows_both_pools_and_the_buttons_copy(self):
		lines = view.open_lines("Alpha", "Beta", 10, 33, pool0=200, pool1=100)
		text = "\n".join(lines)
		assert "Alpha" in text and "Beta" in text and "#33" in text
		assert "200" in text and "100" in text
		assert "React" not in text          # the reaction era is over

	def test_fresh_card_has_zero_pools_and_no_multiplier(self):
		text = "\n".join(view.open_lines("Alpha", "Beta", 10, 33))
		assert "×" not in text

	def test_multiplier_appears_once_both_pools_exist(self):
		text = "\n".join(view.open_lines("Alpha", "Beta", 10, 33, pool0=200, pool1=100))
		assert "×1.5" in text and "×3" in text


class TestFrozenLines:
	def test_names_the_bigger_pool_as_favourite(self):
		text = "\n".join(view.frozen_lines("Alpha", "Beta", 300, 100, 3, 1))
		assert "Alpha" in text and "300" in text and "100" in text

	def test_no_bets_reads_plainly(self):
		text = "\n".join(view.frozen_lines("Alpha", "Beta", 0, 0, 0, 0))
		assert "no" in text.lower()


class TestNoActionLines:
	def test_says_refunded(self):
		text = "\n".join(view.no_action_lines("Alpha", "Beta"))
		assert "refund" in text.lower()


class TestReportLines:
	BETS = [
		dict(user_id=1, nick="anu", side=0, stake=150),
		dict(user_id=2, nick="bala", side=0, stake=50),
		dict(user_id=3, nick="chetan", side=1, stake=100),
	]

	def test_full_report_names_everyone_with_stakes_and_payouts(self):
		text = "\n".join(view.report_lines("Alpha", "Beta", 0, self.BETS, {1: 225, 2: 75}))
		assert "Alpha" in text                       # the winner
		assert "anu" in text and "225" in text and "+75" in text
		assert "bala" in text and "75" in text
		assert "chetan" in text and "100" in text    # the loser and what they lost
		assert "300" in text                         # the pot

	def test_nobody_bet(self):
		text = "\n".join(view.report_lines("Alpha", "Beta", 0, [], {}))
		assert "Nobody bet" in text

	def test_overflow_is_capped_with_a_more_line(self):
		bets = [dict(user_id=i, nick=f"u{i}", side=0, stake=10) for i in range(30)]
		bets.append(dict(user_id=99, nick="loser", side=1, stake=10))
		paid = {i: 10 for i in range(30)}
		lines = view.report_lines("Alpha", "Beta", 0, bets, paid, max_named=25)
		text = "\n".join(lines)
		assert "+5 more" in text


class TestBetConfirmLines:
	def test_states_stake_side_pools_and_balance(self):
		text = "\n".join(view.bet_confirm_lines("Alpha", 50, 60, 230, 180, 430))
		assert "50" in text and "Alpha" in text and "60" in text
		assert "230" in text and "180" in text and "430" in text


class TestGoldLines:
	def test_balance_and_entries(self):
		entries = [
			dict(entry_type="payout", amount=225, match_id=None, post_id=12, created_at=0),
			dict(entry_type="bet", amount=-50, match_id=None, post_id=12, created_at=0),
			dict(entry_type="seed", amount=500, match_id=None, post_id=None, created_at=0),
		]
		text = "\n".join(view.gold_lines(430, entries))
		assert "430" in text and "Winnings" in text and "+225" in text
		assert "Bet placed" in text and "-50" in text and "Starting gold" in text

	def test_no_entries(self):
		text = "\n".join(view.gold_lines(500, []))
		assert "500" in text


class TestGoldTopLines:
	def test_ranks_with_places(self):
		rows = [dict(nick="anu", balance=900), dict(nick="bala", balance=500)]
		text = "\n".join(view.gold_top_lines(rows))
		assert "1." in text and "anu" in text and "900" in text

	def test_empty(self):
		assert view.gold_top_lines([]) == ["Nobody holds any gold yet."]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_predictions.py -v -k "Open or Frozen or NoAction or Report or Confirm or Gold"`
Expected: FAIL (signature mismatches / missing functions).

- [ ] **Step 3: Implement `bot/predictions/view.py`**

Keep `TEAM_EMOJIS`, `split_pct` import, `leaderboard_lines`, `rank_field`. Replace `open_lines`, `frozen_lines`, delete `result_lines`, and add the rest:

```python
from .scoring import multiplier, pools, split_pct

GOLD = "\U0001FA99"  # 🪙

_ENTRY_LABELS = {
	"seed": "Starting gold",
	"match_reward": "Match played",
	"bet": "Bet placed",
	"refund": "Refund",
	"payout": "Winnings",
	"admin_adjust": "Adjustment",
}


def _mult_note(pool_side, pool_other):
	m = multiplier(pool_side, pool_other)
	return f" · pays ×{m:g}" if m and m > 1 else ""


def _side_line(emoji, team, pool_side, pool_other):
	return f"{emoji}  **{team}** — pool **{pool_side}** {GOLD}{_mult_note(pool_side, pool_other)}"


def open_lines(team0, team1, minutes, match_id, pool0=0, pool1=0):
	return [
		f"**Who takes match #{match_id}?**",
		"",
		_side_line(TEAM_EMOJIS[0], team0, pool0, pool1),
		_side_line(TEAM_EMOJIS[1], team1, pool1, pool0),
		"",
		f"Stake your gold with the buttons. Betting closes in {minutes} minutes.",
		"_Spectators only — players in this match cannot bet. Winners split the whole pot._",
	]


def frozen_lines(team0, team1, pool0, pool1, bettors0, bettors1):
	total = pool0 + pool1
	if not total:
		return [f"**Betting closed — no bets on {team0} vs {team1}.**"]
	lines = [
		"**Betting closed. The pots are locked:**",
		"",
		f"{TEAM_EMOJIS[0]}  **{team0}** — **{pool0}** {GOLD} from {bettors0} bettor(s)"
		f"{_mult_note(pool0, pool1)}",
		f"{TEAM_EMOJIS[1]}  **{team1}** — **{pool1}** {GOLD} from {bettors1} bettor(s)"
		f"{_mult_note(pool1, pool0)}",
		"",
	]
	if pool0 == pool1:
		lines.append(f"Dead even at {total} {GOLD}.")
	else:
		favourite = team0 if pool0 > pool1 else team1
		lines.append(f"The gold says **{favourite}**.")
	return lines


def no_action_lines(team0, team1):
	return [
		f"**Betting closed — one-sided book on {team0} vs {team1}.**",
		"Nobody took the other side, so there are no odds to settle. "
		"All stakes have been refunded.",
	]


def voided_lines(reason):
	return [reason, "All stakes have been refunded."]


def bet_confirm_lines(team_name, stake, total_stake, pool0, pool1, balance_after):
	lines = [f"Bet **{stake}** {GOLD} on **{team_name}**"
			 + (f" (your total: {total_stake})" if total_stake != stake else "") + "."]
	lines.append(f"Pools: {pool0} vs {pool1}. Your balance: **{balance_after}** {GOLD}.")
	return lines


def _named(rows, max_named, fmt):
	lines = [fmt(r) for r in rows[:max_named]]
	if len(rows) > max_named:
		lines.append(f"+{len(rows) - max_named} more")
	return lines


def report_lines(team0, team1, winner_idx, bets, paid, max_named=25):
	"""The post-match betting report: the pot, every winner's stake -> payout
	(net gain shown), every loser's stake. bets come from store.bets_for()
	(already biggest-stake-first); paid from scoring.payouts()."""
	winner_name = team0 if winner_idx == 0 else team1
	lines = [f"**{winner_name}** won it."]
	if not bets:
		lines.append("Nobody bet on this one.")
		return lines
	pool0, pool1 = pools(bets)
	lines.append(f"Pot: **{pool0 + pool1}** {GOLD} — {pool0} on {team0}, {pool1} on {team1}.")
	winners = [b for b in bets if b["side"] == winner_idx]
	losers = [b for b in bets if b["side"] != winner_idx]
	if winners:
		lines.append("")
		lines.extend(_named(winners, max_named, lambda b: (
			f"\U0001F3C6 **{b['nick']}** staked {b['stake']} → "
			f"**{paid.get(b['user_id'], 0)}** {GOLD} (+{paid.get(b['user_id'], 0) - b['stake']})")))
	if losers:
		lines.append("")
		lines.extend(_named(losers, max_named, lambda b: (
			f"\U0001F4B8 {b['nick']} staked {b['stake']} on "
			f"{team0 if b['side'] == 0 else team1} — gone")))
	return lines


def gold_lines(balance_amount, entries):
	lines = [f"You hold **{balance_amount}** {GOLD}."]
	if entries:
		lines.append("")
		for e in entries:
			sign = "+" if e["amount"] >= 0 else ""
			lines.append(f"`{sign}{e['amount']}` {_ENTRY_LABELS.get(e['entry_type'], e['entry_type'])}")
	return lines


def gold_top_lines(rows, page=1, per_page=10):
	"""rows: [{nick, balance}] already sorted richest-first."""
	if not rows:
		return ["Nobody holds any gold yet."]
	start = (page - 1) * per_page
	page_rows = rows[start:start + per_page]
	if not page_rows:
		return [f"No entries on page {page}."]
	return [f"`{start + n + 1:>2}.` **{r['nick']}** — {r['balance']} {GOLD}"
			for n, r in enumerate(page_rows)]
```

(`split_pct` stays imported only if still used — if nothing in view.py uses it after the rewrite, drop the import and leave the function in scoring.py for the web layer check in Step 6.)

- [ ] **Step 4: Implement `bot/predictions/embeds.py`**

Replace `open_embed`/`frozen_embed`/`result_embed` and add the rest (keep the colour constants, add `import nextcord` if switching style — the file already imports `Embed, Colour` from nextcord; also import `view` symbols already there):

```python
from nextcord import Embed, Colour, ui, ButtonStyle

from . import view
from .scoring import STAKES
from .view import TEAM_EMOJIS


def bet_view(post_id):
	# auto_defer=False is REQUIRED — same rule as bot/quiz/embeds.card_view:
	# these buttons carry no per-View callback (clicks route through the global
	# on_interaction handler so they work across a Railway redeploy), and
	# nextcord's default auto_defer would silently ack the click first.
	v = ui.View(timeout=None, auto_defer=False)
	for side, style in ((0, ButtonStyle.primary), (1, ButtonStyle.danger)):
		for stake in STAKES:
			v.add_item(ui.Button(
				style=style, row=side, label=str(stake), emoji=TEAM_EMOJIS[side],
				custom_id=f"bet:{post_id}:{side}:{stake}"))
	return v


def open_embed(team0, team1, minutes, match_id, pool0=0, pool1=0):
	return Embed(
		title="\U0001F52E Match betting",
		description="\n".join(view.open_lines(team0, team1, minutes, match_id, pool0, pool1)),
		colour=Colour(_OPEN))


def frozen_embed(team0, team1, pool0, pool1, bettors0, bettors1):
	return Embed(
		title="\U0001F512 Bets locked",
		description="\n".join(view.frozen_lines(team0, team1, pool0, pool1, bettors0, bettors1)),
		colour=Colour(_FROZEN))


def no_action_embed(team0, team1):
	return Embed(
		title="\U0001F512 Bets refunded",
		description="\n".join(view.no_action_lines(team0, team1)),
		colour=Colour(0x95A5A6))


def voided_embed(reason):
	return Embed(
		title="\U0001F52E Match betting",
		description="\n".join(view.voided_lines(reason)),
		colour=Colour(0x95A5A6))


def report_embed(team0, team1, winner_idx, bets, paid):
	return Embed(
		title="\U0001F3C6 Betting report",
		description="\n".join(view.report_lines(team0, team1, winner_idx, bets, paid)),
		colour=Colour(_RESULT))


def gold_embed(balance_amount, entries, seeded_now=False):
	desc = view.gold_lines(balance_amount, entries)
	if seeded_now:
		desc.insert(0, f"Welcome to the betting floor — you start with {balance_amount} {view.GOLD}.")
	return Embed(title=f"{view.GOLD} Your gold", description="\n".join(desc), colour=Colour(_RESULT))


def gold_top_embed(rows, page=1):
	return Embed(
		title=f"{view.GOLD} Gold leaderboard",
		description="\n".join(view.gold_top_lines(rows, page=page)),
		colour=Colour(_RESULT))
```

Keep `leaderboard_embed` unchanged. Delete `result_embed` (replaced by `report_embed`).

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_predictions.py -v`
Expected: all PASS.

- [ ] **Step 6: Check for stale references**

Run: `grep -rn "result_embed\|result_lines\|frozen_embed\|open_embed" bot/ tests/ | grep -v quiz`
Expected: hits only in `bot/predictions/embeds.py`, `bot/predictions/view.py` and `bot/predictions/flow.py` (flow rewritten next task). Fix anything else now (check `bot/web.py` too).

- [ ] **Step 7: Full check + commit**

Run: `ruff check . && pytest tests/ -q`

```bash
git add bot/predictions/view.py bot/predictions/embeds.py tests/test_predictions.py
git commit -m "feat(betting): the betting card, six-button view, confirmations and the post-match betting report

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: The bet button router

**Files:**
- Create: `bot/predictions/interactions.py`
- Modify: `bot/events.py` (`on_interaction` — one more router in the chain)

**Interfaces:**
- Consumes: `scoring.parse_bet_custom_id`, `store.get_post`/`bets_for`, `gold.ensure_seeded`/`place_bet`, `flow._player_ids`, `community.community_for_channel`, `embeds.open_embed`, `view.bet_confirm_lines`.
- Produces: `on_bet_interaction(interaction)` — self-isolating (never raises), no-op on foreign custom_ids. Registered in `bot/events.py`.

- [ ] **Step 1: Implement `bot/predictions/interactions.py`** (mirror `bot/quiz/interactions.py`'s structure and its direct-ephemeral-response rule)

```python
# -*- coding: utf-8 -*-
"""Global component-interaction router for bet buttons. Registered as an
additional on_interaction listener (the client supports multiple handlers per
event). DB-driven — never relies on a live View object, so the buttons keep
working across a Railway redeploy. Foreign interactions fall straight through:
we only act on custom_ids starting with 'bet:'. Only imported at runtime (by
bot.events), never during unit tests."""
import time
import traceback

import nextcord

from core.console import log

from . import embeds, flow, gold, store, view
from .scoring import SEED_AMOUNT, parse_bet_custom_id, pools


async def on_bet_interaction(interaction):
	try:
		if interaction.type != nextcord.InteractionType.component:
			return
		route = parse_bet_custom_id((interaction.data or {}).get("custom_id", ""))
		if route is None:
			return
		post_id, side, stake = route
		now = int(time.time())
		post = await store.get_post(post_id)
		if not post or post["status"] != "open" or now >= post["freezes_at"]:
			return await _eph(interaction, "Betting on this match is closed.")
		if interaction.user.id in flow._player_ids(post["match_id"]):
			return await _eph(interaction, "Players can't bet on their own match.")

		from bot import community
		community_id = await community.community_for_channel(post["channel_id"])
		if community_id is None:
			return await _eph(interaction, "This channel keeps no stats — there is no gold here.")

		seeded_now = await gold.ensure_seeded(community_id, interaction.user.id, now)
		status, value = await gold.place_bet(
			community_id, interaction.user.id, post_id, side, stake, _nick(interaction.user), now)
		if status == "insufficient":
			return await _eph(interaction,
				f"Not enough gold — you hold **{value}** {view.GOLD}. "
				"Playing matches tops you back up.")
		if status == "side_locked":
			locked = post["team0_name"] if value == 0 else post["team1_name"]
			return await _eph(interaction,
				f"You're on **{locked}** this match — bets add up, they don't switch sides.")

		bets = await store.bets_for(post_id)
		pool0, pool1 = pools(bets)
		mine = next((b for b in bets if b["user_id"] == interaction.user.id), None)
		team = post["team0_name"] if side == 0 else post["team1_name"]
		lines = view.bet_confirm_lines(team, stake, mine["stake"] if mine else stake,
									   pool0, pool1, value)
		if seeded_now:
			lines.insert(0, f"Welcome to the betting floor — you started with {SEED_AMOUNT} {view.GOLD}.")
		await _eph(interaction, "\n".join(lines))
		await _refresh_card(post, pool0, pool1, now)
	except Exception as e:
		log.error(f"bet interaction error: {e}\n{traceback.format_exc()}")
		try:
			if not interaction.response.is_done():
				await interaction.response.send_message(
					"Something went wrong placing that bet — nothing was charged if you "
					"didn't get a confirmation. Try again.", ephemeral=True)
		except Exception:
			pass


async def _refresh_card(post, pool0, pool1, now):
	"""Best-effort embed update so the card shows the live pools. The buttons
	(the View) are left untouched by omitting `view` from the edit."""
	try:
		from core.client import dc
		channel = dc.get_channel(post["channel_id"])
		if channel is None or not post.get("message_id"):
			return
		message = await channel.fetch_message(post["message_id"])
		minutes = max(0, (post["freezes_at"] - now) // 60)
		await message.edit(embed=embeds.open_embed(
			post["team0_name"], post["team1_name"], minutes, post["match_id"], pool0, pool1))
	except Exception as e:
		log.warning(f"bet card refresh failed (post {post['id']}): {e}")


def _nick(user):
	return getattr(user, "display_name", None) or getattr(user, "name", None) or str(user.id)


async def _eph(interaction, text):
	if not interaction.response.is_done():
		await interaction.response.send_message(text, ephemeral=True)
	else:
		await interaction.followup.send(text, ephemeral=True)
```

- [ ] **Step 2: Register it in `bot/events.py`**

In `on_interaction`, after the `cls_interactions` line, add (mirroring the comment style already there):

```python
	from bot.predictions import interactions as bet_interactions
	await bet_interactions.on_bet_interaction(interaction)
```

- [ ] **Step 3: Checks + commit**

Run: `ruff check . && pytest tests/ -q`
Expected: pass. (`interactions.py` is runtime-only; its routing pure function was tested in Task 2.)

```bash
git add bot/predictions/interactions.py bot/events.py
git commit -m "feat(betting): route bet buttons through the global interaction handler — redeploy-safe like the quiz

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Lifecycle cutover in `flow.py`

**Files:**
- Modify: `bot/predictions/flow.py`

**Interfaces:**
- Consumes: everything above. Public surface (`jobs`, `open_for_match`, `restart_for_match`, `resolve_for_match`, `void_for_match`) is **unchanged** — `bot/match/match.py`, `bot/match/draft.py` and `tests/test_predictions_wiring.py` keep working untouched.
- Behavior changes: open posts carry buttons and no reactions; freeze reads `prediction_bets` (not reactions) and refunds one-sided books as `no_action`; resolve pays winners, posts the betting report, and grants the playing faucet; every void refunds.

- [ ] **Step 1: Rewrite `bot/predictions/flow.py` piece by piece**

1. Imports: drop `from .view import TEAM_EMOJIS`; keep `scoring, store`; add `gold` to the relative import: `from . import gold, scoring, store`.

2. `open_for_match` — replace the send + reaction loop:

```python
		message = await channel.send(
			embed=embeds.open_embed(
				match.teams[0].name, match.teams[1].name, VOTE_WINDOW // 60, match.id),
			view=embeds.bet_view(post_id))
		await store.set_message_id(post_id, message.id)
```

3. `_freeze` — full replacement (reactions are gone; the DB is the source of truth):

```python
async def _freeze(post, now):
	"""Lock the book. One-sided (either pool empty) -> no_action: every stake
	back immediately, because a book with no opposing gold has no odds to
	settle. Otherwise the pots lock and the card shows the final multipliers."""
	from . import embeds

	bets = await store.bets_for(post["id"])
	pool0, pool1 = scoring.pools(bets)
	if not pool0 or not pool1:
		community_id = await _community_for_post(post)
		if bets and community_id is not None:
			await gold.refund_post(community_id, bets, post["id"], now)
		elif bets:
			log.error(f"Prediction post {post['id']} has bets but no community — refunds skipped!")
		await store.no_action(post["id"], now)
		await _edit_message(post, embeds.no_action_embed(post["team0_name"], post["team1_name"]))
		log.info(f"Bets refunded for match {post['match_id']}: one-sided book ({pool0}-{pool1}).")
		return

	bettors0 = sum(1 for b in bets if b["side"] == 0)
	bettors1 = len(bets) - bettors0
	await store.freeze(post["id"], bettors0, bettors1)
	await _edit_message(post, embeds.frozen_embed(
		post["team0_name"], post["team1_name"], pool0, pool1, bettors0, bettors1))
	log.info(f"Bets locked for match {post['match_id']}: {pool0}-{pool1} gold.")
```

4. Add the community helper (near `_player_ids`):

```python
async def _community_for_post(post):
	from bot import community
	return await community.community_for_channel(post["channel_id"])
```

5. `resolve_for_match` — full replacement. The faucet is independent of the post (a ranked match with a winner pays its players even if the card is long gone):

```python
async def resolve_for_match(match):
	"""Settle a finished ranked match: pay the playing faucet, then the book.
	A match that never reported a clean win/loss (draw, cancelled report) is
	voided — every stake refunded — rather than settled."""
	try:
		now = int(time.time())
		winner_idx = getattr(match, "winner", None)

		# The faucet first, and independently of any post: playing regenerates
		# gold toward the ceiling whether or not anyone bet on this match.
		if winner_idx is not None:
			from bot import community
			community_id = await community.community_for_channel(match.qc.id)
			if community_id is not None:
				for p in match.players:
					try:
						await gold.ensure_seeded(community_id, p.id, now)
						await gold.grant_match_reward(community_id, p.id, match.id, now)
					except Exception as e:
						log.error(f"Match reward failed ({match.id}/{p.id}): {e}")

		post = await store.live_for_match(match.id)
		if post is None:
			return
		if winner_idx is None:
			await _void_with_refunds(post, "No win/loss reported — all bets refunded.", now)
			return

		bets = await store.bets_for(post["id"])
		paid, burned = scoring.payouts(bets, winner_idx)
		if bets and not paid:
			# Defensive: a one-sided book that somehow reached resolve. The
			# freeze rule makes this unreachable; refund rather than settle.
			await _void_with_refunds(post, "One-sided book — all bets refunded.", now)
			return
		community_id = await _community_for_post(post)
		if paid and community_id is not None:
			await gold.pay_post(community_id, paid, post["id"], now)
		await store.resolve(post["id"], winner_idx, now)
		await _announce_report(post, winner_idx, bets, paid)
		if burned:
			log.info(f"Match {match.id} betting settled: {sum(paid.values())} paid, {burned} burned.")
	except Exception as e:
		log.error(f"Prediction resolve failed (match {getattr(match, 'id', '?')}): {e}")
```

6. `void_for_match` and `restart_for_match` — both route through one refunding void:

```python
async def _void_with_refunds(post, reason, now):
	"""Terminal no-settle: refund every stake exactly once, then mark void."""
	from . import embeds

	bets = await store.bets_for(post["id"])
	community_id = await _community_for_post(post)
	if bets and community_id is not None:
		await gold.refund_post(community_id, bets, post["id"], now)
	elif bets:
		log.error(f"Prediction post {post['id']} has bets but no community — refunds skipped!")
	await store.void(post["id"], now)
	await _edit_message(post, embeds.voided_embed(reason))


async def void_for_match(match_id, reason="Match cancelled — all bets refunded."):
	"""Drop a live book on the floor (match aborted). Stakes go back."""
	try:
		post = await store.live_for_match(match_id)
		if post is None:
			return
		await _void_with_refunds(post, reason, int(time.time()))
	except Exception as e:
		log.error(f"Prediction void failed (match {match_id}): {e}")
```

In `restart_for_match`, replace the `store.void(...)` + `_edit_message(...)` pair with:

```python
		await _void_with_refunds(post, "Teams changed — all bets refunded. A fresh book is open.", now)
		await open_for_match(match)
```

7. `_announce_result` → `_announce_report`:

```python
async def _announce_report(post, winner_idx, bets, paid):
	from . import embeds
	from core.client import dc

	channel = dc.get_channel(post["channel_id"])
	if channel is None:
		return
	try:
		await channel.send(embed=embeds.report_embed(
			post["team0_name"], post["team1_name"], winner_idx, bets, paid))
	except Exception as e:
		log.warning(f"Betting report send failed (post {post['id']}): {e}")
```

8. `_edit_message` — strip the buttons on every terminal edit and stop touching reactions:

```python
async def _edit_message(post, embed):
	"""Best-effort rewrite of a post's card; a deleted message is not an error.
	view=None strips the bet buttons — every caller is a terminal state."""
	from core.client import dc

	channel = dc.get_channel(post["channel_id"])
	if channel is None or not post.get("message_id"):
		return
	try:
		message = await channel.fetch_message(post["message_id"])
		await message.edit(embed=embed, view=None)
	except Exception:
		pass
```

9. Delete `_voided_note` (replaced by `embeds.voided_embed`). Keep `MAX_NAMED_WINNERS`? — it moved into `view.report_lines(max_named=25)`; delete the constant here and confirm nothing imports it: `grep -rn MAX_NAMED_WINNERS bot/ tests/`. Keep `_player_ids` exactly as is (the roster check now guards betting, and `interactions.py` calls it).

10. Update the module docstring (freeze sweep now reads `prediction_bets`, not reactions) and the known-limitation note: add a short comment above `resolve_for_match` —

```python
# Settlement runs once, at report time, like the votes era. If the process
# dies mid-settlement the post stays 'frozen'; every movement below is an
# idempotent ledger insert, so re-running settlement for a post is always
# safe — a future sweep (or a manual call) can finish a half-settled book
# without double-paying anyone.
```

- [ ] **Step 2: Checks**

Run: `ruff check . && pytest tests/ -q`
Expected: all pass — especially `tests/test_predictions_wiring.py` (the export surface didn't move).
Run: `grep -rn "TEAM_EMOJIS" bot/predictions/flow.py`
Expected: no hits.

- [ ] **Step 3: Commit**

```bash
git add bot/predictions/flow.py
git commit -m "feat(betting): lifecycle cutover — buttons open the book, freeze locks or refunds it, resolve pays and reports

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: `/gold`, `/gold_top`, and the boot seed

**Files:**
- Modify: `bot/commands/predictions.py` (two new handlers)
- Modify: `bot/context/slash/commands.py` (two new registrations, near `_predictions_leaderboard`)
- Modify: `bot/events.py` (`on_ready` bulk seed, after `seed_ratings_from_csv()`)

**Interfaces:**
- Consumes: `gold.balance/ensure_seeded/recent_entries/top_balances/bulk_seed`, `embeds.gold_embed/gold_top_embed`, `community.community_for_channel`, `identity.profiles_and_names_by_user` (shape: `{user_id: {"profile_ids": [...], "aoe2_names": [...]}}`).
- Produces: slash commands `/gold` (ephemeral) and `/gold_top [page]` (public), and an idempotent seed-everyone pass on every boot.

- [ ] **Step 1: Handlers in `bot/commands/predictions.py`**

Extend `__all__` to `['predictions_leaderboard', 'predictions_me', 'gold', 'gold_top']` and add:

```python
async def gold(ctx):
	""" Your gold balance and recent movements (only you see the reply). """
	import time

	from bot import community
	from bot.predictions import embeds
	from bot.predictions import gold as bank

	community_id = await community.community_for_channel(ctx.channel.id)
	if community_id is None:
		raise bot.Exc.NotFoundError(ctx.qc.gt("This channel is not part of a community with stats."))
	now = int(time.time())
	seeded_now = await bank.ensure_seeded(community_id, ctx.author.id, now)
	balance = await bank.balance(community_id, ctx.author.id)
	entries = await bank.recent_entries(community_id, ctx.author.id, 8)
	# ephemeral works on the fast path (run_slash only defers after ~2.5s, and
	# these are three PK-keyed reads); a deferred reply degrades to public,
	# which leaks nothing but a balance the /gold_top board shows anyway.
	await ctx.reply(embed=embeds.gold_embed(balance, entries, seeded_now), ephemeral=True)


async def gold_top(ctx, page: int = 1):
	""" The community's richest bettors. """
	from core.database import db

	from bot import community, identity
	from bot.predictions import embeds
	from bot.predictions import gold as bank

	community_id = await community.community_for_channel(ctx.channel.id)
	if community_id is None:
		raise bot.Exc.NotFoundError(ctx.qc.gt("This channel is not part of a community with stats."))
	# Hidden players are hidden from THIS board too — same read /eapm uses.
	hidden = {r["user_id"] for r in await db.fetchall(
		"SELECT DISTINCT user_id FROM player_ratings WHERE is_hidden=1") or []}
	names = await identity.profiles_and_names_by_user()
	rows = []
	for r in await bank.top_balances(community_id):
		if r["user_id"] in hidden:
			continue
		aoe2 = (names.get(r["user_id"]) or {}).get("aoe2_names") or []
		rows.append(dict(nick=aoe2[0] if aoe2 else f"user {r['user_id']}", balance=r["balance"]))
	await ctx.reply(embed=embeds.gold_top_embed(rows, page=max(1, int(page or 1))))
```

- [ ] **Step 2: Slash registrations in `bot/context/slash/commands.py`**

After the `_predictions_me` block:

```python
@dc.slash_command(name='gold', description='Your gold balance and recent movements.', **guild_kwargs)
async def _gold(
		interaction: Interaction,
): await run_slash(bot.commands.gold, interaction=interaction)


@dc.slash_command(name='gold_top', description='The richest bettors in the community.', **guild_kwargs)
async def _gold_top(
		interaction: Interaction,
		page: int = SlashOption(required=False, description="Page number.")
): await run_slash(bot.commands.gold_top, interaction=interaction, page=page or 1)
```

- [ ] **Step 3: Boot seed in `bot/events.py`**

`events.py` has no `time` import — add `import time` to its imports. In `on_ready`, directly after `await seed_ratings_from_csv()`:

```python
		# One idempotent pass seeds starting gold for every known player in
		# every community; after the first boot this inserts nothing. Newcomers
		# are seeded lazily on their first gold touch instead.
		try:
			from bot.predictions import gold as gold_bank
			seeded = await gold_bank.bulk_seed(int(time.time()))
			if seeded:
				log.info(f"\tSeeded {seeded} player(s) with starting gold.")
		except Exception:
			log.error(f"Gold bulk seed failed:\n{traceback.format_exc()}")
```

- [ ] **Step 4: Checks + commit**

Run: `ruff check . && pytest tests/ -q`
Expected: pass.

```bash
git add bot/commands/predictions.py bot/context/slash/commands.py bot/events.py
git commit -m "feat(betting): /gold and /gold_top, and the idempotent boot seed

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Docs, final sweep, deploy checklist

**Files:**
- Modify: `CLAUDE.md` (predictions section of the architecture notes)
- Modify: `docs/superpowers/specs/2026-08-05-gold-betting-design.md` only if implementation deviated (record the deviation, don't rewrite history)

- [ ] **Step 1: Update CLAUDE.md**

In the `bot/` architecture section, extend the predictions coverage with a paragraph stating (write it in CLAUDE.md's factual voice, matching neighbouring entries):
- Predictions are now pari-mutuel gold betting: `bot/predictions/gold.py` is the ONLY module that writes `gold_ledger` (append-only, idem-keyed) / `gold_balances` / `prediction_bets`, and every movement is one `db.transaction()`.
- The faucet rule (`min(10, max(0, 500 − balance))`), the seed (500, idempotent, bulk on boot + lazy on first touch), spectators-only, side lock via the `prediction_bets` PK, one-sided book → `no_action` + refund, floored payouts with the remainder burned.
- `prediction_votes` is historical-only; the accuracy leaderboard unions it with bets.
- Buttons route through `bot/predictions/interactions.py` on the global `on_interaction` chain (the quiz pattern) — redeploy-safe, no persistent Views.

- [ ] **Step 2: Full-suite verification**

Run: `ruff check . && pytest tests/ -q`
Expected: clean and green. Also run the two greps that catch stragglers:
`grep -rn "prediction_votes" bot/ | grep -v "store.py\|__init__.py"` → no writer outside store.py's union/history reads;
`grep -rn "add_reaction\|clear_reactions" bot/predictions/` → no hits.

- [ ] **Step 3: Commit docs**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-08-05-gold-betting-design.md
git commit -m "docs(betting): record the gold economy's invariants in CLAUDE.md

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

- [ ] **Step 4: Manual smoke checklist (deploy-time, not CI)**

After deploy, in a test queue channel: seed logged on boot (`Seeded N player(s)`); open a ranked match → card shows six buttons; a player in the match pressing → "Players can't bet"; a spectator pressing → ephemeral confirm + card pools update; same spectator pressing the other side → side-lock message; `/gold` shows balance + entries; freeze with one-sided book → refund card; full match → betting report with payouts; `/gold_top` ranks. Then run `python3 -c "import asyncio; ..."` against `gold.reconcile()` via the Railway console (or a temporary read query) — must return no rows.

---

## Self-Review (done at authoring time)

- **Spec coverage:** pari-mutuel math (T2), spectators-only + side lock (T4/T7), buttons (T6/T7), seed bulk+lazy (T4/T9), faucet cap (T2/T4/T8), ledger+cache+transactions (T1/T3/T4), no_action refund (T5/T8), voids refund (T8), betting report (T6/T8), `/gold`+`/gold_top` (T9), leaderboard union (T5), registry (T3), reconcile utility (T4), CLAUDE.md (T10). Out-of-scope list respected — no admin command, no custom stakes.
- **Type consistency:** `place_bet` returns `(status, value)` tuples consumed in T7; `bets_for` row shape `{user_id, nick, side, stake}` consumed by `scoring.pools/payouts` (T2), `view.report_lines` (T6), `gold.refund_post` (T4), flow (T8). `payouts` returns `(dict, int)` consumed in T8. `no_action` status string consistent between T5 and T8.
- **Placeholder scan:** every code step carries complete code; the only "mirror X" references also inline the code.
