"""NammaPUBobot Web Dashboard — OAuth2, civ stats, channel/queue configuration."""

import csv
import json
import os
import secrets
import time
from urllib.parse import urlencode

import aiohttp as aiohttp_client
from aiohttp import web

from core.config import cfg
from core.cfg_factory import (
	RoleVar, TextChanVar, MemberVar, VariableTable,
	BoolVar, IntVar, SliderVar, OptionVar, DurationVar, TextVar
)
from core.client import dc
import bot

# --- Paths ---
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
HTML_PATH = os.path.join(os.path.dirname(__file__), 'web_page.html')
MIN_GAMES = 50

# --- Session store ---
_sessions = {}  # {session_id: {user_id, username, avatar, expires}}
_oauth_states = {}  # {state: expiry_timestamp}
SESSION_LIFETIME = 86400  # 24 hours
COOKIE_NAME = "pubobot_session"

# --- Discord API ---
DISCORD_API = "https://discord.com/api/v10"
DISCORD_OAUTH_AUTHORIZE = "https://discord.com/api/oauth2/authorize"
DISCORD_OAUTH_TOKEN = "https://discord.com/api/oauth2/token"

# --- Variable filtering ---
SKIP_TYPES = (RoleVar, TextChanVar, MemberVar)

# --- HTML cache ---
_html_cache = None


def _load_html():
	global _html_cache
	try:
		with open(HTML_PATH, 'r') as f:
			_html_cache = f.read()
	except FileNotFoundError:
		_html_cache = "<h1>web_page.html not found</h1>"


def _oauth_enabled():
	return bool(getattr(cfg, 'DC_CLIENT_SECRET', ''))


def _get_root_url(request):
	"""Get public root URL from config or request headers."""
	if hasattr(cfg, 'WS_ROOT_URL') and cfg.WS_ROOT_URL:
		return cfg.WS_ROOT_URL.rstrip('/')
	scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
	host = request.headers.get('X-Forwarded-Host', request.host)
	return f"{scheme}://{host}"


def _get_session(request):
	"""Get session data from cookie, or None if invalid/expired."""
	session_id = request.cookies.get(COOKIE_NAME)
	if not session_id:
		return None
	session = _sessions.get(session_id)
	if not session or session["expires"] < time.time():
		_sessions.pop(session_id, None)
		return None
	return session


def _should_skip(var):
	"""Check if a variable should be excluded from the web UI."""
	if isinstance(var, SKIP_TYPES):
		return True
	if isinstance(var, VariableTable):
		return any(isinstance(v, SKIP_TYPES) for v in var.variables.values())
	return False


def _var_type(var):
	"""Map a Variable subclass to a frontend type string."""
	for cls, name in [
		(BoolVar, "bool"), (SliderVar, "slider"), (IntVar, "int"),
		(OptionVar, "option"), (DurationVar, "duration"),
		(TextVar, "text"), (VariableTable, "table"),
	]:
		if isinstance(var, cls):
			return name
	return "str"


def _var_meta(var, value):
	"""Build metadata dict for a variable (for the frontend)."""
	meta = {
		"type": _var_type(var),
		"display": var.display,
		"description": var.description,
		"section": var.section,
		"notnull": var.notnull,
		"default": var.default,
		"value": value,
	}
	if isinstance(var, OptionVar):
		meta["options"] = list(var.options)
	if isinstance(var, SliderVar):
		meta["min"] = var.min_val
		meta["max"] = var.max_val
		meta["unit"] = var.unit
	if isinstance(var, VariableTable):
		meta["columns"] = list(var.variables.keys())
		meta["blank"] = var.blank
	return meta


def _check_admin(qc, member):
	"""Check if a guild member has admin access for a queue channel."""
	# TODO: restore proper admin checks later
	return True


# ─── Page handler ───

async def handle_index(request):
	if _html_cache is None:
		_load_html()
	return web.Response(text=_html_cache, content_type='text/html')


# ─── Civ stats API (public, unchanged) ───

async def handle_civ_stats(request):
	csv_path = os.path.join(DATA_DIR, 'civ_elo_stats.csv')
	if not os.path.exists(csv_path):
		return web.json_response({'error': 'civ_elo_stats.csv not found'}, status=404)

	rows = []
	with open(csv_path, 'r') as f:
		reader = csv.DictReader(f)
		player_threshold = 1000
		team_threshold = 1100
		for name in (reader.fieldnames or []):
			if name.startswith('games_player_elo_above_'):
				player_threshold = int(name.split('_')[-1])
			elif name.startswith('games_team_elo_above_'):
				team_threshold = int(name.split('_')[-1])
		pt, tt = player_threshold, team_threshold
		for row in reader:
			games = int(row['games'])
			if games < MIN_GAMES:
				continue
			rows.append({
				'civ': row['civ'],
				'games': games,
				'winrate': float(row['winrate']),
				'games_player_above': int(row.get(f'games_player_elo_above_{pt}', 0)),
				'winrate_player_above': float(row.get(f'winrate_player_elo_above_{pt}', 0)),
				'games_player_below': int(row.get(f'games_player_elo_below_{pt}', 0)),
				'winrate_player_below': float(row.get(f'winrate_player_elo_below_{pt}', 0)),
				'games_team_above': int(row.get(f'games_team_elo_above_{tt}', 0)),
				'winrate_team_above': float(row.get(f'winrate_team_elo_above_{tt}', 0)),
				'games_team_below': int(row.get(f'games_team_elo_below_{tt}', 0)),
				'winrate_team_below': float(row.get(f'winrate_team_elo_below_{tt}', 0)),
			})

	return web.json_response({
		'civs': rows,
		'player_threshold': player_threshold,
		'team_threshold': team_threshold,
	})


# ─── Auth routes ───

async def handle_auth_login(request):
	if not _oauth_enabled():
		raise web.HTTPBadRequest(text="OAuth not configured")
	root_url = _get_root_url(request)
	state = secrets.token_urlsafe(16)
	_oauth_states[state] = time.time() + 300  # 5 min expiry
	params = {
		"client_id": str(cfg.DC_CLIENT_ID),
		"redirect_uri": f"{root_url}/auth/callback",
		"response_type": "code",
		"scope": "identify",
		"state": state,
	}
	raise web.HTTPFound(f"{DISCORD_OAUTH_AUTHORIZE}?{urlencode(params)}")


async def handle_auth_callback(request):
	if not _oauth_enabled():
		raise web.HTTPBadRequest(text="OAuth not configured")

	code = request.query.get("code")
	if not code:
		raise web.HTTPBadRequest(text="Missing code parameter")

	state = request.query.get("state")
	if not state or state not in _oauth_states or _oauth_states[state] < time.time():
		_oauth_states.pop(state, None)
		raise web.HTTPBadRequest(text="Invalid or expired state parameter")
	_oauth_states.pop(state, None)

	root_url = _get_root_url(request)
	redirect_uri = f"{root_url}/auth/callback"

	async with aiohttp_client.ClientSession() as http:
		# Exchange code for token
		resp = await http.post(DISCORD_OAUTH_TOKEN, data={
			"client_id": str(cfg.DC_CLIENT_ID),
			"client_secret": cfg.DC_CLIENT_SECRET,
			"grant_type": "authorization_code",
			"code": code,
			"redirect_uri": redirect_uri,
		})
		if resp.status != 200:
			raise web.HTTPBadRequest(text="Failed to exchange code for token")
		token_data = await resp.json()

		# Get user info
		resp = await http.get(f"{DISCORD_API}/users/@me", headers={
			"Authorization": f"Bearer {token_data['access_token']}"
		})
		if resp.status != 200:
			raise web.HTTPBadRequest(text="Failed to get user info")
		user = await resp.json()

	session_id = secrets.token_urlsafe(32)
	_sessions[session_id] = {
		"user_id": int(user["id"]),
		"username": user.get("global_name") or user["username"],
		"avatar": user.get("avatar"),
		"expires": time.time() + SESSION_LIFETIME,
	}

	resp = web.HTTPFound("/")
	is_secure = root_url.startswith("https://")
	resp.set_cookie(COOKIE_NAME, session_id, max_age=SESSION_LIFETIME, httponly=True, samesite="Lax", secure=is_secure)
	raise resp


async def handle_auth_logout(request):
	session_id = request.cookies.get(COOKIE_NAME)
	if session_id:
		_sessions.pop(session_id, None)
	resp = web.HTTPFound("/")
	resp.del_cookie(COOKIE_NAME)
	raise resp


# ─── Dashboard API ───

async def handle_api_me(request):
	session = _get_session(request)
	if not session:
		return web.json_response({"logged_in": False, "oauth_enabled": _oauth_enabled()})
	return web.json_response({
		"logged_in": True,
		"oauth_enabled": True,
		"user_id": session["user_id"],
		"username": session["username"],
		"avatar": session["avatar"],
	})


async def handle_api_guilds(request):
	session = _get_session(request)
	if not session:
		return web.json_response({"error": "Not logged in"}, status=401)

	user_id = session["user_id"]
	guilds = []
	for guild in dc.guilds:
		# Only show guilds with configured queue channels
		qc_ids = [ch_id for ch_id, qc in bot.queue_channels.items() if qc.guild_id == guild.id]
		if not qc_ids:
			continue
		try:
			member = guild.get_member(user_id) or await guild.fetch_member(user_id)
		except Exception:
			continue
		is_admin = any(_check_admin(bot.queue_channels[ch_id], member) for ch_id in qc_ids)
		guilds.append({
			"id": str(guild.id),
			"name": guild.name,
			"icon": str(guild.icon.url) if guild.icon else None,
			"channels": len(qc_ids),
			"is_admin": is_admin,
		})
	return web.json_response({"guilds": guilds})


async def handle_api_channels(request):
	session = _get_session(request)
	if not session:
		return web.json_response({"error": "Not logged in"}, status=401)

	guild_id = int(request.match_info["guild_id"])
	guild = dc.get_guild(guild_id)
	if not guild:
		return web.json_response({"error": "Guild not found"}, status=404)
	try:
		member = guild.get_member(session["user_id"]) or await guild.fetch_member(session["user_id"])
	except Exception:
		return web.json_response({"error": "Not a guild member"}, status=403)

	channels = []
	for ch_id, qc in bot.queue_channels.items():
		if qc.guild_id != guild_id:
			continue
		ch = dc.get_channel(ch_id)
		channels.append({
			"id": str(ch_id),
			"name": ch.name if ch else f"unknown-{ch_id}",
			"queues": len(qc.queues),
			"is_admin": _check_admin(qc, member),
		})
	return web.json_response({"channels": channels})


async def handle_api_channel_config(request):
	session = _get_session(request)
	if not session:
		return web.json_response({"error": "Not logged in"}, status=401)

	channel_id = int(request.match_info["channel_id"])
	qc = bot.queue_channels.get(channel_id)
	if not qc:
		return web.json_response({"error": "Channel not configured"}, status=404)

	channel = dc.get_channel(channel_id)
	if not channel:
		return web.json_response({"error": "Channel not found"}, status=404)
	try:
		member = channel.guild.get_member(session["user_id"]) or await channel.guild.fetch_member(session["user_id"])
	except Exception:
		return web.json_response({"error": "Not a guild member"}, status=403)

	is_admin = _check_admin(qc, member)

	if request.method == "GET":
		readable = qc.cfg.readable()
		variables = {}
		for name, var in qc.cfg_factory.variables.items():
			if _should_skip(var):
				continue
			variables[name] = _var_meta(var, readable.get(name))
		return web.json_response({
			"channel_name": channel.name,
			"guild_name": channel.guild.name,
			"sections": qc.cfg_factory.sections,
			"variables": variables,
			"is_admin": is_admin,
		})

	# POST — update config
	if not is_admin:
		return web.json_response({"error": "Admin access required"}, status=403)
	try:
		data = await request.json()
		filtered = {}
		for key, value in data.items():
			var = qc.cfg_factory.variables.get(key)
			if not var or _should_skip(var):
				continue
			# VariableTable expects list; all others expect strings
			if isinstance(var, VariableTable):
				filtered[key] = value if isinstance(value, list) else json.dumps(value)
			elif value is None:
				filtered[key] = "none"
			else:
				filtered[key] = str(value)
		await qc.cfg.update(filtered)
		return web.json_response({"ok": True})
	except Exception as e:
		return web.json_response({"error": str(e)}, status=400)


async def handle_api_queues(request):
	session = _get_session(request)
	if not session:
		return web.json_response({"error": "Not logged in"}, status=401)

	channel_id = int(request.match_info["channel_id"])
	qc = bot.queue_channels.get(channel_id)
	if not qc:
		return web.json_response({"error": "Channel not configured"}, status=404)

	channel = dc.get_channel(channel_id)
	if not channel:
		return web.json_response({"error": "Channel not found"}, status=404)
	try:
		member = channel.guild.get_member(session["user_id"]) or await channel.guild.fetch_member(session["user_id"])
	except Exception:
		return web.json_response({"error": "Not a guild member"}, status=403)

	return web.json_response({"queues": [
		{"name": q.name, "size": q.cfg.size, "players": len(q.queue), "ranked": bool(q.cfg.ranked)}
		for q in qc.queues
	]})


async def handle_api_queue_config(request):
	session = _get_session(request)
	if not session:
		return web.json_response({"error": "Not logged in"}, status=401)

	channel_id = int(request.match_info["channel_id"])
	queue_name = request.match_info["queue_name"]
	qc = bot.queue_channels.get(channel_id)
	if not qc:
		return web.json_response({"error": "Channel not configured"}, status=404)

	channel = dc.get_channel(channel_id)
	if not channel:
		return web.json_response({"error": "Channel not found"}, status=404)
	try:
		member = channel.guild.get_member(session["user_id"]) or await channel.guild.fetch_member(session["user_id"])
	except Exception:
		return web.json_response({"error": "Not a guild member"}, status=403)

	queue = next((q for q in qc.queues if q.name.lower() == queue_name.lower()), None)
	if not queue:
		return web.json_response({"error": f"Queue '{queue_name}' not found"}, status=404)

	is_admin = _check_admin(qc, member)

	if request.method == "GET":
		readable = queue.cfg.readable()
		variables = {}
		for name, var in queue.cfg_factory.variables.items():
			if _should_skip(var):
				continue
			variables[name] = _var_meta(var, readable.get(name))
		return web.json_response({
			"queue_name": queue.name,
			"sections": queue.cfg_factory.sections,
			"variables": variables,
			"is_admin": is_admin,
		})

	# POST
	if not is_admin:
		return web.json_response({"error": "Admin access required"}, status=403)
	try:
		data = await request.json()
		filtered = {}
		for key, value in data.items():
			var = queue.cfg_factory.variables.get(key)
			if not var or _should_skip(var):
				continue
			if isinstance(var, VariableTable):
				filtered[key] = value if isinstance(value, list) else json.dumps(value)
			elif value is None:
				filtered[key] = "none"
			else:
				filtered[key] = str(value)
		await queue.cfg.update(filtered)
		return web.json_response({"ok": True})
	except Exception as e:
		return web.json_response({"error": str(e)}, status=400)


# ─── App setup ───

def create_app():
	app = web.Application()
	app.router.add_get('/', handle_index)
	# Auth
	app.router.add_get('/auth/login', handle_auth_login)
	app.router.add_get('/auth/callback', handle_auth_callback)
	app.router.add_get('/auth/logout', handle_auth_logout)
	# Public API
	app.router.add_get('/api/civ-stats', handle_civ_stats)
	app.router.add_get('/api/me', handle_api_me)
	# Dashboard API
	app.router.add_get('/api/guilds', handle_api_guilds)
	app.router.add_get('/api/guilds/{guild_id}/channels', handle_api_channels)
	app.router.add_get('/api/channels/{channel_id}/config', handle_api_channel_config)
	app.router.add_post('/api/channels/{channel_id}/config', handle_api_channel_config)
	app.router.add_get('/api/channels/{channel_id}/queues', handle_api_queues)
	app.router.add_get('/api/channels/{channel_id}/queues/{queue_name}/config', handle_api_queue_config)
	app.router.add_post('/api/channels/{channel_id}/queues/{queue_name}/config', handle_api_queue_config)
	return app


async def start_web_server(port=None):
	"""Start the web server. Returns the runner for cleanup."""
	if port is None:
		port = int(os.environ.get('PORT', 8080))
	_load_html()
	app = create_app()
	runner = web.AppRunner(app)
	await runner.setup()
	site = web.TCPSite(runner, '0.0.0.0', port)
	await site.start()
	print(f"Web server started on port {port}")
	return runner
