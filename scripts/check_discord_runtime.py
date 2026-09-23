"""Offline check of real nextcord registration, UI payloads and bundled audio.

Run outside pytest: conftest's database/config fakes keep this away from live
services, but the Discord library and its voice dependencies are real.
"""
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, create_autospec

import aiohttp  # noqa: F401 -- preserve its real submodules before installing DB fakes
import nextcord

# Running a script by path puts scripts/ on sys.path; the package root is given
# explicitly by PYTHONPATH=. in CI and the documented local invocation.
real_modules = {name: module for name, module in sys.modules.items()
	if name.split('.')[0] in ('nextcord', 'aiohttp')}
import tests.conftest  # noqa: F401
for name in list(sys.modules):
	if name.split('.')[0] in ('nextcord', 'aiohttp'):
		del sys.modules[name]
sys.modules.update(real_modules)
sys.modules.pop('nammaoe2bot.runtime.client', None)

from nammaoe2bot.runtime.config import cfg
cfg.DC_SLASH_SERVERS = [123]
from nammaoe2bot.runtime.database import db
db.loop = asyncio.new_event_loop()
db.select = AsyncMock(return_value=[])
from nammaoe2bot.runtime.client import dc
from nammaoe2bot.discord import slash  # noqa: F401
from nammaoe2bot.features.civs import picking
from nammaoe2bot.features.civs.pick_view import card
from nammaoe2bot.features.storylines.opus_source import OpusClip
from nammaoe2bot.features.storylines.voice import StreakVoice
from nammaoe2bot.features.betting import embeds as betting_embeds
from nammaoe2bot.runtime.paths import data


async def check_voice_api():
	# Enforce the installed library's method signatures at the network boundary.
	# A permissive **kwargs fake previously hid an invalid connect(self_deaf=...).
	guild = create_autospec(nextcord.Guild, instance=True)
	channel = create_autospec(nextcord.VoiceChannel, instance=True)
	voice = create_autospec(nextcord.VoiceClient, instance=True)
	guild.id, guild.me, guild.voice_client = 99, SimpleNamespace(id=3), None
	guild.voice_channels = [channel]
	channel.id, channel.guild = 10, guild
	channel.members = [SimpleNamespace(bot=False)]
	channel.permissions_for.return_value = nextcord.Permissions(view_channel=True, connect=True, speak=True)
	channel.connect.return_value = voice
	played_packets = []
	def play(source, *, after):
		assert isinstance(source, nextcord.AudioSource) and source.is_opus()
		played_packets.extend(iter(source.read, b''))
		after(None)
	voice.play.side_effect = play
	match = SimpleNamespace(id=123, ranked=True, streaks={1: 3, 2: 0},
		teams=[[SimpleNamespace(id=1)], [SimpleNamespace(id=2)]], qc=SimpleNamespace(guild_id=99))
	app = SimpleNamespace(ready=True, client=SimpleNamespace(get_guild=lambda _id: guild), active_matches=[match])
	service = StreakVoice(app)
	service.announce(match)
	await asyncio.gather(*service.tasks.values())
	channel.connect.assert_awaited_once_with(timeout=8, reconnect=False)
	guild.change_voice_state.assert_awaited_once_with(channel=channel, self_deaf=True)
	voice.play.assert_called_once()
	voice.disconnect.assert_awaited_once_with(force=True)
	assert played_packets and not service.tasks


async def main():
	assert nextcord.__version__ == '3.2.0'
	from nextcord.voice_client import has_dave, has_nacl
	assert has_dave and has_nacl, 'voice encryption dependencies missing'
	assert dc.intents.voice_states, 'voice channel occupancy requires voice-state events'
	dc.add_all_application_commands()
	commands = dc.get_all_application_commands()
	payloads = [command.get_payload(123) for command in commands]
	def command_count(payload):
		subcommands = [option for option in payload.get('options', []) if option['type'] in (1, 2)]
		return sum(command_count(option) for option in subcommands) if subcommands else 1
	count = sum(command_count(payload) for payload in payloads)
	assert len(payloads) == 21 and count >= 40
	assert {'civpick', 'rank', 'add', 'remove', 'report'} <= {p['name'] for p in payloads}
	queue = next(p for p in payloads if p['name'] == 'queue')
	start = next(p for p in queue['options'] if p['name'] == 'start')
	skip = next(p for p in start['options'] if p['name'] == 'skip_check_in')
	assert skip['type'] == 5 and not skip.get('required'), 'check-in bypass must be an optional boolean'
	for command in queue['options']:
		if command['name'] != 'start':
			assert not any(p['name'] == 'skip_check_in' for p in command.get('options', []))
	json.dumps(payloads)
	await check_voice_api()
	public = betting_embeds.bet_view(123)
	assert [b.custom_id for b in public.children] == ['betpick:123']
	for view in [public, betting_embeds.side_view(123, 'Alpha', 'Beta'),
			betting_embeds.stake_view(123, 0, 7, 400, 456, allow_cancel=True), betting_embeds.cancel_view(123)]:
		assert not view.auto_defer and not view.prevent_update and len(view.to_components()) == 1

	roster = [dict(id=i + 1, team=i % 2) for i in range(8)]
	for when, lengths in [(1000, [5, 5, 3]), (picking.DLC_TRIAL_START, [5, 5, 3, 3])]:
		state = picking.new_round(roster, picking.select_pool([], when), 1, when, 3)
		embed, view = card(123, state)
		assert [len(row['components']) for row in view.to_components()] == lengths
		assert view.is_persistent() and not view.auto_defer and not view.prevent_update
		assert len(embed.description) < 4096

	manifest = json.loads(Path(data('audio', 'dota', 'manifest.json')).read_text())
	assert [row['wins'] for row in manifest] == list(range(3, 11))
	total = 0
	for row in manifest:
		path = Path(data('audio', 'dota', row['filename']))
		assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']
		source = OpusClip(path)
		assert isinstance(source, nextcord.AudioSource) and source.is_opus()
		packets = list(iter(source.read, b''))
		assert packets and len(packets) < 300
		for packet in packets:
			# These prepared files use CELT, one 20 ms frame (RFC 6716 §3.1).
			config, code = packet[0] >> 3, packet[0] & 3
			frames = 1 if code == 0 else 2 if code in (1, 2) else packet[1] & 63
			assert config >= 16 and 2.5 * 2 ** (config % 4) * frames == 20
			assert len(packet) <= 1275
		source.cleanup()
		assert source.read() == b''
		total += path.stat().st_size
	print(json.dumps(dict(nextcord=nextcord.__version__, slash_commands=count,
		voice_encryption='DAVE', voice_api='passed', clips=len(manifest), audio_bytes=total, components='passed')))
	await dc.close()
	db.loop.close()


asyncio.run(main())
