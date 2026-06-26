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
from core.database import db
import bot

# --- Paths ---
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
HTML_PATH = os.path.join(os.path.dirname(__file__), 'web_page.html')
MIN_GAMES = 50
MATCH_STAT_PERIODS = {
	"day": 1,
	"week": 7,
	"month": 30,
	"all": None,
}

# --- Session store (Layer 5: migrated from in-memory dicts to MySQL) ---
#
# Previously `_sessions` and `_oauth_states` were module-level dicts. Every
# Railway redeploy (which is every commit to main) blew them away, so all
# OAuth-logged-in admins had to log back in any time we shipped a fix. Moving
# them to MySQL means sessions survive deploys, and an `expires_at`-indexed
# DELETE in _get_session keeps the tables self-cleaning without a cron.
SESSION_LIFETIME = 86400  # 24 hours
OAUTH_STATE_LIFETIME = 300  # 5 minutes
COOKIE_NAME = "pubobot_session"

# Opportunistic cleanup — run a single DELETE of expired rows at most once
# every 5 minutes. Gated on a module-level timestamp so a burst of requests
# doesn't hammer the DB with the same delete. Amortized cost is essentially
# zero (hits an indexed column) and avoids a dedicated cleanup job.
_last_session_cleanup = 0.0
_SESSION_CLEANUP_INTERVAL = 300  # seconds

db.ensure_table(dict(
	tname="web_sessions",
	columns=[
		dict(cname="session_id", ctype=db.types.str),
		dict(cname="user_id", ctype=db.types.int, notnull=True),
		dict(cname="username", ctype=db.types.str, notnull=True),
		dict(cname="avatar", ctype=db.types.str),  # nullable — not every Discord user has an avatar
		dict(cname="csrf", ctype=db.types.str, notnull=True),
		dict(cname="expires_at", ctype=db.types.int, notnull=True),
	],
	primary_keys=["session_id"],
))

db.ensure_table(dict(
	tname="web_oauth_states",
	columns=[
		dict(cname="state", ctype=db.types.str),
		dict(cname="expires_at", ctype=db.types.int, notnull=True),
	],
	primary_keys=["state"],
))


async def _cleanup_expired_sessions():
	"""Best-effort cleanup of expired sessions and OAuth states.

	Called inline at read/write boundaries so we don't need a dedicated cron
	job. Returns silently on DB errors — an unavailable DB would already have
	prevented the surrounding auth flow from working."""
	global _last_session_cleanup
	now = time.time()
	if now - _last_session_cleanup < _SESSION_CLEANUP_INTERVAL:
		return
	_last_session_cleanup = now
	try:
		cutoff = int(now)
		await db.execute("DELETE FROM `web_sessions` WHERE `expires_at` < %s", (cutoff,))
		await db.execute("DELETE FROM `web_oauth_states` WHERE `expires_at` < %s", (cutoff,))
	except Exception:
		# Don't let cleanup errors bubble into auth flow — next tick will retry
		pass

# --- Discord API ---
DISCORD_API = "https://discord.com/api/v10"
DISCORD_OAUTH_AUTHORIZE = "https://discord.com/api/oauth2/authorize"
DISCORD_OAUTH_TOKEN = "https://discord.com/api/oauth2/token"

# --- Variable filtering ---
SKIP_TYPES = (RoleVar, TextChanVar, MemberVar)

# --- HTML cache ---
_html_cache = None

# Process boot time — used by /health's uptime_seconds field. Set at module
# import (which happens during asyncio bootstrap, before any task starts),
# so it's a reasonable proxy for "when the bot process started".
_boot_time = time.time()


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


async def _get_session(request):
	"""Get session data from cookie, or None if invalid/expired.

	Layer 5: reads from `web_sessions` in MySQL instead of a process-local dict
	so that OAuth logins survive Railway redeploys. Async because DB calls are
	awaitable — all call sites have been updated to `await _get_session(...)`.

	Piggybacks on the request to run opportunistic cleanup of expired
	sessions/oauth states at most once every 5 minutes.
	"""
	await _cleanup_expired_sessions()
	session_id = request.cookies.get(COOKIE_NAME)
	if not session_id:
		return None
	row = await db.select_one(
		('session_id', 'user_id', 'username', 'avatar', 'csrf', 'expires_at'),
		'web_sessions',
		where={'session_id': session_id},
	)
	if not row:
		return None
	if row['expires_at'] < int(time.time()):
		# Stale cookie — drop the row so cleanup stays accurate
		try:
			await db.delete('web_sessions', where={'session_id': session_id})
		except Exception:
			pass
		return None
	# Map the DB row shape to the legacy dict shape that downstream handlers
	# expect. `expires` is kept for backwards compatibility with any code that
	# reads it, even though _get_session already filters on expires_at.
	return {
		'session_id': row['session_id'],
		'user_id': row['user_id'],
		'username': row['username'],
		'avatar': row['avatar'],
		'csrf': row['csrf'],
		'expires': row['expires_at'],
	}


def _should_skip(var):
	"""Check if a variable should be excluded from the web UI."""
	if isinstance(var, SKIP_TYPES):
		return True
	if isinstance(var, VariableTable):
		# Show mixed tables like the rating-ranks table, which has a RoleVar
		# "role" column. The frontend renders skip-type columns as plain text
		# cells (matching Leshaka's UI), so only skip a table whose columns are
		# ALL skip-types. Previously this used any(), which hid the entire
		# Rating ranks editor just because of its optional role column.
		return all(isinstance(v, SKIP_TYPES) for v in var.variables.values())
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
	"""Check if a guild member has admin access for a queue channel.

	Mirrors the permission model used by slash admin commands in
	bot/context/slash/ and by enable_channel/disable_channel in
	bot/main.py: the bot owner, the guild owner, or any member with the
	Manage Guild permission is treated as an admin. Until 2026-04-11
	this returned True unconditionally (see the old TODO comment),
	which meant any OAuth-logged-in Discord user could mutate the
	channel and queue config of every channel the bot manages.
	"""
	if member is None:
		return False
	# Bot owner (global override, mirrors context.Context.check_perms)
	owner_id = getattr(cfg, 'DC_OWNER_ID', 0)
	if owner_id and member.id == owner_id:
		return True
	# Guild owner of the guild this member is in
	guild = getattr(member, 'guild', None)
	if guild is not None and member.id == getattr(guild, 'owner_id', 0):
		return True
	# Anyone with Manage Guild permission
	perms = getattr(member, 'guild_permissions', None)
	if perms is not None and getattr(perms, 'manage_guild', False):
		return True
	return False


def _check_csrf(request, session):
	"""Validate X-CSRF-Token header against the session CSRF token.

	Uses constant-time compare to avoid timing oracles. Returns True only
	when the session has a csrf token AND the header exactly matches.
	Dashboard POST endpoints that don't gate on this are vulnerable to
	cross-site request forgery: a malicious page could trick a logged-in
	admin's browser into POSTing to /api/channels/<id>/config because the
	session cookie rides along automatically.
	"""
	if not session:
		return False
	expected = session.get('csrf')
	if not expected:
		return False
	provided = request.headers.get('X-CSRF-Token', '')
	return secrets.compare_digest(provided, expected)


# ─── Page handler ───

async def handle_index(request):
	if _html_cache is None:
		_load_html()
	return web.Response(text=_html_cache, content_type='text/html')


# ─── Health check (for Railway healthcheckPath) ───

async def handle_health(request):
	"""Liveness probe used by Railway's healthcheckPath.

	Returns 200 only when the Discord client is connected AND the DB pool
	answers a trivial query. Returns 503 in every other state.

	This is what prevents the zombie-bot failure mode: previously a
	Discord 1015 rate limit would kill the Discord task while the web
	task kept the container "alive" from Railway's point of view (it fell
	back to a TCP probe because no healthcheckPath was configured). With
	this endpoint + healthcheckPath = "/health" in railway.toml, Railway
	restarts the container whenever Discord is actually dead.

	The payload also carries non-gating observability fields:
	  - active_matches: current in-flight match count
	  - last_tick_age_seconds: seconds since the last think() tick
	    (>5 with bot_ready=true means the think loop is stalled)
	  - last_elo_sync_at: unix timestamp of the last successful ELO sync,
	    0 if none yet this process run
	  - uptime_seconds: process uptime since import
	These let the Railway dashboard / future `/metrics` scrape see
	degradation before it becomes an outage.
	"""
	import asyncio as _asyncio
	from core.database import db as _db
	from bot import events as _events
	from bot import elo_sync as _elo_sync

	discord_ok = bool(getattr(bot, 'bot_ready', False)) and dc.is_ready()

	db_ok = False
	try:
		# Cap the query at 2s so a slow DB doesn't hang the healthcheck
		await _asyncio.wait_for(_db.fetchone("SELECT 1 AS ok"), timeout=2.0)
		db_ok = True
	except Exception:
		db_ok = False

	now = time.time()
	last_tick = getattr(_events, 'last_tick_at', 0.0) or 0.0
	# If we've never ticked, report None rather than a misleading huge delta
	last_tick_age = int(now - last_tick) if last_tick > 0 else None
	last_elo_sync = getattr(_elo_sync, 'last_elo_sync_at', 0.0) or 0.0

	healthy = discord_ok and db_ok
	payload = {
		"status": "ok" if healthy else "unhealthy",
		"discord_connected": discord_ok,
		"db_connected": db_ok,
		"bot_ready": bool(getattr(bot, 'bot_ready', False)),
		"active_matches": len(getattr(bot, 'active_matches', []) or []),
		"last_tick_age_seconds": last_tick_age,
		"last_elo_sync_at": int(last_elo_sync) if last_elo_sync > 0 else 0,
		"uptime_seconds": int(now - _boot_time),
	}
	return web.json_response(payload, status=200 if healthy else 503)


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


# ─── Strategy insights API (public) ───

# Phase label per classification key, for grouping in the dashboard.
_STRATEGY_PHASE = {
	"scout_rush": "Feudal", "archer_rush": "Feudal", "maa_rush": "Feudal",
	"knight_rush": "Early Castle", "crossbow_rush": "Early Castle", "cav_archer_rush": "Early Castle",
	"camel_rush": "Early Castle", "ram_push": "Early Castle",
	"forward_castle": "Castle", "safe_castle": "Castle",
	"late_knight": "Late Castle", "late_crossbow": "Late Castle", "late_cav_archer": "Late Castle",
	"late_camel": "Late Castle", "late_unique": "Late Castle", "late_ram": "Late Castle",
	"boom_to_imp": "Boom",
}


async def handle_strategies(request):
	"""Public: play-style ('strategy') leaderboards from the cls_* tables — per-strategy totals
	plus a per-player roster, for the dashboard Strategies tab. Titles/conditions come from the
	classification registry; counts from cls_results."""
	from utils.classifications.registry import REGISTRY

	rows = await db.fetchall(
		"SELECT `key` AS k, identity AS player, COUNT(*) AS games, "
		"SUM(winner=1) AS wins, SUM(winner=0) AS losses "
		"FROM cls_results GROUP BY `key`, identity")
	by_key = {}
	for r in (rows or []):
		by_key.setdefault(r["k"], []).append({
			"player": r["player"] or "?", "games": int(r["games"]),
			"wins": int(r["wins"] or 0), "losses": int(r["losses"] or 0)})

	# Top civs per strategy.
	civ_by_key = {}
	for r in (await db.fetchall(
			"SELECT `key` AS k, civ, COUNT(*) AS n FROM cls_results "
			"WHERE civ IS NOT NULL AND civ <> '' GROUP BY `key`, civ") or []):
		civ_by_key.setdefault(r["k"], []).append((r["civ"], int(r["n"])))

	strategies = []
	for key, c in REGISTRY.items():
		roster = sorted(by_key.get(key, []), key=lambda p: -p["games"])
		for p in roster:
			dec = p["wins"] + p["losses"]
			p["winrate"] = round(100 * p["wins"] / dec) if dec else None
		tg = sum(p["games"] for p in roster)
		tw = sum(p["wins"] for p in roster)
		tl = sum(p["losses"] for p in roster)
		# Top 3 players by win rate among the top 10 by games (decided win rate only) — this
		# filters out 1-game wonders without an arbitrary min-games cutoff.
		ranked = sorted([p for p in roster[:10] if p["winrate"] is not None],
		                key=lambda p: (-p["winrate"], -p["games"]))[:3]
		top_players = [{"player": p["player"], "winrate": p["winrate"], "games": p["games"]} for p in ranked]
		top_civs = [civ for civ, _ in sorted(civ_by_key.get(key, []), key=lambda x: -x[1])[:3]]
		strategies.append({
			"key": key, "title": c.title, "phase": _STRATEGY_PHASE.get(key, ""),
			"category": getattr(c, "category", "strategy"),
			"condition": c.trigger_spec, "games": tg, "players": len(roster),
			"wins": tw, "losses": tl,
			"winrate": round(100 * tw / (tw + tl)) if (tw + tl) else None,
			"roster": roster, "top_civs": top_civs, "top_players": top_players,
		})

	# Per-player corpus totals (the denominator for "% of total") + distinct categorized matches
	# (so the web can derive the "mixed / uncategorized" remainder = total - categorized).
	totals = {}
	for r in (await db.fetchall("SELECT identity, games, wins, losses FROM cls_player_totals") or []):
		totals[r["identity"]] = {"games": int(r["games"] or 0), "wins": int(r["wins"] or 0),
		                         "losses": int(r["losses"] or 0)}
	# "categorized" feeds the STRATEGIES "mixed / uncategorized" remainder, so it must count only
	# strategy keys — luck keys (esp. luck_baseline, which fires every valid game) would otherwise
	# mark nearly every game as categorized and collapse the remainder to ~0.
	luck_keys = [k for k, cc in REGISTRY.items() if getattr(cc, "category", "strategy") == "luck"]
	cat_filter = ("WHERE `key` NOT IN ({}) ".format(",".join(["%s"] * len(luck_keys)))
	              if luck_keys else "")
	categorized = {}
	for r in (await db.fetchall(
			"SELECT identity, COUNT(DISTINCT aoe2_match_id) AS g, "
			"COUNT(DISTINCT IF(winner=1, aoe2_match_id, NULL)) AS w, "
			"COUNT(DISTINCT IF(winner=0, aoe2_match_id, NULL)) AS l "
			"FROM cls_results " + cat_filter + "GROUP BY identity", luck_keys) or []):
		categorized[r["identity"]] = {"games": int(r["g"] or 0), "wins": int(r["w"] or 0),
		                              "losses": int(r["l"] or 0)}

	return web.json_response({
		"strategies": strategies,
		"player_totals": totals,
		"player_categorized": categorized,
	})


# ─── Match stats API (public) ───

def _period_start(period):
	days = MATCH_STAT_PERIODS.get(period, MATCH_STAT_PERIODS["week"])
	if days is None:
		return None
	return int(time.time()) - days * 86400


def _period_filter(period, alias="m"):
	start = _period_start(period)
	if start is None:
		return "", []
	return f" AND {alias}.at >= %s", [start]


def _winrate(wins, losses):
	decided = int(wins or 0) + int(losses or 0)
	return round(100 * int(wins or 0) / decided) if decided else None


def _avatar_for_user_id(user_id):
	try:
		uid = int(user_id)
	except (TypeError, ValueError):
		return None
	user = dc.get_user(uid)
	if user is not None and getattr(user, "display_avatar", None):
		return str(user.display_avatar.url)
	for guild in dc.guilds:
		member = guild.get_member(uid)
		if member is not None and getattr(member, "display_avatar", None):
			return str(member.display_avatar.url)
	return None


def _visible_user_clause(alias="pm"):
	return (
		" AND NOT EXISTS (SELECT 1 FROM qc_players hp "
		f"WHERE hp.user_id={alias}.user_id AND hp.is_hidden=1)"
	)


async def _player_is_hidden(user_id):
	row = await db.fetchone(
		"SELECT 1 AS hidden FROM qc_players WHERE user_id=%s AND is_hidden=1 LIMIT 1",
		[user_id])
	return bool(row)


async def _player_has_public_stats(user_id):
	if await _player_is_hidden(user_id):
		return False
	row = await db.fetchone(
		"SELECT 1 AS x FROM qc_player_matches pm WHERE pm.user_id=%s" +
		_visible_user_clause("pm") + " LIMIT 1",
		[user_id])
	if row:
		return True
	return int(user_id) in await _mapped_profiles_by_user()


def _map_counts(rows):
	counts = {}
	for r in rows or []:
		for name in (r.get("maps") or "").split("\n"):
			name = name.strip()
			if name:
				counts[name] = counts.get(name, 0) + 1
	return [{"map": k, "games": v} for k, v in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:12]]


def _csv_profile_rows():
	path = os.path.join(DATA_DIR, "player_profile_map.csv")
	if not os.path.exists(path):
		return []
	rows = []
	with open(path, newline="") as f:
		for r in csv.DictReader(f):
			uid = (r.get("user_id") or "").strip()
			if not uid.isdigit():
				continue
			pids = [p.strip() for p in (r.get("profile_id") or "").split("/") if p.strip().isdigit()]
			names = [n.strip() for n in (r.get("aoe2_name") or "").split("/") if n.strip()]
			rows.append({
				"user_id": int(uid),
				"nick": r.get("nick") or "",
				"profile_ids": [int(p) for p in pids],
				"aoe2_names": names,
			})
	return rows


async def _mapped_profiles_by_user():
	"""Existing Discord-user -> AoE2 profile/name mapping from live DBs + CSV fallback."""
	out = {}

	def add(uid, profile_id=None, name=None, nick=None):
		if not uid:
			return
		d = out.setdefault(int(uid), {"profile_ids": set(), "aoe2_names": set(), "nick": ""})
		if profile_id:
			d["profile_ids"].add(int(profile_id))
		if name:
			d["aoe2_names"].add(str(name).strip())
		if nick and not d["nick"]:
			d["nick"] = str(nick).strip()

	for r in await db.fetchall("SELECT user_id, profile_id, name FROM qc_profile_map"):
		add(r.get("user_id"), r.get("profile_id"), r.get("name"))
	for r in await db.fetchall("SELECT user_id, profile_id, name FROM rs_profiles WHERE user_id IS NOT NULL"):
		add(r.get("user_id"), r.get("profile_id"), r.get("name"))
	for r in _csv_profile_rows():
		for pid in r["profile_ids"]:
			add(r["user_id"], pid, nick=r["nick"])
		for name in r["aoe2_names"]:
			add(r["user_id"], name=name, nick=r["nick"])
	return out


async def _match_stat_players():
	hidden_rows = await db.fetchall("SELECT DISTINCT user_id FROM qc_players WHERE is_hidden=1")
	hidden_users = {int(r["user_id"]) for r in hidden_rows or []}
	rows = await db.fetchall(
		"SELECT pm.user_id, MAX(pm.nick) AS nick, COUNT(DISTINCT pm.match_id) AS games "
		"FROM qc_player_matches pm WHERE 1=1" + _visible_user_clause("pm") +
		" GROUP BY pm.user_id ORDER BY games DESC, nick ASC LIMIT 250")
	mapped = await _mapped_profiles_by_user()
	players = {}
	for r in rows or []:
		uid = int(r["user_id"])
		if uid in hidden_users:
			continue
		players[uid] = {
			"user_id": str(uid),
			"nick": r["nick"] or mapped.get(uid, {}).get("nick") or str(uid),
			"games": int(r["games"] or 0),
			"profile_ids": sorted(mapped.get(uid, {}).get("profile_ids", [])),
			"avatar": _avatar_for_user_id(uid),
		}
	for uid, m in mapped.items():
		if uid not in players and uid not in hidden_users:
			players[uid] = {
				"user_id": str(uid),
				"nick": m.get("nick") or next(iter(m.get("aoe2_names") or []), str(uid)),
				"games": 0,
				"profile_ids": sorted(m.get("profile_ids", [])),
				"avatar": _avatar_for_user_id(uid),
			}
	return sorted(players.values(), key=lambda p: (-p["games"], p["nick"].lower()))[:500]


async def _mapped_player_identity(user_id):
	mapped = (await _mapped_profiles_by_user()).get(int(user_id), {})
	return sorted(mapped.get("profile_ids", [])), sorted(n.lower() for n in mapped.get("aoe2_names", []) if n)


def _civ_player_clause(user_id, aoe2_names):
	clauses = ["user_id=%s"]
	args = [user_id]
	if aoe2_names:
		clauses.append("LOWER(aoe2_name) IN (" + ",".join(["%s"] * len(aoe2_names)) + ")")
		args.extend(aoe2_names)
	return "(" + " OR ".join(clauses) + ")", args


def _linked_civ_clause(alias=""):
	prefix = f"{alias}." if alias else ""
	return f"{prefix}bot_match_id IS NOT NULL AND {prefix}user_id IS NOT NULL"


def _rating_payload(row):
	if not row:
		return {"rating_start": None, "rating_end": None, "rating_delta": None}
	start = row.get("rating_start")
	end = row.get("rating_end")
	delta = row.get("rating_delta")
	return {
		"rating_start": int(start) if start is not None else None,
		"rating_end": int(end) if end is not None else None,
		"rating_delta": int(delta) if delta is not None else None,
	}


async def _rating_deltas(period, user_ids=None):
	clauses = []
	args = []
	start = _period_start(period)
	if start is not None:
		clauses.append("at >= %s")
		args.append(start)
	if user_ids is not None:
		user_ids = sorted({int(u) for u in user_ids})
		if not user_ids:
			return {}
		clauses.append("user_id IN (" + ",".join(["%s"] * len(user_ids)) + ")")
		args.extend(user_ids)
	where = " WHERE " + " AND ".join(clauses) if clauses else ""
	rows = await db.fetchall(
		"SELECT user_id, "
		"SUBSTRING_INDEX(GROUP_CONCAT(rating_before ORDER BY at ASC, id ASC), ',', 1) AS rating_start, "
		"SUBSTRING_INDEX(GROUP_CONCAT(rating_before + rating_change ORDER BY at DESC, id DESC), ',', 1) AS rating_end, "
		"SUM(rating_change) AS rating_delta "
		"FROM qc_rating_history" + where + " GROUP BY user_id",
		args)
	return {int(r["user_id"]): _rating_payload(r) for r in rows or []}


async def _rating_delta(period, user_id):
	return (await _rating_deltas(period, [user_id])).get(int(user_id), _rating_payload(None))


def _avg(rows, key):
	vals = [float(r[key]) for r in rows if r.get(key) is not None]
	return sum(vals) / len(vals) if vals else None


def _std(rows, key):
	vals = [float(r[key]) for r in rows if r.get(key) is not None]
	if len(vals) < 2:
		return 1.0
	mean = sum(vals) / len(vals)
	variance = sum((v - mean) ** 2 for v in vals) / len(vals)
	return max(variance ** 0.5, 1.0)


def _z(row, rows, key, invert=False):
	if row.get(key) is None:
		return 0.0
	mean = _avg(rows, key)
	if mean is None:
		return 0.0
	val = float(row[key])
	score = (mean - val if invert else val - mean) / _std(rows, key)
	return max(-2.0, min(2.0, score))


def _score_component(value):
	return max(0, min(100, round(50 + value * 15)))


def _impact_payload(row, group):
	eco_z = (_z(row, group, "villagers") * 0.65) + (_z(row, group, "vil_pre_castle") * 0.35)
	army_z = (_z(row, group, "military") * 0.65) + (_z(row, group, "mil_pre_castle") * 0.35)
	timing_z = (_z(row, group, "feudal_s", invert=True) * 0.35) + (_z(row, group, "castle_s", invert=True) * 0.45) + (_z(row, group, "imperial_s", invert=True) * 0.20)
	early_eco_z = _z(row, group, "vil_pre_castle")
	recovery_z = _z(row, group, "villagers") - early_eco_z
	eco = _score_component(eco_z)
	army = _score_component(army_z)
	timing = _score_component(timing_z)
	recovery = _score_component(recovery_z)
	impact = round((army * 0.34) + (eco * 0.30) + (timing * 0.18) + (recovery * 0.18))
	tags = []
	if army >= 68 and eco < 52:
		tags.append("Low-eco pressure")
	elif army >= 66:
		tags.append("Army pressure")
	if eco >= 66:
		tags.append("Eco carry")
	if timing >= 66:
		tags.append("Timing edge")
	if recovery >= 66:
		tags.append("Recovery")
	if impact >= 72 and not tags:
		tags.append("High impact")
	return {
		"user_id": str(row["user_id"]) if row.get("user_id") is not None else None,
		"nick": row.get("identity") or str(row.get("user_id") or ""),
		"impact_score": impact,
		"army_score": army,
		"eco_score": eco,
		"timing_score": timing,
		"recovery_score": recovery,
		"impact_tags": tags[:3],
	}


def _avg_impact(impacts, key):
	vals = [float(i[key]) for i in impacts if i.get(key) is not None]
	return round(sum(vals) / len(vals), 1) if vals else None


def _style_scout_report(style, top_tags, best_civs, duration_edges, has_impacts):
	if not has_impacts:
		return {
			"headline": "Replay sample needed",
			"description": "No parsed replay sample yet, so style read is unavailable.",
			"traits": [],
		}
	openers = {
		"Pressure player": "Tempo-forward profile: creates map space through army presence before full boom.",
		"Economy carry": "Boom-and-carry profile: scales well when allowed to build economy and take late fights.",
		"Timing specialist": "Timing-window profile: impact spikes around age-up or upgrade windows.",
		"Recovery anchor": "Stabilizer profile: absorbs rough starts and rebuilds into useful team position.",
		"High-impact flex": "Flex profile: contributes across army, economy, timing, and recovery lanes.",
		"Balanced flex": "Balanced team profile: no single lane dominates, but output stays steady.",
	}
	parts = [openers.get(style, openers["Balanced flex"])]
	traits = [t["tag"] for t in top_tags[:3]]
	if best_civs:
		civ_names = ", ".join(c["civ"] for c in best_civs[:3])
		parts.append(f"Best civ results: {civ_names}.")
		traits.extend(f"{c['civ']} comfort pick" for c in best_civs[:2])
	if duration_edges:
		buckets = ", ".join(d["bucket"] for d in duration_edges[:2])
		parts.append(f"Strongest match window: {buckets}.")
		traits.extend(f"{d['bucket']} window" for d in duration_edges[:1])
	if top_tags:
		parts.append("Recurring tags: " + ", ".join(t["tag"] for t in top_tags[:3]) + ".")
	return {
		"headline": style,
		"description": " ".join(parts),
		"traits": traits[:6],
	}


def _player_impact_profile(impacts, civs=None, durations=None):
	impacts = list(impacts or [])
	if not impacts:
		return {
			"style": "No replay style",
			"summary": "No parsed replay impact data",
			"matches": 0,
			"avg_impact": None,
			"avg_army": None,
			"avg_eco": None,
			"avg_timing": None,
			"avg_recovery": None,
			"top_tags": [],
			"best_civs": [],
			"duration_edges": [],
			"scout_report": _style_scout_report("No replay style", [], [], [], False),
		}

	tag_counts = {}
	for impact in impacts:
		for tag in impact.get("impact_tags") or []:
			tag_counts[tag] = tag_counts.get(tag, 0) + 1
	top_tags = [
		{"tag": tag, "count": count, "rate": round(count * 100 / len(impacts), 1)}
		for tag, count in sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
	]
	best_civs = []
	for row in civs or []:
		games = int(row.get("games") or 0)
		if games < 3:
			continue
		winrate = _winrate(row.get("wins"), row.get("losses"))
		if winrate is not None:
			best_civs.append({"civ": row["civ"], "games": games, "winrate": winrate})
	best_civs = sorted(best_civs, key=lambda r: (-r["winrate"], -r["games"], r["civ"]))[:3]
	duration_edges = []
	for row in durations or []:
		games = int(row.get("games") or 0)
		if games < 3:
			continue
		winrate = _winrate(row.get("wins"), row.get("losses"))
		if winrate is not None:
			duration_edges.append({"bucket": row["bucket"], "games": games, "winrate": winrate})
	duration_edges = sorted(duration_edges, key=lambda r: (-r["winrate"], -r["games"], r["bucket"]))[:2]
	avg_impact = _avg_impact(impacts, "impact_score")
	avg_army = _avg_impact(impacts, "army_score")
	avg_eco = _avg_impact(impacts, "eco_score")
	avg_timing = _avg_impact(impacts, "timing_score")
	avg_recovery = _avg_impact(impacts, "recovery_score")
	scores = {
		"Army": avg_army or 0,
		"Eco": avg_eco or 0,
		"Timing": avg_timing or 0,
		"Recovery": avg_recovery or 0,
	}
	top_component, top_score = max(scores.items(), key=lambda kv: kv[1])
	if top_component == "Army" and top_score >= 58 and top_score >= scores["Eco"] + 5:
		style = "Pressure player"
	elif top_component == "Eco" and top_score >= 58 and top_score >= scores["Army"] + 5:
		style = "Economy carry"
	elif top_component == "Timing" and top_score >= 58:
		style = "Timing specialist"
	elif top_component == "Recovery" and top_score >= 58:
		style = "Recovery anchor"
	elif avg_impact is not None and avg_impact >= 62:
		style = "High-impact flex"
	else:
		style = "Balanced flex"
	summary_bits = []
	if top_tags:
		summary_bits.append(", ".join(t["tag"] for t in top_tags[:2]))
	summary_bits.append(f"{top_component.lower()} led")
	return {
		"style": style,
		"summary": "; ".join(summary_bits),
		"matches": len(impacts),
		"avg_impact": avg_impact,
		"avg_army": avg_army,
		"avg_eco": avg_eco,
		"avg_timing": avg_timing,
		"avg_recovery": avg_recovery,
		"top_tags": top_tags,
		"best_civs": best_civs,
		"duration_edges": duration_edges,
		"scout_report": _style_scout_report(style, top_tags, best_civs, duration_edges, True),
	}


async def _match_impacts(match_ids, focus_user_id=None, focus_profile_ids=None):
	match_ids = [m for m in dict.fromkeys(match_ids or []) if m is not None]
	if not match_ids:
		return {}
	hidden_rows = await db.fetchall("SELECT DISTINCT user_id FROM qc_players WHERE is_hidden=1")
	hidden_users = {int(r["user_id"]) for r in hidden_rows or []}
	rows = await db.fetchall(
		"SELECT rm.bot_match_id, g.profile_id, g.user_id, g.identity, g.villagers, g.vil_pre_castle, "
		"g.military, g.mil_pre_castle, g.feudal_s, g.castle_s, g.imperial_s "
		"FROM rs_matches rm JOIN rs_player_games g ON g.aoe2_match_id=rm.aoe2_match_id "
		"WHERE rm.bot_match_id IN (" + ",".join(["%s"] * len(match_ids)) + ")",
		match_ids)
	groups = {}
	for r in rows or []:
		groups.setdefault(r["bot_match_id"], []).append(r)
	focus_profiles = {int(p) for p in focus_profile_ids or []}
	out = {}
	for match_id, group in groups.items():
		payloads = []
		for row in group:
			row_user_id = row.get("user_id")
			if row_user_id is not None and int(row_user_id) in hidden_users:
				continue
			if focus_user_id is not None:
				if row_user_id != focus_user_id and int(row.get("profile_id") or 0) not in focus_profiles:
					continue
			elif row_user_id is None:
				continue
			payloads.append(_impact_payload(row, group))
		if payloads:
			out[match_id] = max(payloads, key=lambda p: p["impact_score"])
	return out


async def _match_stats_overall(period):
	at_clause, params = _period_filter(period)
	summary = await db.fetchone(
		"SELECT COUNT(DISTINCT m.match_id) AS games, "
		"COUNT(DISTINCT IF(m.ranked=1, m.match_id, NULL)) AS ranked_games, "
		"COUNT(DISTINCT pm.user_id) AS players, MAX(m.at) AS last_match_at "
		"FROM qc_matches m LEFT JOIN qc_player_matches pm "
		"ON pm.match_id=m.match_id AND pm.channel_id=m.channel_id" + _visible_user_clause("pm") +
		" WHERE 1=1" + at_clause,
		params)
	board = await db.fetchall(
		"SELECT pm.user_id, MAX(pm.nick) AS nick, COUNT(DISTINCT m.match_id) AS games, "
		"SUM(m.ranked=1 AND m.winner=pm.team) AS wins, "
		"SUM(m.ranked=1 AND m.winner IS NOT NULL AND m.winner<>pm.team) AS losses, "
		"SUM(m.ranked=1 AND m.winner IS NULL) AS draws "
		"FROM qc_player_matches pm JOIN qc_matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"WHERE 1=1" + _visible_user_clause("pm") + at_clause +
		" GROUP BY pm.user_id ORDER BY wins DESC, games DESC LIMIT 50",
		params)
	ratings = await _rating_deltas(period, [r["user_id"] for r in board or []])
	civs = await db.fetchall(
		"SELECT civ, COUNT(*) AS games, SUM(result='W') AS wins, SUM(result='L') AS losses "
		"FROM qc_match_civs WHERE " + _linked_civ_clause() + " AND civ IS NOT NULL AND civ<>''"
		+ (" AND at >= %s" if params else "") +
		" GROUP BY civ ORDER BY games DESC LIMIT 20",
		params)
	maps = _map_counts(await db.fetchall("SELECT maps FROM qc_matches m WHERE maps IS NOT NULL" + at_clause, params))
	trend = await db.fetchall(
		"SELECT DATE(CONVERT_TZ(FROM_UNIXTIME(m.at), '+00:00', '+05:30')) AS bucket, COUNT(*) AS games "
		"FROM qc_matches m WHERE 1=1" + at_clause + " GROUP BY bucket ORDER BY bucket ASC",
		params)
	recent = await db.fetchall(
		"SELECT m.match_id, m.queue_name, m.at, m.ranked, m.winner, m.maps, rm.duration_s "
		"FROM qc_matches m LEFT JOIN rs_matches rm ON rm.bot_match_id=m.match_id "
		"WHERE 1=1" + at_clause +
		" ORDER BY m.at DESC, m.match_id DESC LIMIT 20",
		params)
	impacts = await _match_impacts([r["match_id"] for r in recent or []])
	return {
		"summary": {
			"games": int((summary or {}).get("games") or 0),
			"ranked_games": int((summary or {}).get("ranked_games") or 0),
			"players": int((summary or {}).get("players") or 0),
			"last_match_at": (summary or {}).get("last_match_at"),
		},
		"leaderboard": [
			{
				**{
					"user_id": str(r["user_id"]),
					"nick": r["nick"] or str(r["user_id"]),
					"games": int(r["games"] or 0),
					"wins": int(r["wins"] or 0),
					"losses": int(r["losses"] or 0),
					"draws": int(r["draws"] or 0),
					"winrate": _winrate(r["wins"], r["losses"]),
					"avatar": _avatar_for_user_id(r["user_id"]),
				},
				**ratings.get(int(r["user_id"]), _rating_payload(None)),
			}
			for r in board or []
		],
		"civs": [
			{"civ": r["civ"], "games": int(r["games"] or 0), "wins": int(r["wins"] or 0),
			 "losses": int(r["losses"] or 0), "winrate": _winrate(r["wins"], r["losses"])}
			for r in civs or []
		],
		"maps": maps,
		"trend": [{"bucket": str(r["bucket"]), "games": int(r["games"] or 0)} for r in trend or []],
		"recent": [
			{"match_id": r["match_id"], "queue": r["queue_name"], "at": r["at"],
			 "ranked": bool(r["ranked"]), "map": ((r.get("maps") or "").split("\n")[0] or "").strip(),
			 "duration_s": r.get("duration_s"), "impact": impacts.get(r["match_id"])}
			for r in recent or []
		],
	}


async def _player_streak(user_id, at_clause, params):
	rows = await db.fetchall(
		"SELECT m.winner, pm.team FROM qc_player_matches pm JOIN qc_matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"WHERE pm.user_id=%s AND m.ranked=1" + at_clause + " ORDER BY m.at DESC, m.match_id DESC LIMIT 20",
		[user_id, *params])
	streak = []
	for r in rows or []:
		if r["winner"] is None:
			streak.append("D")
		elif r["winner"] == r["team"]:
			streak.append("W")
		else:
			streak.append("L")
	return streak


async def _match_stats_player(user_id, period):
	at_clause, params = _period_filter(period)
	profile_ids, aoe2_names = await _mapped_player_identity(user_id)
	rating = await _rating_delta(period, user_id)
	summary = await db.fetchone(
		"SELECT COUNT(DISTINCT m.match_id) AS games, "
		"SUM(m.ranked=1 AND m.winner=pm.team) AS wins, "
		"SUM(m.ranked=1 AND m.winner IS NOT NULL AND m.winner<>pm.team) AS losses, "
		"SUM(m.ranked=1 AND m.winner IS NULL) AS draws, MAX(m.at) AS last_match_at, MAX(pm.nick) AS nick "
		"FROM qc_player_matches pm JOIN qc_matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"WHERE pm.user_id=%s" + at_clause,
		[user_id, *params])
	civ_clause, civ_args = _civ_player_clause(user_id, aoe2_names)
	civs = await db.fetchall(
		"SELECT civ, COUNT(*) AS games, SUM(result='W') AS wins, SUM(result='L') AS losses "
		"FROM qc_match_civs WHERE " + _linked_civ_clause() + " AND " + civ_clause +
		" AND civ IS NOT NULL AND civ<>''"
		+ (" AND at >= %s" if params else "") +
		" GROUP BY civ ORDER BY wins DESC, games DESC LIMIT 12",
		[*civ_args, *params])
	maps = _map_counts(await db.fetchall(
		"SELECT m.maps FROM qc_player_matches pm JOIN qc_matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"WHERE pm.user_id=%s AND m.maps IS NOT NULL" + at_clause,
		[user_id, *params]))
	teammates = await db.fetchall(
		"SELECT mate.user_id, MAX(mate.nick) AS nick, COUNT(*) AS games, "
		"SUM(m.winner=pm.team) AS wins, SUM(m.winner IS NOT NULL AND m.winner<>pm.team) AS losses "
		"FROM qc_player_matches pm JOIN qc_matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"JOIN qc_player_matches mate ON mate.match_id=pm.match_id AND mate.channel_id=pm.channel_id "
		"AND mate.team=pm.team AND mate.user_id<>pm.user_id" + _visible_user_clause("mate") +
		" WHERE pm.user_id=%s AND m.ranked=1" + at_clause +
		" GROUP BY mate.user_id HAVING games >= 2 ORDER BY wins DESC, games DESC LIMIT 8",
		[user_id, *params])
	opponents = await db.fetchall(
		"SELECT opp.user_id, MAX(opp.nick) AS nick, COUNT(*) AS games, "
		"SUM(m.winner=pm.team) AS wins, SUM(m.winner IS NOT NULL AND m.winner=opp.team) AS losses "
		"FROM qc_player_matches pm JOIN qc_matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"JOIN qc_player_matches opp ON opp.match_id=pm.match_id AND opp.channel_id=pm.channel_id "
		"AND opp.team<>pm.team AND opp.user_id<>pm.user_id" + _visible_user_clause("opp") +
		" WHERE pm.user_id=%s AND m.ranked=1" + at_clause +
		" GROUP BY opp.user_id HAVING games >= 2 ORDER BY losses DESC, games DESC LIMIT 8",
		[user_id, *params])
	recent = await db.fetchall(
		"SELECT m.match_id, m.queue_name, m.at, m.ranked, m.winner, m.maps, pm.team "
		"FROM qc_player_matches pm JOIN qc_matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"WHERE pm.user_id=%s" + at_clause + " ORDER BY m.at DESC, m.match_id DESC LIMIT 12",
		[user_id, *params])
	recent_civs = {}
	impacts = {}
	if recent:
		match_ids = [r["match_id"] for r in recent]
		impacts = await _match_impacts(match_ids, user_id, profile_ids)
		civ_clause, civ_args = _civ_player_clause(user_id, aoe2_names)
		rows = await db.fetchall(
			"SELECT bot_match_id, civ FROM qc_match_civs WHERE bot_match_id IN ("
			+ ",".join(["%s"] * len(match_ids)) + ") AND " + civ_clause,
			[*match_ids, *civ_args])
		for r in rows or []:
			if r.get("civ") and r["bot_match_id"] not in recent_civs:
				recent_civs[r["bot_match_id"]] = r["civ"]
	trend = await db.fetchall(
		"SELECT DATE(CONVERT_TZ(FROM_UNIXTIME(m.at), '+00:00', '+05:30')) AS bucket, "
		"COUNT(*) AS games, SUM(m.ranked=1 AND m.winner=pm.team) AS wins, "
		"SUM(m.ranked=1 AND m.winner IS NOT NULL AND m.winner<>pm.team) AS losses "
		"FROM qc_player_matches pm JOIN qc_matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"WHERE pm.user_id=%s" + at_clause + " GROUP BY bucket ORDER BY bucket ASC",
		[user_id, *params])
	return {
		"summary": {
			"nick": (summary or {}).get("nick") or str(user_id),
			"avatar": _avatar_for_user_id(user_id),
			"profile_ids": profile_ids,
			**rating,
			"games": int((summary or {}).get("games") or 0),
			"wins": int((summary or {}).get("wins") or 0),
			"losses": int((summary or {}).get("losses") or 0),
			"draws": int((summary or {}).get("draws") or 0),
			"winrate": _winrate((summary or {}).get("wins"), (summary or {}).get("losses")),
			"last_match_at": (summary or {}).get("last_match_at"),
			"streak": await _player_streak(user_id, at_clause, params),
		},
		"civs": [
			{"civ": r["civ"], "games": int(r["games"] or 0), "wins": int(r["wins"] or 0),
			 "losses": int(r["losses"] or 0), "winrate": _winrate(r["wins"], r["losses"])}
			for r in civs or []
		],
		"maps": maps,
		"teammates": [
			{"nick": r["nick"] or str(r["user_id"]), "games": int(r["games"] or 0),
			 "wins": int(r["wins"] or 0), "losses": int(r["losses"] or 0),
			 "winrate": _winrate(r["wins"], r["losses"]), "avatar": _avatar_for_user_id(r["user_id"])}
			for r in teammates or []
		],
		"opponents": [
			{"nick": r["nick"] or str(r["user_id"]), "games": int(r["games"] or 0),
			 "wins": int(r["wins"] or 0), "losses": int(r["losses"] or 0),
			 "winrate": _winrate(r["wins"], r["losses"]), "avatar": _avatar_for_user_id(r["user_id"])}
			for r in opponents or []
		],
		"recent": [
			{"match_id": r["match_id"], "queue": r["queue_name"], "at": r["at"],
			 "ranked": bool(r["ranked"]), "result": (
				"D" if r["ranked"] and r["winner"] is None else
				"W" if r["winner"] == r["team"] else
				"L" if r["winner"] is not None else "-"
			 ), "map": ((r.get("maps") or "").split("\n")[0] or "").strip(),
			 "civ": recent_civs.get(r["match_id"]), "impact": impacts.get(r["match_id"])}
			for r in recent or []
		],
		"trend": [{"bucket": str(r["bucket"]), "games": int(r["games"] or 0),
		           "wins": int(r["wins"] or 0), "losses": int(r["losses"] or 0)}
		          for r in trend or []],
	}


async def handle_match_stats(request):
	period = request.query.get("period", "week")
	if period not in MATCH_STAT_PERIODS:
		period = "week"
	player_raw = request.query.get("player_id") or ""
	players = await _match_stat_players()
	payload = {"period": period, "players": players, "scope": "overall"}
	if player_raw and player_raw != "all":
		try:
			user_id = int(player_raw)
		except ValueError:
			return web.json_response({"error": "Invalid player_id"}, status=400)
		if not await _player_has_public_stats(user_id):
			return web.json_response({"error": "Player not found"}, status=404)
		payload["scope"] = "player"
		payload["selected_player_id"] = str(user_id)
		payload.update(await _match_stats_player(user_id, period))
	else:
		payload.update(await _match_stats_overall(period))
	return web.json_response(payload)


async def handle_leaderboard(request):
	period = request.query.get("period", "week")
	if period not in MATCH_STAT_PERIODS:
		period = "week"
	mode = request.query.get("mode", "players")
	at_clause, params = _period_filter(period)
	if mode == "civs":
		rows = await db.fetchall(
			"SELECT civ, COUNT(*) AS games, SUM(result='W') AS wins, SUM(result='L') AS losses "
			"FROM qc_match_civs WHERE " + _linked_civ_clause() + " AND civ IS NOT NULL AND civ<>''"
			+ (" AND at >= %s" if params else "") +
			" GROUP BY civ ORDER BY wins DESC, games DESC LIMIT 100",
			params)
		return web.json_response({
			"period": period,
			"mode": "civs",
			"rows": [
				{"civ": r["civ"], "games": int(r["games"] or 0), "wins": int(r["wins"] or 0),
				 "losses": int(r["losses"] or 0), "winrate": _winrate(r["wins"], r["losses"])}
				for r in rows or []
			],
		})
	rows = await db.fetchall(
		"SELECT pm.user_id, MAX(pm.nick) AS nick, COUNT(DISTINCT m.match_id) AS games, "
		"SUM(m.ranked=1 AND m.winner=pm.team) AS wins, "
		"SUM(m.ranked=1 AND m.winner IS NOT NULL AND m.winner<>pm.team) AS losses, "
		"SUM(m.ranked=1 AND m.winner IS NULL) AS draws, MAX(p.rating) AS rating "
		"FROM qc_player_matches pm JOIN qc_matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"LEFT JOIN qc_players p ON p.user_id=pm.user_id AND p.channel_id=pm.channel_id "
		"WHERE 1=1" + _visible_user_clause("pm") + at_clause +
		" GROUP BY pm.user_id ORDER BY wins DESC, games DESC LIMIT 100",
		params)
	ratings = await _rating_deltas(period, [r["user_id"] for r in rows or []])
	return web.json_response({
		"period": period,
		"mode": "players",
		"rows": [
			{
				**{
					"user_id": str(r["user_id"]),
					"nick": r["nick"] or str(r["user_id"]),
					"games": int(r["games"] or 0),
					"wins": int(r["wins"] or 0),
					"losses": int(r["losses"] or 0),
					"draws": int(r["draws"] or 0),
					"rating": r.get("rating"),
					"winrate": _winrate(r["wins"], r["losses"]),
					"avatar": _avatar_for_user_id(r["user_id"]),
				},
				**ratings.get(int(r["user_id"]), _rating_payload(None)),
			}
			for r in rows or []
		],
	})


async def handle_player_stats(request):
	period = request.query.get("period", "week")
	if period not in MATCH_STAT_PERIODS:
		period = "week"
	try:
		user_id = int(request.query.get("player_id") or "0")
	except ValueError:
		return web.json_response({"error": "Invalid player_id"}, status=400)
	if not user_id:
		return web.json_response({"error": "Missing player_id"}, status=400)
	if not await _player_has_public_stats(user_id):
		return web.json_response({"error": "Player not found"}, status=404)

	at_clause, params = _period_filter(period)
	profile_ids, aoe2_names = await _mapped_player_identity(user_id)
	rating = await _rating_delta(period, user_id)
	base_args = [user_id, *params]
	summary = await db.fetchone(
		"SELECT MAX(pm.nick) AS nick, COUNT(DISTINCT m.match_id) AS games, "
		"SUM(m.ranked=1 AND m.winner=pm.team) AS wins, "
		"SUM(m.ranked=1 AND m.winner IS NOT NULL AND m.winner<>pm.team) AS losses, "
		"SUM(m.ranked=1 AND m.winner IS NULL) AS draws, MAX(m.at) AS last_match_at "
		"FROM qc_player_matches pm JOIN qc_matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"WHERE pm.user_id=%s" + at_clause,
		base_args)
	opponents = await db.fetchall(
		"SELECT opp.user_id, MAX(opp.nick) AS nick, COUNT(*) AS games, "
		"SUM(m.winner=pm.team) AS wins, SUM(m.winner IS NOT NULL AND m.winner=opp.team) AS losses "
		"FROM qc_player_matches pm JOIN qc_matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"JOIN qc_player_matches opp ON opp.match_id=pm.match_id AND opp.channel_id=pm.channel_id "
		"AND opp.team<>pm.team AND opp.user_id<>pm.user_id" + _visible_user_clause("opp") +
		" WHERE pm.user_id=%s AND m.ranked=1" + at_clause +
		" GROUP BY opp.user_id HAVING games >= 1 ORDER BY games DESC, wins DESC LIMIT 12",
		base_args)
	durations = await db.fetchall(
		"SELECT CASE "
		"WHEN rm.duration_s < 300 THEN 'Less than 5 min' "
		"WHEN rm.duration_s < 900 THEN '5 - <15 min' "
		"WHEN rm.duration_s < 1500 THEN '15 - <25 min' "
		"WHEN rm.duration_s < 2400 THEN '25 - <40 min' "
		"ELSE 'More than 40 min' END AS bucket, "
		"CASE WHEN rm.duration_s < 300 THEN 1 WHEN rm.duration_s < 900 THEN 2 "
		"WHEN rm.duration_s < 1500 THEN 3 WHEN rm.duration_s < 2400 THEN 4 ELSE 5 END AS ord, "
		"COUNT(*) AS games, SUM(m.winner=pm.team) AS wins, "
		"SUM(m.winner IS NOT NULL AND m.winner<>pm.team) AS losses "
		"FROM qc_player_matches pm JOIN qc_matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"JOIN rs_matches rm ON rm.bot_match_id=m.match_id "
		"WHERE pm.user_id=%s AND m.ranked=1 AND rm.duration_s IS NOT NULL" + at_clause +
		" GROUP BY bucket, ord ORDER BY ord",
		base_args)
	civ_clause, civ_args = _civ_player_clause(user_id, aoe2_names)
	civs = await db.fetchall(
		"SELECT civ, COUNT(*) AS games, SUM(result='W') AS wins, SUM(result='L') AS losses "
		"FROM qc_match_civs WHERE " + _linked_civ_clause() + " AND " + civ_clause +
		" AND civ IS NOT NULL AND civ<>''"
		+ (" AND at >= %s" if params else "") +
		" GROUP BY civ ORDER BY wins DESC, games DESC LIMIT 30",
		[*civ_args, *params])
	opp_civs = await db.fetchall(
		"SELECT oc.civ, COUNT(*) AS games, SUM(m.winner=pm.team) AS wins, "
		"SUM(m.winner IS NOT NULL AND m.winner<>pm.team) AS losses "
		"FROM qc_player_matches pm JOIN qc_matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"JOIN qc_match_civs oc ON oc.bot_match_id=m.match_id AND oc.team<>pm.team "
		"WHERE pm.user_id=%s AND m.ranked=1 AND oc.civ IS NOT NULL AND oc.civ<>''" + at_clause +
		" GROUP BY oc.civ ORDER BY wins DESC, games DESC LIMIT 30",
		base_args)
	matches = await db.fetchall(
		"SELECT m.match_id, m.queue_name, m.at, m.ranked, m.winner, m.maps, pm.team, rm.duration_s "
		"FROM qc_player_matches pm JOIN qc_matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"LEFT JOIN rs_matches rm ON rm.bot_match_id=m.match_id "
		"WHERE pm.user_id=%s" + at_clause +
		" ORDER BY m.at DESC, m.match_id DESC LIMIT 50",
		base_args)
	impact_match_rows = await db.fetchall(
		"SELECT DISTINCT m.match_id FROM qc_player_matches pm JOIN qc_matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"JOIN rs_matches rm ON rm.bot_match_id=m.match_id "
		"WHERE pm.user_id=%s" + at_clause,
		base_args)
	period_impacts = await _match_impacts([r["match_id"] for r in impact_match_rows or []], user_id, profile_ids)
	impact_profile = _player_impact_profile(period_impacts.values(), civs, durations)
	match_civs = {}
	opp_match_civs = {}
	if matches:
		match_ids = [r["match_id"] for r in matches]
		match_id_clause = ",".join(["%s"] * len(match_ids))
		civ_rows = await db.fetchall(
			"SELECT bot_match_id, civ FROM qc_match_civs WHERE bot_match_id IN ("
			+ match_id_clause + ") AND " + civ_clause,
			[*match_ids, *civ_args])
		for r in civ_rows or []:
			if r.get("civ") and r["bot_match_id"] not in match_civs:
				match_civs[r["bot_match_id"]] = r["civ"]
		opp_rows = await db.fetchall(
			"SELECT oc.bot_match_id, GROUP_CONCAT(DISTINCT oc.civ ORDER BY oc.civ SEPARATOR ', ') AS civs "
			"FROM qc_player_matches pm JOIN qc_match_civs oc "
			"ON oc.bot_match_id=pm.match_id AND oc.team<>pm.team "
			"WHERE pm.user_id=%s AND pm.match_id IN (" + match_id_clause + ") "
			"AND oc.civ IS NOT NULL AND oc.civ<>'' GROUP BY oc.bot_match_id",
			[user_id, *match_ids])
		opp_match_civs = {r["bot_match_id"]: r["civs"] for r in opp_rows or []}
	return web.json_response({
		"period": period,
		"summary": {
			"user_id": str(user_id),
			"nick": (summary or {}).get("nick") or str(user_id),
			"avatar": _avatar_for_user_id(user_id),
			"profile_ids": profile_ids,
			**rating,
			"games": int((summary or {}).get("games") or 0),
			"wins": int((summary or {}).get("wins") or 0),
			"losses": int((summary or {}).get("losses") or 0),
			"draws": int((summary or {}).get("draws") or 0),
			"winrate": _winrate((summary or {}).get("wins"), (summary or {}).get("losses")),
			"last_match_at": (summary or {}).get("last_match_at"),
			"impact_profile": impact_profile,
		},
		"opponents": [
			{"user_id": str(r["user_id"]), "nick": r["nick"] or str(r["user_id"]),
			 "games": int(r["games"] or 0), "wins": int(r["wins"] or 0),
			 "losses": int(r["losses"] or 0), "winrate": _winrate(r["wins"], r["losses"]),
			 "avatar": _avatar_for_user_id(r["user_id"])}
			for r in opponents or []
		],
		"durations": [
			{"bucket": r["bucket"], "games": int(r["games"] or 0), "wins": int(r["wins"] or 0),
			 "losses": int(r["losses"] or 0), "winrate": _winrate(r["wins"], r["losses"])}
			for r in durations or []
		],
		"civs": [
			{"civ": r["civ"], "games": int(r["games"] or 0), "wins": int(r["wins"] or 0),
			 "losses": int(r["losses"] or 0), "winrate": _winrate(r["wins"], r["losses"])}
			for r in civs or []
		],
		"opponent_civs": [
			{"civ": r["civ"], "games": int(r["games"] or 0), "wins": int(r["wins"] or 0),
			 "losses": int(r["losses"] or 0), "winrate": _winrate(r["wins"], r["losses"])}
			for r in opp_civs or []
		],
		"matches": [
			{"match_id": r["match_id"], "queue": r["queue_name"], "at": r["at"],
			 "ranked": bool(r["ranked"]), "result": (
				"D" if r["ranked"] and r["winner"] is None else
				"W" if r["winner"] == r["team"] else
				"L" if r["winner"] is not None else "-"
			 ), "map": ((r.get("maps") or "").split("\n")[0] or "").strip(),
			 "duration_s": r.get("duration_s"), "civ": match_civs.get(r["match_id"]),
			 "opponent_civs": opp_match_civs.get(r["match_id"]),
			 "impact": period_impacts.get(r["match_id"])}
			for r in matches or []
		],
	})


# ─── Auth routes ───

async def handle_auth_login(request):
	if not _oauth_enabled():
		raise web.HTTPBadRequest(text="OAuth not configured")
	root_url = _get_root_url(request)
	state = secrets.token_urlsafe(16)
	# Persist the OAuth state in MySQL so we survive a redeploy that happens
	# between the user clicking "Login" and Discord redirecting them back.
	await _cleanup_expired_sessions()
	await db.insert('web_oauth_states', {
		'state': state,
		'expires_at': int(time.time()) + OAUTH_STATE_LIFETIME,
	}, on_dublicate='replace')
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
	if not state:
		raise web.HTTPBadRequest(text="Invalid or expired state parameter")
	state_row = await db.select_one(
		('state', 'expires_at'), 'web_oauth_states', where={'state': state}
	)
	if not state_row or state_row['expires_at'] < int(time.time()):
		# Clean up the stale row if it exists — keeps the table tight
		if state_row:
			try:
				await db.delete('web_oauth_states', where={'state': state})
			except Exception:
				pass
		raise web.HTTPBadRequest(text="Invalid or expired state parameter")
	# Single-use — delete immediately to prevent replay
	try:
		await db.delete('web_oauth_states', where={'state': state})
	except Exception:
		pass

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
	await db.insert('web_sessions', {
		'session_id': session_id,
		'user_id': int(user["id"]),
		'username': user.get("global_name") or user["username"],
		'avatar': user.get("avatar"),
		# Per-session CSRF token — required on all POST endpoints via the
		# X-CSRF-Token header. Generated once at login so the dashboard JS
		# can fetch it from /api/me and cache it for the session.
		'csrf': secrets.token_urlsafe(32),
		'expires_at': int(time.time()) + SESSION_LIFETIME,
	}, on_dublicate='replace')

	resp = web.HTTPFound("/")
	is_secure = root_url.startswith("https://")
	resp.set_cookie(COOKIE_NAME, session_id, max_age=SESSION_LIFETIME, httponly=True, samesite="Lax", secure=is_secure)
	raise resp


async def handle_auth_logout(request):
	session_id = request.cookies.get(COOKIE_NAME)
	if session_id:
		try:
			await db.delete('web_sessions', where={'session_id': session_id})
		except Exception:
			pass
	resp = web.HTTPFound("/")
	resp.del_cookie(COOKIE_NAME)
	raise resp


# ─── Dashboard API ───

async def handle_api_me(request):
	session = await _get_session(request)
	if not session:
		return web.json_response({"logged_in": False, "oauth_enabled": _oauth_enabled()})
	# Lazily issue a CSRF token for any session missing one (e.g. legacy
	# rows from before the CSRF feature landed). Safe because this endpoint
	# requires a valid same-origin session cookie — an attacker without
	# that cookie can't trigger the issuance, and cross-origin JS can't
	# read the response under the browser's same-origin policy.
	if not session.get('csrf'):
		new_csrf = secrets.token_urlsafe(32)
		try:
			await db.update('web_sessions', {'csrf': new_csrf}, keys={'session_id': session['session_id']})
			session['csrf'] = new_csrf
		except Exception:
			# If the update fails, fall back to an ephemeral token for this
			# response — it won't match on the next POST but at least /api/me
			# still returns a usable payload.
			session['csrf'] = new_csrf
	return web.json_response({
		"logged_in": True,
		"oauth_enabled": True,
		"user_id": session["user_id"],
		"username": session["username"],
		"avatar": session["avatar"],
		"csrf": session["csrf"],
	})


async def handle_api_guilds(request):
	session = await _get_session(request)
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
	session = await _get_session(request)
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
	session = await _get_session(request)
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
	# CSRF check first: reject cross-site POSTs before running any admin
	# or config-mutation logic. Pre-CSRF this endpoint accepted any POST
	# with a valid session cookie, so a malicious page could rewrite a
	# logged-in admin's channel config with no interaction.
	if not _check_csrf(request, session):
		return web.json_response({"error": "Invalid or missing CSRF token"}, status=403)
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
	session = await _get_session(request)
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
		# We don't use the member object — we only call fetch_member for
		# its side effect: raising if the caller isn't actually in the
		# guild. Assigning to `_` is what tells the linter "the name is
		# unused on purpose" without losing the membership check.
		_ = channel.guild.get_member(session["user_id"]) or await channel.guild.fetch_member(session["user_id"])
	except Exception:
		return web.json_response({"error": "Not a guild member"}, status=403)

	return web.json_response({"queues": [
		{"name": q.name, "size": q.cfg.size, "players": len(q.queue), "ranked": bool(q.cfg.ranked)}
		for q in qc.queues
	]})


async def handle_api_queue_config(request):
	session = await _get_session(request)
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
	# CSRF check first — see handle_api_channel_config for rationale.
	if not _check_csrf(request, session):
		return web.json_response({"error": "Invalid or missing CSRF token"}, status=403)
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


# ─── Debug endpoint (temporary) ───

async def handle_api_debug(request):
	"""Temporary debug endpoint to diagnose guild/channel state."""
	return web.json_response({
		"bot_guilds": [{"id": str(g.id), "name": g.name} for g in dc.guilds],
		"queue_channels": {
			str(ch_id): {"guild_id": str(qc.guild_id), "queues": len(qc.queues)}
			for ch_id, qc in bot.queue_channels.items()
		},
		"bot_ready": getattr(bot, 'bot_ready', 'unknown'),
	})


# ─── AoE2 lobby join / spectate redirects ───

def _aoe2_redirect(request, mode):
	"""Bounce the browser to the `aoe2de://` deep link that launches AoE2:DE into a
	lobby. Discord link buttons can't carry the aoe2de:// scheme, so the lobby buttons
	point here (https) and we redirect. mode is 'join' (aoe2de://0/<id>) or 'spectate'
	(aoe2de://1/<id>). The game id is validated as digits so the target is injection-safe."""
	game_id = request.match_info.get('game_id', '')
	if not game_id.isdigit():
		return web.Response(status=404, text="invalid game id")
	target = f"aoe2de://1/{game_id}" if mode == "spectate" else f"aoe2de://0/{game_id}"
	what = "Spectating" if mode == "spectate" else "Joining"
	html = (
		'<!doctype html><html><head><meta charset="utf-8">'
		'<meta name="viewport" content="width=device-width,initial-scale=1">'
		f'<title>{what} AoE2 lobby…</title>'
		f'<meta http-equiv="refresh" content="0;url={target}">'
		f'<script>window.location.href = {target!r};</script></head>'
		'<body style="font-family:sans-serif;text-align:center;padding-top:3em;background:#1b1d22;color:#eee">'
		f'<h2>{what} the Age of Empires II lobby…</h2>'
		f'<p>If the game didn\'t open, click <a style="color:#50e3c2" href="{target}">{target}</a></p>'
		'<p style="color:#888">Steam and Age of Empires II: Definitive Edition must be running.</p>'
		'</body></html>'
	)
	return web.Response(text=html, content_type='text/html')


async def handle_lobby_join(request):
	return _aoe2_redirect(request, "join")


async def handle_lobby_spectate(request):
	return _aoe2_redirect(request, "spectate")


# ─── App setup ───

def create_app():
	app = web.Application()
	app.router.add_get('/', handle_index)
	# Health check (Railway healthcheckPath)
	app.router.add_get('/health', handle_health)
	# AoE2 lobby join / spectate deep-link redirects (clicked from Discord buttons)
	app.router.add_get('/join/{game_id}', handle_lobby_join)
	app.router.add_get('/spectate/{game_id}', handle_lobby_spectate)
	# Auth
	app.router.add_get('/auth/login', handle_auth_login)
	app.router.add_get('/auth/callback', handle_auth_callback)
	app.router.add_get('/auth/logout', handle_auth_logout)
	# Public API
	app.router.add_get('/api/civ-stats', handle_civ_stats)
	app.router.add_get('/api/strategies', handle_strategies)
	app.router.add_get('/api/match-stats', handle_match_stats)
	app.router.add_get('/api/leaderboard', handle_leaderboard)
	app.router.add_get('/api/player-stats', handle_player_stats)
	app.router.add_get('/api/me', handle_api_me)
	# Dashboard API
	app.router.add_get('/api/debug', handle_api_debug)
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
