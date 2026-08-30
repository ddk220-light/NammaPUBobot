import csv
import os
import time
import traceback
from nextcord import ChannelType, Activity, ActivityType

from nammaoe2bot.runtime.client import dc
from nammaoe2bot.runtime.database import db
from nammaoe2bot.runtime.console import log
from nammaoe2bot.runtime.paths import data as data_path
from nammaoe2bot.runtime.config import cfg
from nammaoe2bot.features.civs import reconcile
from nammaoe2bot.discord.commands import queues as queue_commands
from nammaoe2bot import derived
from nammaoe2bot.features import lobby
from nammaoe2bot.features import betting
from nammaoe2bot.features import quiz
from nammaoe2bot import ingest
from nammaoe2bot.exceptions import Exceptions as Exc
from nammaoe2bot.pickup.expire import expire
from nammaoe2bot.state import load_state, save_state_if_changed
from nammaoe2bot.pickup.channel import QueueChannel
from nammaoe2bot.pickup import stats
from nammaoe2bot.pickup.noadds import noadds
from nammaoe2bot.features.elo_sync import process_elo_sync
from nammaoe2bot.features.civs.sync import parse_lobby_embed, buffer_lobby_result, persist_lobby_civs
from nammaoe2bot.features.message_log import log_bot_message
from nammaoe2bot.community import enroll_channel, replay_pipeline_available
from nammaoe2bot.discord.shortcuts import queue_shortcut_action


async def seed_ratings_from_csv():
	"""One-time bulk seed of player ratings from data/qc_players.csv into all queue channels.

	qc_players.csv is an on-disk *filename*, not the `player_ratings` table it
	seeds — it predates the stage-1 table rename and nothing renames files on
	disk, so keep this literal as-is even though the table below is now
	called player_ratings.
	"""
	csv_path = data_path('qc_players.csv')
	if not os.path.exists(csv_path):
		log.info("No data/qc_players.csv found, skipping rating seed.")
		return

	for qc in dc.app.channels.values():
		dest_id = qc.rating.channel_id
		# Check if this channel already has rated players
		existing = await db.select(['user_id'], 'player_ratings', where={'channel_id': dest_id})
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

		await db.insert_many('player_ratings', to_insert, on_duplicate='replace')
		log.info(f"\tSeeded {len(to_insert)} player ratings from CSV into channel {dest_id}.")


@dc.event
async def on_init():
	await stats.check_match_id_counter()


_last_state_save = 0
_STATE_SAVE_INTERVAL = 30  # seconds; crash-survivability backstop for in-flight matches

# Stamped every tick — read by nammaoe2bot/web/server.py handle_health as the "last_tick_age_seconds"
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
	for match in list(dc.app.active_matches):
		try:
			await match.think(frame_time)
			match._think_errors = 0
		except Exception as e:
			match._think_errors = getattr(match, "_think_errors", 0) + 1
			log.error("\n".join([
				"Error at Match.think().",
				f"match_id: {match.id}, consecutive errors: {match._think_errors}.",
				f"{str(e)}. Traceback:\n{traceback.format_exc()}=========="
			]))
			# Don't silently drop an in-flight match on a single transient error
			# — that leaves the captain unable to /report and frees players to
			# re-queue (the 1390237 incident). Only remove after repeated
			# failures, so one persistently-broken match still can't starve the rest.
			if match._think_errors >= 5 and match in dc.app.active_matches:
				log.error(f"Removing match {match.id} after {match._think_errors} consecutive think errors.")
				dc.app.active_matches.remove(match)
			continue
	await expire.think(frame_time)
	await noadds.think(frame_time)
	await stats.jobs.think(frame_time)
	await reconcile.reconcile.think(frame_time)
	await lobby.jobs.think(frame_time)   # opt-in lobby feature; think() is self-isolating (never raises)
	await quiz.jobs.think(frame_time)    # opt-in quiz feature; think() is self-isolating (never raises)
	await betting.jobs.think(frame_time)   # freeze sweep; think() is self-isolating (never raises)
	# Replay download/parse and its replay-derived repair/retention jobs move as
	# one unit.  When replay analysis is paused, none of them is even scheduled:
	# historical rows stay frozen and the bot performs no hidden replay scans.
	if replay_pipeline_available():
		await ingest.jobs.think(frame_time)
		await derived.jobs.think(frame_time)
		await derived.sweeper_jobs.think(frame_time)

	# In normal replay mode this rebuilds player_rollups, metric_boards and
	# civ_stats.  In paused mode refresh.py deliberately runs only the tiny
	# civ_stats recovery path, preserving civ W/L while freezing replay outputs.
	await derived.refresh_jobs.think(frame_time)

	# Sweep leaked check-in reaction callbacks. See TTLReactionDict
	# docstring in nammaoe2bot/app.py — entries older than 30 minutes are
	# guaranteed-dead leaks from check-in exit paths that raised before
	# unsubscribing. Cheap (O(n), n ≈ 0-3) so no need to gate on interval.
	dc.app.waiting_reactions.sweep_expired(frame_time)

	# Periodic state snapshot — if the process crashes before a clean
	# shutdown, SIGTERM (or the crash supervisor in nammaoe2bot/__main__.py) can only
	# save state best-effort. This keeps a rolling ≤30s-old backup on
	# disk for unexpected exits.
	if frame_time - _last_state_save >= _STATE_SAVE_INTERVAL:
		try:
			await save_state_if_changed(dc.app)
			_last_state_save = frame_time
		except Exception as e:
			log.error(f"Periodic save_state failed: {e}\n{traceback.format_exc()}")


# Answering a DM. Was cfg.HELP, an env-driven string that defaulted to
# upstream's one-liner naming the wrong bot — so anyone who DMed us was told
# "nammaoe2bot.__main__ is a discord bot for pickup games organisation." The bot only works
# inside its pickup channel, so the useful answer is to say exactly that.
_DM_REPLY = (
	"I'm **NammaAoe2Bot** — I run the AoE2 pickup queue.\n\n"
	"I only work inside the pickup channel, not in DMs. Head there and type "
	"`/add` to join the queue, or `/profile_link` to connect your AoE2 profile "
	"so your games get tracked."
)


@dc.event
async def on_message(message):
	if message.channel.type == ChannelType.private and message.author.id != dc.user.id:
		await message.channel.send(_DM_REPLY)

	if message.channel.type != ChannelType.text:
		return

	# `++` / `--` shorthand: add/remove the author to/from the channel queues.
	# Smart punctuation may turn two hyphens into one Unicode dash, so parse the
	# small explicit alias set rather than comparing only the ASCII spelling.
	# Restored after Layer 5 removed the text-command system — these two are the
	# only shorthands kept. They reuse the existing add/remove command handlers
	# (add with no args -> default/active queues; remove with no args -> all).
	if (shortcut := queue_shortcut_action(message.content)) is not None:
		if (qc := dc.app.channels.get(message.channel.id)) is not None and dc.app.ready:
			from nammaoe2bot.discord.message_context import MessageContext
			ctx = MessageContext(qc, message)
			try:
				if shortcut == 'add':
					await queue_commands.add(ctx)
				else:
					await queue_commands.remove(ctx)
			except Exc.BotException as e:
				await ctx.error(str(e), title=e.__class__.__name__)
			except Exception as e:
				log.error(f"Error processing queue shortcut '{shortcut}': {e}\n{traceback.format_exc()}")
		return

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
				await persist_lobby_civs(message.channel.id, parsed)
		except Exception as e:
			log.error(f"Civ sync buffer error: {e}\n{traceback.format_exc()}")

@dc.event
async def on_interaction(interaction):
	# CRITICAL: nammaoe2bot.runtime.client's @dc.event system replaces nextcord's built-in
	# Client.on_interaction (which is just `process_application_commands`), so we MUST
	# call it here or EVERY slash command + autocomplete silently stops working.
	# Then route quiz component clicks (type 3, custom_id 'quiz:*'). The two handle
	# disjoint interaction types — process_* no-ops on components, the quiz router
	# no-ops on application commands — so calling both is safe. on_quiz_interaction is
	# self-isolating (never raises). Lazy import keeps quiz's nextcord modules out of
	# events' import path until first use.
	await dc.process_application_commands(interaction)
	from nammaoe2bot.features.quiz import interactions as quiz_interactions
	await quiz_interactions.on_quiz_interaction(interaction)
	if bool(getattr(cfg, "REPLAY_DASHBOARD_ENABLED", False)):
		from nammaoe2bot.derived.classifications import interactions as cls_interactions
		await cls_interactions.on_insights_interaction(interaction)
	else:
		# Old insight buttons remain in Discord history. Acknowledge those clicks
		# without importing/querying the frozen classification stack; other
		# component ids continue to the active quiz/betting routers.
		custom_id = (getattr(interaction, "data", None) or {}).get("custom_id", "")
		if (custom_id.startswith("insights:full:")
				and not interaction.response.is_done()):
			await interaction.response.send_message(
				"Replay insights are currently paused.", ephemeral=True)
	from nammaoe2bot.features.betting import interactions as bet_interactions
	await bet_interactions.on_bet_interaction(interaction)


async def _route_raw_reaction(payload, *, remove):
	"""Route an add/remove without requiring Nextcord's message cache.

	Reaction callbacks only use ``str(reaction)`` plus the reacting Member, so a
	raw payload's PartialEmoji is behaviorally equivalent and lets us keep a much
	smaller message cache. Guild reaction-add payloads normally include ``member``;
	remove payloads do not, hence the cache lookup fallback shared by both paths.
	"""
	if payload.user_id == dc.user.id:
		return
	if payload.message_id not in dc.app.waiting_reactions:
		return
	member = getattr(payload, "member", None)
	if member is None:
		guild = dc.get_guild(payload.guild_id) if payload.guild_id else None
		member = guild.get_member(payload.user_id) if guild else None
	if member is None:
		return
	try:
		await dc.app.waiting_reactions[payload.message_id](payload.emoji, member, remove=remove)
	except Exception as e:
		event = "remove" if remove else "add"
		log.error(f"on_raw_reaction_{event} callback error: {e}\n{traceback.format_exc()}")


@dc.event
async def on_raw_reaction_add(payload):
	await _route_raw_reaction(payload, remove=False)


@dc.event
async def on_raw_reaction_remove(payload):
	await _route_raw_reaction(payload, remove=True)


@dc.event
async def on_ready():
	await dc.change_presence(activity=Activity(type=ActivityType.watching, name=cfg.STATUS))
	if not dc.app.was_ready:  # Connected for the first time, load everything
		log.info(f"Logged in discord as '{dc.user.name}#{dc.user.discriminator}'.")
		log.info("Loading queue channels...")
		for channel_id in await QueueChannel.cfg_factory.p_keys():
			channel = dc.get_channel(channel_id)
			if channel:
				dc.app.channels[channel_id] = await QueueChannel.create(channel, dc.app)
				await dc.app.channels[channel_id].update_info(channel)
				log.info(f"\tInit channel {channel.guild.name}>#{channel.name} successful.")
			else:
				log.info(f"\tCould not reach a text channel with id {channel_id}.")

		# Enroll every successfully-initialised queue channel into a community
		# (one per Discord guild). Guild objects only exist once Discord is
		# connected, which is why this is a runtime hook here rather than a
		# migration — see nammaoe2bot/community.py. Never let a failure here stop
		# the bot from booting.
		try:
			enrolled_communities = set()
			for channel_id in dc.app.channels:
				channel = dc.get_channel(channel_id)
				if not channel:
					continue
				community_id = await enroll_channel(channel)
				if community_id is not None:
					enrolled_communities.add(community_id)
			log.info(
				f"\tEnrolled {len(dc.app.channels)} channels into "
				f"{len(enrolled_communities)} communities."
			)
		except Exception:
			log.error(f"Failed to enroll queue channels into communities:\n{traceback.format_exc()}")

		await seed_ratings_from_csv()

		# One idempotent pass seeds starting gold for every known player in
		# every community; after the first boot this inserts nothing. Newcomers
		# are seeded lazily on their first gold touch instead.
		try:
			from nammaoe2bot.features.betting import gold as gold_bank
			seeded = await gold_bank.bulk_seed(int(time.time()))
			if seeded:
				log.info(f"\tSeeded {seeded} player(s) with starting gold.")
		except Exception:
			log.error(f"Gold bulk seed failed:\n{traceback.format_exc()}")

		await load_state()
		dc.app.was_ready = True
		dc.app.ready = True
		log.info("Done.")
	else:  # Reconnected, fetch new channel objects
		dc.app.ready = True
		log.info("Reconnected to discord.")


@dc.event
async def on_disconnect():
	log.info("Connection to discord is lost.")
	dc.app.ready = False


@dc.event
async def on_resumed():
	log.info("Connection to discord is resumed.")
	if dc.app.was_ready:
		dc.app.ready = True


@dc.event
async def on_presence_update(before, after):
	if after.raw_status not in ['idle', 'offline']:
		return
	for qc in filter(lambda i: i.guild_id == after.guild.id, dc.app.channels.values()):
		if after.raw_status == "offline" and qc.cfg.remove_offline:
			await qc.remove_members(after, reason="offline")

		if after.raw_status == "idle" and qc.cfg.remove_afk and expire.get(qc, after) is None:
			await qc.remove_members(after, reason="afk", highlight=True)


@dc.event
async def on_member_remove(member):
	for qc in filter(lambda i: i.id == member.guild.id, dc.app.channels.values()):
		await qc.remove_members(member, reason="left guild")
