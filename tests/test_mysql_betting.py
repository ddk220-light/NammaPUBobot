"""Real InnoDB money tests, restricted to the disposable CI database."""
import asyncio
from contextlib import asynccontextmanager

import pytest

from nammaoe2bot.features.betting import gold, store
from nammaoe2bot.features.betting.tax_policy import WEEK, latest_cutoff
from tests.test_mysql_reliability import live_database, pytestmark  # noqa: F401

T = latest_cutoff(1800000000, 870)


@asynccontextmanager
async def bank(monkeypatch, users=(1, 2)):
	async with live_database(monkeypatch, (
			'nammaoe2bot/community.py', 'nammaoe2bot/features/betting/__init__.py')) as (db, module):
		monkeypatch.setattr(gold, 'db', db)
		monkeypatch.setattr(store, 'db', db)
		for channel, community in ((900, 5), (901, 5), (902, 6)):
			await db.insert('community_channels', dict(channel_id=channel, community_id=community))
		await db.insert('gold_tax_policy', dict(community_id=5, enabled=1, activated_at=T - 2 * WEEK, minute_of_day=870))
		for uid in users:
			await gold.ensure_seeded(5, uid, T - 2 * WEEK)
		await db.insert('prediction_posts', dict(id=12, channel_id=900, match_id=77,
			opened_at=T - 100, freezes_at=2**63 - 1, status='open'))
		yield db, module


async def reconciled(db):
	rows = await db.fetchall(
		'SELECT b.community_id,b.user_id,b.balance,SUM(l.amount) AS ledger '
		'FROM gold_balances b JOIN gold_ledger l '
		'ON l.community_id=b.community_id AND l.user_id=b.user_id '
		'GROUP BY b.community_id,b.user_id,b.balance')
	assert rows
	assert all(r['balance'] >= 0 and r['balance'] == r['ledger'] for r in rows)


async def place(uid=1, post=12, quote=123, now=T - 50):
	return await gold.place_bet(5, uid, post, 0, 50, 'player', now, chooser_id=quote)


def test_mysql_tax_eligibility_boundaries_refunds_grace_and_tenancy(monkeypatch):
	async def run():
		async with bank(monkeypatch, users=(1, 2, 3, 4, 5, 8)) as (db, _):
			await gold.ensure_seeded(5, 6, T - 100)  # new holder
			await gold.ensure_seeded(6, 2, T - 2 * WEEK)
			await place(1)
			await place(2)
			await gold.cancel_bet(5, 2, 12, T - 40)
			await store.close_betting(12, T - 1)
			for uid, pid, closed, channel in ((3, 13, T + 1, 900), (4, 14, T - 2, 900),
					(5, 15, T - WEEK, 901), (8, 18, T, 900)):
				await db.insert('prediction_posts', dict(id=pid, channel_id=channel, match_id=pid,
					opened_at=T - 2 * WEEK, freezes_at=2**63 - 1, status='open'))
				await place(uid, pid)
				if uid != 3:
					await store.close_betting(pid, closed)
				if uid == 4:
					await gold.refund_post(5, await store.bets_for(pid), pid, T - 1)
			result = await gold.apply_weekly_tax(5, T, T + 1)
			assert result == dict(taxed_holders=3, total_tax=140)  # cancelled, still open, exact cutoff
			balances = {r['user_id']: r['balance'] for r in await db.fetchall(
				'SELECT user_id,balance FROM gold_balances WHERE community_id=5')}
			assert balances == {1: 450, 2: 450, 3: 405, 4: 500, 5: 450, 6: 500, 8: 405}
			assert await gold.balance(6, 2) == 500
			assert await gold.apply_weekly_tax(5, T, T + 2) is None
			await reconciled(db)
	asyncio.run(run())


def test_mysql_tax_every_write_rolls_back_and_retry_is_once(monkeypatch):
	async def run():
		async with bank(monkeypatch) as (db, module):
			original = module.Transaction.execute
			control = dict(count=0, fail=0)
			async def execute(tx, *args):
				control['count'] += 1
				if control['count'] == control['fail']:
					raise ConnectionError('injected tax write failure')
				return await original(tx, *args)
			monkeypatch.setattr(module.Transaction, 'execute', execute)
			for fail in range(1, 7):
				control.update(count=0, fail=fail)
				with pytest.raises(ConnectionError):
					await gold.apply_weekly_tax(5, T, T + 1)
				assert await gold.balance(5, 1) == 500
				assert await gold.balance(5, 2) == 500
				assert not await db.fetchall('SELECT * FROM gold_tax_runs')
				await reconciled(db)
			control.update(count=0, fail=0)
			assert (await gold.apply_weekly_tax(5, T, T + 1))['total_tax'] == 100
			assert await gold.apply_weekly_tax(5, T, T + 2) is None
			await reconciled(db)
	asyncio.run(run())


def test_mysql_tax_lost_ack_and_overlapping_assessors(monkeypatch):
	async def run():
		async with bank(monkeypatch) as (db, _):
			transaction = db.transaction
			@asynccontextmanager
			async def lose_ack():
				async with transaction() as tx:
					yield tx
				raise ConnectionError('commit acknowledgement lost')
			monkeypatch.setattr(db, 'transaction', lose_ack)
			with pytest.raises(ConnectionError):
				await gold.apply_weekly_tax(5, T, T + 1)
			monkeypatch.setattr(db, 'transaction', transaction)
			assert await gold.apply_weekly_tax(5, T, T + 2) is None
			await db.execute('UPDATE prediction_posts SET freezes_at=%s', [T + 2 * WEEK])
			results = await asyncio.wait_for(asyncio.gather(
				gold.apply_weekly_tax(5, T + WEEK, T + WEEK + 1),
				gold.apply_weekly_tax(5, T + WEEK, T + WEEK + 1)), 10)
			assert sum(r is not None for r in results) == 1
			assert await gold.balance(5, 1) == 405
			await reconciled(db)
	asyncio.run(run())


@pytest.mark.parametrize('operation', ['bet', 'cancel', 'reward', 'payout'])
def test_mysql_tax_races_with_wallet_movements(monkeypatch, operation):
	async def run():
		async with bank(monkeypatch) as (db, _):
			if operation in ('cancel', 'reward'):
				await place()
			async def movement():
				if operation == 'bet':
					assert (await place(now=T + 1))[0] == 'ok'
				elif operation == 'cancel':
					assert (await gold.cancel_bet(5, 1, 12, T + 1))[0] == 'ok'
				elif operation == 'reward':
					await gold.grant_quiz_reward(5, 1, 99, True, T + 1)
				else:
					await gold.pay_post(5, {1: 200}, 99, T + 1)
			await asyncio.wait_for(asyncio.gather(gold.apply_weekly_tax(5, T, T + 1), movement()), 10)
			await reconciled(db)
			assert len(await db.fetchall("SELECT id FROM gold_ledger WHERE entry_type='inactivity_tax'")) == 2
	asyncio.run(run())


def test_mysql_duplicate_quote_lost_ack_and_stale_amount(monkeypatch):
	async def run():
		async with bank(monkeypatch) as (db, _):
			results = await asyncio.wait_for(asyncio.gather(place(), place()), 10)
			assert sorted(r[0] for r in results) == ['duplicate', 'ok']
			assert await gold.balance(5, 1) == 450
			await gold._credit(5, 1, 'admin_adjust', 51, 'fixture:51', T - 1)
			assert (await place(quote=456))[0] == 'stale'
			assert (await place())[0] == 'duplicate'
			assert await gold.balance(5, 1) == 501
			await reconciled(db)
	asyncio.run(run())


def test_mysql_no_opportunity_and_disabled_tax_do_not_debit(monkeypatch):
	async def run():
		async with bank(monkeypatch) as (db, _):
			await db.execute('UPDATE gold_tax_policy SET enabled=0')
			assert await gold.apply_weekly_tax(5, T, T + 1) is None
			assert not await db.fetchall('SELECT * FROM gold_tax_runs')
			await db.execute('UPDATE gold_tax_policy SET enabled=1')
			await db.execute('UPDATE prediction_posts SET opened_at=%s', [T])
			assert await gold.apply_weekly_tax(5, T, T + 1) == dict(taxed_holders=0, total_tax=0)
			assert await gold.balance(5, 1) == 500
			await reconciled(db)
	asyncio.run(run())
