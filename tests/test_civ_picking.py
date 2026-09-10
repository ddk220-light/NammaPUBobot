"""Behavior and transaction races for the voluntary civ picker."""
import asyncio
import copy
import json
import random
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from nammaoe2bot.features.civs import picking, pick_store as store
from nammaoe2bot.features.civs.pick_service import PickService


def state(n=8):
	return picking.new_round([dict(id=i + 1, team=i % 2) for i in range(n)],
		list(picking.CIVS[:12]), 1, 1000, 3)


def test_pool_preserves_every_unused_civ_when_filling_shortage():
	history = [dict(civ=c, uses=2, last_at=900) for c in picking.CIVS[5:]]
	history[0]['uses'] = 1
	pool = picking.select_pool(history, rng=random.Random(7))
	assert len(pool) == len(set(pool)) == 12
	assert set(picking.CIVS[:6]) <= set(pool)


def test_pool_avoids_recent_and_previous_options_when_possible():
	history = [dict(civ=c.upper(), uses=1, last_at=900) for c in picking.CIVS[:15]]
	previous = picking.CIVS[15:27]
	pool = picking.select_pool(history, previous, random.Random(1))
	assert not set(pool) & set(picking.CIVS[:27])


def test_pool_prefers_least_frequent_then_oldest_repeats():
	history = [dict(civ=c, uses=1, last_at=i) for i, c in enumerate(picking.CIVS)]
	assert set(picking.select_pool(history)) == set(picking.CIVS[:12])


def test_only_successful_claim_spends_choice_and_random_is_unlimited():
	s = state(4)
	assert picking.claim(s, 1, 0, 1, 1001) is None
	assert 'taken' in picking.claim(s, 2, 0, 1, 1002)
	assert picking.claim(s, 2, 1, 1, 1003) is None
	assert 'already' in picking.claim(s, 1, 2, 1, 1004)
	assert picking.claim(s, 3, 12, 1, 1004) is None
	assert picking.claim(s, 4, 12, 1, 1005) is None
	assert s['status'] == 'closed'
	assert s['picks'] == {'1': 0, '2': 1, '3': 12, '4': 12}


def test_timeout_fills_missing_and_exact_deadline_rejects():
	s = state(2)
	picking.claim(s, 1, 2, 1, 1001)
	assert picking.claim(s, 2, 3, 1, 1180) == 'This round is closed.'
	assert s['picks'] == {'1': 2, '2': 12}
	assert s['timed_out'] == ['2']
	assert not picking.expire(s, 1200)


def test_stale_foreign_and_bad_choices_do_not_claim():
	s = state()
	assert picking.claim(s, 1, 2, 0, 1001)
	assert picking.claim(s, 99, 2, 1, 1001)
	assert picking.claim(s, 1, -1, 1, 1001)
	assert picking.claim(s, 1, 13, 1, 1001)
	assert not s['picks']


class MemoryDB:
	"""A serializable connection with rollback, so races use the real store code."""
	def __init__(self):
		self.rows = {}
		self.lock = asyncio.Lock()
		self.in_tx = False
		self.trace = []
		self.recent = []
		self.on_lock = None

	@asynccontextmanager
	async def transaction(self):
		async with self.lock:
			before = copy.deepcopy(self.rows)
			self.in_tx = True
			try:
				yield self
			except Exception:
				self.rows = before
				raise
			finally:
				self.in_tx = False

	async def fetchone(self, sql, args):
		self.trace.append((sql, args))
		if 'FOR UPDATE' in sql:
			assert self.in_tx
			if self.on_lock:
				self.on_lock()
		await asyncio.sleep(0)
		return copy.deepcopy(self.rows.get(tuple(args)))

	async def fetchall(self, sql, args=None):
		self.trace.append((sql, args))
		if 'civ_picks' in sql:
			assert self.in_tx
			return self.recent
		return [copy.deepcopy(r) for r in self.rows.values() if r['status'] == 'open' or r['dirty']]

	async def execute(self, sql, args):
		self.trace.append((sql, args))
		if sql.startswith('INSERT'):
			assert self.in_tx
			key = tuple(args)
			self.rows.setdefault(key, dict(channel_id=key[0], match_id=key[1], state_json='{}',
				status='new', dirty=0, message_id=None))
		elif 'SET state_json=' in sql:
			assert self.in_tx
			row = self.rows[tuple(args[2:])]
			row.update(state_json=args[0], status=args[1], dirty=1)
		elif 'SET message_id=' in sql:
			row = self.rows[tuple(args[1:3])]
			if row['state_json'] == args[3]:
				row.update(message_id=args[0], dirty=0)
		else:
			raise AssertionError(sql)


def setup_db(monkeypatch):
	db = MemoryDB()
	monkeypatch.setattr(store, 'db', db)
	clock = SimpleNamespace(now=1000)
	monkeypatch.setattr(store, 'time', SimpleNamespace(time=lambda: clock.now))
	return db, clock


async def start(n=8, redo=False, user=1, admin=False, channel=10):
	return await store.start(channel, 123, state(n)['roster'], user, 3, redo, admin)


async def pick(uid, choice, generation=1):
	return await store.change(10, 123, lambda s, now: picking.claim(s, uid, choice, generation, now))


def test_concurrent_creations_and_claims(monkeypatch):
	setup_db(monkeypatch)
	async def run():
		assert sorted(await asyncio.gather(start(), start())) == [False, True]
		results = await asyncio.gather(pick(1, 0), pick(2, 0))
		assert results.count(None) == 1
		assert len((await store.get(10, 123))['state']['picks']) == 1
		loser = 2 if results[0] is None else 1
		assert await pick(loser, 1) is None
	asyncio.run(run())


def test_replay_command_no_reset_redo_permissions_and_generation(monkeypatch):
	db, clock = setup_db(monkeypatch)
	async def run():
		await start()
		await pick(1, 0)
		before = copy.deepcopy(db.rows)
		assert not await start()
		assert db.rows == before
		with pytest.raises(ValueError, match='30 seconds'):
			await start(redo=True)
		clock.now += 31
		with pytest.raises(ValueError, match='initiator'):
			await start(redo=True, user=2)
		assert db.rows == before
		assert await start(redo=True)
		assert 'replaced' in await pick(2, 1, 1)
		assert await pick(2, 1, 2) is None
		assert (await store.get(10, 123))['state']['picks'] == {'2': 1}
	asyncio.run(run())


def test_deadline_read_after_waiting_for_lock(monkeypatch):
	db, clock = setup_db(monkeypatch)
	async def run():
		await start(2)
		db.on_lock = lambda: setattr(clock, 'now', 1180)
		assert 'closed' in await pick(1, 0)
		s = (await store.get(10, 123))['state']
		assert s['picks'] == {'1': 12, '2': 12}
	asyncio.run(run())


def test_history_query_scoped_and_failed_validation_rolls_back(monkeypatch):
	db, _clock = setup_db(monkeypatch)
	async def run():
		await start()
		query, args = next((q, a) for q, a in db.trace if 'GROUP BY civ' in q)
		assert 'channel_id=%s' in query
		assert args == [10, 1000 - 86400, 1000]
		def fail():
			raise ValueError('roster changed')
		with pytest.raises(ValueError):
			await store.start(20, 999, state()['roster'], 1, 3, False, validate=fail)
		assert (20, 999) not in db.rows
	asyncio.run(run())


def test_channel_isolation_and_render_cas(monkeypatch):
	db, _clock = setup_db(monkeypatch)
	async def run():
		await start()
		await start(channel=20)
		row = await store.get(10, 123)
		await pick(1, 1)
		await store.rendered(10, 123, row, 900)
		assert db.rows[10, 123]['dirty'] == 1
		assert (await store.get(20, 123))['state']['picks'] == {}
		await store.rendered(10, 123, await store.get(10, 123), 901)
		assert db.rows[10, 123]['dirty'] == 0
	asyncio.run(run())


def app_for(n=2):
	players = [SimpleNamespace(id=i + 1) for i in range(n)]
	match = SimpleNamespace(id=123, qc=SimpleNamespace(id=10), ranked=True, players=players,
		teams=[players[::2], players[1::2], []], cfg={})
	return SimpleNamespace(ready=True, active_matches=[match], client=None)


def test_restart_closes_expired_round_once_and_idle_does_no_queries(monkeypatch):
	db, clock = setup_db(monkeypatch)
	async def run():
		await start(2)
		clock.now = 1200
		service = PickService(app_for())
		renders = []
		async def render(key, row):
			renders.append(row['state'])
			await store.rendered(*key, row, 900)
		service.render = render
		await service._work()
		assert renders[0]['picks'] == {'1': 12, '2': 12}
		assert not service.due
		before = len(db.trace)
		service.think()
		assert service.task is None
		assert len(db.trace) == before
	asyncio.run(run())


def test_restart_with_changed_roster_invalidates(monkeypatch):
	setup_db(monkeypatch)
	async def run():
		await start(4)
		service = PickService(app_for(2))
		async def render(key, row):
			await store.rendered(*key, row, 900)
		service.render = render
		await service._work()
		assert (await store.get(10, 123))['state']['status'] == 'invalidated'
	asyncio.run(run())


def test_missing_session_does_not_leave_retry_loop(monkeypatch):
	setup_db(monkeypatch)
	async def run():
		service = PickService(app_for())
		service.schedule((10, 123))
		await service._work()
		assert not service.due
	asyncio.run(run())


def test_history_aliases_and_unpicked_bucket():
	history = [dict(civ=c, uses=1, last_at=900) for c in ('Inca', 'Maya', 'Khitans', 'Khmers')]
	pool = picking.select_pool(history)
	assert not set(pool) & {'Incas', 'Mayans', 'Khitan', 'Khmer'}
	match = app_for().active_matches[0]
	assert picking.validate_match(match) is None
	match.teams[2].append(SimpleNamespace(id=99))
	assert 'formed' in picking.validate_match(match)


def test_render_failure_retries_are_bounded(monkeypatch):
	db, clock = setup_db(monkeypatch)
	async def run():
		await start(2)
		clock.now = 1200
		service = PickService(app_for())
		calls = []
		async def fail(_key, _row):
			calls.append(1)
			raise RuntimeError('Discord unavailable')
		service.render = fail
		for _ in range(3):
			if service.due:
				service.due[10, 123] = 0
			await service._work()
		assert len(calls) == 3
		assert not service.due
		assert db.rows[10, 123]['dirty'] == 1
		assert (await store.get(10, 123))['state']['status'] == 'closed'
	asyncio.run(run())


def test_card_has_thirteen_buttons_and_final_assignments():
	from nammaoe2bot.features.civs.pick_view import card
	s = state(2)
	embed, view = card(123, s)
	assert len(view.children) == 13
	assert view.auto_defer is False and view.prevent_update is False
	assert [b.row for b in view.children] == [0] * 5 + [1] * 5 + [2] * 3
	assert len({b.custom_id for b in view.children}) == 13
	picking.claim(s, 1, 0, 1, 1001)
	_embed, view = card(123, s)
	assert view.children[0].disabled
	assert not view.children[12].disabled
	picking.expire(s, 1180)
	embed, view = card(123, s)
	assert all(b.disabled for b in view.children)
	assert 'Random (timed out)' in embed.description
	assert 'Final' in embed.title


class Reply:
	def __init__(self):
		self.done = False
		self.messages = []

	def is_done(self):
		return self.done

	async def defer(self, **_kwargs):
		self.done = True

	async def send_message(self, message, **kwargs):
		self.done = True
		self.messages.append((message, kwargs))

	async def send(self, message, **kwargs):
		self.messages.append((message, kwargs))


def context(app, user_id=1, admin=False):
	response, followup = Reply(), Reply()
	channel = SimpleNamespace(id=10, guild=SimpleNamespace(id=5),
		permissions_for=lambda _user: SimpleNamespace(manage_channels=admin))
	return SimpleNamespace(qc=SimpleNamespace(app=app, id=10),
		author=SimpleNamespace(id=user_id), channel=channel,
		interaction=SimpleNamespace(response=response), reply=followup.send,
		replies=followup.messages)


def install_service(app):
	service = PickService(app)
	app.civ_picker = service
	service.throttled = lambda _uid: False
	async def render(key, row):
		await store.rendered(*key, row, 900)
	service.render = render
	return service


def test_command_create_retrieve_and_spectator_permissions(monkeypatch):
	from nammaoe2bot.features.civs.pick_commands import civpick
	db, _clock = setup_db(monkeypatch)
	async def run():
		app = app_for()
		install_service(app)
		ctx = context(app, 99)
		await civpick(ctx, 123)
		assert 'Only match participants' in ctx.replies[-1][0]
		assert not db.rows
		ctx = context(app)
		await civpick(ctx, 123)
		assert 'discord.com/channels/5/10/900' in ctx.replies[-1][0]
		await pick(1, 0)
		before = (await store.get(10, 123))['state']
		app.active_matches.clear()
		await civpick(context(app, 99), 123)
		assert (await store.get(10, 123))['state'] == before
	asyncio.run(run())


def test_interaction_rejects_foreign_message_and_records_one_pick(monkeypatch):
	from nammaoe2bot.features.civs.pick_commands import on_interaction
	setup_db(monkeypatch)
	async def run():
		app = app_for()
		service = install_service(app)
		await start(2)
		await service.refresh((10, 123))
		def interaction(message_id, uid=1):
			return SimpleNamespace(data={'custom_id': 'civpick:123:1:0'}, channel_id=10,
				message=SimpleNamespace(id=message_id), user=SimpleNamespace(id=uid),
				response=Reply(), followup=Reply())
		wrong = interaction(901)
		await on_interaction(wrong, app)
		assert 'latest' in wrong.followup.messages[-1][0]
		assert not (await store.get(10, 123))['state']['picks']
		good = interaction(900)
		await on_interaction(good, app)
		assert good.followup.messages[-1][0] == 'Your pick is saved.'
		assert (await store.get(10, 123))['state']['picks'] == {'1': 0}
		duplicate = interaction(900)
		await on_interaction(duplicate, app)
		assert 'already' in duplicate.followup.messages[-1][0]
	asyncio.run(run())


def test_history_index_migration_is_idempotent():
	from nammaoe2bot.runtime import migrations
	from tests.test_migrations import FakeDb
	db = FakeDb(tables={'civ_picks'})
	asyncio.run(migrations._m012(db))
	asyncio.run(migrations._m012(db))
	assert sum('CREATE INDEX' in q for q in db.executed) == 1


def test_redo_racing_old_claim_cannot_leak_into_new_round(monkeypatch):
	_db, clock = setup_db(monkeypatch)
	async def run():
		await start(2)
		clock.now = 1040
		await asyncio.gather(start(2, redo=True), pick(2, 0, generation=1))
		s = (await store.get(10, 123))['state']
		assert s['generation'] == 2
		assert s['picks'] == {}
		assert s['closes_at'] == 1220
	asyncio.run(run())


def test_two_simultaneous_choices_by_one_player_only_record_one(monkeypatch):
	setup_db(monkeypatch)
	async def run():
		await start()
		results = await asyncio.gather(pick(1, 0), pick(1, 1))
		assert results.count(None) == 1
		assert len((await store.get(10, 123))['state']['picks']) == 1
	asyncio.run(run())


def test_timeout_racing_claim_has_one_terminal_snapshot(monkeypatch):
	_db, clock = setup_db(monkeypatch)
	async def run():
		await start(2)
		clock.now = 1180
		await asyncio.gather(store.change(10, 123, picking.expire), pick(1, 0))
		s = (await store.get(10, 123))['state']
		assert s['picks'] == {'1': 12, '2': 12}
		assert s['status'] == 'closed'
	asyncio.run(run())
