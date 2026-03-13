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


@dc.event
async def on_think(frame_time):
	for match in bot.active_matches:
		try:
			await match.think(frame_time)
		except Exception as e:
			log.error("\n".join([
				f"Error at Match.think().",
				f"match_id: {match.id}).",
				f"{str(e)}. Traceback:\n{traceback.format_exc()}=========="
			]))
			bot.active_matches.remove(match)
			break
	await bot.expire.think(frame_time)
	await bot.noadds.think(frame_time)
	await bot.stats.jobs.think(frame_time)
	await bot.expire_auto_ready(frame_time)


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
			await process_elo_sync(message)
		except Exception as e:
			log.error(f"ELO sync error: {e}\n{traceback.format_exc()}")


@dc.event
async def on_reaction_add(reaction, user):
	if user.id != dc.user.id and reaction.message.id in bot.waiting_reactions.keys():
		await bot.waiting_reactions[reaction.message.id](reaction, user)


@dc.event
async def on_reaction_remove(reaction, user):  # FIXME: this event does not get triggered for some reason
	if user.id != dc.user.id and reaction.message.channel.id in bot.waiting_reactions.keys():
		await bot.waiting_reactions[reaction.message.id](reaction, user, remove=True)


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
