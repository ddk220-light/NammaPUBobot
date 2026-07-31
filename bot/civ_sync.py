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
	tname="civ_picks",
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
# How stale a LobbyBOT result may be and still be paired with a Pubobot ELO
# result. Unchanged from when the pairing keyed on player names.
LOBBY_MATCH_WINDOW_S = 7200


async def persist_lobby_civs(channel_id, parsed):
	"""Store the civs from a parsed AOE2LobbyBOT result into civ_picks.

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
			"SELECT 1 AS x FROM civ_picks WHERE channel_id=%s AND aoe2_match_id=%s LIMIT 1",
			[channel_id, aoe2_match_id]
		)
		if exists:
			return

	await db.insert_many('civ_picks', rows)
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
	"""Link one parsed LobbyBOT result to a bot match by profile overlap and write civ_picks."""
	dbw = db_adapter or db
	if await dbw.fetchone("SELECT 1 AS x FROM civ_picks WHERE bot_match_id=%s LIMIT 1", [bot_match_id]):
		return True

	if uid_to_pids is None or nick_to_pids is None:
		# No nick fallback here either (see bot/civ_matcher._map_players_to_profiles) —
		# the identity resolver is user_id-keyed only.
		from bot import identity
		uid_to_pids = await identity.profiles_for_users([user_id for user_id, _nick, _team in players])
		nick_to_pids = {}

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

	await dbw.insert_many("civ_picks", rows)
	log.info(
		f"Civ history: bot match {bot_match_id} -> aoe2 {parsed.get('aoe2_match_id')}, "
		f"recorded {len(rows)} civs (overlap {overlap}).")
	return True


async def find_and_record_lobby_from_history(channel, channel_id, bot_match_id, players, winner, match_at,
											 limit=300):
	"""Scan LobbyBOT messages around a bot match time and backfill civ_picks if profiles overlap."""
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


def _team_shape(parsed):
	"""Per-team player counts, sorted — (4, 4) for a 4v4, (1, 1) for a 1v1.

	The only structural fact a Pubobot ELO result and a LobbyBOT embed can both
	state without agreeing on who anybody is. This matcher used to translate the
	ELO message's Discord nicks into AoE2 names through a hand-maintained CSV and
	pair on the name overlap; identity v2 makes `identities` the sole answer to
	"who is this person" and that table is keyed by AoE2 profile_id, which a
	Pubobot ELO message never carries. So the pairing is now identity-free.
	"""
	return tuple(sorted(len(team.get('players') or []) for team in parsed.get('teams') or []))


def _pair_lobby(lobbies, elo_parsed, elo_timestamp, source):
	"""The ONE candidate lobby that can be paired with this ELO result, or None.

	Signals: time proximity (the unchanged window) and team shape. That is
	weaker evidence than the old name overlap, so genuine ambiguity is now
	possible — two 4v4s reported inside the same two hours are indistinguishable
	here. Ambiguity resolves to pairing NOTHING, deliberately: an unpaired match
	costs one match's civ detail, while a wrong pairing writes civs and results
	against the wrong game, and every layer downstream reads that as fact.
	"""
	shape = _team_shape(elo_parsed)
	candidates = [
		lobby for lobby in lobbies
		if 0 <= elo_timestamp - (lobby.get('timestamp') or 0) <= LOBBY_MATCH_WINDOW_S
		and _team_shape(lobby) == shape
	]
	if not candidates:
		return None
	if len(candidates) > 1:
		ids = ', '.join(str(c.get('aoe2_match_id')) for c in candidates)
		log.info(
			f"Civ sync: {len(candidates)} LobbyBOT results in the {source} share this ELO "
			f"result's shape {shape} inside the window ({ids}) — pairing none of them.")
		return None
	log.info(f"Civ sync: paired ELO result with LobbyBOT match "
			 f"{candidates[0].get('aoe2_match_id')} from the {source} (shape {shape}).")
	return candidates[0]


def find_matching_lobby(elo_parsed, elo_timestamp):
	"""Find the buffered LobbyBOT result that matches this Pubobot ELO message.
	Returns the matched lobby dict, or None when there is no candidate or more
	than one (see _pair_lobby)."""
	return _pair_lobby(_lobby_buffer, elo_parsed, elo_timestamp, 'buffer')


async def find_matching_lobby_from_history(channel, elo_parsed, elo_timestamp):
	"""Fallback: scan recent channel history for a matching LobbyBOT embed.

	Every candidate in the scanned window is collected before deciding, rather
	than returning the first that fits — with the weaker signals _pair_lobby now
	uses, "first match wins" would silently pick one of several equally good
	candidates, which is exactly the wrong pairing that function refuses to make.
	"""
	from core.config import cfg
	lobbybot_id = getattr(cfg, 'LOBBYBOT_USER_ID', None)
	if not lobbybot_id:
		return None

	found = []
	try:
		async for msg in channel.history(limit=50):
			if msg.author.id != lobbybot_id:
				continue
			if not msg.embeds:
				continue
			parsed = parse_lobby_embed(msg)
			if parsed is not None:
				found.append(parsed)
	except Exception as e:
		log.error(f"Civ sync: error scanning channel history: {e}")
		return None

	return _pair_lobby(found, elo_parsed, elo_timestamp, 'channel history')


def link_and_write(bot_match_id, lobby_data):
	"""Link a Pubobot match to a LobbyBOT result and append the two offline
	analysis CSVs (data/match_id_map.csv, data/match_civ_details.csv).

	The player column of match_civ_details.csv is the AoE2 in-game NAME. It
	used to be the Discord nick, translated from the in-game name through the
	hand-maintained profile-map CSV; that CSV is no longer read anywhere at
	runtime (identity v2 — `identities` is the one store, and it is keyed by
	profile_id, which the Pubobot ELO message does not carry). Team and result
	likewise come from the LobbyBOT embed alone, which states both directly per
	team; the ELO side was only ever consulted to cross-check them through that
	same nick translation, so there is nothing left for it to contribute here.

	Note for whoever picks this up next: these two files are the last runtime
	CSV writes in bot/, and on Railway they live on an ephemeral filesystem, so
	every row is lost on redeploy. The durable record of the same facts is the
	`civ_picks` table (persist_lobby_civs + bot/civ_matcher.py). Retiring this
	function in favour of that table is its own task, not this one.
	"""
	aoe2_match_id = lobby_data['aoe2_match_id']
	now_iso = datetime.now(timezone.utc).isoformat()
	match_date = datetime.fromtimestamp(
		lobby_data['timestamp'], tz=timezone.utc
	).strftime('%Y-%m-%d %H:%M')

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
			team_idx = 0 if team['is_winner'] else 1
			result = 'W' if team['is_winner'] else 'L'
			for player in team['players']:
				writer.writerow([bot_match_id, aoe2_match_id, match_date,
								 player['aoe2_name'], team_idx, player['civ'], result])

	log.info(f"Civ sync: linked match {bot_match_id} -> aoe2:{aoe2_match_id}, wrote civ details")
