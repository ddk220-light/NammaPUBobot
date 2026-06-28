import csv
import os
import re
import time
from datetime import datetime, timezone
from core.console import log
from core.database import db

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')

# Durable record of civs played per AOE2LobbyBOT-reported match. Written when
# LobbyBOT posts a completed-match embed (see persist_lobby_civs), and read by
# bot/civ_stats.get_today_civs to drive /test_random_civs' "exclude civs played
# today" filter — replacing the old fragile channel-history scrape.
db.ensure_table(dict(
	tname="qc_match_civs",
	columns=[
		dict(cname="id", ctype=db.types.int, autoincrement=True),
		dict(cname="channel_id", ctype=db.types.int),
		dict(cname="aoe2_match_id", ctype=db.types.int, notnull=False),
		dict(cname="aoe2_name", ctype=db.types.str),
		dict(cname="civ", ctype=db.types.str),
		dict(cname="at", ctype=db.types.int),
		# Linkage to the bot match + player, filled by bot/civ_matcher.py when a
		# completed match is matched to its aoe2companion game on /report.
		dict(cname="bot_match_id", ctype=db.types.int, notnull=False),
		dict(cname="user_id", ctype=db.types.int, notnull=False),
		dict(cname="nick", ctype=db.types.str),
		dict(cname="team", ctype=db.types.int, notnull=False),
		dict(cname="result", ctype=db.types.str),
	],
	primary_keys=["id"]
))

# In-memory buffer of parsed LobbyBOT match results (max 20)
_lobby_buffer = []
MAX_BUFFER = 20
FULL_TEAM_OVERLAP = 8


async def persist_lobby_civs(channel_id, parsed):
	"""Store the civs from a parsed AOE2LobbyBOT result into qc_match_civs.

	Called from on_message when LobbyBOT posts a completed match, using the same
	proven parse_lobby_embed output. Idempotent: a match already recorded (by
	aoe2_match_id in this channel) is skipped, so redeliveries don't duplicate.
	"""
	if not parsed:
		return
	at = int(parsed.get('timestamp') or time.time())
	aoe2_match_id = parsed.get('aoe2_match_id')

	rows = []
	for team in parsed.get('teams', []):
		for p in team.get('players', []):
			civ = (p.get('civ') or '').strip()
			if not civ or civ.lower() == 'unknown':
				continue
			rows.append(dict(
				channel_id=channel_id,
				aoe2_match_id=aoe2_match_id,
				aoe2_name=p.get('aoe2_name', ''),
				civ=civ,
				at=at,
			))
	if not rows:
		return

	if aoe2_match_id is not None:
		exists = await db.fetchone(
			"SELECT 1 AS x FROM qc_match_civs WHERE channel_id=%s AND aoe2_match_id=%s LIMIT 1",
			[channel_id, aoe2_match_id]
		)
		if exists:
			return

	await db.insert_many('qc_match_civs', rows)
	log.info(f"Civ record: stored {len(rows)} civs for aoe2 match {aoe2_match_id} in channel {channel_id}.")


def _lobby_players_by_profile(parsed):
	players = {}
	for team in parsed.get('teams', []):
		for p in team.get('players', []):
			pid = p.get('profile_id')
			if pid:
				players[int(pid)] = {
					"aoe2_name": p.get("aoe2_name", ""),
					"civ": p.get("civ", ""),
					"is_winner": team.get("is_winner"),
				}
	return players


def _bot_player_profiles(players, uid_to_pids, nick_to_pids):
	out = {}
	active_pids = set()
	for user_id, nick, team in players:
		pids = uid_to_pids.get(user_id) or nick_to_pids.get(nick, [])
		pids = [int(p) for p in pids if str(p).isdigit()]
		if not pids:
			continue
		out[user_id] = (nick, team, pids)
		active_pids.update(pids)
	return out, active_pids


async def record_lobby_match(channel_id, bot_match_id, players, winner, match_at, parsed,
							 db_adapter=None, uid_to_pids=None, nick_to_pids=None):
	"""Link one parsed LobbyBOT result to a bot match by profile overlap and write qc_match_civs."""
	dbw = db_adapter or db
	if await dbw.fetchone("SELECT 1 AS x FROM qc_match_civs WHERE bot_match_id=%s LIMIT 1", [bot_match_id]):
		return True

	if uid_to_pids is None or nick_to_pids is None:
		from bot.civ_matcher import _load_profile_map, _load_profile_uid_map
		uid_to_pids = _load_profile_uid_map()
		nick_to_pids = _load_profile_map()

	player_info, active_pids = _bot_player_profiles(players, uid_to_pids or {}, nick_to_pids or {})
	if len(player_info) < 2:
		return False

	lobby_players = _lobby_players_by_profile(parsed)
	overlap = len(active_pids & set(lobby_players))
	threshold = min(FULL_TEAM_OVERLAP, len(player_info))
	if overlap < threshold:
		return False

	rows = []
	for user_id, (nick, team, pids) in player_info.items():
		match = next((lobby_players[pid] for pid in pids if pid in lobby_players), None)
		if not match:
			continue
		civ = (match.get("civ") or "").strip()
		if not civ or civ.lower() == "unknown":
			continue
		result = ("W" if team == winner else "L") if (winner is not None and team is not None) else (
			"W" if match.get("is_winner") else "L" if match.get("is_winner") is False else None
		)
		rows.append(dict(
			channel_id=channel_id,
			aoe2_match_id=parsed.get("aoe2_match_id"),
			aoe2_name=match.get("aoe2_name", ""),
			civ=civ,
			at=match_at,
			bot_match_id=bot_match_id,
			user_id=user_id,
			nick=nick,
			team=team,
			result=result,
		))
	if not rows:
		return False

	await dbw.insert_many("qc_match_civs", rows)
	log.info(
		f"Civ history: bot match {bot_match_id} -> aoe2 {parsed.get('aoe2_match_id')}, "
		f"recorded {len(rows)} civs (overlap {overlap}).")
	return True


async def find_and_record_lobby_from_history(channel, channel_id, bot_match_id, players, winner, match_at,
											 limit=300):
	"""Scan LobbyBOT messages around a bot match time and backfill qc_match_civs if profiles overlap."""
	from core.config import cfg
	lobbybot_id = getattr(cfg, 'LOBBYBOT_USER_ID', None)
	if not lobbybot_id or channel is None:
		return False

	after = datetime.fromtimestamp(max(0, int(match_at) - 4 * 3600), tz=timezone.utc)
	before = datetime.fromtimestamp(int(match_at) + 90 * 60, tz=timezone.utc)
	try:
		async for msg in channel.history(limit=limit, after=after, before=before, oldest_first=False):
			if msg.author.id != lobbybot_id or not getattr(msg.author, "bot", False) or not msg.embeds:
				continue
			parsed = parse_lobby_embed(msg)
			if not parsed:
				continue
			if await record_lobby_match(channel_id, bot_match_id, players, winner, match_at, parsed):
				return True
	except Exception as e:
		log.error(f"Civ history scan failed for match {bot_match_id}: {e}")
	return False


def parse_lobby_embed(message):
	"""Parse an AOE2LobbyBOT match completion embed.

	Extracts: aoe2_match_id, map_name, duration, teams with players
	(aoe2_name, profile_id, civ), winner.

	Returns dict or None.
	"""
	if not message.embeds:
		return None

	embed = message.embeds[0]
	desc = embed.description or ''

	# Extract map and duration from description
	map_name = None
	duration = None
	map_match = re.search(r'Map:\s*(.+)', desc)
	if map_match:
		map_name = map_match.group(1).strip()
	dur_match = re.search(r'Duration:\s*(\d+)\s*min', desc)
	if dur_match:
		duration = int(dur_match.group(1))

	# Try fields if not in description
	if not map_name:
		for field in (embed.fields or []):
			m = re.search(r'Map:\s*(.+)', field.value or '')
			if m:
				map_name = m.group(1).strip()
			d = re.search(r'Duration:\s*(\d+)\s*min', field.value or '')
			if d:
				duration = int(d.group(1))

	if not map_name:
		return None

	# Extract aoe2_match_id from replay links (gameId=NNNN)
	aoe2_match_id = None
	full_text = desc + ' '.join(f.value or '' for f in (embed.fields or []))
	game_id_match = re.search(r'gameId=(\d+)', full_text)
	if game_id_match:
		aoe2_match_id = int(game_id_match.group(1))

	teams = _parse_teams_from_embed(embed)

	if not teams or not aoe2_match_id:
		return None

	return {
		'aoe2_match_id': aoe2_match_id,
		'map': map_name,
		'duration': duration,
		'teams': teams,
		'timestamp': message.created_at.timestamp(),
		'message_id': message.id,
	}


def _parse_teams_from_embed(embed):
	"""Parse team/player/civ data from the embed.

	Returns list of team dicts:
	[{'is_winner': bool, 'players': [{'aoe2_name': str, 'profile_id': int, 'civ': str}]}]
	"""
	desc = embed.description or ''
	fields = embed.fields or []

	all_text = desc
	for f in fields:
		all_text += '\n' + (f.name or '') + '\n' + (f.value or '')

	lines = all_text.split('\n')

	teams = []
	current_team = None
	collecting_civs = False
	civ_list = []

	for line in lines:
		line_stripped = line.strip()

		# Detect team headers with trophy (winner) or black square (loser)
		if re.search(r'Team\s+\d+\s*.*(:trophy:|🏆)', line_stripped):
			if current_team is not None:
				current_team['civ_list'] = civ_list
				teams.append(current_team)
			current_team = {'is_winner': True, 'player_links': [], 'civ_list': []}
			civ_list = []
			collecting_civs = False
			continue

		if re.search(r'Team\s+\d+\s*.*(:black_large_square:|⬛)', line_stripped):
			if current_team is not None:
				current_team['civ_list'] = civ_list
				teams.append(current_team)
			current_team = {'is_winner': False, 'player_links': [], 'civ_list': []}
			civ_list = []
			collecting_civs = False
			continue

		if current_team is None:
			continue

		# Detect player links: [PlayerName](https://www.aoe2insights.com/user/relic/PROFILE_ID/)
		link_match = re.search(
			r'\[([^\]]+)\]\(https?://www\.aoe2insights\.com/user/relic/(\d+)/?\)',
			line_stripped
		)
		if link_match:
			current_team['player_links'].append({
				'aoe2_name': link_match.group(1),
				'profile_id': int(link_match.group(2)),
			})
			collecting_civs = False
			continue

		# Detect "Civ" header
		if line_stripped == 'Civ':
			collecting_civs = True
			continue

		# Collect civ names (skip download links and Rec header)
		if collecting_civs and line_stripped:
			if line_stripped.startswith('[') or line_stripped.startswith('⬇') or line_stripped == 'Rec':
				collecting_civs = False
				continue
			civ_list.append(line_stripped)
			continue

		if line_stripped == 'Rec':
			collecting_civs = False

	# Don't forget the last team
	if current_team is not None:
		current_team['civ_list'] = civ_list
		teams.append(current_team)

	# Build final team structure
	result = []
	for team in teams:
		players = []
		for i, pl in enumerate(team['player_links']):
			civ = team['civ_list'][i] if i < len(team['civ_list']) else 'Unknown'
			players.append({
				'aoe2_name': pl['aoe2_name'],
				'profile_id': pl['profile_id'],
				'civ': civ,
			})
		result.append({
			'is_winner': team['is_winner'],
			'players': players,
		})

	return result if result else None


def buffer_lobby_result(parsed):
	"""Add a parsed LobbyBOT result to the in-memory buffer."""
	_lobby_buffer.append(parsed)
	while len(_lobby_buffer) > MAX_BUFFER:
		_lobby_buffer.pop(0)
	log.info(f"Civ sync: buffered LobbyBOT match aoe2_id={parsed['aoe2_match_id']} ({len(_lobby_buffer)} in buffer)")


def load_profile_map():
	"""Load player_profile_map.csv. Returns dict of nick -> {aoe2_name, profile_id, user_id}."""
	path = os.path.join(DATA_DIR, 'player_profile_map.csv')
	mapping = {}
	if not os.path.exists(path):
		return mapping
	with open(path, 'r') as f:
		for row in csv.DictReader(f):
			nick = row['nick']
			aoe2_name = row.get('aoe2_name', '')
			profile_id = row.get('profile_id', '')
			if aoe2_name and profile_id:
				for name, pid in zip(
					aoe2_name.split(' / '),
					profile_id.split(' / ')
				):
					mapping[nick] = {
						'aoe2_name': name.strip(),
						'profile_id': pid.strip(),
						'user_id': row.get('user_id', ''),
					}
	return mapping


def find_matching_lobby(elo_parsed, elo_timestamp):
	"""Find a buffered LobbyBOT result that matches the Pubobot ELO message.

	Matching: time proximity (within 2 hours) + full 8-player overlap.
	Returns the matched lobby dict or None.
	"""
	profile_map = load_profile_map()

	elo_nicks = set()
	for team in elo_parsed['teams']:
		for player in team['players']:
			elo_nicks.add(player['nick'])

	elo_aoe2_names = set()
	for nick in elo_nicks:
		if nick in profile_map:
			elo_aoe2_names.add(profile_map[nick]['aoe2_name'].lower())

	best_match = None
	best_overlap = 0

	for lobby in _lobby_buffer:
		time_diff = elo_timestamp - lobby['timestamp']
		if time_diff < 0 or time_diff > 7200:
			continue

		lobby_names = set()
		for team in lobby['teams']:
			for player in team['players']:
				lobby_names.add(player['aoe2_name'].lower())

		overlap = len(elo_aoe2_names & lobby_names)
		if overlap >= FULL_TEAM_OVERLAP and overlap > best_overlap:
			best_overlap = overlap
			best_match = lobby

	return best_match


async def find_matching_lobby_from_history(channel, elo_parsed, elo_timestamp):
	"""Fallback: scan recent channel history for a matching LobbyBOT embed."""
	from core.config import cfg
	lobbybot_id = getattr(cfg, 'LOBBYBOT_USER_ID', None)
	if not lobbybot_id:
		return None

	profile_map = load_profile_map()

	elo_nicks = set()
	for team in elo_parsed['teams']:
		for player in team['players']:
			elo_nicks.add(player['nick'])

	elo_aoe2_names = set()
	for nick in elo_nicks:
		if nick in profile_map:
			elo_aoe2_names.add(profile_map[nick]['aoe2_name'].lower())

	try:
		async for msg in channel.history(limit=50):
			if msg.author.id != lobbybot_id:
				continue
			if not msg.embeds:
				continue
			parsed = parse_lobby_embed(msg)
			if parsed is None:
				continue

			lobby_names = set()
			for team in parsed['teams']:
				for player in team['players']:
					lobby_names.add(player['aoe2_name'].lower())

			time_diff = elo_timestamp - parsed['timestamp']
			overlap = len(elo_aoe2_names & lobby_names)

			if overlap >= FULL_TEAM_OVERLAP and 0 <= time_diff <= 7200:
				log.info(f"Civ sync: found match in channel history (overlap={overlap})")
				return parsed
	except Exception as e:
		log.error(f"Civ sync: error scanning channel history: {e}")

	return None


def link_and_write(bot_match_id, elo_parsed, lobby_data):
	"""Link a Pubobot match to a LobbyBOT result and write to CSV files."""
	aoe2_match_id = lobby_data['aoe2_match_id']
	now_iso = datetime.now(timezone.utc).isoformat()
	match_date = datetime.fromtimestamp(
		lobby_data['timestamp'], tz=timezone.utc
	).strftime('%Y-%m-%d %H:%M')

	profile_map = load_profile_map()
	# Reverse map: aoe2_name.lower() -> nick
	aoe2_to_nick = {}
	for nick, info in profile_map.items():
		aoe2_to_nick[info['aoe2_name'].lower()] = nick

	# Build ELO nick -> team/result info
	elo_players = {}
	for team in elo_parsed['teams']:
		for player in team['players']:
			elo_players[player['nick']] = {
				'team_index': team['index'],
				'is_winner': team['index'] == 0,
			}

	# Append to match_id_map.csv
	map_path = os.path.join(DATA_DIR, 'match_id_map.csv')
	with open(map_path, 'a', newline='') as f:
		writer = csv.writer(f)
		writer.writerow([bot_match_id, aoe2_match_id, now_iso])

	# Append to match_civ_details.csv
	details_path = os.path.join(DATA_DIR, 'match_civ_details.csv')
	with open(details_path, 'a', newline='') as f:
		writer = csv.writer(f)
		for team in lobby_data['teams']:
			for player in team['players']:
				aoe2_name = player['aoe2_name']
				civ = player['civ']

				# Resolve nick from aoe2_name
				nick = aoe2_to_nick.get(aoe2_name.lower(), aoe2_name)

				# Determine team and result from ELO data if possible
				if nick in elo_players:
					team_idx = elo_players[nick]['team_index']
					result = 'W' if elo_players[nick]['is_winner'] else 'L'
				else:
					team_idx = 0 if team['is_winner'] else 1
					result = 'W' if team['is_winner'] else 'L'

				writer.writerow([bot_match_id, aoe2_match_id, match_date, nick, team_idx, civ, result])

	# Auto-add new profile mappings
	_auto_add_profile_mappings(elo_parsed, lobby_data, profile_map)

	log.info(f"Civ sync: linked match {bot_match_id} -> aoe2:{aoe2_match_id}, wrote civ details")


def _auto_add_profile_mappings(elo_parsed, lobby_data, profile_map):
	"""Auto-add new entries to player_profile_map.csv.

	For each team, if there's exactly one unmapped player on both the ELO side
	and the LobbyBOT side, we can confidently map them.
	"""
	aoe2_to_nick = {}
	for nick, info in profile_map.items():
		aoe2_to_nick[info['aoe2_name'].lower()] = nick

	new_mappings = []

	for elo_team in elo_parsed['teams']:
		elo_is_winner = (elo_team['index'] == 0)

		# Find matching lobby team
		lobby_team = None
		for lt in lobby_data['teams']:
			if lt['is_winner'] == elo_is_winner:
				lobby_team = lt
				break
		if lobby_team is None:
			continue

		# Unmapped ELO players (nick not in profile_map)
		unmapped_elo = [p['nick'] for p in elo_team['players'] if p['nick'] not in profile_map]

		# Unmapped lobby players (aoe2_name not in reverse map)
		unmapped_lobby = [
			p for p in lobby_team['players']
			if p['aoe2_name'].lower() not in aoe2_to_nick
		]

		# Confident mapping: exactly one unmapped on each side
		if len(unmapped_elo) == 1 and len(unmapped_lobby) == 1:
			nick = unmapped_elo[0]
			lp = unmapped_lobby[0]
			new_mappings.append({
				'nick': nick,
				'aoe2_name': lp['aoe2_name'],
				'profile_id': str(lp['profile_id']),
			})
			log.info(f"Civ sync: auto-mapped '{nick}' -> '{lp['aoe2_name']}' (profile {lp['profile_id']})")

	if not new_mappings:
		return

	map_path = os.path.join(DATA_DIR, 'player_profile_map.csv')
	with open(map_path, 'a', newline='') as f:
		writer = csv.writer(f)
		for m in new_mappings:
			# user_id,nick,aoe2_name,profile_id,country
			writer.writerow(['', m['nick'], m['aoe2_name'], m['profile_id'], ''])
