import re
import time
from core.database import db
from core.console import log


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

	channel_id = qc.rating.channel_id

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

	for team in parsed['teams']:
		is_winner = (team['index'] == winner_index)

		for player in team['players']:
			nick = player['nick']
			rating_before = player['before']
			rating_after = player['after']
			rating_change = rating_after - rating_before

			# Look up player by nick
			p = await db.select_one(
				('user_id', 'rating', 'deviation', 'wins', 'losses', 'draws', 'streak'),
				'qc_players',
				where={'nick': nick, 'channel_id': channel_id}
			)

			if p is None:
				# Auto-create player with the after rating
				user_id = _resolve_user_id(message, nick)
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
				continue

			user_id = p['user_id']

			# Update existing player
			update_data = {
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

			log.info(f"ELO sync: {nick} {rating_before} -> {rating_after} ({'+' if rating_change >= 0 else ''}{rating_change})")

	log.info(f"ELO sync: processed match {match_id} ({queue_name})")


def _resolve_user_id(message, nick):
	"""Try to resolve a Discord user_id from the guild member list.

	Falls back to 0 if not found.
	"""
	if hasattr(message, 'guild') and message.guild:
		for member in message.guild.members:
			if member.display_name == nick or member.name == nick:
				return member.id
	return 0
