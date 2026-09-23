import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nammaoe2bot.features.storylines import streaks, voice


def match(values=(5, 2, 3, 0)):
	players = [SimpleNamespace(id=i + 1) for i in range(4)]
	return SimpleNamespace(id=123, ranked=True, streaks=dict(zip(range(1, 5), values)),
		teams=[players[:2], players[2:]], qc=SimpleNamespace(guild_id=99), restored=False)


@pytest.mark.parametrize(('wins', 'tier'), [(0, None), (1, None), (2, None),
	(3, 3), (4, 4), (5, 5), (6, 6), (7, 7), (8, 8), (9, 9), (10, 10), (99, 10)])
def test_audio_thresholds(wins, tier):
	m = match((wins, -2, 0, -4))
	assert streaks.clip_for(wins) == tier
	assert streaks.match_leader(m) == ((1, wins) if tier else None)


@pytest.mark.parametrize('values', [(5, 5, 3, 0), (5, 1, 5, 0), (0, -5, 0, -3), (2, 1, 1, 0)])
def test_ties_and_small_streaks_are_silent(values):
	assert streaks.match_leader(match(values)) is None


def setup(monkeypatch, *, counts=(1, 3), mode='success'):
	class Channel:
		def __init__(self, guild, ident, count):
			self.id, self.guild = ident, guild
			self.members = [SimpleNamespace(bot=False) for _ in range(count)]
			self.perms = SimpleNamespace(view_channel=True, connect=True, speak=True)
			self.calls = 0

		def permissions_for(self, _member):
			return self.perms

		async def connect(self, **kwargs):
			assert kwargs == dict(timeout=8, reconnect=False, self_deaf=True)
			self.calls += 1
			self.guild.voice_client = client
			if mode == 'connect_failure':
				raise RuntimeError('connection failed after registration')
			if mode == 'empty_after_connect':
				self.members = []
			if mode == 'roster_changed':
				m.teams[0].reverse()
			if mode == 'permissions_changed':
				self.perms.speak = False
			if mode == 'match_ended':
				app.active_matches.clear()
			await asyncio.sleep(0)
			return client

	class Client:
		def __init__(self):
			self.plays = self.disconnects = 0

		def play(self, source, *, after):
			self.plays += 1
			if mode == 'play_failure':
				raise RuntimeError('play failed')
			if mode != 'hang':
				after(RuntimeError('audio thread failed') if mode == 'callback_failure' else None)

		async def disconnect(self, *, force):
			assert force
			self.disconnects += 1
			guild.voice_client = None

	m = match()
	guild = SimpleNamespace(id=99, me=object(), voice_client=None)
	guild.voice_channels = [Channel(guild, i + 10, n) for i, n in enumerate(counts)]
	app = SimpleNamespace(ready=True, client=SimpleNamespace(get_guild=lambda _id: guild), active_matches=[m])
	client = Client()
	source = SimpleNamespace(cleanup=lambda: None)
	tiers = []
	def factory(tier):
		tiers.append(tier)
		return source
	monkeypatch.setattr(voice, '_source', factory)
	return m, guild, app, client, tiers, voice.StreakVoice(app)


def test_selects_most_people_excluding_bots_with_stable_channel_ties(monkeypatch):
	_m, guild, _app, _client, _tiers, _service = setup(monkeypatch)
	guild.voice_channels[0].members += [SimpleNamespace(bot=True)] * 20
	assert voice.busiest_channel(guild).id == 11
	guild.voice_channels[1].perms.speak = False
	assert voice.busiest_channel(guild).id == 10
	guild.voice_channels[1].perms.speak = True
	guild.voice_channels[1].members = [SimpleNamespace(bot=False)]
	assert voice.busiest_channel(guild).id == 10


def test_one_person_is_enough_and_duplicate_events_do_not_replay(monkeypatch):
	m, guild, _app, client, tiers, service = setup(monkeypatch, counts=(0, 1))
	async def run():
		service.announce(m)
		service.announce(m)
		assert len(service.tasks) == 1
		await asyncio.gather(*service.tasks.values())
		service.announce(m)
		assert not service.tasks
	assert asyncio.run(run()) is None
	assert client.plays == client.disconnects == 1
	assert tiers == [5]
	assert guild.voice_channels[1].calls == 1
	assert guild.voice_client is None


@pytest.mark.parametrize('reason', ['tie', 'empty', 'restored', 'unranked', 'busy', 'not_ready', 'no_streaks'])
def test_silent_conditions_do_not_connect(monkeypatch, reason):
	m, guild, app, client, tiers, service = setup(monkeypatch)
	if reason == 'tie':
		m.streaks[2] = 5
	elif reason == 'empty':
		for channel in guild.voice_channels:
			channel.members = [SimpleNamespace(bot=True)]
	elif reason == 'restored':
		m.restored = True
	elif reason == 'unranked':
		m.ranked = False
	elif reason == 'busy':
		guild.voice_client = object()
	elif reason == 'not_ready':
		app.ready = False
	else:
		m.streaks = None
	asyncio.run(_announce(service, m))
	assert not tiers and not client.plays and not client.disconnects
	assert not any(channel.calls for channel in guild.voice_channels)


async def _announce(service, m):
	service.announce(m)
	if service.tasks:
		await asyncio.gather(*service.tasks.values())


@pytest.mark.parametrize('mode', ['connect_failure', 'play_failure', 'callback_failure',
	'empty_after_connect', 'roster_changed', 'permissions_changed', 'match_ended'])
def test_failures_and_stale_announcements_disconnect_without_raising(monkeypatch, mode):
	m, guild, _app, client, _tiers, service = setup(monkeypatch, mode=mode)
	asyncio.run(_announce(service, m))
	assert client.disconnects == 1
	assert guild.voice_client is None
	assert not service.tasks
	if mode not in ('play_failure', 'callback_failure'):
		assert client.plays == 0


def test_cancellation_disconnects_and_releases_guild(monkeypatch):
	m, guild, _app, client, _tiers, service = setup(monkeypatch, mode='hang')
	async def run():
		service.announce(m)
		task, = service.tasks.values()
		for _ in range(10):
			await asyncio.sleep(0)
			if client.plays:
				break
		assert client.plays == 1
		task.cancel()
		with pytest.raises(asyncio.CancelledError):
			await task
	asyncio.run(run())
	assert client.disconnects == 1 and guild.voice_client is None
	assert not service.tasks


def test_timeout_stops_a_stuck_player_and_disconnects(monkeypatch):
	m, guild, _app, client, _tiers, service = setup(monkeypatch, mode='hang')
	timeout = asyncio.timeout
	monkeypatch.setattr(voice.asyncio, 'timeout', lambda _seconds: timeout(0.01))
	asyncio.run(_announce(service, m))
	assert client.plays == client.disconnects == 1
	assert not service.tasks and guild.voice_client is None


def test_simultaneous_matches_do_not_fight_for_one_guild_connection(monkeypatch):
	m, _guild, app, client, _tiers, service = setup(monkeypatch)
	other = copy.copy(m)
	other.id = 124
	app.active_matches.append(other)
	async def run():
		service.announce(m)
		service.announce(other)
		assert len(service.tasks) == 1
		await asyncio.gather(*service.tasks.values())
	asyncio.run(run())
	assert client.plays == 1


def test_posting_failure_never_triggers_audio_and_audio_failure_preserves_tease(monkeypatch):
	from nammaoe2bot import wiring
	m = match()
	m.storyline_ctx = {'seed': 123}
	service = SimpleNamespace(announce=lambda _m: (_ for _ in ()).throw(RuntimeError('audio failure')))
	m.qc.app = SimpleNamespace(streak_voice=service)
	monkeypatch.setattr(wiring.insights, 'build_insights_embed', AsyncMock(return_value=object()))
	ctx = SimpleNamespace(notice=AsyncMock())
	asyncio.run(wiring._post_team_insights(m, ctx))
	assert m.storyline_ctx == {'seed': 123}
	service.announce = lambda _m: pytest.fail('must not play when text failed')
	ctx.notice.side_effect = RuntimeError('text failed')
	asyncio.run(wiring._post_team_insights(m, ctx))
	assert m.storyline_ctx is None
