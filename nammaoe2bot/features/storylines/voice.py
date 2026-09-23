"""One short announcement; no idle connection, polling, or playback queue."""
import asyncio

from nammaoe2bot.runtime.console import log
from nammaoe2bot.runtime.paths import data
from .streaks import clip_for, match_leader


def listeners(channel):
	return sum(not member.bot for member in channel.members)


def can_play(channel):
	if not channel.guild.me:
		return False
	perms = channel.permissions_for(channel.guild.me)
	return perms.view_channel and perms.connect and perms.speak and listeners(channel) > 0


def busiest_channel(guild):
	# voice_channels excludes Stage channels. Break population ties consistently.
	eligible = [channel for channel in guild.voice_channels if can_play(channel)]
	return min(eligible, key=lambda channel: (-listeners(channel), channel.id), default=None)


def _source(tier):
	from .opus_source import OpusClip
	return OpusClip(data('audio', 'dota', f'{tier}.ogg'))


class StreakVoice:
	def __init__(self, app):
		self.app = app
		self.tasks = {}

	def announce(self, match):
		# Restored matches are quiet even if a crash preceded the next snapshot.
		if (getattr(match, 'restored', False) or getattr(match, '_streak_audio_attempted', False)
				or not self.app.ready):
			return
		match._streak_audio_attempted = True
		leader = match_leader(match)
		if not leader:
			return
		guild = self.app.client.get_guild(match.qc.guild_id)
		if guild is None or guild.id in self.tasks or guild.voice_client is not None:
			return
		channel = busiest_channel(guild)
		if channel is None:
			log.info(f'Streak audio skipped for match {match.id}: no occupied, permitted voice channel.')
			return
		roster = tuple(p.id for team in match.teams[:2] for p in team)
		task = asyncio.create_task(self._play(match, channel, clip_for(leader[1]), roster),
			name=f'streak-audio:{match.id}')
		self.tasks[guild.id] = task
		task.add_done_callback(lambda _task: self.tasks.pop(guild.id, None))

	async def _play(self, match, channel, tier, roster):
		if channel.guild.voice_client is not None:
			return
		voice, source = None, None
		try:
			# Bound the whole attempt, including library handshake retries.
			async with asyncio.timeout(15):
				voice = await channel.connect(timeout=8, reconnect=False)
				# nextcord sets self-deafening through the guild, not connect().
				await channel.guild.change_voice_state(channel=channel, self_deaf=True)
				if (not self.app.ready or match not in self.app.active_matches
						or getattr(match, '_cancelled', False) or getattr(match, '_result_committed', False)
						or tuple(p.id for team in match.teams[:2] for p in team) != roster
						or not can_play(channel)):
					return
				source = _source(tier)
				loop = asyncio.get_running_loop()
				finished = loop.create_future()
				def complete(error):
					if not finished.done():
						finished.set_result(error)
				def after(error):
					# nextcord calls this from its audio thread.
					try:
						loop.call_soon_threadsafe(complete, error)
					except RuntimeError:
						pass  # Event loop already closed during process shutdown.
				voice.play(source, after=after)
				if error := await finished:
					raise error
				log.info(f'Streak audio played for match {match.id}: tier={tier}, channel={channel.id}.')
		except Exception as exc:
			log.error(f'Streak audio failed for match {match.id}: {type(exc).__name__}: {exc}')
		finally:
			# connect can fail after registering a partial client with the guild.
			voice = voice or channel.guild.voice_client
			if voice is not None:
				try:
					await asyncio.wait_for(voice.disconnect(force=True), timeout=1)
				except Exception as exc:
					log.error(f'Streak voice disconnect failed: {type(exc).__name__}: {exc}')
			if source is not None:
				source.cleanup()
