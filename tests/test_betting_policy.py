"""Dynamic stakes, personal routing, and the weekly scheduler's idle contract."""
import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from nammaoe2bot.features.betting import gold, scoring, tax
from nammaoe2bot.features.betting.tax_policy import WEEK, ZONE, first_cutoff, latest_cutoff, tax_amount
from tests.test_predictions_gold import use_fake
from tests.test_predictions_interactions import FakeInteraction, run, wire


@pytest.mark.parametrize('balance,expected', [
	(0, ()), (9, ()), (10, (10,)), (20, (10, 20)), (50, (10, 25, 50)),
	(100, (20, 50, 100)), (249, (49, 124, 249)), (250, (50, 125, 250)),
	(499, (50, 125, 250)), (500, (50, 125, 250)), (501, (100, 250, 500)),
	(1000, (100, 250, 500)), (1001, (150, 375, 750)), (1501, (200, 500, 1000)),
])
def test_stake_boundaries(balance, expected):
	assert scoring.stake_options(balance) == expected


def test_all_options_are_unique_affordable_and_ordered():
	for balance in [*range(10001), 2**63 - 1]:
		options = scoring.stake_options(balance)
		assert options == tuple(sorted(set(options)))
		assert len(options) <= 3
		assert all(10 <= amount <= balance for amount in options)


@pytest.mark.parametrize('cid', ['betpick:-1:0', 'betpick:1:2', 'betstake:1:0:7:0:123',
	'betstake:1:0:7:50:0', 'betstake:1:0:7:no:123', 'betstake:1:0:7:50:123:extra'])
def test_malformed_personal_buttons(cid):
	assert scoring.parse_personal_bet_id(cid) is None


@pytest.mark.parametrize('cid', ['bet:12:0:10', 'betpick:12:0'])
def test_public_and_legacy_cards_open_personal_choices_without_charging(monkeypatch, cid):
	bank = wire(monkeypatch)
	i = run(FakeInteraction(custom_id=cid))
	assert not bank.placed
	assert [b.label for b in i.reply_view.children] == ['100', '250', '500']  # fake wallet: 999
	assert all(scoring.parse_personal_bet_id(b.custom_id)[2] == 7 for b in i.reply_view.children)
	assert bank.errors == []


def test_personal_choice_is_bound_to_user(monkeypatch):
	bank = wire(monkeypatch)
	i = run(FakeInteraction(custom_id='betstake:12:0:8:50:123', user_id=7))
	assert 'your own' in i.reply
	assert not bank.placed


@pytest.mark.parametrize('side', [0, 1])
@pytest.mark.parametrize('cid', ['betpick:12', 'betpick:12:0', 'betpick:12:1', 'bet:12:0:50'])
def test_match_players_go_straight_to_their_own_team_amounts(monkeypatch, side, cid):
	bank = wire(monkeypatch, team0=[7] if side == 0 else [], team1=[7] if side == 1 else [])
	i = run(FakeInteraction(custom_id=cid))
	assert [b.label for b in i.reply_view.children] == ['100', '250', '500']
	assert all(scoring.parse_personal_bet_id(b.custom_id)[1:3] == (side, 7) for b in i.reply_view.children)
	assert not bank.placed and not bank.errors


def test_spectator_picks_a_side_before_seeing_amounts(monkeypatch):
	bank = wire(monkeypatch)
	i = run(FakeInteraction(custom_id='betpick:12'))
	assert [b.custom_id for b in i.reply_view.children] == ['betpick:12:0', 'betpick:12:1']
	assert [b.label for b in i.reply_view.children] == ['Alpha', 'Bravo']
	i = run(FakeInteraction(custom_id=i.reply_view.children[1].custom_id))
	assert all(scoring.parse_personal_bet_id(b.custom_id)[1] == 1 for b in i.reply_view.children)
	assert not bank.placed and not bank.errors


def test_repeated_presses_of_the_same_amount_have_distinct_transaction_ids(monkeypatch):
	bank = wire(monkeypatch, place_bet=('ok', 400), bets=[dict(user_id=7, side=0, stake=100)])
	for click in (456, 457):
		i = FakeInteraction(custom_id='betstake:12:0:7:50:123')
		i.id = click
		run(i)
		assert '(your total: 100)' in i.reply
		assert not i.response.sent and len(i.response.edits) == 1
		assert [b.label for b in i.reply_view.children] == ['50', '125', '250', 'Cancel my bet']
	assert [b['stake'] for b in bank.placed] == [50, 50]
	assert bank.interaction_ids == [456, 457]
	assert not bank.errors


def test_empty_wallet_keeps_cancel_but_has_no_more_stake_buttons(monkeypatch):
	bank = wire(monkeypatch, place_bet=('ok', 0))
	i = run(FakeInteraction())
	assert [b.custom_id for b in i.reply_view.children] == ['betcancel:12']
	assert 'at least 10 gold' in i.reply
	assert not bank.errors


def test_stale_quote_refreshes_without_confirming_a_charge(monkeypatch):
	bank = wire(monkeypatch, place_bet=('stale', 501))
	i = run(FakeInteraction())
	assert 'nothing was charged' in i.reply
	assert [b.label for b in i.reply_view.children] == ['100', '250', '500', 'Cancel my bet']
	assert bank.errors == []


def test_duplicate_does_not_claim_a_second_stake(monkeypatch):
	wire(monkeypatch, place_bet=('duplicate', 450))
	assert 'no additional gold' in run(FakeInteraction()).reply


def test_wallet_change_rejects_an_old_amount_before_writing(monkeypatch):
	fake = use_fake(monkeypatch).answer(balance=501)
	assert asyncio.run(gold.place_bet(5, 7, 12, 0, 50, 'nick', 1000)) == ('stale', 501)
	assert not fake.inserts()
	assert not fake.sql('UPDATE gold_balances')


def test_click_key_is_recorded_and_duplicate_checked_before_staleness(monkeypatch):
	fake = use_fake(monkeypatch)
	fake.answers({}, {'balance': 500}, None, {'balance': 450})
	assert asyncio.run(gold.place_bet(5, 7, 12, 0, 50, 'nick', 1000, interaction_id=123)) == ('ok', 450)
	entry = next(c[2] for c in fake.inserts() if c[1] == 'gold_ledger')
	assert entry['idem_key'] == 'bet:5:7:123'
	fake.calls.clear()
	fake.answers({}, {'balance': 501}, {'id': 1})
	assert asyncio.run(gold.place_bet(5, 7, 12, 0, 50, 'nick', 1000, interaction_id=123)) == ('duplicate', 501)
	assert not fake.inserts() and not fake.sql('UPDATE gold_balances')


def test_thursday_boundaries_and_full_grace():
	thursday = int(datetime(2026, 10, 1, 14, 30, tzinfo=ZONE).timestamp())
	assert latest_cutoff(thursday - 1, 870) == thursday - WEEK
	assert latest_cutoff(thursday, 870) == thursday
	assert latest_cutoff(thursday + WEEK - 1, 870) == thursday
	assert first_cutoff(thursday - WEEK, 870) == thursday
	assert first_cutoff(thursday - WEEK + 1, 870) == thursday + WEEK
	assert tax_amount(1001, thursday - WEEK, thursday, False) == 100
	assert tax_amount(1001, thursday - WEEK + 1, thursday, False) == 0
	assert tax_amount(1001, None, thursday, False) == 0
	assert tax_amount(1001, 0, thursday, True) == 0
	assert tax_amount(9, 0, thursday, False) == 0


def test_scheduler_catches_up_only_latest_week_and_then_stays_db_free(monkeypatch):
	async def scenario():
		now = int(datetime(2026, 10, 3, 12, tzinfo=ZONE).timestamp())
		reads, assessments = [], []
		async def policies(_sql):
			reads.append(1)
			return [dict(community_id=5, activated_at=now - 8 * WEEK, minute_of_day=870)]
		async def apply(cid, cutoff, actual):
			assessments.append((cid, cutoff, actual))
			return None
		monkeypatch.setattr(tax, 'db', SimpleNamespace(fetchall=policies))
		monkeypatch.setattr(tax.gold, 'apply_weekly_tax', apply)
		job = tax.TaxScheduler()
		await job.run_due(now)
		assert assessments == [(5, latest_cutoff(now, 870), now)]
		for tick in range(now + 1, now + 10000):
			await job.run_due(tick)
		assert reads == [1]
		assert job.next_run == latest_cutoff(now, 870) + WEEK
	asyncio.run(scenario())


def test_scheduler_errors_back_off_and_isolate_communities(monkeypatch):
	async def scenario():
		now = int(datetime(2026, 10, 3, 12, tzinfo=ZONE).timestamp())
		calls = []
		async def policies(_sql):
			return [dict(community_id=c, activated_at=0, minute_of_day=870) for c in (5, 6)]
		async def apply(cid, *_args):
			calls.append(cid)
			if cid == 5:
				raise ConnectionError('offline')
		monkeypatch.setattr(tax, 'db', SimpleNamespace(fetchall=policies))
		monkeypatch.setattr(tax.gold, 'apply_weekly_tax', apply)
		job = tax.TaxScheduler()
		await job.run_due(now)
		assert calls == [5, 6]
		assert job.next_run == now + 300
		await job.run_due(now + 299)
		assert calls == [5, 6]
		await job.run_due(now + 300)
		assert job.next_run == now + 900
	asyncio.run(scenario())


def test_opening_a_match_does_not_reset_tax_deadline():
	from nammaoe2bot.features.betting.flow import PredictionJobs
	job = PredictionJobs()
	job._tax.next_run = 123456
	job.arm()
	assert job.next_run == 0
	assert job._tax.next_run == 123456


def test_operational_preview_tool_help_has_no_application_side_effects():
	import subprocess
	import sys
	result = subprocess.run([sys.executable, '-m', 'scripts.gold_tax', '--help'], capture_output=True, text=True)
	assert result.returncode == 0, result.stderr
	assert '--enable' in result.stdout and 'read-only' in result.stdout.replace('\n', '')


def test_disabled_tax_stops_querying_until_restart(monkeypatch):
	async def scenario():
		reads = []
		async def policies(_sql):
			reads.append(1)
			return []
		monkeypatch.setattr(tax, 'db', SimpleNamespace(fetchall=policies))
		job = tax.TaxScheduler()
		await job.run_due(1000)
		await job.run_due(1000 + WEEK)
		assert reads == [1]
		assert job.next_run == float('inf')
	asyncio.run(scenario())
