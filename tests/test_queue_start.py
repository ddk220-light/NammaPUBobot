"""Manual check-in bypass must stay local to one match and keep its lifecycle."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from nammaoe2bot.app import Application
from nammaoe2bot.discord.commands import queues
from nammaoe2bot.exceptions import Exceptions as Exc
from nammaoe2bot.pickup import stats
from nammaoe2bot.pickup.match.match import Match
from nammaoe2bot.runtime.database import db
from tests.test_match_lifecycle_e2e import FakeCtx, FakeMember, FakeQueueChannel


def setup(monkeypatch):
	# The queue declares its config tables on import. Allow only that read of
	# an empty test schema; restore the fail-on-unexpected-DB stub immediately.
	loop = asyncio.new_event_loop()
	try:
		with monkeypatch.context() as patch:
			patch.setattr(db, 'loop', loop, raising=False)
			patch.setattr(db, 'select', AsyncMock(return_value=[]))
			from nammaoe2bot.pickup.queue import PickupQueue
	finally:
		loop.close()
	app = Application(client=None)
	qc = FakeQueueChannel(app)
	settings = dict(Match.default_cfg)
	settings.update(p_key=1, name='test', size=4, team_size=2, ranked=True,
		pick_teams='matchmaking', team_names='Alpha Beta', team_emojis='',
		captains_role=None, check_in_timeout=300, map_cooldown=1,
		autostart=True, blacklist_role=None, whitelist_role=None)
	queue = PickupQueue(qc, SimpleNamespace(**settings))
	queue.queue = [FakeMember(i) for i in range(1, 5)]
	qc.queues, qc.topic = [queue], 'Queue status'
	qc.queue_started = AsyncMock(side_effect=lambda *_a, **_k: queue.queue.clear())
	ctx = FakeCtx(qc)
	ctx.Perms = SimpleNamespace(MODERATOR='moderator')
	ctx.check_perms, ctx.reply = Mock(), AsyncMock()
	monkeypatch.setattr(stats, 'next_match', AsyncMock(side_effect=[100, 101]))
	return app, queue, ctx


@pytest.mark.parametrize('options', [{}, {'skip_check_in': False}, {'skip_check_in': True}])
def test_manual_start_reaches_expected_stage_and_announcements(monkeypatch, options):
	app, queue, ctx = setup(monkeypatch)
	checkin = AsyncMock()
	monkeypatch.setattr('nammaoe2bot.pickup.match.checkin.CheckIn.start', checkin)
	posted, live = AsyncMock(), AsyncMock()
	app.match_events.on('teams_posted', posted)
	app.match_events.on('live', live)
	async def run():
		await queues.start(ctx, queue='TEST', **options)
		created, = app.active_matches
		await created.next_state(ctx)
		return created
	created = asyncio.run(run())
	skipped = options.get('skip_check_in', False)
	assert created.state == (Match.WAITING_REPORT if skipped else Match.CHECK_IN)
	assert checkin.await_count == int(not skipped)
	assert posted.await_count == live.await_count == int(skipped)
	assert created.serialize()['cfg']['check_in_timeout'] == (0 if skipped else 300)
	assert queue.cfg.check_in_timeout == 300
	ctx.check_perms.assert_called_once_with(ctx.Perms.MODERATOR)
	ctx.reply.assert_awaited_once_with(ctx.qc.topic)


def test_skipping_one_match_does_not_bypass_the_next_automatic_checkin(monkeypatch):
	app, queue, ctx = setup(monkeypatch)
	players = list(queue.queue)
	async def run():
		await queues.start(ctx, queue='test', skip_check_in=True)
		for player in players:
			await queue.add_member(ctx, player)
	asyncio.run(run())
	manual, automatic = app.active_matches
	assert Match.CHECK_IN not in manual.states
	assert automatic.states == [Match.CHECK_IN, Match.WAITING_REPORT]
	assert automatic.check_in.timeout == queue.cfg.check_in_timeout == 300


def test_skip_still_requires_moderator_permissions(monkeypatch):
	app, queue, ctx = setup(monkeypatch)
	ctx.check_perms.side_effect = Exc.PermissionError('moderator required')
	with pytest.raises(Exc.PermissionError):
		asyncio.run(queues.start(ctx, queue='test', skip_check_in=True))
	assert not app.active_matches
	assert len(queue.queue) == 4
	ctx.qc.queue_started.assert_not_awaited()


def test_skip_still_requires_at_least_two_players(monkeypatch):
	app, queue, ctx = setup(monkeypatch)
	queue.queue = queue.queue[:1]
	with pytest.raises(Exc.BotException, match='Not enough players'):
		asyncio.run(queues.start(ctx, queue='test', skip_check_in=True))
	assert not app.active_matches
	ctx.qc.queue_started.assert_not_awaited()
