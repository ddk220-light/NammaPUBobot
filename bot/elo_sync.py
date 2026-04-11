import re
import time
import zlib
from core.database import db
from core.console import log
from bot.civ_sync import find_matching_lobby, find_matching_lobby_from_history, link_and_write

# Last successful ELO sync timestamp (unix). Read by bot/web.py handle_health as
# a liveness signal — a long gap here, combined with no new Pubobot messages,
# is just quiet; but a long gap while matches are happening means the
# sync pipeline is broken.
last_elo_sync_at = 0.0


def parse_elo_message(content):
	"""Parse a Pubobot ELO result message.

	Expected format (inside markdown code block):
		4v4(1354050) results
		-------------
		0. A 1053 ⟼ 1075
		> thelivi 1590 ⟼ 1612
		> guruGreatest 1126 ⟼ 1148
		1. B 1056 ⟼ 1034
		> M1k3 1735 ⟼ 1713

	Returns dict with keys: match_id, queue_name, teams
	Each team: {index, name, avg_before, avg_after, players: [{nick, before, after}]}
	Returns None if message doesn't match expected format.
	"""
	# Extract content from markdown code block
	m = re.search(r'```markdown\n(.+?)```', content, re.DOTALL)
	if not m:
		return None

	block = m.group(1).strip()
	lines = block.split('\n')

	# First line: "QueueName(MatchID) results"
	header = re.match(r'(\w+)\((\d+)\)\s+results', lines[0])
	if not header:
		return None

	queue_name = header.group(1)
	match_id = int(header.group(2))

	teams = []
	current_team = None

	for line in lines[1:]:
		line = line.strip()
		if not line or line.startswith('-'):
			continue

		# Team line: "0. A 1053 ⟼ 1075"
		team_match = re.match(r'(\d+)\.\s+(\S+)\s+(\d+)\s+⟼\s+(\d+)', line)
		if team_match:
			current_team = {
				'index': int(team_match.group(1)),
				'name': team_match.group(2),
				'avg_before': int(team_match.group(3)),
				'avg_after': int(team_match.group(4)),
				'players': [],
			}
			teams.append(current_team)
			continue

		# Player line: "> nick 1590 ⟼ 1612"
		player_match = re.match(r'>\s+(.+?)\s+(\d+)\s+⟼\s+(\d+)', line)
		if player_match and current_team is not None:
			current_team['players'].append({
				'nick': player_match.group(1),
				'before': int(player_match.group(2)),
				'after': int(player_match.group(3)),
			})
			continue

		# 1v1 format: "1. PlayerName 1200 ⟼ 1220"
		# (when team name looks like a player nick — no separate player lines)
		# This is handled by the team_match above; the "name" field doubles as nick.

	if not teams:
		return None

	# Handle 1v1 format: if teams have no players, the "name" is actually the player nick
	for team in teams:
		if not team['players']:
			team['players'].append({
				'nick': team['name'],
				'before': team['avg_before'],
				'after': team['avg_after'],
			})

	return {
		'match_id': match_id,
		'queue_name': queue_name,
		'teams': teams,
	}


async def process_elo_sync(message):
	"""Process an ELO result message from the original Pubobot.

	1. Parse the message
	2. Check for duplicates (match_id already in qc_rating_history)
	3. For each player: look up or create in qc_players, apply rating delta
	4. Log to qc_rating_history
	"""
	import bot

	parsed = parse_elo_message(message.content)
	if parsed is None:
		return

	match_id = parsed['match_id']
	queue_name = parsed['queue_name']

	# Find the queue channel for this Discord channel
	qc = bot.queue_channels.get(message.channel.id)
	if qc is None:
		return

	# qc_players and qc_rating_history are keyed on the shared rating
	# channel; qc_matches and qc_player_matches are keyed on the pickup
	# channel (matches register_match_ranked's convention — and the
	# stats.py queries join on the pickup channel id). Keep these two
	# variables distinct — conflating them poisons the stats joins.
	channel_id = qc.rating.channel_id
	qc_channel_id = qc.id

	# Dedup: check if this match_id already exists in rating history
	existing = await db.select_one(
		('id',), 'qc_rating_history',
		where={'match_id': match_id, 'channel_id': channel_id}
	)
	if existing:
		log.debug(f"ELO sync: match {match_id} already processed, skipping.")
		return

	now = int(time.time())
	winner_index = 0  # Team at index 0 is the winner in Pubobot format

	# Write the match row itself. Until this landed (2026-04-11) the live
	# ELO sync only wrote qc_players and qc_rating_history — every
	# Pubobot-sourced match silently skipped qc_matches and
	# qc_player_matches, so any stats query that JOINed those tables
	# drifted from reality on every new match. Pubobot's ELO message
	# doesn't include map info, so maps='' and the "set score" is just
	# the single win (1-0). ranked=1, winner=0 is the Pubobot convention
	# (winning team is always at index 0).
	alpha_name = parsed['teams'][0]['name'] if len(parsed['teams']) > 0 else 'A'
	beta_name = parsed['teams'][1]['name'] if len(parsed['teams']) > 1 else 'B'
	await db.insert('qc_matches', {
		'match_id': match_id,
		'channel_id': qc_channel_id,
		'queue_id': None,
		'queue_name': queue_name,
		'at': now,
		'alpha_name': alpha_name,
		'beta_name': beta_name,
		'ranked': 1,
		'winner': winner_index,
		'alpha_score': 1,
		'beta_score': 0,
		'maps': '',
	}, on_dublicate='ignore')

	for team in parsed['teams']:
		is_winner = (team['index'] == winner_index)
		team_bit = team['index']  # 0 or 1, used for qc_player_matches.team

		for player in team['players']:
			nick = player['nick']
			rating_before = player['before']
			rating_after = player['after']
			rating_change = rating_after - rating_before

			# Resolve the Discord user_id up front. Used for the lookup
			# preference below, for the insert user_id, and for the
			# qc_player_matches row. Falls back to a deterministic
			# synthetic negative id (see _resolve_user_id) so unresolved
			# players never collide on the qc_player_matches PK.
			resolved_user_id = _resolve_user_id(message, nick)

			# Prefer user_id lookup when we have a real Discord id —
			# that's the only way to survive a nick change without
			# creating a duplicate qc_players row. Fall back to nick
			# lookup for legacy rows (imported before user_id was known)
			# and for synthetic ids (where nick IS the stable key).
			p = None
			if resolved_user_id > 0:
				p = await db.select_one(
					('user_id', 'rating', 'deviation', 'wins', 'losses', 'draws', 'streak', 'nick'),
					'qc_players',
					where={'user_id': resolved_user_id, 'channel_id': channel_id}
				)
			if p is None:
				p = await db.select_one(
					('user_id', 'rating', 'deviation', 'wins', 'losses', 'draws', 'streak', 'nick'),
					'qc_players',
					where={'nick': nick, 'channel_id': channel_id}
				)

			if p is None:
				# Auto-create player with the after rating
				user_id = resolved_user_id
				await db.insert('qc_players', {
					'channel_id': channel_id,
					'user_id': user_id,
					'nick': nick,
					'rating': rating_after,
					'deviation': 300,
					'wins': 1 if is_winner else 0,
					'losses': 0 if is_winner else 1,
					'draws': 0,
					'streak': 1 if is_winner else -1,
				}, on_dublicate='ignore')
				log.info(f"ELO sync: created new player '{nick}' (user_id={user_id}) with rating {rating_after}")

				await db.insert('qc_rating_history', {
					'channel_id': channel_id,
					'user_id': user_id,
					'at': now,
					'rating_before': rating_before,
					'rating_change': rating_change,
					'deviation_before': 300,
					'deviation_change': 0,
					'match_id': match_id,
					'reason': queue_name,
				})

				await db.insert('qc_player_matches', {
					'match_id': match_id,
					'channel_id': qc_channel_id,
					'user_id': user_id,
					'nick': nick,
					'team': team_bit,
				}, on_dublicate='ignore')
				continue

			user_id = p['user_id']

			# Update existing player. Always refresh nick so the row stays
			# findable by nick for future lookups (players rename in
			# Discord, and the nick-lookup fallback above relies on this).
			update_data = {
				'nick': nick,
				'rating': rating_after,
				'last_ranked_match_at': now,
			}
			if is_winner:
				update_data['wins'] = p['wins'] + 1
				update_data['streak'] = max(p['streak'], 0) + 1
			else:
				update_data['losses'] = p['losses'] + 1
				update_data['streak'] = min(p['streak'], 0) - 1

			await db.update(
				'qc_players', update_data,
				keys={'channel_id': channel_id, 'user_id': user_id}
			)

			await db.insert('qc_rating_history', {
				'channel_id': channel_id,
				'user_id': user_id,
				'at': now,
				'rating_before': rating_before,
				'rating_change': rating_change,
				'deviation_before': p['deviation'],
				'deviation_change': 0,
				'match_id': match_id,
				'reason': queue_name,
			})

			await db.insert('qc_player_matches', {
				'match_id': match_id,
				'channel_id': qc_channel_id,
				'user_id': user_id,
				'nick': nick,
				'team': team_bit,
			}, on_dublicate='ignore')

			log.info(f"ELO sync: {nick} {rating_before} -> {rating_after} ({'+' if rating_change >= 0 else ''}{rating_change})")

	log.info(f"ELO sync: processed match {match_id} ({queue_name})")

	# Stamp the last-success timestamp AFTER the DB writes complete — if any
	# of the inserts above raised, we don't want to report the sync pipeline
	# as "healthy" in /health.
	global last_elo_sync_at
	last_elo_sync_at = time.time()

	# Try to link with a LobbyBOT match for civ data
	try:
		lobby = find_matching_lobby(parsed, message.created_at.timestamp())
		if lobby is None:
			log.info("Civ sync: no buffered LobbyBOT match found, scanning channel history...")
			lobby = await find_matching_lobby_from_history(
				message.channel, parsed, message.created_at.timestamp()
			)
		if lobby:
			link_and_write(match_id, parsed, lobby)
		else:
			log.info(f"Civ sync: no matching LobbyBOT result found for match {match_id}")
	except Exception as e:
		log.error(f"Civ sync error for match {match_id}: {e}")


def _resolve_user_id(message, nick):
	"""Try to resolve a Discord user_id from the guild member list.

	Returns the real Discord snowflake (positive 64-bit int) when the nick
	resolves to a guild member. Falls back to a deterministic synthetic
	NEGATIVE id derived from the nick when no member matches.

	Why the synthetic fallback: unresolved players used to all collapse
	to user_id=0, stomping each other on the qc_players (channel_id,
	user_id) primary key and — once we started writing qc_player_matches
	from the live sync — the (match_id, user_id) primary key too. A
	stable per-nick id avoids that collision while still never colliding
	with a real Discord id (snowflakes are always positive). We use
	zlib.crc32 (stable across interpreter restarts) rather than Python's
	hash() (randomised per run by default). A nightly reconciliation job
	can later merge a synthetic row into its real-id row once the player
	rejoins and becomes resolvable.
	"""
	if hasattr(message, 'guild') and message.guild:
		for member in message.guild.members:
			if member.display_name == nick or member.name == nick:
				return member.id
	# +1 guarantees we never return 0 (synonym of "unresolved" in old code)
	return -(zlib.crc32(nick.encode('utf-8')) + 1)
