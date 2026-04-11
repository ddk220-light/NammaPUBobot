import csv
import os
import traceback
from nextcord import ChannelType, Activity, ActivityType

from core.client import dc
from core.database import db
from core.console import log
from core.config import cfg
import bot
from bot.elo_sync import process_elo_sync
from bot.civ_sync import parse_lobby_embed, buffer_lobby_result
from bot.message_logger import log_channel_message, log_bot_message


async def seed_ratings_from_csv():
	"""One-time bulk seed of player ratings from data/qc_players.csv into all queue channels."""
	csv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'qc_players.csv')
	if not os.path.exists(csv_path):
		log.info("No data/qc_players.csv found, skipping rating seed.")
		return

	for qc in bot.queue_channels.values():
		dest_id = qc.rating.channel_id
		# Check if this channel already has rated players
		existing = await db.select(['user_id'], 'qc_players', where={'channel_id': dest_id})
		rated_existing = [p for p in existing if p.get('user_id')]
		if len(rated_existing) > 0:
			log.info(f"\tChannel {dest_id} already has {len(rated_existing)} players, skipping CSV seed.")
			continue

		with open(csv_path, newline='') as f:
			reader = csv.DictReader(f)
			rows = [r for r in reader if r.get('rating')]

		if not rows:
			continue

		to_insert = []
		for r in rows:
			to_insert.append({
				'channel_id': dest_id,
				'user_id': int(r['user_id']),
				'nick': r['nick'],
				'rating': int(r['rating']),
				'deviation': int(r['deviation']) if r.get('deviation') else 300,
				'wins': int(r.get('wins') or 0),
				'losses': int(r.get('losses') or 0),
				'draws': int(r.get('draws') or 0),
				'streak': int(r.get('streak') or 0),
			})

		await db.insert_many('qc_players', to_insert, on_dublicate='replace')
		log.info(f"\tSeeded {len(to_insert)} player ratings from CSV into channel {dest_id}.")


@dc.event
async def on_init():
	await bot.stats.check_match_id_counter()


_last_state_save = 0
_STATE_SAVE_INTERVAL = 30  # seconds; crash-survivability backstop for in-flight matches

# Stamped every tick — read by bot/web.py handle_health as the "last_tick_age_seconds"
# liveness signal. If think() stops running (rare, but possible under a deep
# deadlock or if the supervisor misses an exception) this value stops advancing
# and /health reflects the stall.
last_tick_at = 0.0


@dc.event
async def on_think(frame_time):
	global _last_state_save, last_tick_at
	last_tick_at = frame_time

	# Iterate over a snapshot so removing a failed match from the set
	# doesn't skip the rest of the tick. Previously an exception in one
	# match.think() broke the whole for-loop and starved every later
	# match that tick.
	for match in list(bot.active_matches):
		try:
			await match.think(frame_time)
		except Exception as e:
			log.error("\n".join([
				"Error at Match.think().",
				f"match_id: {match.id}).",
				f"{str(e)}. Traceback:\n{traceback.format_exc()}=========="
			]))
			if match in bot.active_matches:
				bot.active_matches.remove(match)
			continue
	await bot.expire.think(frame_time)
	await bot.noadds.think(frame_time)
	await bot.stats.jobs.think(frame_time)
	await bot.expire_auto_ready(frame_time)

	# Sweep leaked check-in reaction callbacks. See _TTLReactionDict
	# docstring in bot/__init__.py — entries older than 30 minutes are
	# guaranteed-dead leaks from check-in exit paths that raised before
	# unsubscribing. Cheap (O(n), n ≈ 0-3) so no need to gate on interval.
	bot.waiting_reactions.sweep_expired(frame_time)

	# Periodic state snapshot — if the process crashes before a clean
	# shutdown, SIGTERM (or the crash supervisor in PUBobot2.py) can only
	# save state best-effort. This keeps a rolling ≤30s-old backup on
	# disk for unexpected exits.
	if frame_time - _last_state_save >= _STATE_SAVE_INTERVAL:
		try:
			bot.save_state()
			_last_state_save = frame_time
		except Exception as e:
			log.error(f"Periodic save_state failed: {e}\n{traceback.format_exc()}")


@dc.event
async def on_message(message):
	if message.channel.type == ChannelType.private and message.author.id != dc.user.id:
		await message.channel.send(cfg.HELP)

	if message.channel.type != ChannelType.text:
		return

	if message.content == '!enable_pubobot':
		await bot.enable_channel(message)
	elif message.content == '!disable_pubobot':
		await bot.disable_channel(message)

	# Sync ELO from original Pubobot
	pubobot_id = getattr(cfg, 'PUBOBOT_USER_ID', None)
	if (pubobot_id
		and message.author.id == pubobot_id
		and message.author.bot
		and '```markdown' in message.content
		and 'results' in message.content):
		try:
			log_bot_message(message, 'Pubobot')
			await process_elo_sync(message)
		except Exception as e:
			log.error(f"ELO sync error: {e}\n{traceback.format_exc()}")

	# Buffer AOE2LobbyBOT match results for civ sync
	lobbybot_id = getattr(cfg, 'LOBBYBOT_USER_ID', None)
	if (lobbybot_id
		and message.author.id == lobbybot_id
		and message.author.bot
		and message.embeds):
		try:
			log_bot_message(message, 'AOE2LobbyBOT')
			parsed = parse_lobby_embed(message)
			if parsed:
				buffer_lobby_result(parsed)
		except Exception as e:
			log.error(f"Civ sync buffer error: {e}\n{traceback.format_exc()}")

	# Log all channel messages in queue channels
	if message.channel.id in bot.queue_channels:
		try:
			log_channel_message(message)
		except Exception:
			pass


@dc.event
async def on_reaction_add(reaction, user):
	if user.id != dc.user.id and reaction.message.id in bot.waiting_reactions:
		await bot.waiting_reactions[reaction.message.id](reaction, user)


@dc.event
async def on_raw_reaction_remove(payload):
	# Fixed in Layer 5 (was `on_reaction_remove` with a FIXME saying "event does not
	# get triggered"). Two separate problems:
	#
	#   1. Nextcord only fires the cached `on_reaction_remove` for messages still
	#      in its internal message cache. Check-in messages typically live 1-2
	#      minutes but the cache turnover during a busy channel can evict them
	#      before the user un-reacts, so the callback silently never ran.
	#   2. Even when it did fire, the original code checked
	#      `reaction.message.channel.id in bot.waiting_reactions` (channel vs
	#      message id typo), so the lookup always failed. That's a 2022-vintage
	#      bug that nobody caught because of (1).
	#
	# `on_raw_reaction_remove` fires for ALL reaction removes, cached or not,
	# and gives us `payload.message_id` directly. The callback only uses
	# `str(reaction)` and `user` — `payload.emoji` is a PartialEmoji whose
	# __str__ returns the same string as Reaction's __str__, so the check-in
	# callback (`str(reaction) == self.READY_EMOJI`) continues to work.
	if payload.user_id == dc.user.id:
		return
	if payload.message_id not in bot.waiting_reactions:
		return
	# Resolve Member — the callback checks `user not in self.m.players`, so we
	# need the actual Member object, not just the id.
	guild = dc.get_guild(payload.guild_id) if payload.guild_id else None
	if guild is None:
		return
	member = guild.get_member(payload.user_id)
	if member is None:
		return
	try:
		await bot.waiting_reactions[payload.message_id](payload.emoji, member, remove=True)
	except Exception as e:
		log.error(f"on_raw_reaction_remove callback error: {e}\n{traceback.format_exc()}")


@dc.event
async def on_ready():
	await dc.change_presence(activity=Activity(type=ActivityType.watching, name=cfg.STATUS))
	if not bot.bot_was_ready:  # Connected for the first time, load everything
		log.info(f"Logged in discord as '{dc.user.name}#{dc.user.discriminator}'.")
		log.info("Loading queue channels...")
		for channel_id in await bot.QueueChannel.cfg_factory.p_keys():
			channel = dc.get_channel(channel_id)
			if channel:
				bot.queue_channels[channel_id] = await bot.QueueChannel.create(channel)
				await bot.queue_channels[channel_id].update_info(channel)
				log.info(f"\tInit channel {channel.guild.name}>#{channel.name} successful.")
			else:
				log.info(f"\tCould not reach a text channel with id {channel_id}.")

		await seed_ratings_from_csv()
		await bot.load_state()
		bot.bot_was_ready = True
		bot.bot_ready = True
		log.info("Done.")
	else:  # Reconnected, fetch new channel objects
		bot.bot_ready = True
		log.info("Reconnected to discord.")


@dc.event
async def on_disconnect():
	log.info("Connection to discord is lost.")
	bot.bot_ready = False


@dc.event
async def on_resumed():
	log.info("Connection to discord is resumed.")
	if bot.bot_was_ready:
		bot.bot_ready = True


@dc.event
async def on_presence_update(before, after):
	if after.raw_status not in ['idle', 'offline']:
		return
	if after.id in bot.allow_offline:
		return

	for qc in filter(lambda i: i.guild_id == after.guild.id, bot.queue_channels.values()):
		if after.raw_status == "offline" and qc.cfg.remove_offline:
			await qc.remove_members(after, reason="offline")

		if after.raw_status == "idle" and qc.cfg.remove_afk and bot.expire.get(qc, after) is None:
			await qc.remove_members(after, reason="afk", highlight=True)


@dc.event
async def on_member_remove(member):
	for qc in filter(lambda i: i.id == member.guild.id, bot.queue_channels.values()):
		await qc.remove_members(member, reason="left guild")
