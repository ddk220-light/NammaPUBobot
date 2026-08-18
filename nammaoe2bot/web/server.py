"""NammaPUBobot Web Dashboard — OAuth2, civ stats, channel/queue configuration.

STAGE 5d, what this file may and may not read. The dashboard owns exactly two
tables (`web_sessions`, `web_oauth_states`); everything else it shows is read
from the core/raw/derived layers, and stage 5d moved the last four reads off
the stores stage 6 drops:

  * the strategy leaderboards, the per-player strategy tags and the per-match
    strategy chips read `game_labels` (kind='strategy'), joined to `game_stats`
    for the profile/winner/civ and to `replay_players` for the in-game name —
    the same three-table join nammaoe2bot/derived/classifications/query.py uses, for the same
    reasons (see fetch_results' docstring). The `cls_*` tables are gone from
    this file.
  * `/api/civ-stats` reads the derived-community `civ_stats` table. It used to
    read `data/civ_elo_stats.csv` off disk — a frozen April snapshot nothing
    regenerated — which is why that file could not be deleted in 5c. The
    per-elo-bracket splits went with it: `civ_stats` is a per-community tally
    with no elo dimension, and inventing brackets it does not store would be a
    fabricated number, not a smaller one.
  * the player page's scouting report reads `player_rollups`, through the same
    nammaoe2bot/features/scouting/report.py contract `/rank` renders. A player with NO ROW
    yields exactly PENDING — the absence is the signal (identity v2 §5).
  * the generated persona is gone from both ends: the stored overlay (the
    legacy persona table, whose writer stage 5a deleted, so every row in it is
    frozen at its last ingest) and the live `derive_persona`/scout-read
    narration. Stage 5a retired the persona from `/rank` for asserting
    personality where it had arithmetic; presenting the same blurb here, and
    a frozen one at that, is exactly what this migration exists to delete.

The luck page went with them rather than being repointed: its data source was
`luck_baseline`, which fires for every player in every valid Nomad game and is
deliberately stored in no table (nammaoe2bot/derived/game_labels.py's kind_for).

The viewing layer is allowed to be thinner for it — the design says so in as
many words (§1.5). Public reads are community-scoped, while the broader
community-first settings redesign remains separate work. Do not repopulate
any of the above from a live re-derivation.
"""

import json
import os
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import aiohttp as aiohttp_client
from aiohttp import web

from nammaoe2bot.runtime.config import cfg
from nammaoe2bot.runtime.cfg_factory import (
	RoleVar, TextChanVar, MemberVar, VariableTable,
	BoolVar, IntVar, SliderVar, OptionVar, DurationVar, TextVar
)
from nammaoe2bot.runtime.client import dc
from nammaoe2bot.runtime.console import log
from nammaoe2bot.runtime.database import db
from nammaoe2bot.features.identity import resolver
from nammaoe2bot.features.scouting import report as scouting_report
from nammaoe2bot.derived import game_labels, rollups
from nammaoe2bot.ingest import scoring as rs_scoring
from nammaoe2bot.features.scouting.tag_leaderboard import tag_leaderboard_score
from nammaoe2bot.web import onboarding
from nammaoe2bot.web import pubobot_migration

# --- Paths ---
HTML_PATH = os.path.join(os.path.dirname(__file__), 'page.html')
# A civ needs this many recorded picks in the community before /api/civ-stats
# lists it. Applied at read time and NOT shared with nammaoe2bot/features/civs/pools.py's
# MIN_CIV_GAMES, which happens to hold the same number for a different question
# ("enough evidence to put this civ in a randomised pool"): `civ_stats` is a
# plain tally with no opinion about what counts as enough evidence, and two
# readers must be free to disagree without a schema change.
MIN_GAMES = 50
DEFAULT_STATS_PERIOD = "all"
MATCH_STAT_PERIODS = {
	"all": None,
	"year": 365,
	"month6": 183,
	"month3": 92,
	"month": 30,
	"week": 7,
}
STRATEGY_TAG_LABELS = {
	"archer_rush": "Feudal archer poke",
	"scout_rush": "Scout-map opener",
	"maa_rush": "MAA opening",
	"knight_rush": "Knight rush opener",
	"crossbow_rush": "Xbow timing",
	"cav_archer_rush": "CA switch",
	"camel_rush": "Camel counter-punch",
	"ram_push": "Siege shove",
	"forward_castle": "Castle dropper",
	"safe_castle": "Home-castle turtle",
	"late_knight": "Late knight flood",
	"late_crossbow": "Late xbow mass",
	"late_cav_archer": "CA snowball",
	"late_camel": "Camel mass",
	"late_unique": "UU spam",
	"late_ram": "Late siege push",
	"boom_to_imp": "Greedy boom to Imp",
}
IMPACT_TAG_LABELS = {
	"Low-eco pressure": "All-in pressure",
	"Army pressure": "Map pressure",
	"Boom carry": "Boom carry",
	"Eco carry": "Eco carry",
	"Timing edge": "Age-up tempo",
	"Recovery": "Reboom",
	"High impact": "High impact",
	"All-in pressure": "All-in pressure",
	"Map pressure": "Map pressure",
	"Age-up tempo": "Age-up tempo",
	"Reboom": "Reboom",
	"Naked FC": "Naked FC",
	"Greedy boom": "Greedy boom",
	"Feudal all-in": "Feudal all-in",
	"Fast Imp": "Fast Imp",
	"Army spammer": "Army spammer",
	"Tech greedy": "Tech greedy",
	"Upgrade timer": "Upgrade timer",
	"Knight-heavy comp": "Knight-heavy comp",
	"Monk support": "Monk support",
	"Trash switch": "Trash switch",
	"One-trick comp": "One-trick comp",
	"Mixed comp": "Mixed comp",
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
COOKIE_NAME = "nammaoe2bot_session"
# The name this cookie used to carry, still READ so the rename does not log
# every dashboard admin out. A session lives in MySQL keyed on session_id; the
# cookie only carries that id, so an old cookie names a perfectly valid row.
# Written under the new name only — a browser that presents the old one gets
# the new one set on its next login, and _LEGACY_COOKIE_NAME can be deleted
# once SESSION_LIFETIME (24h) has passed with no old cookies arriving.
_LEGACY_COOKIE_NAME = "pubobot_session"


def _session_cookie(request):
	"""The session id this request carries, under either name."""
	return request.cookies.get(COOKIE_NAME) or request.cookies.get(_LEGACY_COOKIE_NAME)

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

db.ensure_table(dict(
	tname="community_imports",
	columns=[
		dict(cname="import_id", ctype=db.types.str),
		dict(cname="community_id", ctype=db.types.int, notnull=True),
		dict(cname="channel_id", ctype=db.types.int, notnull=True),
		dict(cname="rating_channel_id", ctype=db.types.int, notnull=True),
		dict(cname="kind", ctype=db.types.str, notnull=True),
		dict(cname="source_name", ctype=db.types.str, notnull=True),
		dict(cname="timezone", ctype=db.types.str, notnull=True),
		dict(cname="created_by", ctype=db.types.int, notnull=True),
		dict(cname="created_at", ctype=db.types.int, notnull=True),
		dict(cname="match_count", ctype=db.types.int, notnull=True),
		dict(cname="player_count", ctype=db.types.int, notnull=True),
	],
	primary_keys=["import_id"],
	indexes=[("community_imports_tenant", ["community_id", "created_at"])],
))

db.ensure_table(dict(
	tname="community_import_match_map",
	columns=[
		dict(cname="import_id", ctype=db.types.str, notnull=True),
		dict(cname="community_id", ctype=db.types.int, notnull=True),
		dict(cname="channel_id", ctype=db.types.int, notnull=True),
		dict(cname="source_match_id", ctype=db.types.int, notnull=True),
		dict(cname="match_id", ctype=db.types.int, notnull=True),
	],
	primary_keys=["import_id", "source_match_id"],
	unique_keys=[("community_import_match_target", ["match_id"])],
	indexes=[("community_import_match_tenant", ["community_id", "channel_id"])],
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
		_html_cache = "<h1>page.html not found</h1>"


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
	session_id = _session_cookie(request)
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


def _member_is_admin(member):
	"""Whether a Discord guild member may administer its community.

	Mirrors the permission model used by slash admin commands in
	bot/context/slash/ and by enable_channel/disable_channel in
	nammaoe2bot/state.py: the bot owner, the guild owner, or any member with the
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
	admin's browser into POSTing to an admin community config route because
	the session cookie rides along automatically.
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
	from nammaoe2bot.runtime.database import db as _db
	from nammaoe2bot.discord import events as _events
	from nammaoe2bot.features import elo_sync as _elo_sync

	discord_ok = bool(dc.app.ready) and dc.is_ready()

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
		"bot_ready": bool(dc.app.ready),
		"active_matches": len(dc.app.active_matches),
		"last_tick_age_seconds": last_tick_age,
		"last_elo_sync_at": int(last_elo_sync) if last_elo_sync > 0 else 0,
		"uptime_seconds": int(now - _boot_time),
	}
	return web.json_response(payload, status=200 if healthy else 503)


# ─── The community the public pages describe ───

@dataclass(frozen=True)
class PublicCommunity:
	"""The tenant boundary carried through one public-dashboard request.

	Do not pass a naked community id through the web read path. Keeping the
	guild alongside it lets member/avatar lookups stay inside the same Discord
	server, and making the object mandatory gives helper signatures something
	the tests can enforce instead of relying on ambient FLAGSHIP_GUILD_IDS.
	"""
	community_id: int
	guild_id: int
	name: str

	@property
	def sql_id(self):
		"""A DB-validated integer safe to embed in fixed SQL fragments.

		The dynamic value came from an integer route parse and was then resolved
		through `communities`; returning the canonical DB value here avoids adding
		a tenant placeholder in the middle of every pre-existing parameter list.
		"""
		return str(int(self.community_id))

	def payload(self):
		return {
			"id": str(self.community_id),
			"guild_id": str(self.guild_id),
			"name": self.name,
		}


def _explicit_community_id(request):
	"""The numeric tenant in `/api/communities/{community_id}/...`, or None.

	False-y/invalid ids never fall back to the flagship alias: a malformed
	explicit tenant must fail closed rather than silently serving another
	community's data.
	"""
	raw = getattr(request, "match_info", {}).get("community_id")
	if raw is None:
		return None
	try:
		value = int(raw)
	except (TypeError, ValueError):
		return 0
	return value if value > 0 else 0


async def _public_community(request=None):
	"""Resolve the explicit route tenant, or the legacy flagship alias.

	Unprefixed public URLs are retained while the existing deployment and old
	bookmarks move to `/c/{community_id}/...`. They still resolve exactly one
	configured flagship and never aggregate communities. New prefixed routes do
	not consult deployment config at all.
	"""
	explicit = _explicit_community_id(request) if request is not None else None
	if explicit is not None:
		if not explicit:
			return None
		row = await db.select_one(
			["community_id", "guild_id", "name"], "communities",
			where={"community_id": explicit})
		return PublicCommunity(**row) if row else None

	for guild_id in getattr(cfg, "FLAGSHIP_GUILD_IDS", None) or []:
		row = await db.select_one(
			["community_id", "guild_id", "name"], "communities",
			where={"guild_id": int(guild_id)})
		if row:
			return PublicCommunity(**row)
	return None


async def _public_community_id(request=None):
	"""Compatibility shim for non-web callers during the route migration."""
	community = await _public_community(request)
	return community.community_id if community else None


def _community_not_found():
	return web.json_response({"error": "Community not found"}, status=404)


def _community_channel_predicate(community, channel_expression):
	return (
		"EXISTS (SELECT 1 FROM community_channels tenant_cc "
		f"WHERE tenant_cc.channel_id={channel_expression} "
		f"AND tenant_cc.community_id={community.sql_id})"
	)


def _community_replay_predicate(community, replay_expression):
	return (
		"EXISTS (SELECT 1 FROM match_replays tenant_mr "
		f"WHERE tenant_mr.replay_match_id={replay_expression} "
		f"AND tenant_mr.community_id={community.sql_id})"
	)


@dataclass(frozen=True)
class AdminCommunityContext:
	"""An authenticated administrator bound to one enrolled community."""
	community: PublicCommunity
	guild: object
	member: object
	retention: str | None = None

	def payload(self, channels=0):
		icon = getattr(self.guild, "icon", None)
		return {
			"id": str(self.community.community_id),
			"guild_id": str(self.community.guild_id),
			"name": self.community.name or getattr(self.guild, "name", "Community"),
			"icon": str(icon.url) if icon and getattr(icon, "url", None) else None,
			"channels": int(channels),
			"is_admin": True,
			"retention": self.retention,
		}


@dataclass(frozen=True)
class AdminChannelContext:
	"""A configured channel proven to belong to an admin community context."""
	admin: AdminCommunityContext
	channel_id: int
	channel: object
	queue_channel: object


@dataclass(frozen=True)
class AdminRatingContext:
	"""A pickup channel plus its guild-proven rating storage target."""
	channel: AdminChannelContext
	target_channel_id: int
	target_channel: object
	rating: object


def _positive_route_id(request, key):
	raw = getattr(request, "match_info", {}).get(key)
	try:
		value = int(raw)
	except (TypeError, ValueError):
		return 0
	return value if value > 0 else 0


async def _member_in_guild(guild, user_id):
	member = guild.get_member(user_id)
	if member is not None:
		return member
	return await guild.fetch_member(user_id)


async def _authorized_admin_row(row, session):
	guild = dc.get_guild(int(row["guild_id"]))
	if guild is None:
		return None, web.json_response({"error": "Community unavailable"}, status=404)
	try:
		member = await _member_in_guild(guild, session["user_id"])
	except Exception:
		return None, web.json_response({"error": "Not a community member"}, status=403)
	if not _member_is_admin(member):
		return None, web.json_response({"error": "Admin access required"}, status=403)

	community = PublicCommunity(
		community_id=int(row["community_id"]),
		guild_id=int(row["guild_id"]),
		name=row.get("name") or getattr(guild, "name", "Community"),
	)
	return AdminCommunityContext(
		community=community,
		guild=guild,
		member=member,
		retention=row.get("retention"),
	), None


async def _admin_community_context(request, session, *, guild_id=None, channel_id=None):
	"""Resolve and authorize exactly one community for the settings surface.

	New `/api/admin/communities/{community_id}/...` routes resolve the explicit
	community first. Legacy guild/channel routes resolve through the same
	persistent tables, so possession of a configured runtime channel id is no
	longer enough to choose a tenant.
	"""
	explicit = _explicit_community_id(request)
	row = None
	if explicit is not None:
		if explicit:
			row = await db.select_one(
				["community_id", "guild_id", "name", "retention"],
				"communities", where={"community_id": explicit})
	elif guild_id:
		row = await db.select_one(
			["community_id", "guild_id", "name", "retention"],
			"communities", where={"guild_id": int(guild_id)})
	elif channel_id:
		link = await db.select_one(
			["community_id"], "community_channels", where={"channel_id": int(channel_id)})
		if link:
			row = await db.select_one(
				["community_id", "guild_id", "name", "retention"],
				"communities", where={"community_id": link["community_id"]})
	if not row:
		return None, web.json_response({"error": "Community not found"}, status=404)
	return await _authorized_admin_row(row, session)


async def _community_channel_ids(community_id):
	rows = await db.select(
		["channel_id"], "community_channels", where={"community_id": int(community_id)})
	return {int(row["channel_id"]) for row in rows or []}


async def _admin_channel_context(request, session):
	"""Authorize a route channel and prove its persistent/runtime guild agree."""
	channel_id = _positive_route_id(request, "channel_id")
	if not channel_id:
		return None, web.json_response({"error": "Channel not found"}, status=404)
	admin, error = await _admin_community_context(
		request, session, channel_id=channel_id)
	if error is not None:
		return None, error
	link = await db.select_one(
		["community_id"], "community_channels", where={"channel_id": channel_id})
	if not link or int(link["community_id"]) != admin.community.community_id:
		return None, web.json_response({"error": "Channel not found"}, status=404)
	queue_channel = dc.app.channels.get(channel_id)
	if queue_channel is None or int(queue_channel.guild_id) != admin.community.guild_id:
		return None, web.json_response({"error": "Channel not configured"}, status=404)
	channel = dc.get_channel(channel_id)
	channel_guild = getattr(channel, "guild", None)
	if channel is None or int(getattr(channel_guild, "id", 0)) != admin.community.guild_id:
		return None, web.json_response({"error": "Channel not found"}, status=404)
	return AdminChannelContext(
		admin=admin,
		channel_id=channel_id,
		channel=channel,
		queue_channel=queue_channel,
	), None


async def _admin_rating_context(request, session):
	"""Resolve the selected pickup channel and prove its rating host is local.

	A queue can deliberately share ratings with another Discord text channel,
	so the write key is ``qc.rating.channel_id`` rather than the route channel.
	That target need not itself be a loaded pickup channel; Discord guild
	ownership is the tenant boundary in that case.
	"""
	channel, error = await _admin_channel_context(request, session)
	if error is not None:
		return None, error
	target_channel_id = int(channel.queue_channel.rating.channel_id)
	target = dc.get_channel(target_channel_id)
	if target is None and target_channel_id == channel.channel_id:
		target = channel.channel
	if target is None or int(getattr(getattr(target, "guild", None), "id", 0)) != channel.admin.community.guild_id:
		return None, web.json_response({"error": "Rating host channel not found in this community"}, status=404)
	return AdminRatingContext(
		channel=channel,
		target_channel_id=target_channel_id,
		target_channel=target,
		rating=channel.queue_channel.rating,
	), None


# ─── Civ stats API (public) ───

async def handle_civ_stats(request):
	""" Per-civ win rates for this community, from the derived `civ_stats` table.

	games == wins + losses holds on every stored row by construction (see
	nammaoe2bot/derived/civ_stats.py's compute_civ_stats: a game whose outcome was never
	resolved is counted into none of the three), so the quotient below is a real
	win rate rather than one deflated by games nobody won.

	`winrate` is a 0..1 fraction, matching what the page's fmtWr() has always
	been handed. """
	community = await _public_community(request)
	if community is None:
		return _community_not_found()
	rows = await db.fetchall(
		"SELECT civ, games, wins, losses FROM civ_stats "
		"WHERE community_id=%s AND games >= %s ORDER BY games DESC, civ ASC",
		[community.community_id, MIN_GAMES]) or []

	civs = []
	for r in rows:
		games = int(r["games"] or 0)
		if not games or not r["civ"]:
			continue
		civs.append({
			"civ": r["civ"],
			"games": games,
			"wins": int(r["wins"] or 0),
			"losses": int(r["losses"] or 0),
			"winrate": int(r["wins"] or 0) / games,
		})

	return web.json_response({
		"community": community.payload(),
		"civs": civs,
		"min_games": MIN_GAMES,
	})


# ─── Strategy insights API (public) ───

# The join every strategy read in this file makes, and why each table is in it
# (nammaoe2bot/derived/classifications/query.py's fetch_results argues the same three in full):
#
#   game_labels    — which (game, player) earned the label, and when. Its `kind`
#                    column is the stored answer to "is this a strategy or a
#                    spawn", so a reader asks for a kind instead of carrying its
#                    own copy of the 17-key allowlist.
#   game_stats     — the profile behind that slot, whether it won, and the civ it
#                    played. game_labels stores none of the three by design (see
#                    nammaoe2bot/derived/__init__.py); its PK is exactly game_labels'
#                    grain minus the label, so this join can neither drop a
#                    labelled player nor duplicate one.
#   replay_players — the in-game name to print, LEFT JOINed on its own PK
#                    (match, profile_id). Deliberately NOT joined on
#                    player_number: that PK does not constrain player_number
#                    (nammaoe2bot/derived/backfill.py says so explicitly), so joining on
#                    it could duplicate rows.
_LABEL_JOIN = (
	"FROM game_labels gl "
	"JOIN game_stats gs ON gs.replay_match_id=gl.replay_match_id "
	"AND gs.player_number=gl.player_number "
	"LEFT JOIN replay_players rp ON rp.replay_match_id=gs.replay_match_id "
	"AND rp.profile_id=gs.profile_id "
)

# Bound as a parameter everywhere rather than inlined, so the one place that
# decides which stored category the web shows is this constant.
_STRATEGY_KIND = "strategy"

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
	"""Public: play-style ('strategy') leaderboards from `game_labels` — per-strategy totals
	plus a per-player roster, for the dashboard Strategies tab. Titles/conditions come from the
	classification registry; counts from the derived label rows.

	The luck category is absent from this payload, and that is the reason the luck page went
	rather than being repointed. Its rows rested on `luck_baseline`, which fires for every
	player in every valid Nomad game and is stored in no table by design
	(nammaoe2bot/derived/game_labels.kind_for) — so the "% of valid starts" denominator every luck
	figure was quoted against does not exist any more. `kind='strategy'` is now the whole
	filter, which also retires the old NOT IN (luck keys) exclusion the categorized count
	needed."""
	from utils.classifications.registry import REGISTRY
	community = await _public_community(request)
	if community is None:
		return _community_not_found()
	replay_scope = _community_replay_predicate(community, "gl.replay_match_id")

	rows = await db.fetchall(
		"SELECT gl.label AS k, rp.identity AS player, COUNT(*) AS games, "
		"SUM(gs.winner=1) AS wins, SUM(gs.winner=0) AS losses "
		+ _LABEL_JOIN +
		"WHERE gl.kind=%s AND " + replay_scope +
		" GROUP BY gl.label, rp.identity", [_STRATEGY_KIND])
	by_key = {}
	for r in (rows or []):
		by_key.setdefault(r["k"], []).append({
			"player": r["player"] or "?", "games": int(r["games"]),
			"wins": int(r["wins"] or 0), "losses": int(r["losses"] or 0)})

	# Top civs per strategy. The civ comes off game_stats, which stores it per
	# (match, player) slot — the same grain the label was earned at.
	civ_by_key = {}
	for r in (await db.fetchall(
			"SELECT gl.label AS k, gs.civ AS civ, COUNT(*) AS n "
			+ _LABEL_JOIN +
			"WHERE gl.kind=%s AND " + replay_scope +
			" AND gs.civ IS NOT NULL AND gs.civ <> '' "
			"GROUP BY gl.label, gs.civ", [_STRATEGY_KIND]) or []):
		civ_by_key.setdefault(r["k"], []).append((r["civ"], int(r["n"])))

	strategies = []
	for key, c in REGISTRY.items():
		if game_labels.kind_for(key) != _STRATEGY_KIND:
			# Registered upstream but stored under no strategy kind — a luck key,
			# or a key the storage allowlist has never seen. Rendering it would
			# print a row of zeros for a question this table cannot answer.
			continue
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
	#
	# The totals come from `replay_players` — one row per (match, profile), the raw record of
	# every parsed game — rather than from the retired per-player totals mirror of it. Same
	# grain, same numbers, and one fewer table to keep in step.
	totals = {}
	for r in (await db.fetchall(
			"SELECT identity, COUNT(DISTINCT replay_match_id) AS games, "
			"SUM(winner=1) AS wins, SUM(winner=0) AS losses "
			"FROM replay_players rp WHERE identity IS NOT NULL AND identity <> '' AND "
			+ _community_replay_predicate(community, "rp.replay_match_id") + " "
			"GROUP BY identity") or []):
		totals[r["identity"]] = {"games": int(r["games"] or 0), "wins": int(r["wins"] or 0),
		                         "losses": int(r["losses"] or 0)}
	# "categorized" feeds the STRATEGIES "mixed / uncategorized" remainder. It counts DISTINCT
	# matches, not label rows: one game can earn several strategy labels, and summing them would
	# let a player's categorized count exceed their games played.
	categorized = {}
	for r in (await db.fetchall(
			"SELECT rp.identity AS identity, COUNT(DISTINCT gl.replay_match_id) AS g, "
			"COUNT(DISTINCT IF(gs.winner=1, gl.replay_match_id, NULL)) AS w, "
			"COUNT(DISTINCT IF(gs.winner=0, gl.replay_match_id, NULL)) AS l "
			+ _LABEL_JOIN +
			"WHERE gl.kind=%s AND " + replay_scope +
			" GROUP BY rp.identity", [_STRATEGY_KIND]) or []):
		categorized[r["identity"]] = {"games": int(r["g"] or 0), "wins": int(r["w"] or 0),
		                              "losses": int(r["l"] or 0)}

	return web.json_response({
		"community": community.payload(),
		"strategies": strategies,
		"player_totals": totals,
		"player_categorized": categorized,
	})


# ─── Match stats API (public) ───

def _period_start(period):
	days = MATCH_STAT_PERIODS.get(period, MATCH_STAT_PERIODS[DEFAULT_STATS_PERIOD])
	if days is None:
		return None
	return int(time.time()) - days * 86400


def _period_filter(period, alias="m"):
	start = _period_start(period)
	if start is None:
		return "", []
	return f" AND {alias}.reported_at >= %s", [start]


def _trend_bucket_expr(period, alias="m"):
	local_at = f"CONVERT_TZ(FROM_UNIXTIME({alias}.reported_at), '+00:00', '+05:30')"
	if period in ("all", "year", "month6"):
		return f"DATE_FORMAT({local_at}, '%%Y-%%m')"
	if period == "month3":
		return f"DATE_FORMAT(DATE_SUB({local_at}, INTERVAL WEEKDAY({local_at}) DAY), '%%Y-%%m-%%d')"
	return f"DATE({local_at})"


def _winrate(wins, losses):
	decided = int(wins or 0) + int(losses or 0)
	return round(100 * int(wins or 0) / decided) if decided else None


def _avatar_for_user_id(user_id, community):
	try:
		uid = int(user_id)
	except (TypeError, ValueError):
		return None
	# Never search every guild: doing so lets one tenant's player directory
	# learn that a globally-known Discord user belongs to some other server.
	guild = dc.get_guild(community.guild_id)
	member = guild.get_member(uid) if guild is not None else None
	if member is not None and getattr(member, "display_avatar", None):
		return str(member.display_avatar.url)
	return None


def _visible_user_clause(alias, community):
	return (
		" AND NOT EXISTS (SELECT 1 FROM player_ratings hp "
		f"WHERE hp.user_id={alias}.user_id AND hp.is_hidden=1 AND "
		+ _community_channel_predicate(community, "hp.channel_id") + ")"
	)


async def _player_is_hidden(community, user_id):
	row = await db.fetchone(
		"SELECT 1 AS hidden FROM player_ratings pr WHERE user_id=%s AND is_hidden=1 AND "
		+ _community_channel_predicate(community, "pr.channel_id") + " LIMIT 1",
		[user_id])
	return bool(row)


async def _player_has_public_stats(community, user_id):
	if await _player_is_hidden(community, user_id):
		return False
	row = await db.fetchone(
		"SELECT 1 AS x FROM match_players pm WHERE pm.user_id=%s AND "
		+ _community_channel_predicate(community, "pm.channel_id")
		+ _visible_user_clause("pm", community) + " LIMIT 1",
		[user_id])
	if row:
		return True
	# A seeded player is part of this community even before their first match.
	row = await db.fetchone(
		"SELECT 1 AS x FROM player_ratings pr WHERE pr.user_id=%s AND "
		+ _community_channel_predicate(community, "pr.channel_id") + " LIMIT 1",
		[user_id])
	if row:
		return True
	# A linked profile with no local row is visible only when Discord confirms
	# the user is a member of this community's guild.
	uid = int(user_id)
	guild = dc.get_guild(community.guild_id)
	member = guild.get_member(uid) if guild is not None else None
	return bool(member and (await resolver.profiles_for_users([uid])).get(uid))


def _map_counts(rows):
	counts = {}
	for r in rows or []:
		for name in (r.get("maps") or "").split("\n"):
			name = name.strip()
			if name:
				counts[name] = counts.get(name, 0) + 1
	return [{"map": k, "games": v} for k, v in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:12]]


async def _match_stat_players(community):
	"""The player directory: everyone with reported matches, plus everyone with a
	linked AoE2 profile but no reported match yet (they still have replay-derived
	stats, keyed on profile_id, so they must not be missing from the list).

	`mapped` comes from the identity resolver alone. It used to be a union of two
	now-retired profile tables and a hand-maintained CSV; identity v2 makes
	`identities` the sole store, so this reads one place. The one thing that
	union supplied and `identities` does not is a Discord nickname — see
	resolver.profiles_and_names_by_user for why that is correct rather than a
	gap — so a player with no row in match_players is now labelled by their
	AoE2 in-game name instead of a stale CSV nick.
	"""
	rating_rows = await db.fetchall(
		"SELECT pr.user_id, pr.is_hidden FROM player_ratings pr WHERE "
		+ _community_channel_predicate(community, "pr.channel_id"))
	hidden_rows = [r for r in rating_rows or [] if r.get("is_hidden")]
	hidden_users = {int(r["user_id"]) for r in hidden_rows or []}
	rows = await db.fetchall(
		"SELECT pm.user_id, MAX(pm.nick) AS nick, COUNT(DISTINCT pm.match_id) AS games "
		"FROM match_players pm WHERE " + _community_channel_predicate(community, "pm.channel_id")
		+ _visible_user_clause("pm", community) +
		" GROUP BY pm.user_id ORDER BY games DESC, nick ASC LIMIT 250")
	mapped = await resolver.profiles_and_names_by_user()
	eligible_users = {int(r["user_id"]) for r in rating_rows or []}
	guild = dc.get_guild(community.guild_id)
	players = {}
	for r in rows or []:
		uid = int(r["user_id"])
		if uid in hidden_users:
			continue
		players[uid] = {
			"user_id": str(uid),
			"nick": r["nick"] or next(iter(mapped.get(uid, {}).get("aoe2_names", [])), str(uid)),
			"games": int(r["games"] or 0),
			"profile_ids": mapped.get(uid, {}).get("profile_ids", []),
			"avatar": _avatar_for_user_id(uid, community),
		}
	for uid, m in mapped.items():
		is_member = guild is not None and guild.get_member(uid) is not None
		if uid not in players and uid not in hidden_users and (uid in eligible_users or is_member):
			players[uid] = {
				"user_id": str(uid),
				"nick": next(iter(m["aoe2_names"]), str(uid)),
				"games": 0,
				"profile_ids": m["profile_ids"],
				"avatar": _avatar_for_user_id(uid, community),
			}
	return sorted(players.values(), key=lambda p: (-p["games"], p["nick"].lower()))[:500]


async def _mapped_player_identity(user_id):
	"""(profile_ids, aoe2_names) for a Discord user, via the identity resolver.
	aoe2_names feeds _civ_player_clause's fallback match on civ_picks rows
	recorded without a user_id (the un-linked lobby scrape — see
	nammaoe2bot.features.civs.sync.persist_lobby_civs)."""
	uid = int(user_id)
	profile_ids = (await resolver.profiles_for_users([uid])).get(uid, [])
	names = await resolver.names_for_profiles(profile_ids)
	return sorted(profile_ids), sorted({n.lower() for n in names.values() if n})


async def _community_user_ids(community):
	"""Discord users with stored participation/rating evidence in this tenant.

	Global identity bindings are not membership. This set is the gate used
	before a replay profile can become a clickable Discord user on a community
	page; otherwise an identity learned in guild A could expose that user id in
	guild B merely because the same AoE profile appeared there.
	"""
	rows = await db.fetchall(
		"SELECT DISTINCT tenant_users.user_id FROM ("
		"SELECT pm.user_id FROM match_players pm WHERE "
		+ _community_channel_predicate(community, "pm.channel_id")
		+ " UNION SELECT pr.user_id FROM player_ratings pr WHERE "
		+ _community_channel_predicate(community, "pr.channel_id")
		+ ") tenant_users")
	user_ids = {int(row["user_id"]) for row in rows or [] if row.get("user_id") is not None}
	# Current guild membership is equally strong evidence for identity-only
	# players who have linked before playing their first pickup match.
	guild = dc.get_guild(community.guild_id)
	for member in getattr(guild, "members", ()) or ():
		user_ids.add(int(member.id))
	return user_ids


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


async def _rating_deltas(community, period, user_ids=None):
	clauses = [_community_channel_predicate(community, "rh.channel_id")]
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
		"FROM rating_history rh" + where + " GROUP BY user_id",
		args)
	return {int(r["user_id"]): _rating_payload(r) for r in rows or []}


async def _rating_delta(community, period, user_id):
	return (await _rating_deltas(community, period, [user_id])).get(int(user_id), _rating_payload(None))


async def _rating_history(community, period, user_id):
	clauses = ["user_id=%s", _community_channel_predicate(community, "rh.channel_id")]
	args = [int(user_id)]
	start = _period_start(period)
	if start is not None:
		clauses.append("at >= %s")
		args.append(start)
	rows = await db.fetchall(
		"SELECT at, rating_before, rating_change FROM rating_history rh "
		"WHERE " + " AND ".join(clauses) + " ORDER BY at ASC, id ASC",
		args)
	out = []
	for idx, row in enumerate(rows or []):
		before = row.get("rating_before")
		change = row.get("rating_change")
		at = row.get("at")
		if before is None or change is None or at is None:
			continue
		if idx == 0:
			out.append({"at": at, "rating": int(before)})
		out.append({"at": at, "rating": int(before + change)})
	return out


def _impact_payload(row, group):
	scores = rs_scoring.impact_scores(row, group)
	return {
		"user_id": str(row["user_id"]) if row.get("user_id") is not None else None,
		"profile_id": str(row["profile_id"]) if row.get("profile_id") is not None else None,
		"nick": row.get("identity") or str(row.get("user_id") or ""),
		"civ": row.get("civ"),
		"team": row.get("team"),
		"impact_score": scores["impact"],
		"army_score": scores["army"],
		"eco_score": scores["eco"],
		"timing_score": scores["timing"],
		"early_eco_score": scores["early_eco"],
		"early_army_score": scores["early_army"],
		"recovery_score": scores["reboom"],
		"impact_tags": rs_scoring.impact_tag_names_with_fallback(scores, row)[:3],
	}


def _tag_meta(key, tag_type):
	if tag_type == "strategy":
		return {"key": key, "label": STRATEGY_TAG_LABELS.get(key, str(key or "").replace("_", " ").title()),
				"type": "strategy"}
	return {"key": key, "label": IMPACT_TAG_LABELS.get(key, key), "type": tag_type or "impact"}


async def _parsed_games_by_user(community, period, profile_to_user):
	at_clause, params = _period_filter(period)
	rows = await db.fetchall(
		"SELECT g.user_id, g.profile_id, COUNT(DISTINCT g.replay_match_id) AS games "
		"FROM replay_players g JOIN match_replays mr ON mr.replay_match_id=g.replay_match_id "
		f"AND mr.community_id={community.sql_id} "
		"JOIN matches m ON m.match_id=mr.match_id "
		"WHERE 1=1" + at_clause +
		" GROUP BY g.user_id, g.profile_id",
		params)
	out = {}
	for r in rows or []:
		uid = r.get("user_id") or profile_to_user.get(int(r["profile_id"])) if r.get("profile_id") else r.get("user_id")
		if uid is None:
			continue
		out[int(uid)] = out.get(int(uid), 0) + int(r.get("games") or 0)
	return out


def _empty_tag_row(uid, nick, avatar, tag_key, tag_type):
	meta = _tag_meta(tag_key, tag_type)
	return {
		"user_id": str(uid),
		"nick": nick or str(uid),
		"avatar": avatar,
		"tag_key": meta["key"],
		"tag_label": meta["label"],
		"tag_type": meta["type"],
		"games": 0,
		"tag_games": 0,
		"parsed_games": 0,
		"wins": 0,
		"losses": 0,
		"winrate": None,
		"tag_rate": 0,
		"avg_impact": None,
		"score": 0,
		"last_tagged_at": None,
	}


def _finish_tag_rows(rows_by_key, parsed_games):
	rows = []
	for row in rows_by_key.values():
		tag_games = int(row["tag_games"] or 0)
		decided = int(row["wins"] or 0) + int(row["losses"] or 0)
		row["games"] = tag_games
		row["parsed_games"] = int(parsed_games.get(int(row["user_id"]), row.get("parsed_games") or tag_games) or 0)
		row["winrate"] = _winrate(row["wins"], row["losses"]) if decided else None
		row["tag_rate"] = round(tag_games * 100 / row["parsed_games"], 1) if row["parsed_games"] else 0
		if row.pop("_impact_count", 0):
			row["avg_impact"] = round(row.pop("_impact_sum", 0) / tag_games, 1)
		else:
			row.pop("_impact_sum", None)
		row["score"] = tag_leaderboard_score(
			tag_games, row["wins"], row["losses"], row["tag_rate"], row.get("avg_impact"))
		rows.append(row)
	return sorted(rows, key=lambda r: (-r["score"], -r["tag_games"], -(r["winrate"] or 0), r["nick"].lower()))


def _label_for(mapped, user_id):
	"""The resolver's stored AoE2 name for a user, or None.

	Used as the leaderboard label in preference to the per-row `identity`, which
	is whatever that one game recorded: one stable name per player beats a label
	that changes with whichever row happened to be grouped first. Was a Discord
	nick from the old three-store union; `identities` holds in-game names only
	(see resolver.profiles_and_names_by_user for why that is the right shape).
	"""
	return next(iter(mapped.get(user_id, {}).get("aoe2_names", [])), None)


async def _strategy_tag_leaderboard(community, period, tag_key, mapped, profile_to_user, hidden_users):
	"""The strategy half of /api/leaderboard?mode=tags, from `game_labels`.

	`gl.kind` carries the filtering the old `key IN (...17 keys...)` list did by
	hand: the allowlist that decided what to store is the same one that decides
	what this reads, so the two cannot drift. The window is applied to the stored
	`played_at`, which is the match's own timestamp rather than the ingest's."""
	start = _period_start(period)
	args = [_STRATEGY_KIND]
	time_clause = ""
	if start is not None:
		time_clause = " AND gl.played_at >= %s"
		args.append(start)
	rows = await db.fetchall(
		"SELECT gs.profile_id AS profile_id, rp.identity AS identity, gl.label AS `key`, "
		"COUNT(*) AS games, SUM(gs.winner=1) AS wins, SUM(gs.winner=0) AS losses, "
		"MAX(gl.played_at) AS last_tagged_at "
		+ _LABEL_JOIN +
		"WHERE gl.kind=%s AND " + _community_replay_predicate(community, "gl.replay_match_id")
		+ time_clause +
		" GROUP BY gs.profile_id, rp.identity, gl.label",
		args)
	out = {}
	available = {}
	for r in rows or []:
		key = r.get("key")
		profile_id = r.get("profile_id")
		uid = profile_to_user.get(int(profile_id)) if profile_id is not None else None
		if uid is None or uid in hidden_users:
			continue
		available.setdefault(key, {**_tag_meta(key, "strategy"), "games": 0})["games"] += int(r.get("games") or 0)
		if tag_key != "all" and key != tag_key:
			continue
		row_key = (uid, key, "strategy")
		cur = out.setdefault(row_key, _empty_tag_row(
			uid, _label_for(mapped, uid) or r.get("identity"),
			_avatar_for_user_id(uid, community), key, "strategy"))
		cur["tag_games"] += int(r.get("games") or 0)
		cur["wins"] += int(r.get("wins") or 0)
		cur["losses"] += int(r.get("losses") or 0)
		cur["last_tagged_at"] = max(cur["last_tagged_at"] or 0, int(r.get("last_tagged_at") or 0)) or None
	return list(available.values()), out


async def _stored_tag_leaderboard(
		community, period, tag_key, mapped, profile_to_user, hidden_users, eligible_users):
	start = _period_start(period)
	params = []
	time_clause = ""
	if start is not None:
		time_clause = " AND t.played_at >= %s"
		params.append(start)
	rows = await db.fetchall(
		"SELECT t.user_id, t.profile_id, t.identity, t.tag, MAX(t.tag_label) AS tag_label, "
		"MAX(t.category) AS category, COUNT(*) AS games, SUM(t.winner=1) AS wins, "
		"SUM(t.winner=0) AS losses, AVG(t.score) AS avg_score, MAX(t.played_at) AS last_tagged_at "
		"FROM rs_player_game_tags t WHERE "
		+ _community_replay_predicate(community, "t.aoe2_match_id") + time_clause +
		" GROUP BY t.user_id, t.profile_id, t.identity, t.tag",
		params)
	out = {}
	available = {}
	for r in rows or []:
		uid = r.get("user_id") or profile_to_user.get(int(r["profile_id"])) if r.get("profile_id") else r.get("user_id")
		if uid is None or int(uid) in hidden_users or int(uid) not in eligible_users:
			continue
		key = r.get("tag")
		tag_type = r.get("category") or "impact"
		meta = _tag_meta(key, tag_type)
		meta["label"] = r.get("tag_label") or meta["label"]
		available.setdefault(key, {**meta, "games": 0})["games"] += int(r.get("games") or 0)
		if tag_key != "all" and key != tag_key:
			continue
		row_key = (int(uid), key, tag_type)
		cur = out.setdefault(row_key, _empty_tag_row(
			int(uid), _label_for(mapped, int(uid)) or r.get("identity"),
			_avatar_for_user_id(uid, community), key, tag_type))
		cur["tag_label"] = meta["label"]
		cur["tag_games"] += int(r.get("games") or 0)
		cur["wins"] += int(r.get("wins") or 0)
		cur["losses"] += int(r.get("losses") or 0)
		cur["last_tagged_at"] = max(cur["last_tagged_at"] or 0, int(r.get("last_tagged_at") or 0)) or None
		if r.get("avg_score") is not None:
			cur["_impact_sum"] = cur.get("_impact_sum", 0) + (float(r["avg_score"]) * int(r.get("games") or 0))
			cur["_impact_count"] = cur.get("_impact_count", 0) + int(r.get("games") or 0)
	return list(available.values()), out


async def _tag_leaderboard(community, period, tag_key="all"):
	# Same single source as the player directory: the tag leaderboards join
	# replay-derived rows (keyed on profile_id) back to Discord users, which is
	# the identity resolver's whole job.
	eligible_users = await _community_user_ids(community)
	mapped = {
		int(uid): data for uid, data in (await resolver.profiles_and_names_by_user()).items()
		if int(uid) in eligible_users
	}
	profile_to_user = {}
	for uid, data in mapped.items():
		for pid in data.get("profile_ids") or []:
			profile_to_user[int(pid)] = int(uid)
	hidden_rows = await db.fetchall(
		"SELECT DISTINCT pr.user_id FROM player_ratings pr WHERE pr.is_hidden=1 AND "
		+ _community_channel_predicate(community, "pr.channel_id"))
	hidden_users = {int(r["user_id"]) for r in hidden_rows or []}
	parsed_games = await _parsed_games_by_user(community, period, profile_to_user)
	strategy_tags, rows_by_key = await _strategy_tag_leaderboard(
		community, period, tag_key, mapped, profile_to_user, hidden_users)
	stored_tags, stored_rows = await _stored_tag_leaderboard(
		community, period, tag_key, mapped, profile_to_user, hidden_users, eligible_users)
	for k, r in stored_rows.items():
		rows_by_key[k] = r
	tags = {}
	for tag in strategy_tags + stored_tags:
		tags[(tag["type"], tag["key"])] = tag
	# Coverage fallbacks ('role'/'data' categories) fire on almost every game,
	# so they sort last and never win the default pick — the default leaderboard
	# should show a rare, high-signal tag, not "Partial replay".
	fallback_types = ("role", "data")
	tag_options = sorted(tags.values(), key=lambda t: (t["type"] in fallback_types, t["type"], t["label"]))
	selected_tag = tag_key
	if selected_tag in (None, "", "all"):
		selected_tag = tag_options[0]["key"] if tag_options else ""
	if selected_tag:
		rows_by_key = {k: v for k, v in rows_by_key.items() if k[1] == selected_tag}
	rows = _finish_tag_rows(rows_by_key, parsed_games)
	return {
		"tag": selected_tag,
		"tags": tag_options,
		"rows": rows,
	}


def _avg_impact(impacts, key):
	vals = [float(i[key]) for i in impacts if i.get(key) is not None]
	return round(sum(vals) / len(vals), 1) if vals else None


def _num(v, digits=1):
	if v is None:
		return None
	return round(float(v), digits)


def _identity_clause(user_id, profile_ids, alias="g"):
	clauses = [f"{alias}.user_id=%s"]
	args = [user_id]
	if profile_ids:
		clauses.append(f"{alias}.profile_id IN (" + ",".join(["%s"] * len(profile_ids)) + ")")
		args.extend(profile_ids)
	return "(" + " OR ".join(clauses) + ")", args


def _phase_minutes(seconds):
	return None if seconds is None else round(float(seconds) / 60, 1)


_ECO_TECHS = {
	"Loom", "Wheelbarrow", "Hand Cart",
	"Double-Bit Axe", "Bow Saw", "Two-Man Saw",
	"Horse Collar", "Heavy Plow", "Crop Rotation",
	"Gold Mining", "Gold Shaft Mining", "Stone Mining", "Stone Shaft Mining",
}


_ARMY_TECH_HINTS = (
	"Fletching", "Bodkin Arrow", "Bracer",
	"Forging", "Iron Casting", "Blast Furnace",
	"Bloodlines", "Husbandry",
	"Scale Barding Armor", "Chain Barding Armor", "Plate Barding Armor",
	"Padded Archer Armor", "Leather Archer Armor", "Ring Archer Armor",
	"Scale Mail Armor", "Chain Mail Armor", "Plate Mail Armor",
	"Ballistics", "Chemistry", "Thumb Ring", "Conscription",
)


def _cat_label(cat):
	labels = {
		"archer_line": "archers",
		"cav_archer": "cavalry archers",
		"elephant": "elephants",
		"knight_line": "knights",
		"militia_line": "infantry",
		"monk": "monks",
		"scout": "scouts",
		"siege": "siege",
		"skirmisher": "skirmishers",
		"spearman_line": "spears",
		"unique_other": "unique units",
	}
	return labels.get(cat, str(cat or "").replace("_", " "))


async def _player_strategy_profile(community, user_id, profile_ids, period):
	at_clause, at_params = _period_filter(period)
	identity_clause, identity_args = _identity_clause(user_id, profile_ids)
	args = [*identity_args, *at_params]
	base_join = (
		"FROM replay_players g JOIN match_replays mr ON mr.replay_match_id=g.replay_match_id "
		f"AND mr.community_id={community.sql_id} "
		"JOIN matches m ON m.match_id=mr.match_id "
		"WHERE " + identity_clause + at_clause
	)
	summary = await db.fetchone(
		"SELECT COUNT(DISTINCT g.replay_match_id) AS games, "
		"AVG(g.villagers) AS avg_villagers, AVG(g.vil_pre_castle) AS avg_vil_pre_castle, "
		"AVG(g.military) AS avg_military, AVG(g.mil_pre_castle) AS avg_mil_pre_castle, "
		"AVG(g.castle_s) AS avg_castle_s, AVG(g.imperial_s) AS avg_imperial_s, "
		"SUM(g.vil_pre_castle >= 30 AND g.mil_pre_castle <= 6) AS quiet_boom_games "
		+ base_join,
		args)
	if not summary or not summary.get("games"):
		return {"games": 0, "summary": "No parsed strategy sample yet.", "army_mix": [], "top_units": [], "eco_techs": [], "army_techs": []}
	unit_join = (
		"FROM replay_players g JOIN match_replays mr ON mr.replay_match_id=g.replay_match_id "
		f"AND mr.community_id={community.sql_id} "
		"JOIN matches m ON m.match_id=mr.match_id "
		"JOIN replay_units u ON u.replay_match_id=g.replay_match_id AND u.player_number=g.player_number "
		"WHERE " + identity_clause + at_clause + " AND u.is_military=1 AND u.total>0 "
	)
	army_mix = await db.fetchall(
		"SELECT u.category, COUNT(DISTINCT u.replay_match_id) AS games, SUM(u.total) AS total, "
		"SUM(u.pre_castle) AS pre_castle "
		+ unit_join +
		"GROUP BY u.category ORDER BY total DESC LIMIT 8",
		args)
	top_units = await db.fetchall(
		"SELECT u.unit, u.category, COUNT(DISTINCT u.replay_match_id) AS games, SUM(u.total) AS total, "
		"SUM(u.pre_castle) AS pre_castle "
		+ unit_join +
		"GROUP BY u.unit, u.category ORDER BY total DESC LIMIT 8",
		args)
	tech_join = (
		"FROM replay_players g JOIN match_replays mr ON mr.replay_match_id=g.replay_match_id "
		f"AND mr.community_id={community.sql_id} "
		"JOIN matches m ON m.match_id=mr.match_id "
		"JOIN replay_techs t ON t.replay_match_id=g.replay_match_id AND t.player_number=g.player_number "
		"WHERE " + identity_clause + at_clause + " "
	)
	techs = await db.fetchall(
		"SELECT t.tech, t.phase, COUNT(DISTINCT t.replay_match_id) AS games, AVG(t.click_s) AS avg_click_s "
		+ tech_join +
		"GROUP BY t.tech, t.phase ORDER BY games DESC LIMIT 120",
		args)
	games = int(summary.get("games") or 0)
	quiet_boom_games = int(summary.get("quiet_boom_games") or 0)
	eco_techs = [r for r in techs or [] if r.get("tech") in _ECO_TECHS]
	army_techs = [r for r in techs or [] if r.get("tech") in _ARMY_TECH_HINTS]
	eco_techs = sorted(eco_techs, key=lambda r: (-int(r.get("games") or 0), float(r.get("avg_click_s") or 999999)))[:8]
	army_techs = sorted(army_techs, key=lambda r: (-int(r.get("games") or 0), float(r.get("avg_click_s") or 999999)))[:8]
	top_mix = [
		{
			"category": _cat_label(r.get("category")),
			"games": int(r.get("games") or 0),
			"total": int(r.get("total") or 0),
			"pre_castle": int(r.get("pre_castle") or 0),
		}
		for r in army_mix or []
	]
	unit_mix = [
		{
			"unit": r.get("unit"),
			"category": _cat_label(r.get("category")),
			"games": int(r.get("games") or 0),
			"total": int(r.get("total") or 0),
			"pre_castle": int(r.get("pre_castle") or 0),
		}
		for r in top_units or []
	]
	return {
		"games": games,
		"avg_villagers": _num(summary.get("avg_villagers")),
		"avg_pre_castle_villagers": _num(summary.get("avg_vil_pre_castle")),
		"avg_military": _num(summary.get("avg_military")),
		"avg_pre_castle_military": _num(summary.get("avg_mil_pre_castle")),
		"avg_castle_min": _phase_minutes(summary.get("avg_castle_s")),
		"avg_imperial_min": _phase_minutes(summary.get("avg_imperial_s")),
		"quiet_boom_games": quiet_boom_games,
		"quiet_boom_rate": round(100 * quiet_boom_games / games, 1) if games else 0,
		"army_mix": top_mix,
		"top_units": unit_mix,
		"eco_techs": [
			{"tech": r.get("tech"), "phase": r.get("phase"), "games": int(r.get("games") or 0), "avg_min": _phase_minutes(r.get("avg_click_s"))}
			for r in eco_techs
		],
		"army_techs": [
			{"tech": r.get("tech"), "phase": r.get("phase"), "games": int(r.get("games") or 0), "avg_min": _phase_minutes(r.get("avg_click_s"))}
			for r in army_techs
		],
	}


def _strategy_label(key):
	""" A stored strategy key as a display name.

	The hand-written map first, then the same `archer_rush` -> `Archer Rush`
	fallback nammaoe2bot/features/scouting/report.py and nammaoe2bot/ingest/card_query.py apply.
	The fallback is what makes this safe against a new classifier key: it renders
	as its own name rather than as nothing at all. """
	return STRATEGY_TAG_LABELS.get(key, str(key or "").replace("_", " ").title())


def _strategy_tag_payload(row):
	key = row.get("key")
	games = int(row.get("games") or 0)
	wins = int(row.get("wins") or 0)
	losses = int(row.get("losses") or 0)
	return {
		"key": key,
		"label": _strategy_label(key),
		"category": "strategy",
		"type": "strategy",
		"games": games,
		"wins": wins,
		"losses": losses,
		"winrate": _winrate(wins, losses),
		"avg_impact": None,
	}


async def _player_strategy_tags(community, profile_ids, period, limit=24):
	"""One player's strategy labels in the selected window, from `game_labels`.

	Deliberately NOT read out of that player's `player_rollups` blob, which
	carries the same three splits: the rollup is a lifetime, community-scoped
	aggregate with no time dimension, and this page has a period selector. Serving
	lifetime numbers under a "3 months" filter is the class of quiet lie this
	migration exists to remove. The rollup drives the scouting-report block
	instead, where it is labelled as what it is."""
	profile_ids = [str(p) for p in profile_ids or []]
	if not profile_ids:
		return []
	start = _period_start(period)
	args = [_STRATEGY_KIND, *profile_ids]
	time_clause = ""
	if start is not None:
		time_clause = " AND gl.played_at >= %s"
		args.append(start)
	rows = await db.fetchall(
		"SELECT gl.label AS `key`, COUNT(*) AS games, "
		"SUM(gs.winner=1) AS wins, SUM(gs.winner=0) AS losses "
		+ _LABEL_JOIN +
		"WHERE gl.kind=%s AND " + _community_replay_predicate(community, "gl.replay_match_id")
		+ " AND gs.profile_id IN (" + ",".join(["%s"] * len(profile_ids)) + ")"
		+ time_clause +
		" GROUP BY gl.label ORDER BY games DESC, wins DESC, gl.label LIMIT %s",
		[*args, limit])
	return [_strategy_tag_payload(r) for r in rows or []]


async def _player_stored_tags(community, profile_ids, period, limit=12):
	profile_ids = [str(p) for p in profile_ids or []]
	if not profile_ids:
		return []
	start = _period_start(period)
	args = [*profile_ids]
	time_clause = ""
	if start is not None:
		time_clause = " AND played_at >= %s"
		args.append(start)
	rows = await db.fetchall(
		"SELECT tag AS `key`, MAX(tag_label) AS label, MAX(category) AS category, "
		"COUNT(*) AS games, SUM(winner=1) AS wins, SUM(winner=0) AS losses, AVG(score) AS avg_impact "
		"FROM rs_player_game_tags t WHERE "
		+ _community_replay_predicate(community, "t.aoe2_match_id")
		+ " AND profile_id IN (" + ",".join(["%s"] * len(profile_ids)) + ")" +
		time_clause + " GROUP BY tag ORDER BY games DESC, wins DESC, tag LIMIT %s",
		[*args, limit])
	return [
		{
			"key": r.get("key"),
			"label": r.get("label") or r.get("key"),
			"category": r.get("category"),
			"type": r.get("category") or "impact",
			"games": int(r.get("games") or 0),
			"wins": int(r.get("wins") or 0),
			"losses": int(r.get("losses") or 0),
			"winrate": _winrate(r.get("wins"), r.get("losses")),
			"avg_impact": _num(r.get("avg_impact")),
		}
		for r in rows or []
	]


async def _player_profile_tags(community, profile_ids, period):
	stored = await _player_stored_tags(community, profile_ids, period)
	strategy = await _player_strategy_tags(community, profile_ids, period)
	out = []
	seen = set()
	for tag in stored + strategy:
		key = tag.get("key")
		tag_type = tag.get("type") or tag.get("category") or "tag"
		if not key or (tag_type, key) in seen:
			continue
		seen.add((tag_type, key))
		out.append(tag)
	return sorted(out, key=lambda t: (-int(t.get("games") or 0), -(t.get("winrate") or 0), str(t.get("label") or "")))[:24]


async def _classification_tags_for_bot_matches(community, match_ids):
	"""Per-(match, player) chips for the match rows: strategy labels from
	`game_labels`, plus the stored impact tags.

	The bot-match -> replay hop goes through the community-owned `match_replays`
	link. Besides surviving the stage-6 removal of `replay_matches.bot_match_id`,
	that makes it impossible for an otherwise matching replay from another
	community to contribute a chip."""
	match_ids = [m for m in dict.fromkeys(match_ids or []) if m is not None]
	if not match_ids:
		return {}, {}
	rows = await db.fetchall(
		"SELECT mr.match_id AS bot_match_id, gs.profile_id AS profile_id, rp.identity AS identity, "
		"gl.label AS `key` "
		"FROM match_replays mr "
		"JOIN game_labels gl ON gl.replay_match_id=mr.replay_match_id "
		"JOIN game_stats gs ON gs.replay_match_id=gl.replay_match_id "
		"AND gs.player_number=gl.player_number "
		"LEFT JOIN replay_players rp ON rp.replay_match_id=gs.replay_match_id "
		"AND rp.profile_id=gs.profile_id "
		f"WHERE mr.community_id={community.sql_id} AND mr.match_id IN ("
		+ ",".join(["%s"] * len(match_ids)) + ") AND gl.kind=%s",
		[*match_ids, _STRATEGY_KIND])
	by_profile = {}
	by_name = {}
	for row in rows or []:
		tag = {"key": row.get("key"), "label": _strategy_label(row.get("key"))}
		if row.get("profile_id") is not None:
			by_profile.setdefault((row["bot_match_id"], str(row["profile_id"])), []).append(tag)
		if row.get("identity"):
			by_name.setdefault((row["bot_match_id"], str(row["identity"]).lower()), []).append(tag)
	stored_rows = await db.fetchall(
		"SELECT mr.match_id AS bot_match_id, t.profile_id, t.identity, t.tag, t.tag_label "
		"FROM match_replays mr JOIN rs_player_game_tags t ON t.aoe2_match_id=mr.replay_match_id "
		f"WHERE mr.community_id={community.sql_id} AND mr.match_id IN ("
		+ ",".join(["%s"] * len(match_ids)) + ")",
		match_ids)
	for row in stored_rows or []:
		tag = {"key": row.get("tag"), "label": row.get("tag_label") or row.get("tag")}
		if row.get("profile_id") is not None:
			by_profile.setdefault((row["bot_match_id"], str(row["profile_id"])), []).append(tag)
		if row.get("identity"):
			by_name.setdefault((row["bot_match_id"], str(row["identity"]).lower()), []).append(tag)
	return by_profile, by_name


def _player_impact_profile(impacts, civs=None, durations=None):
	"""The per-match impact scores this page shows, averaged over the window.

	These are render-time numbers computed from the match's own replay_players
	rows (nammaoe2bot/ingest/scoring.py) — the same component scores the match
	cards compute, never stored, and explicitly kept that way by the design.

	What stage 5d removed from this dict is the NARRATION built on top of them:
	the generated persona (name/epithet/tagline) and the `scout_report` prose.
	Stage 5a deleted both from `/rank` for asserting personality where they had
	arithmetic, and the player page's measured facts now come from
	`player_rollups` through _scouting_payload below. Do not reintroduce either
	here — a sentence generated from four averages reads as a finding, and it
	is not one."""
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
			"impact_sd": None,
			"carry_rate": None,
			"top_tags": [],
			"best_civs": [],
			"duration_edges": [],
		}

	tag_counts = {}
	for impact in impacts:
		for tag in impact.get("impact_tags") or []:
			# Coverage fallbacks fire on nearly every untagged game by design;
			# counting them here would bury the rare, distinguishing tags that
			# top_tags (and the style shortcut below) exist to surface.
			if tag in rs_scoring.FALLBACK_TAG_NAMES:
				continue
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
	top_tag = top_tags[0]["tag"] if top_tags else None
	if top_tag == "Boom carry" and (avg_eco or 0) >= 56:
		style = "Boom carry"
	elif top_component == "Army" and top_score >= 58 and top_score >= scores["Eco"] + 5:
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
	impact_scores = [i["impact_score"] for i in impacts if i.get("impact_score") is not None]
	impact_sd = None
	if len(impact_scores) >= 2:
		mean = sum(impact_scores) / len(impact_scores)
		impact_sd = round((sum((x - mean) ** 2 for x in impact_scores) / len(impact_scores)) ** 0.5, 1)
	carry_rate = round(100 * sum(1 for i in impacts if i.get("team_top")) / len(impacts))
	return {
		"style": style,
		"summary": "; ".join(summary_bits),
		"matches": len(impacts),
		"avg_impact": avg_impact,
		"avg_army": avg_army,
		"avg_eco": avg_eco,
		"avg_timing": avg_timing,
		"avg_recovery": avg_recovery,
		"impact_sd": impact_sd,
		"carry_rate": carry_rate,
		"top_tags": top_tags,
		"best_civs": best_civs,
		"duration_edges": duration_edges,
	}


def _scouting_payload(rollup):
	""" One player's measured scouting report as data, from their
	`player_rollups` blob — or the pending sentinel when they have no row.

	Pure, and deliberately the web's own shaping of the same blob
	nammaoe2bot/features/scouting/report.py renders for `/rank`. The copy rules are that module's,
	restated here because they are the point rather than a formatting detail:

	  * NO ROW -> exactly PENDING, imported from scouting_report so the two
	    surfaces cannot drift into two slightly different sentences. An unlinked
	    player has no row at all rather than a row of zeros (identity v2 §5), so
	    the absence IS the signal, and a payload of zeros here would be a report
	    of a measurement nobody took.
	  * EVERY NUMBER CARRIES ITS OWN SAMPLE. medal rates ship with
	    games_ranked, each eAPM median with its own count, each split with its
	    own games — the denominators genuinely differ and only naming them makes
	    that legible.
	  * A MISSING PIECE IS OMITTED, NEVER ZEROED. `peak_eapm` is NULL on every
	    production row today, so median_peak is null and games_peak is 0: the
	    peak keys are absent from the payload rather than shipped as a null the
	    page would have to render as a blank, an em-dash, or a 0. When buckets
	    start arriving they appear on their own, with their own count.
	  * THE SPLITS ARE ALREADY FLOORED. compute_rollup drops a split below
	    SPLIT_MIN_GAMES from the blob entirely, so there is no low-sample flag to
	    pass through and there must never be one. A split's `games` can also be
	    smaller than the player's total games (unresolved outcomes count as games
	    played but are excluded from splits) — that is correct and must not be
	    reconciled.

	`losses` is derived as games - wins rather than stored: inside a split both
	describe the same set of RESOLVED games, so the subtraction is exact. """
	if rollup is None:
		return {"pending": scouting_report.PENDING}

	medals = rollup.get("medal_rates") or {}
	military, villager = medals.get("military"), medals.get("villager")
	ranked = medals.get("games_ranked") or 0
	medal_payload = None
	if ranked >= scouting_report.MIN_GAMES and military is not None and villager is not None:
		# PER GAME, not a percentage, matching `/rank` exactly. The two are the
		# same number — a player holds at most one military medal in a game — so
		# this is a framing choice, and it is made in one place so the card and
		# the Discord report cannot end up quoting the same figure two ways.
		medal_payload = {
			"military_per_game": military,
			"villager_per_game": villager,
			"games_ranked": ranked,
		}

	apm = rollup.get("apm") or {}
	median_avg, games_avg = apm.get("median_avg"), apm.get("games_avg") or 0
	median_peak, games_peak = apm.get("median_peak"), apm.get("games_peak") or 0
	apm_payload = None
	if median_avg is not None and games_avg >= scouting_report.MIN_GAMES:
		apm_payload = {"median_avg": median_avg, "games_avg": games_avg}
		if median_peak is not None and games_peak >= scouting_report.MIN_GAMES:
			apm_payload["median_peak"] = median_peak
			apm_payload["games_peak"] = games_peak

	def split(block, key_field, label):
		out = []
		for row in rollup.get(block) or []:
			games, wins = row.get("games") or 0, row.get("wins") or 0
			out.append({
				"key": row.get(key_field),
				"label": label(row.get(key_field)),
				"games": games,
				"wins": wins,
				"losses": games - wins,
				"winrate": _winrate(wins, games - wins),
			})
		return out

	return {
		"pending": None,
		# What span every figure below covers, so the page can say so. None on a
		# lifetime rollup. Shipped rather than hardcoded in the page for the same
		# reason it is stored in the blob at all: a card captioned "last 60 days"
		# over a 30-day rollup is wrong in the one way none of its numbers show.
		"window_days": rollup.get("window_days"),
		"window_games": (rollup.get("baseline") or {}).get("games") or 0,
		"medals": medal_payload,
		"apm": apm_payload,
		# THE CHOSEN CLAUSES, PICKED IN PYTHON. Which strategy is a player's
		# strength is a shrunk-rate calculation against their own baseline
		# (nammaoe2bot/features/scouting/report.highlights), not "the first row of the list" — so
		# the page is handed the answer instead of re-deriving it in JavaScript,
		# where a second copy of the shrinkage would drift silently and both
		# surfaces would still look entirely plausible.
		"highlights": scouting_report.highlights(rollup),
		# The full ranked lists stay, for the page's own table and spawn read.
		# The `spawn_` prefix is stripped for display only — the label already
		# says spawn, so the prefix would render twice.
		"strategies": split("strategies", "key", _strategy_label),
		"spawns": split("spawns", "key", lambda k: str(k or "").removeprefix("spawn_").replace("_", " ").title()),
		"units": split("units", "unit", lambda u: str(u or "")),
	}


async def _player_scouting_report(user_id, community=None):
	""" The scouting-report block for `user_id`, or None when there is no
	community to read one from.

	Three outcomes, the same three nammaoe2bot/discord/commands/stats.py's _scouting_report
	distinguishes, because they are three different statements:

	  no community resolved -> None. Nothing was ever measured here; the page
	    omits the block rather than claiming a linking gap that does not exist.
	  no rollup row         -> {"pending": PENDING}.
	  a rollup              -> its measured blocks. """
	community = community or await _public_community()
	if community is None:
		return None
	return _scouting_payload(await rollups.fetch(community.community_id, int(user_id)))


def _relationship_payload(row, community):
	if not row:
		return None
	wins = int(row.get("wins") or 0)
	losses = int(row.get("losses") or 0)
	return {
		"user_id": str(row["user_id"]),
		"nick": row["nick"] or str(row["user_id"]),
		"games": int(row.get("games") or 0),
		"wins": wins,
		"losses": losses,
		"winrate": _winrate(wins, losses),
		"avatar": _avatar_for_user_id(row["user_id"], community),
	}


def _best_relationship(rows, kind, community):
	"""Top pick for a relation quadrant. ``kind``: 'ally' (highest winrate
	together), 'worst_ally' (lowest winrate together), 'enemy' (they beat you
	the most), 'easy_enemy' (you beat them the most)."""
	payloads = [_relationship_payload(r, community) for r in rows or []]
	payloads = [p for p in payloads if p]
	if not payloads:
		return None
	qualified = [p for p in payloads if p["games"] >= 10] or [p for p in payloads if p["games"] >= 2] or payloads
	if kind == "ally":
		return sorted(qualified, key=lambda p: (-(p["winrate"] or 0), -p["wins"], -p["games"], p["nick"]))[0]
	if kind == "worst_ally":
		# The new quadrants use hard gates (10+ games, winrate actually leaning
		# that way) so the summary card never names someone the list below
		# rejects — return None instead of falling back to a 50-50 pairing.
		gated = [p for p in payloads if p["games"] >= 10 and p["winrate"] is not None and p["winrate"] <= 45]
		if not gated:
			return None
		return sorted(gated, key=lambda p: (p["winrate"], -p["losses"], -p["games"], p["nick"]))[0]
	if kind == "easy_enemy":
		gated = [p for p in payloads if p["games"] >= 10 and p["winrate"] is not None and p["winrate"] >= 55]
		if not gated:
			return None
		return sorted(gated, key=lambda p: (-p["winrate"], -p["wins"], -p["games"], p["nick"]))[0]
	# 'enemy': relative to the focus player, low winrate = they beat you.
	return sorted(
		qualified,
		key=lambda p: (p["winrate"] if p["winrate"] is not None else 101, -p["losses"], -p["games"], p["nick"])
	)[0]


async def _match_impacts(community, match_ids, focus_user_id=None, focus_profile_ids=None):
	match_ids = [m for m in dict.fromkeys(match_ids or []) if m is not None]
	if not match_ids:
		return {}
	hidden_rows = await db.fetchall(
		"SELECT DISTINCT pr.user_id FROM player_ratings pr WHERE pr.is_hidden=1 AND "
		+ _community_channel_predicate(community, "pr.channel_id"))
	hidden_users = {int(r["user_id"]) for r in hidden_rows or []}
	rows = await db.fetchall(
		"SELECT mr.match_id AS bot_match_id, g.profile_id, scoped_pm.user_id, g.identity, g.civ, g.team, "
		"g.villagers, g.vil_pre_castle, g.vil_pre_imperial, "
		"g.military, g.mil_pre_castle, g.mil_pre_imperial, g.feudal_s, g.castle_s, g.imperial_s "
		"FROM match_replays mr JOIN matches scoped_m ON scoped_m.match_id=mr.match_id "
		"JOIN replay_players g ON g.replay_match_id=mr.replay_match_id "
		"LEFT JOIN match_players scoped_pm ON scoped_pm.match_id=mr.match_id "
		"AND scoped_pm.channel_id=scoped_m.channel_id AND scoped_pm.user_id=g.user_id "
		f"WHERE mr.community_id={community.sql_id} AND "
		+ _community_channel_predicate(community, "scoped_m.channel_id") + " AND mr.match_id IN ("
		+ ",".join(["%s"] * len(match_ids)) + ")",
		match_ids)
	strategy_by_profile, strategy_by_name = await _classification_tags_for_bot_matches(community, match_ids)
	groups = {}
	for r in rows or []:
		groups.setdefault(r["bot_match_id"], []).append(r)
	focus_profiles = {int(p) for p in focus_profile_ids or []}
	out = {}
	for match_id, group in groups.items():
		# Score everyone first (hidden players included) so the team-top flag
		# reflects who actually led the team, then filter what we return.
		scored = []
		for row in group:
			payload = _impact_payload(row, group)
			scored.append((row, payload))
		by_team = {}
		for _row, payload in scored:
			if payload.get("team") is not None:
				by_team.setdefault(payload["team"], []).append(payload)
		for members in by_team.values():
			# carry_sort_key adds a nick tiebreak so exact ties don't flip
			# with DB row order between requests.
			top = min(members, key=rs_scoring.carry_sort_key)
			top["team_top"] = True
		payloads = []
		for row, payload in scored:
			row_user_id = row.get("user_id")
			if row_user_id is not None and int(row_user_id) in hidden_users:
				continue
			if focus_user_id is not None:
				if row_user_id != focus_user_id and int(row.get("profile_id") or 0) not in focus_profiles:
					continue
			elif row_user_id is None:
				continue
			payload["strategy_tags"] = (
				strategy_by_profile.get((match_id, str(row.get("profile_id"))))
				or strategy_by_name.get((match_id, str(row.get("identity") or "").lower()))
				or []
			)[:3]
			payloads.append(payload)
		if payloads:
			out[match_id] = max(payloads, key=lambda p: p["impact_score"])
	return out


async def _match_player_impacts(community, match_ids):
	match_ids = [m for m in dict.fromkeys(match_ids or []) if m is not None]
	if not match_ids:
		return {}
	hidden_rows = await db.fetchall(
		"SELECT DISTINCT pr.user_id FROM player_ratings pr WHERE pr.is_hidden=1 AND "
		+ _community_channel_predicate(community, "pr.channel_id"))
	hidden_users = {int(r["user_id"]) for r in hidden_rows or []}
	rows = await db.fetchall(
		"SELECT mr.match_id AS bot_match_id, g.profile_id, scoped_pm.user_id, g.identity, g.civ, g.team, "
		"g.villagers, g.vil_pre_castle, g.vil_pre_imperial, g.military, g.mil_pre_castle, g.mil_pre_imperial, "
		"g.feudal_s, g.castle_s, g.imperial_s "
		"FROM match_replays mr JOIN matches scoped_m ON scoped_m.match_id=mr.match_id "
		"JOIN replay_players g ON g.replay_match_id=mr.replay_match_id "
		"LEFT JOIN match_players scoped_pm ON scoped_pm.match_id=mr.match_id "
		"AND scoped_pm.channel_id=scoped_m.channel_id AND scoped_pm.user_id=g.user_id "
		f"WHERE mr.community_id={community.sql_id} AND "
		+ _community_channel_predicate(community, "scoped_m.channel_id") + " AND mr.match_id IN ("
		+ ",".join(["%s"] * len(match_ids)) + ")",
		match_ids)
	strategy_by_profile, strategy_by_name = await _classification_tags_for_bot_matches(community, match_ids)
	groups = {}
	for r in rows or []:
		groups.setdefault(r["bot_match_id"], []).append(r)
	out = {}
	for match_id, group in groups.items():
		payloads = []
		for row in group:
			row_user_id = row.get("user_id")
			if row_user_id is not None and int(row_user_id) in hidden_users:
				continue
			payload = _impact_payload(row, group)
			payload["strategy_tags"] = (
				strategy_by_profile.get((match_id, str(row.get("profile_id"))))
				or strategy_by_name.get((match_id, str(row.get("identity") or "").lower()))
				or []
			)[:3]
			payloads.append(payload)
		out[match_id] = sorted(payloads, key=lambda p: (str(p.get("team") or ""), -(p.get("impact_score") or 0), p.get("nick") or ""))
	return out


async def _match_rosters(community, match_ids):
	match_ids = [m for m in dict.fromkeys(match_ids or []) if m is not None]
	if not match_ids:
		return {}
	placeholder = ",".join(["%s"] * len(match_ids))
	impacts = await _match_player_impacts(community, match_ids)
	impact_by_user = {}
	impact_by_name = {}
	for match_id, rows in impacts.items():
		for impact in rows:
			if impact.get("user_id"):
				impact_by_user[(match_id, str(impact["user_id"]))] = impact
			if impact.get("nick"):
				impact_by_name[(match_id, impact["nick"].lower())] = impact
	civ_rows = await db.fetchall(
		"SELECT bot_match_id, user_id, nick, team, civ, result FROM civ_picks "
		"WHERE bot_match_id IN (" + placeholder + ") AND "
		+ _community_channel_predicate(community, "civ_picks.channel_id"),
		match_ids)
	civs_by_user = {}
	civs_by_name = {}
	for row in civ_rows or []:
		match_id = row["bot_match_id"]
		if row.get("user_id") is not None:
			civs_by_user[(match_id, str(row["user_id"]))] = row
		if row.get("nick"):
			civs_by_name[(match_id, row["nick"].lower())] = row
		if row.get("aoe2_name"):
			civs_by_name[(match_id, row["aoe2_name"].lower())] = row
	players = await db.fetchall(
		"SELECT pm.match_id, pm.user_id, MAX(pm.nick) AS nick, pm.team, MAX(m.winner) AS winner "
		"FROM match_players pm JOIN matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"WHERE pm.match_id IN (" + placeholder + ") AND "
		+ _community_channel_predicate(community, "pm.channel_id")
		+ _visible_user_clause("pm", community) +
		" GROUP BY pm.match_id, pm.user_id, pm.team ORDER BY pm.match_id, pm.team, nick",
		match_ids)
	out = {match_id: [] for match_id in match_ids}
	seen = set()
	for row in players or []:
		match_id = row["match_id"]
		user_id = str(row["user_id"])
		nick = row["nick"] or user_id
		civ = civs_by_user.get((match_id, user_id)) or civs_by_name.get((match_id, nick.lower())) or {}
		impact = impact_by_user.get((match_id, user_id)) or impact_by_name.get((match_id, nick.lower()))
		result = civ.get("result")
		if result is None and row.get("winner") is not None:
			result = "W" if row["winner"] == row["team"] else "L"
		payload = {
			"user_id": user_id,
			"nick": nick,
			"avatar": _avatar_for_user_id(user_id, community),
			"team": row["team"],
			"civ": civ.get("civ") or (impact or {}).get("civ"),
			"result": result,
			"impact": impact,
		}
		out.setdefault(match_id, []).append(payload)
		seen.add((match_id, user_id))
	for match_id, rows in impacts.items():
		for impact in rows:
			user_id = impact.get("user_id")
			if user_id and (match_id, user_id) in seen:
				continue
			payload = {
				"user_id": user_id,
				"profile_id": impact.get("profile_id"),
				"nick": impact.get("nick") or user_id or "Unknown",
				"avatar": _avatar_for_user_id(user_id, community),
				"team": impact.get("team"),
				"civ": impact.get("civ"),
				"result": None,
				"impact": impact,
			}
			out.setdefault(match_id, []).append(payload)
	return {
		match_id: sorted(rows, key=lambda p: (str(p.get("team") or ""), p.get("nick") or ""))
		for match_id, rows in out.items()
	}


async def _match_stats_overall(community, period):
	at_clause, params = _period_filter(period)
	summary = await db.fetchone(
		"SELECT COUNT(DISTINCT m.match_id) AS games, "
		"COUNT(DISTINCT IF(m.ranked=1, m.match_id, NULL)) AS ranked_games, "
		"COUNT(DISTINCT pm.user_id) AS players, MAX(m.reported_at) AS last_match_at "
		"FROM matches m LEFT JOIN match_players pm "
		"ON pm.match_id=m.match_id AND pm.channel_id=m.channel_id"
		+ _visible_user_clause("pm", community) +
		" WHERE " + _community_channel_predicate(community, "m.channel_id") + at_clause,
		params)
	board = await db.fetchall(
		"SELECT pm.user_id, MAX(pm.nick) AS nick, COUNT(DISTINCT m.match_id) AS games, "
		"SUM(m.ranked=1 AND m.winner=pm.team) AS wins, "
		"SUM(m.ranked=1 AND m.winner IS NOT NULL AND m.winner<>pm.team) AS losses, "
		"SUM(m.ranked=1 AND m.winner IS NULL) AS draws "
		"FROM match_players pm JOIN matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"WHERE " + _community_channel_predicate(community, "m.channel_id")
		+ _visible_user_clause("pm", community) + at_clause +
		" GROUP BY pm.user_id ORDER BY wins DESC, games DESC LIMIT 500",
		params)
	ratings = await _rating_deltas(community, period, [r["user_id"] for r in board or []])
	civs = await db.fetchall(
		"SELECT civ, COUNT(*) AS games, SUM(result='W') AS wins, SUM(result='L') AS losses "
		"FROM civ_picks cp WHERE " + _linked_civ_clause("cp") + " AND "
		+ _community_channel_predicate(community, "cp.channel_id")
		+ " AND civ IS NOT NULL AND civ<>''"
		+ (" AND at >= %s" if params else "") +
		" GROUP BY civ ORDER BY games DESC LIMIT 20",
		params)
	maps = _map_counts(await db.fetchall(
		"SELECT maps FROM matches m WHERE maps IS NOT NULL AND "
		+ _community_channel_predicate(community, "m.channel_id") + at_clause, params))
	trend_bucket = _trend_bucket_expr(period)
	trend = await db.fetchall(
		"SELECT " + trend_bucket + " AS bucket, COUNT(*) AS games "
		"FROM matches m WHERE " + _community_channel_predicate(community, "m.channel_id")
		+ at_clause + " GROUP BY bucket ORDER BY bucket ASC",
		params)
	recent = await db.fetchall(
		"SELECT m.match_id, m.queue_name, m.reported_at AS at, m.ranked, m.winner, m.maps, rm.duration_s "
		"FROM matches m LEFT JOIN match_replays mr "
		f"ON mr.match_id=m.match_id AND mr.community_id={community.sql_id} "
		"LEFT JOIN replay_matches rm ON rm.replay_match_id=mr.replay_match_id "
		"WHERE " + _community_channel_predicate(community, "m.channel_id") + at_clause +
		" ORDER BY m.reported_at DESC, m.match_id DESC LIMIT 50",
		params)
	impacts = await _match_impacts(community, [r["match_id"] for r in recent or []])
	rosters = await _match_rosters(community, [r["match_id"] for r in recent or []])
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
					"avatar": _avatar_for_user_id(r["user_id"], community),
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
			 "duration_s": r.get("duration_s"), "impact": impacts.get(r["match_id"]),
			 "players": rosters.get(r["match_id"], [])}
			for r in recent or []
		],
		"matches": [
			{"match_id": r["match_id"], "queue": r["queue_name"], "at": r["at"],
			 "ranked": bool(r["ranked"]), "map": ((r.get("maps") or "").split("\n")[0] or "").strip(),
			 "duration_s": r.get("duration_s"), "impact": impacts.get(r["match_id"]),
			 "players": rosters.get(r["match_id"], [])}
			for r in recent or []
		],
	}


async def _player_streak(community, user_id, at_clause, params):
	rows = await db.fetchall(
		"SELECT m.winner, pm.team FROM match_players pm JOIN matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"WHERE pm.user_id=%s AND m.ranked=1 AND "
		+ _community_channel_predicate(community, "m.channel_id") + at_clause
		+ " ORDER BY m.reported_at DESC, m.match_id DESC LIMIT 20",
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


async def _match_stats_player(community, user_id, period):
	# The stored player-commentary feature was retired (its backing table
	# dropped) — the key stays in the payload for API compatibility, but is
	# always None now.
	commentary = None

	at_clause, params = _period_filter(period)
	profile_ids, aoe2_names = await _mapped_player_identity(user_id)
	rating = await _rating_delta(community, period, user_id)
	rating_history = await _rating_history(community, period, user_id)
	strategy_tags = await _player_profile_tags(community, profile_ids, period)
	summary = await db.fetchone(
		"SELECT COUNT(DISTINCT m.match_id) AS games, "
		"SUM(m.ranked=1 AND m.winner=pm.team) AS wins, "
		"SUM(m.ranked=1 AND m.winner IS NOT NULL AND m.winner<>pm.team) AS losses, "
		"SUM(m.ranked=1 AND m.winner IS NULL) AS draws, MAX(m.reported_at) AS last_match_at, MAX(pm.nick) AS nick "
		"FROM match_players pm JOIN matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"WHERE pm.user_id=%s AND " + _community_channel_predicate(community, "m.channel_id")
		+ at_clause,
		[user_id, *params])
	civ_clause, civ_args = _civ_player_clause(user_id, aoe2_names)
	civs = await db.fetchall(
		"SELECT civ, COUNT(*) AS games, SUM(result='W') AS wins, SUM(result='L') AS losses "
		"FROM civ_picks cp WHERE " + _linked_civ_clause("cp") + " AND "
		+ _community_channel_predicate(community, "cp.channel_id") + " AND " + civ_clause +
		" AND civ IS NOT NULL AND civ<>''"
		+ (" AND at >= %s" if params else "") +
		" GROUP BY civ ORDER BY wins DESC, games DESC LIMIT 12",
		[*civ_args, *params])
	maps = _map_counts(await db.fetchall(
		"SELECT m.maps FROM match_players pm JOIN matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"WHERE pm.user_id=%s AND m.maps IS NOT NULL AND "
		+ _community_channel_predicate(community, "m.channel_id") + at_clause,
		[user_id, *params]))
	teammates = await db.fetchall(
		"SELECT mate.user_id, MAX(mate.nick) AS nick, COUNT(*) AS games, "
		"SUM(m.winner=pm.team) AS wins, SUM(m.winner IS NOT NULL AND m.winner<>pm.team) AS losses "
		"FROM match_players pm JOIN matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"JOIN match_players mate ON mate.match_id=pm.match_id AND mate.channel_id=pm.channel_id "
		"AND mate.team=pm.team AND mate.user_id<>pm.user_id"
		+ _visible_user_clause("mate", community) +
		" WHERE pm.user_id=%s AND m.ranked=1 AND "
		+ _community_channel_predicate(community, "m.channel_id") + at_clause +
		" GROUP BY mate.user_id HAVING games >= 2 AND wins + losses > 0 "
		"ORDER BY wins / NULLIF(wins + losses, 0) DESC, games DESC, wins DESC LIMIT 8",
		[user_id, *params])
	opponents = await db.fetchall(
		"SELECT opp.user_id, MAX(opp.nick) AS nick, COUNT(*) AS games, "
		"SUM(m.winner=pm.team) AS wins, SUM(m.winner IS NOT NULL AND m.winner=opp.team) AS losses "
		"FROM match_players pm JOIN matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"JOIN match_players opp ON opp.match_id=pm.match_id AND opp.channel_id=pm.channel_id "
		"AND opp.team<>pm.team AND opp.user_id<>pm.user_id"
		+ _visible_user_clause("opp", community) +
		" WHERE pm.user_id=%s AND m.ranked=1 AND "
		+ _community_channel_predicate(community, "m.channel_id") + at_clause +
		" GROUP BY opp.user_id HAVING games >= 2 AND wins + losses > 0 "
		"ORDER BY wins / NULLIF(wins + losses, 0) ASC, losses DESC, games DESC LIMIT 8",
		[user_id, *params])
	recent = await db.fetchall(
		"SELECT m.match_id, m.queue_name, m.reported_at AS at, m.ranked, m.winner, m.maps, pm.team "
		"FROM match_players pm JOIN matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"WHERE pm.user_id=%s AND " + _community_channel_predicate(community, "m.channel_id")
		+ at_clause + " ORDER BY m.reported_at DESC, m.match_id DESC LIMIT 50",
		[user_id, *params])
	recent_civs = {}
	impacts = {}
	match_rosters = {}
	impact_profile = _player_impact_profile([], civs)
	impact_match_rows = await db.fetchall(
		"SELECT DISTINCT m.match_id FROM match_players pm JOIN matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"JOIN match_replays mr ON mr.match_id=m.match_id "
		f"AND mr.community_id={community.sql_id} "
		"WHERE pm.user_id=%s AND " + _community_channel_predicate(community, "m.channel_id")
		+ at_clause,
		[user_id, *params])
	period_impacts = await _match_impacts(
		community, [r["match_id"] for r in impact_match_rows or []], user_id, profile_ids)
	if period_impacts:
		impact_profile = _player_impact_profile(period_impacts.values(), civs)
	scouting = await _player_scouting_report(user_id, community)
	if recent:
		match_ids = [r["match_id"] for r in recent]
		match_rosters = await _match_rosters(community, match_ids)
		impacts = {match_id: period_impacts.get(match_id) for match_id in match_ids}
		civ_clause, civ_args = _civ_player_clause(user_id, aoe2_names)
		rows = await db.fetchall(
			"SELECT bot_match_id, civ FROM civ_picks WHERE bot_match_id IN ("
			+ ",".join(["%s"] * len(match_ids)) + ") AND "
			+ _community_channel_predicate(community, "civ_picks.channel_id")
			+ " AND " + civ_clause,
			[*match_ids, *civ_args])
		for r in rows or []:
			if r.get("civ") and r["bot_match_id"] not in recent_civs:
				recent_civs[r["bot_match_id"]] = r["civ"]
	trend_bucket = _trend_bucket_expr(period)
	trend = await db.fetchall(
		"SELECT " + trend_bucket + " AS bucket, "
		"COUNT(*) AS games, SUM(m.ranked=1 AND m.winner=pm.team) AS wins, "
		"SUM(m.ranked=1 AND m.winner IS NOT NULL AND m.winner<>pm.team) AS losses "
		"FROM match_players pm JOIN matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"WHERE pm.user_id=%s AND " + _community_channel_predicate(community, "m.channel_id")
		+ at_clause + " GROUP BY bucket ORDER BY bucket ASC",
		[user_id, *params])
	return {
		"summary": {
			"user_id": str(user_id),
			"nick": (summary or {}).get("nick") or str(user_id),
			"avatar": _avatar_for_user_id(user_id, community),
			"profile_ids": profile_ids,
			**rating,
			"games": int((summary or {}).get("games") or 0),
			"wins": int((summary or {}).get("wins") or 0),
			"losses": int((summary or {}).get("losses") or 0),
			"draws": int((summary or {}).get("draws") or 0),
			"winrate": _winrate((summary or {}).get("wins"), (summary or {}).get("losses")),
			"last_match_at": (summary or {}).get("last_match_at"),
			"streak": await _player_streak(community, user_id, at_clause, params),
			"impact_profile": impact_profile,
			"scouting_report": scouting,
			"strategy_tags": strategy_tags,
		},
		"rating_history": rating_history,
		"civs": [
			{"civ": r["civ"], "games": int(r["games"] or 0), "wins": int(r["wins"] or 0),
			 "losses": int(r["losses"] or 0), "winrate": _winrate(r["wins"], r["losses"])}
			for r in civs or []
		],
		"maps": maps,
		"teammates": [
			{"user_id": str(r["user_id"]), "nick": r["nick"] or str(r["user_id"]), "games": int(r["games"] or 0),
			 "wins": int(r["wins"] or 0), "losses": int(r["losses"] or 0),
				 "winrate": _winrate(r["wins"], r["losses"]),
				 "avatar": _avatar_for_user_id(r["user_id"], community)}
			for r in teammates or []
		],
		"opponents": [
			{"user_id": str(r["user_id"]), "nick": r["nick"] or str(r["user_id"]), "games": int(r["games"] or 0),
			 "wins": int(r["wins"] or 0), "losses": int(r["losses"] or 0),
				 "winrate": _winrate(r["wins"], r["losses"]),
				 "avatar": _avatar_for_user_id(r["user_id"], community)}
			for r in opponents or []
		],
		"recent": [
			{"match_id": r["match_id"], "queue": r["queue_name"], "at": r["at"],
			 "ranked": bool(r["ranked"]), "result": (
				"D" if r["ranked"] and r["winner"] is None else
				"W" if r["winner"] == r["team"] else
				"L" if r["winner"] is not None else "-"
			 ), "map": ((r.get("maps") or "").split("\n")[0] or "").strip(),
			 "civ": recent_civs.get(r["match_id"]), "impact": impacts.get(r["match_id"]),
			 "players": match_rosters.get(r["match_id"], [])}
			for r in recent or []
		],
		"matches": [
			{"match_id": r["match_id"], "queue": r["queue_name"], "at": r["at"],
			 "ranked": bool(r["ranked"]), "result": (
				"D" if r["ranked"] and r["winner"] is None else
				"W" if r["winner"] == r["team"] else
				"L" if r["winner"] is not None else "-"
			 ), "map": ((r.get("maps") or "").split("\n")[0] or "").strip(),
			 "civ": recent_civs.get(r["match_id"]), "impact": impacts.get(r["match_id"]),
			 "players": match_rosters.get(r["match_id"], [])}
			for r in recent or []
		],
		"trend": [{"bucket": str(r["bucket"]), "games": int(r["games"] or 0),
		           "wins": int(r["wins"] or 0), "losses": int(r["losses"] or 0)}
		          for r in trend or []],
		"commentary": commentary,
	}


async def handle_match_stats(request):
	community = await _public_community(request)
	if community is None:
		return _community_not_found()
	period = request.query.get("period", DEFAULT_STATS_PERIOD)
	if period not in MATCH_STAT_PERIODS:
		period = DEFAULT_STATS_PERIOD
	player_raw = request.query.get("player_id") or ""
	players = await _match_stat_players(community)
	payload = {
		"community": community.payload(),
		"period": period,
		"players": players,
		"scope": "overall",
	}
	if player_raw and player_raw != "all":
		try:
			user_id = int(player_raw)
		except ValueError:
			return web.json_response({"error": "Invalid player_id"}, status=400)
		if not await _player_has_public_stats(community, user_id):
			return web.json_response({"error": "Player not found"}, status=404)
		payload["scope"] = "player"
		payload["selected_player_id"] = str(user_id)
		payload.update(await _match_stats_player(community, user_id, period))
	else:
		payload.update(await _match_stats_overall(community, period))
	return web.json_response(payload)


async def handle_leaderboard(request):
	community = await _public_community(request)
	if community is None:
		return _community_not_found()
	period = request.query.get("period", DEFAULT_STATS_PERIOD)
	if period not in MATCH_STAT_PERIODS:
		period = DEFAULT_STATS_PERIOD
	mode = request.query.get("mode", "players")
	at_clause, params = _period_filter(period)
	if mode == "civs":
		rows = await db.fetchall(
			"SELECT civ, COUNT(*) AS games, SUM(result='W') AS wins, SUM(result='L') AS losses "
			"FROM civ_picks cp WHERE " + _linked_civ_clause("cp") + " AND "
			+ _community_channel_predicate(community, "cp.channel_id")
			+ " AND civ IS NOT NULL AND civ<>''"
			+ (" AND at >= %s" if params else "") +
			" GROUP BY civ ORDER BY wins DESC, games DESC LIMIT 500",
			params)
		return web.json_response({
			"community": community.payload(),
			"period": period,
			"mode": "civs",
			"rows": [
				{"civ": r["civ"], "games": int(r["games"] or 0), "wins": int(r["wins"] or 0),
				 "losses": int(r["losses"] or 0), "winrate": _winrate(r["wins"], r["losses"])}
				for r in rows or []
			],
		})
	if mode == "tags":
		tag_key = request.query.get("tag") or "all"
		payload = await _tag_leaderboard(community, period, tag_key)
		return web.json_response({
			"community": community.payload(),
			"period": period,
			"mode": "tags",
			"tag": tag_key,
			**payload,
		})
	rows = await db.fetchall(
		"SELECT pm.user_id, MAX(pm.nick) AS nick, COUNT(DISTINCT m.match_id) AS games, "
		"SUM(m.ranked=1 AND m.winner=pm.team) AS wins, "
		"SUM(m.ranked=1 AND m.winner IS NOT NULL AND m.winner<>pm.team) AS losses, "
		"SUM(m.ranked=1 AND m.winner IS NULL) AS draws, MAX(p.rating) AS rating "
		"FROM match_players pm JOIN matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"LEFT JOIN player_ratings p ON p.user_id=pm.user_id AND p.channel_id=pm.channel_id "
		"WHERE " + _community_channel_predicate(community, "m.channel_id")
		+ _visible_user_clause("pm", community) + at_clause +
		" GROUP BY pm.user_id ORDER BY wins DESC, games DESC LIMIT 500",
		params)
	ratings = await _rating_deltas(community, period, [r["user_id"] for r in rows or []])
	return web.json_response({
		"community": community.payload(),
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
					"avatar": _avatar_for_user_id(r["user_id"], community),
				},
				**ratings.get(int(r["user_id"]), _rating_payload(None)),
			}
			for r in rows or []
		],
	})


async def handle_player_stats(request):
	community = await _public_community(request)
	if community is None:
		return _community_not_found()
	period = request.query.get("period", DEFAULT_STATS_PERIOD)
	if period not in MATCH_STAT_PERIODS:
		period = DEFAULT_STATS_PERIOD
	try:
		user_id = int(request.query.get("player_id") or "0")
	except ValueError:
		return web.json_response({"error": "Invalid player_id"}, status=400)
	if not user_id:
		return web.json_response({"error": "Missing player_id"}, status=400)
	if not await _player_has_public_stats(community, user_id):
		return web.json_response({"error": "Player not found"}, status=404)

	# The stored player-commentary feature was retired (its backing table
	# dropped) — the key stays in the payload for API compatibility, but is
	# always None now.
	commentary = None

	at_clause, params = _period_filter(period)
	profile_ids, aoe2_names = await _mapped_player_identity(user_id)
	rating = await _rating_delta(community, period, user_id)
	rating_history = await _rating_history(community, period, user_id)
	strategy_tags = await _player_profile_tags(community, profile_ids, period)
	base_args = [user_id, *params]
	summary = await db.fetchone(
		"SELECT MAX(pm.nick) AS nick, COUNT(DISTINCT m.match_id) AS games, "
		"SUM(m.ranked=1 AND m.winner=pm.team) AS wins, "
		"SUM(m.ranked=1 AND m.winner IS NOT NULL AND m.winner<>pm.team) AS losses, "
		"SUM(m.ranked=1 AND m.winner IS NULL) AS draws, MAX(m.reported_at) AS last_match_at "
		"FROM match_players pm JOIN matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"WHERE pm.user_id=%s AND " + _community_channel_predicate(community, "m.channel_id")
		+ at_clause,
		base_args)
	allies = await db.fetchall(
		"SELECT ally.user_id, MAX(ally.nick) AS nick, COUNT(*) AS games, "
		"SUM(m.winner=pm.team) AS wins, SUM(m.winner IS NOT NULL AND m.winner<>pm.team) AS losses "
		"FROM match_players pm JOIN matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"JOIN match_players ally ON ally.match_id=pm.match_id AND ally.channel_id=pm.channel_id "
		"AND ally.team=pm.team AND ally.user_id<>pm.user_id"
		+ _visible_user_clause("ally", community) +
		" WHERE pm.user_id=%s AND m.ranked=1 AND "
		+ _community_channel_predicate(community, "m.channel_id") + at_clause +
		" GROUP BY ally.user_id HAVING games >= 1 AND wins + losses > 0 "
		# No LIMIT: any winrate-sorted cut silently drops one of the two tails
		# (dream duos vs cursed duos). The roster is small, so the full list is
		# at most a few dozen rows.
		"ORDER BY games >= 10 DESC, wins / NULLIF(wins + losses, 0) DESC, games DESC, wins DESC",
		base_args)
	opponents = await db.fetchall(
		"SELECT opp.user_id, MAX(opp.nick) AS nick, COUNT(*) AS games, "
		"SUM(m.winner=pm.team) AS wins, SUM(m.winner IS NOT NULL AND m.winner=opp.team) AS losses "
		"FROM match_players pm JOIN matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"JOIN match_players opp ON opp.match_id=pm.match_id AND opp.channel_id=pm.channel_id "
		"AND opp.team<>pm.team AND opp.user_id<>pm.user_id"
		+ _visible_user_clause("opp", community) +
		" WHERE pm.user_id=%s AND m.ranked=1 AND "
		+ _community_channel_predicate(community, "m.channel_id") + at_clause +
		" GROUP BY opp.user_id HAVING games >= 1 AND wins + losses > 0 "
		"ORDER BY games >= 10 DESC, wins / NULLIF(wins + losses, 0) ASC, losses DESC, games DESC",
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
		"FROM match_players pm JOIN matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"JOIN match_replays mr ON mr.match_id=m.match_id "
		f"AND mr.community_id={community.sql_id} "
		"JOIN replay_matches rm ON rm.replay_match_id=mr.replay_match_id "
		"WHERE pm.user_id=%s AND m.ranked=1 AND rm.duration_s IS NOT NULL AND "
		+ _community_channel_predicate(community, "m.channel_id") + at_clause +
		" GROUP BY bucket, ord ORDER BY ord",
		base_args)
	civ_clause, civ_args = _civ_player_clause(user_id, aoe2_names)
	civs = await db.fetchall(
		"SELECT civ, COUNT(*) AS games, SUM(result='W') AS wins, SUM(result='L') AS losses "
		"FROM civ_picks cp WHERE " + _linked_civ_clause("cp") + " AND "
		+ _community_channel_predicate(community, "cp.channel_id") + " AND " + civ_clause +
		" AND civ IS NOT NULL AND civ<>''"
		+ (" AND at >= %s" if params else "") +
		" GROUP BY civ ORDER BY wins DESC, games DESC LIMIT 30",
		[*civ_args, *params])
	opp_civs = await db.fetchall(
		"SELECT oc.civ, COUNT(*) AS games, SUM(m.winner=pm.team) AS wins, "
		"SUM(m.winner IS NOT NULL AND m.winner<>pm.team) AS losses "
		"FROM match_players pm JOIN matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"JOIN civ_picks oc ON oc.bot_match_id=m.match_id AND oc.team<>pm.team "
		"WHERE pm.user_id=%s AND m.ranked=1 AND oc.civ IS NOT NULL AND oc.civ<>'' AND "
		+ _community_channel_predicate(community, "m.channel_id") + " AND "
		+ _community_channel_predicate(community, "oc.channel_id") + at_clause +
		" GROUP BY oc.civ ORDER BY wins DESC, games DESC LIMIT 30",
		base_args)
	matches = await db.fetchall(
		"SELECT m.match_id, m.queue_name, m.reported_at AS at, m.ranked, m.winner, m.maps, pm.team, rm.duration_s "
		"FROM match_players pm JOIN matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"LEFT JOIN match_replays mr ON mr.match_id=m.match_id "
		f"AND mr.community_id={community.sql_id} "
		"LEFT JOIN replay_matches rm ON rm.replay_match_id=mr.replay_match_id "
		"WHERE pm.user_id=%s AND " + _community_channel_predicate(community, "m.channel_id")
		+ at_clause +
		" ORDER BY m.reported_at DESC, m.match_id DESC LIMIT 50",
		base_args)
	impact_match_rows = await db.fetchall(
		"SELECT DISTINCT m.match_id FROM match_players pm JOIN matches m "
		"ON m.match_id=pm.match_id AND m.channel_id=pm.channel_id "
		"JOIN match_replays mr ON mr.match_id=m.match_id "
		f"AND mr.community_id={community.sql_id} "
		"WHERE pm.user_id=%s AND " + _community_channel_predicate(community, "m.channel_id")
		+ at_clause,
		base_args)
	period_impacts = await _match_impacts(
		community, [r["match_id"] for r in impact_match_rows or []], user_id, profile_ids)
	impact_profile = _player_impact_profile(period_impacts.values(), civs, durations)
	scouting = await _player_scouting_report(user_id, community)
	strategy_profile = await _player_strategy_profile(community, user_id, profile_ids, period)
	match_civs = {}
	opp_match_civs = {}
	match_rosters = {}
	if matches:
		match_ids = [r["match_id"] for r in matches]
		match_id_clause = ",".join(["%s"] * len(match_ids))
		match_rosters = await _match_rosters(community, match_ids)
		civ_rows = await db.fetchall(
			"SELECT bot_match_id, civ FROM civ_picks WHERE bot_match_id IN ("
			+ match_id_clause + ") AND "
			+ _community_channel_predicate(community, "civ_picks.channel_id")
			+ " AND " + civ_clause,
			[*match_ids, *civ_args])
		for r in civ_rows or []:
			if r.get("civ") and r["bot_match_id"] not in match_civs:
				match_civs[r["bot_match_id"]] = r["civ"]
		opp_rows = await db.fetchall(
			"SELECT oc.bot_match_id, GROUP_CONCAT(DISTINCT oc.civ ORDER BY oc.civ SEPARATOR ', ') AS civs "
			"FROM match_players pm JOIN civ_picks oc "
			"ON oc.bot_match_id=pm.match_id AND oc.team<>pm.team "
			"WHERE pm.user_id=%s AND pm.match_id IN (" + match_id_clause + ") "
			"AND " + _community_channel_predicate(community, "pm.channel_id")
			+ " AND " + _community_channel_predicate(community, "oc.channel_id")
			+ " AND oc.civ IS NOT NULL AND oc.civ<>'' GROUP BY oc.bot_match_id",
			[user_id, *match_ids])
		opp_match_civs = {r["bot_match_id"]: r["civs"] for r in opp_rows or []}
	return web.json_response({
		"community": community.payload(),
		"period": period,
		"commentary": commentary,
		"summary": {
			"user_id": str(user_id),
			"nick": (summary or {}).get("nick") or str(user_id),
			"avatar": _avatar_for_user_id(user_id, community),
			"profile_ids": profile_ids,
			**rating,
			"games": int((summary or {}).get("games") or 0),
			"wins": int((summary or {}).get("wins") or 0),
			"losses": int((summary or {}).get("losses") or 0),
			"draws": int((summary or {}).get("draws") or 0),
			"winrate": _winrate((summary or {}).get("wins"), (summary or {}).get("losses")),
			"last_match_at": (summary or {}).get("last_match_at"),
			"impact_profile": impact_profile,
			"scouting_report": scouting,
			"strategy_profile": strategy_profile,
			"strategy_tags": strategy_tags,
			"best_ally": _best_relationship(allies, "ally", community),
			"worst_ally": _best_relationship(allies, "worst_ally", community),
			"worst_enemy": _best_relationship(opponents, "enemy", community),
			"easiest_enemy": _best_relationship(opponents, "easy_enemy", community),
		},
		"allies": [_relationship_payload(r, community) for r in allies or []],
		"rating_history": rating_history,
		"opponents": [
			{"user_id": str(r["user_id"]), "nick": r["nick"] or str(r["user_id"]),
			 "games": int(r["games"] or 0), "wins": int(r["wins"] or 0),
			 "losses": int(r["losses"] or 0), "winrate": _winrate(r["wins"], r["losses"]),
				 "avatar": _avatar_for_user_id(r["user_id"], community)}
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
			 "impact": period_impacts.get(r["match_id"]),
			 "players": match_rosters.get(r["match_id"], [])}
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
	}, on_duplicate='replace')
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
	}, on_duplicate='replace')

	resp = web.HTTPFound("/")
	is_secure = root_url.startswith("https://")
	resp.set_cookie(COOKIE_NAME, session_id, max_age=SESSION_LIFETIME, httponly=True, samesite="Lax", secure=is_secure)
	raise resp


async def handle_auth_logout(request):
	session_id = _session_cookie(request)
	if session_id:
		try:
			await db.delete('web_sessions', where={'session_id': session_id})
		except Exception:
			pass
	resp = web.HTTPFound("/")
	resp.del_cookie(COOKIE_NAME)
	resp.del_cookie(_LEGACY_COOKIE_NAME)
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


async def _admin_communities_for_session(session):
	rows = await db.select(
		["community_id", "guild_id", "name", "retention"], "communities")
	links = await db.select(["community_id", "channel_id"], "community_channels")
	channel_ids = {}
	for link in links or []:
		channel_ids.setdefault(int(link["community_id"]), set()).add(int(link["channel_id"]))
	communities = []
	for row in rows or []:
		admin, error = await _authorized_admin_row(row, session)
		if error is not None:
			continue
		enrolled = channel_ids.get(admin.community.community_id, set())
		configured = sum(
			1 for channel_id, qc in dc.app.channels.items()
			if channel_id in enrolled and int(qc.guild_id) == admin.community.guild_id)
		communities.append(admin.payload(configured))
	return sorted(communities, key=lambda item: item["name"].lower())


def _overview_count(row, key):
	return int((row or {}).get(key) or 0)


def _overview_step(key, title, complete, detail, *, required=True, action=None):
	return {
		"key": key,
		"title": title,
		"status": "complete" if complete else ("required" if required else "recommended"),
		"required": required,
		"detail": detail,
		"action": action,
	}


def _overview_capability(key, title, status, summary, *, scope, configurable, metrics=None, note=None):
	return {
		"key": key,
		"title": title,
		"status": status,
		"scope": scope,
		"configurable": configurable,
		"summary": summary,
		"metrics": metrics or {},
		"note": note,
	}


async def _admin_community_overview(admin):
	"""Setup, feature scope and diagnostics for one authorized tenant.

	Every stored count crosses `community_id` or `community_channels`; global
	identity/replay tables are never counted on their own. Runtime feature state
	is labelled by its real control scope (queue, channel, or deployment) so the
	settings UI does not promise a tenant switch that does not exist.
	"""
	community_id = admin.community.community_id
	enrolled = await _community_channel_ids(community_id)
	loaded = {
		int(channel_id): qc for channel_id, qc in dc.app.channels.items()
		if int(channel_id) in enrolled and int(qc.guild_id) == admin.community.guild_id
	}
	queues = [queue for qc in loaded.values() for queue in qc.queues]
	ranked_queues = [queue for queue in queues if bool(getattr(queue.cfg, "ranked", False))]
	prediction_queues = [
		queue for queue in ranked_queues
		if bool(getattr(queue.cfg, "predictions_enabled", False))
	]

	ratings = await db.fetchone(
		"SELECT COUNT(DISTINCT pr.user_id) AS players, "
		"COUNT(DISTINCT CASE WHEN i.profile_id IS NOT NULL THEN pr.user_id END) AS linked_players, "
		"COUNT(DISTINCT i.profile_id) AS linked_profiles "
		"FROM player_ratings pr "
		"JOIN community_channels cc ON cc.channel_id=pr.channel_id "
		"LEFT JOIN identities i ON i.user_id=pr.user_id "
		"WHERE cc.community_id=%s",
		[community_id])
	matches = await db.fetchone(
		"SELECT COUNT(DISTINCT m.match_id) AS matches, MAX(m.reported_at) AS last_match_at "
		"FROM matches m JOIN community_channels cc ON cc.channel_id=m.channel_id "
		"WHERE cc.community_id=%s",
		[community_id])
	replays = await db.fetchone(
		"SELECT COUNT(*) AS linked_replays, SUM(ri.status='done') AS parsed_replays, "
		"SUM(ri.status IN ('unavailable','parse_failed','gave_up','pending_parser_update')) "
		"AS attention_replays FROM ("
		"SELECT DISTINCT replay_match_id FROM match_replays WHERE community_id=%s"
		") tenant_replays LEFT JOIN replay_ingest ri "
		"ON ri.replay_match_id=tenant_replays.replay_match_id",
		[community_id])
	predictions = await db.fetchone(
		"SELECT COUNT(*) AS posts, SUM(pp.status='open') AS open_posts, "
		"SUM(pp.status='resolved') AS resolved_posts "
		"FROM prediction_posts pp JOIN community_channels cc ON cc.channel_id=pp.channel_id "
		"WHERE cc.community_id=%s",
		[community_id])
	lobbies = await db.fetchone(
		"SELECT COUNT(*) AS tracked_lobbies, SUM(l.launched_at IS NOT NULL) AS launched_lobbies, "
		"SUM(l.status IN ('created','filling','verifying','in_progress')) AS active_lobbies "
		"FROM lobbies l JOIN community_channels cc ON cc.channel_id=l.channel_id "
		"WHERE cc.community_id=%s",
		[community_id])
	gold = await db.fetchone(
		"SELECT COUNT(*) AS holders FROM gold_balances WHERE community_id=%s",
		[community_id])
	quiz_rows = await db.fetchall(
		"SELECT qs.channel_id, qs.enabled, qs.quiz_hour, qs.open_window "
		"FROM quiz_settings qs JOIN community_channels cc ON cc.channel_id=qs.channel_id "
		"WHERE cc.community_id=%s ORDER BY qs.channel_id",
		[community_id]) or []

	player_count = _overview_count(ratings, "players")
	linked_players = _overview_count(ratings, "linked_players")
	linked_profiles = _overview_count(ratings, "linked_profiles")
	match_count = _overview_count(matches, "matches")
	linked_replays = _overview_count(replays, "linked_replays")
	parsed_replays = _overview_count(replays, "parsed_replays")
	attention_replays = _overview_count(replays, "attention_replays")
	quiz_enabled = [row for row in quiz_rows if bool(row.get("enabled"))]

	rating_required = bool(ranked_queues)
	steps = [
		_overview_step(
			"channels", "Connect a pickup channel", bool(loaded),
			f"{len(loaded)} configured channel(s) are loaded in this community."),
		_overview_step(
			"queues", "Create a pickup queue", bool(queues),
			f"{len(queues)} queue(s) are available across the loaded channels."),
		_overview_step(
			"history", "Migrate historical matches (optional)", bool(match_count),
			(f"{match_count} recorded match(es) are already available."
			 if match_count else "Import a complete Pubobot export before this community starts playing."),
			required=False, action="migrate_history" if loaded and not match_count else None),
		_overview_step(
			"ratings", "Seed player ratings", bool(player_count) or not rating_required,
			(f"{player_count} player(s) currently have a seeded rating."
			 if rating_required else "No ranked queue currently requires a rating seed."),
			required=rating_required, action="seed_ratings" if rating_required else None),
		_overview_step(
			"identities", "Link Discord users to AoE2 profiles", bool(linked_profiles),
			f"{linked_players} player(s) are linked to {linked_profiles} AoE2 profile(s).",
			required=False, action="map_identities"),
	]
	required_steps = [step for step in steps if step["required"]]
	completed_required = sum(step["status"] == "complete" for step in required_steps)

	if not ranked_queues:
		prediction_status = "unavailable"
	elif not prediction_queues:
		prediction_status = "disabled"
	elif len(prediction_queues) == len(ranked_queues):
		prediction_status = "active"
	else:
		prediction_status = "partial"
	quiz_status = "active" if quiz_enabled else ("disabled" if quiz_rows else "not_configured")
	replay_enabled = bool(getattr(cfg, "REPLAY_INGEST_ENABLED", True))
	replay_status = "deployment_disabled" if not replay_enabled else "active"
	capabilities = [
		_overview_capability(
			"pickup", "Pickup games", "active" if queues else "setup_required",
			"Queue formation, check-in and match reporting.", scope="community",
			configurable=True, metrics={"channels": len(loaded), "queues": len(queues)}),
		_overview_capability(
			"ratings", "Ratings", "active" if ranked_queues and player_count else "setup_required",
			"Per-channel ratings for ranked queues.", scope="channel", configurable=True,
			metrics={"ranked_queues": len(ranked_queues), "players": player_count}),
		_overview_capability(
			"predictions", "Flash predictions", prediction_status,
			"Audience gold betting on ranked matches.", scope="queue", configurable=True,
			metrics={"enabled_queues": len(prediction_queues), "ranked_queues": len(ranked_queues),
			         "posts": _overview_count(predictions, "posts"),
			         "open_posts": _overview_count(predictions, "open_posts")}),
		_overview_capability(
			"gold", "Gold economy", "active" if _overview_count(gold, "holders") else "available",
			"Automatic balances used by predictions and quiz rewards.", scope="community",
			configurable=False, metrics={"holders": _overview_count(gold, "holders")}),
		_overview_capability(
			"quiz", "Daily quiz", quiz_status,
			"Scheduled AoE2 quiz posts and rewards.", scope="channel", configurable=False,
			metrics={"configured_channels": len(quiz_rows), "enabled_channels": len(quiz_enabled)},
			note="The current scheduler permits one enabled quiz channel per deployment; tenant controls are not built yet."),
		_overview_capability(
			"lobby", "Lobby tracking", "available" if ranked_queues else "unavailable",
			"Automatic lobby detection and API-confirmed game starts.", scope="built_in",
			configurable=False,
			metrics={"tracked": _overview_count(lobbies, "tracked_lobbies"),
			         "launched": _overview_count(lobbies, "launched_lobbies"),
			         "active": _overview_count(lobbies, "active_lobbies")}),
		_overview_capability(
			"replay_analysis", "Replay analysis", replay_status,
			"Replay parsing, classifications and scouting rollups.", scope="deployment",
			configurable=False,
			metrics={"linked": linked_replays, "parsed": parsed_replays,
			         "attention": attention_replays},
			note="Hosted deployments share this compute switch; self-hosted installations can control it locally."),
		_overview_capability(
			"public_dashboard", "Public dashboard", "active",
			"Community-scoped leaderboards and player statistics.", scope="community",
			configurable=False, metrics={"matches": match_count}),
	]

	loaded_for_guild = {
		int(channel_id) for channel_id, qc in dc.app.channels.items()
		if int(qc.guild_id) == admin.community.guild_id
	}
	enrolled_not_loaded = enrolled - set(loaded)
	loaded_not_enrolled = loaded_for_guild - enrolled
	discord_unavailable = {
		channel_id for channel_id in enrolled if dc.get_channel(channel_id) is None
	}
	active_matches = sum(
		1 for match in dc.app.active_matches
		if int(getattr(getattr(match, "qc", None), "id", 0)) in enrolled)
	diagnostic_attention = (
		not bool(dc.app.ready) or not bool(dc.is_ready()) or bool(enrolled_not_loaded)
		or bool(loaded_not_enrolled) or bool(discord_unavailable) or bool(attention_replays)
	)
	return {
		"community": admin.payload(len(loaded)),
		"onboarding": {
			"status": "ready" if completed_required == len(required_steps) else "needs_setup",
			"completed_required": completed_required,
			"required_steps": len(required_steps),
			"steps": steps,
		},
		"capabilities": capabilities,
		"diagnostics": {
			"status": "attention" if diagnostic_attention else "healthy",
			"discord_connected": bool(dc.is_ready()),
			"bot_ready": bool(dc.app.ready),
			"active_matches": active_matches,
			"channel_consistency": {
				"enrolled": len(enrolled),
				"loaded": len(loaded),
				"enrolled_not_loaded": len(enrolled_not_loaded),
				"loaded_not_enrolled": len(loaded_not_enrolled),
				"discord_unavailable": len(discord_unavailable),
			},
			"data": {
				"players": player_count,
				"linked_players": linked_players,
				"linked_profiles": linked_profiles,
				"matches": match_count,
				"last_match_at": (matches or {}).get("last_match_at"),
				"linked_replays": linked_replays,
				"parsed_replays": parsed_replays,
				"replays_needing_attention": attention_replays,
			},
		},
	}


async def handle_api_communities(request):
	"""Communities the signed-in user is allowed to administer."""
	session = await _get_session(request)
	if not session:
		return web.json_response({"error": "Not logged in"}, status=401)
	return web.json_response({"communities": await _admin_communities_for_session(session)})


async def handle_api_community(request):
	"""One explicit admin community, used by `/c/{id}/dashboard`."""
	session = await _get_session(request)
	if not session:
		return web.json_response({"error": "Not logged in"}, status=401)
	admin, error = await _admin_community_context(request, session)
	if error is not None:
		return error
	channel_ids = await _community_channel_ids(admin.community.community_id)
	configured = sum(
		1 for channel_id, qc in dc.app.channels.items()
		if channel_id in channel_ids and int(qc.guild_id) == admin.community.guild_id)
	return web.json_response({"community": admin.payload(configured)})


async def handle_api_community_overview(request):
	session = await _get_session(request)
	if not session:
		return web.json_response({"error": "Not logged in"}, status=401)
	admin, error = await _admin_community_context(request, session)
	if error is not None:
		return error
	return web.json_response(await _admin_community_overview(admin))


def _seed_member_name(guild, user_id):
	if not user_id:
		return ""
	member = guild.get_member(user_id)
	if member is None:
		return ""
	return str(
		getattr(member, "display_name", None)
		or getattr(member, "nick", None)
		or getattr(member, "name", None)
		or ""
	).strip()


async def _rating_seed_preview(context, payload):
	"""Parse once, resolve existing rows once, and build apply-ready statuses."""
	parsed = onboarding.parse_seed_payload(payload, context.rating.init_deviation)
	digest = onboarding.seed_digest(context.target_channel_id, parsed)
	existing_rows = await db.select(
		["user_id", "nick", "rating", "deviation"],
		"player_ratings", where={"channel_id": context.target_channel_id})
	existing = {int(row["user_id"]): row for row in existing_rows or []}
	prepared = []
	summary = {"received": len(parsed["rows"]), "ready": 0, "new": 0,
	           "unrated": 0, "existing": 0, "invalid": 0}
	for source_row in parsed["rows"]:
		row = dict(source_row)
		member_name = _seed_member_name(context.channel.admin.guild, row["user_id"])
		write_nick = row["nick"] or member_name
		current = existing.get(row["user_id"])
		if row["errors"]:
			status = "invalid"
			message = " ".join(row["errors"])
		elif current is None:
			status = "new"
			message = "Ready to add."
		elif current.get("rating") is None:
			status = "unrated"
			message = "Existing unrated player; rating fields can be initialized safely."
		else:
			status = "existing"
			message = f"Already rated at {int(current['rating'])}; this import will not overwrite it."

		summary[status] += 1
		if status in ("new", "unrated"):
			summary["ready"] += 1
		prepared.append({
			**row,
			"status": status,
			"message": message,
			"member_name": member_name,
			"write_nick": write_nick,
			"current_rating": current.get("rating") if current else None,
		})

	public_rows = []
	for row in prepared:
		public_rows.append({
			key: value for key, value in row.items()
			if key not in ("errors", "write_nick")
		})
	return {
		"community": context.channel.admin.payload(),
		"target": {
			"pickup_channel_id": str(context.channel.channel_id),
			"pickup_channel_name": context.channel.channel.name,
			"rating_channel_id": str(context.target_channel_id),
			"rating_channel_name": getattr(context.target_channel, "name", str(context.target_channel_id)),
			"rating_system": type(context.rating).__name__.removesuffix("Rating"),
			"initial_rating": int(context.rating.init_rp),
			"initial_deviation": int(context.rating.init_deviation),
		},
		"source": parsed["name"],
		"summary": summary,
		"rows": public_rows,
		"digest": digest,
		"can_apply": summary["invalid"] == 0 and summary["ready"] > 0,
	}, prepared


async def _rating_seed_request(request):
	session = await _get_session(request)
	if not session:
		return None, web.json_response({"error": "Not logged in"}, status=401)
	context, error = await _admin_rating_context(request, session)
	if error is not None:
		return None, error
	if not _check_csrf(request, session):
		return None, web.json_response({"error": "Invalid or missing CSRF token"}, status=403)
	try:
		payload = await request.json()
	except Exception:
		return None, web.json_response({"error": "Request body must be valid JSON"}, status=400)
	try:
		preview, prepared = await _rating_seed_preview(context, payload)
	except onboarding.SeedInputError as exc:
		return None, web.json_response({"error": str(exc)}, status=400)
	return (session, context, payload, preview, prepared), None


async def handle_api_rating_seed_preview(request):
	result, error = await _rating_seed_request(request)
	if error is not None:
		return error
	return web.json_response(result[3])


async def handle_api_rating_seed_apply(request):
	result, error = await _rating_seed_request(request)
	if error is not None:
		return error
	session, context, payload, preview, prepared = result
	provided_digest = payload.get("digest") if isinstance(payload, dict) else None
	if not isinstance(provided_digest, str) or not secrets.compare_digest(provided_digest, preview["digest"]):
		return web.json_response({"error": "Import changed after preview; preview it again"}, status=409)
	if preview["summary"]["invalid"]:
		return web.json_response({"error": "Fix every invalid row before applying the import"}, status=400)
	if not preview["can_apply"]:
		return web.json_response({"error": "No new or unrated players remain to seed"}, status=409)

	now = int(time.time())
	applied = 0
	raced_out = 0
	applied_user_ids = []
	reason = f"web onboarding seed by {int(session['user_id'])}"
	try:
		async with db.transaction() as tx:
			for row in prepared:
				if row["status"] not in ("new", "unrated"):
					continue
				if row["status"] == "new":
					write_nick = row["write_nick"] or f"Discord user {row['user_id']}"
					changed = await tx.insert("player_ratings", {
						"channel_id": context.target_channel_id,
						"user_id": row["user_id"],
						"nick": write_nick,
						"rating": row["rating"],
						"deviation": row["deviation"],
						"wins": 0,
						"losses": 0,
						"draws": 0,
						"streak": 0,
					}, on_duplicate="ignore")
				else:
					# Empty means "preserve the nick already on this unrated
					# row", not "replace it with a generated placeholder".
					write_nick = row["write_nick"]
					changed = await tx.execute(
						"UPDATE player_ratings SET "
						"nick=IF(%s='',nick,%s), rating=%s, deviation=%s "
						"WHERE channel_id=%s AND user_id=%s AND rating IS NULL",
						[write_nick, write_nick, row["rating"], row["deviation"],
						 context.target_channel_id, row["user_id"]])
				if not changed:
					raced_out += 1
					continue
				await tx.insert("rating_history", {
					"channel_id": context.target_channel_id,
					"user_id": row["user_id"],
					"at": now,
					"rating_before": int(context.rating.init_rp),
					"rating_change": row["rating"] - int(context.rating.init_rp),
					"deviation_before": int(context.rating.init_deviation),
					"deviation_change": row["deviation"] - int(context.rating.init_deviation),
					"match_id": None,
					"reason": reason,
				})
				applied += 1
				applied_user_ids.append(row["user_id"])
	except Exception as exc:
		log.error(
			f"Rating onboarding failed for community={context.channel.admin.community.community_id} "
			f"channel={context.target_channel_id}: {exc}")
		return web.json_response({"error": "Rating seed could not be applied"}, status=500)

	# Keep the existing slash-command behavior: after a successful seed, apply
	# configured rating roles/nick prefixes for members currently in the guild.
	members = [context.channel.admin.guild.get_member(user_id) for user_id in applied_user_ids]
	members = [member for member in members if member is not None]
	if members and callable(getattr(context.channel.queue_channel, "update_rating_roles", None)):
		try:
			await context.channel.queue_channel.update_rating_roles(*members)
		except Exception as exc:
			# The rating transaction is already committed. Role/nickname sync is
			# a follow-up convenience and must not turn a successful seed into a
			# false 500 that invites the admin to retry it.
			log.error(
				f"Rating onboarding role sync failed for channel={context.target_channel_id}: {exc}")
	return web.json_response({
		"ok": True,
		"applied": applied,
		"skipped_after_preview": raced_out,
		"rating_channel_id": str(context.target_channel_id),
	})


async def _identity_import_preview(admin, payload):
	"""Validate global identity claims through one authorized Discord guild."""
	parsed = onboarding.parse_identity_payload(payload)
	digest = onboarding.identity_digest(admin.community.community_id, parsed)
	existing_rows = await db.select(
		["profile_id", "user_id", "confidence", "aoe2_name"], "identities")
	existing = {int(row["profile_id"]): row for row in existing_rows or []}
	summary = {
		"received": len(parsed["rows"]), "ready": 0, "new": 0, "unowned": 0,
		"existing": 0, "conflict": 0, "not_member": 0, "invalid": 0,
	}
	prepared = []
	members = {}
	for source_row in parsed["rows"]:
		row = dict(source_row)
		member = members.get(row["user_id"])
		if row["user_id"] is not None and row["user_id"] not in members:
			try:
				member = await _member_in_guild(admin.guild, row["user_id"])
			except Exception:
				member = None
			members[row["user_id"]] = member
		current = existing.get(row["profile_id"])
		if row["errors"]:
			status = "invalid"
			message = " ".join(row["errors"])
		elif member is None or bool(getattr(member, "bot", False)):
			status = "not_member"
			message = "Discord user is not a current non-bot member of this community."
		elif current is None:
			status = "new"
			message = "Ready to link as an additional AoE2 profile."
		elif current.get("user_id") is None:
			status = "unowned"
			message = "Known but unowned profile; ready to link."
		elif int(current["user_id"]) == row["user_id"]:
			status = "existing"
			message = "This profile is already linked to the same Discord user."
		else:
			status = "conflict"
			message = "Profile is already owned by a different Discord user; bulk import cannot reassign it."
		summary[status] += 1
		if status in ("new", "unowned"):
			summary["ready"] += 1
		prepared.append({
			**row,
			"status": status,
			"message": message,
			"member_name": str(
				getattr(member, "display_name", None)
				or getattr(member, "nick", None)
				or getattr(member, "name", None)
				or ""),
		})

	public_rows = [{key: value for key, value in row.items() if key != "errors"} for row in prepared]
	return {
		"community": admin.payload(),
		"source": parsed["name"],
		"summary": summary,
		"rows": public_rows,
		"digest": digest,
		"can_apply": (
			summary["ready"] > 0 and not summary["invalid"]
			and not summary["not_member"] and not summary["conflict"]),
		"name_policy": "Imported AoE2 names are preview labels only until observed from the game API or a replay.",
	}, prepared


async def _identity_import_request(request):
	session = await _get_session(request)
	if not session:
		return None, web.json_response({"error": "Not logged in"}, status=401)
	admin, error = await _admin_community_context(request, session)
	if error is not None:
		return None, error
	if not _check_csrf(request, session):
		return None, web.json_response({"error": "Invalid or missing CSRF token"}, status=403)
	try:
		payload = await request.json()
	except Exception:
		return None, web.json_response({"error": "Request body must be valid JSON"}, status=400)
	try:
		preview, prepared = await _identity_import_preview(admin, payload)
	except onboarding.SeedInputError as exc:
		return None, web.json_response({"error": str(exc)}, status=400)
	return (session, admin, payload, preview, prepared), None


async def handle_api_identity_import_preview(request):
	result, error = await _identity_import_request(request)
	if error is not None:
		return error
	return web.json_response(result[3])


async def handle_api_identity_import_apply(request):
	result, error = await _identity_import_request(request)
	if error is not None:
		return error
	_session, admin, payload, preview, prepared = result
	provided_digest = payload.get("digest") if isinstance(payload, dict) else None
	if not isinstance(provided_digest, str) or not secrets.compare_digest(provided_digest, preview["digest"]):
		return web.json_response({"error": "Identity import changed after preview; preview it again"}, status=409)
	if preview["summary"]["invalid"] or preview["summary"]["not_member"]:
		return web.json_response({"error": "Fix every invalid or non-member row before applying"}, status=400)
	if preview["summary"]["conflict"]:
		return web.json_response({"error": "Resolve conflicting profile ownership before applying"}, status=409)
	if not preview["can_apply"]:
		return web.json_response({"error": "No new or unowned profiles remain to link"}, status=409)

	now = int(time.time())
	applied = 0
	raced_out = 0
	try:
		async with db.transaction() as tx:
			for row in prepared:
				if row["status"] not in ("new", "unowned"):
					continue
				if row["status"] == "new":
					changed = await tx.insert("identities", {
						"profile_id": row["profile_id"],
						"user_id": row["user_id"],
						# Imported names are not game observations. Keeping this
						# NULL preserves resolver.py's provenance contract.
						"aoe2_name": None,
						"confidence": "manual",
						"first_seen_at": now,
						"last_seen_at": now,
						"bound_at": now,
					}, on_duplicate="ignore")
				else:
					changed = await tx.execute(
						"UPDATE identities SET user_id=%s, confidence='manual', "
						"last_seen_at=%s, bound_at=%s "
						"WHERE profile_id=%s AND user_id IS NULL",
						[row["user_id"], now, now, row["profile_id"]])
				if not changed:
					raced_out += 1
					continue
				applied += 1
	except Exception as exc:
		log.error(
			f"Identity onboarding failed for community={admin.community.community_id}: {exc}")
		return web.json_response({"error": "Identity import could not be applied"}, status=500)
	if applied:
		resolver.invalidate_cache()
	return web.json_response({"ok": True, "applied": applied, "skipped_after_preview": raced_out})


class _MigrationPreflightError(RuntimeError):
	pass


def _migration_rating_blockers(existing_rows, source_players):
	"""Refuse to replace any rating state not created by ratings-only seeding."""
	source = {int(row["user_id"]): row for row in source_players}
	blockers = []
	for current in existing_rows or []:
		user_id = int(current["user_id"])
		incoming = source.get(user_id)
		if incoming is None:
			blockers.append(f"Existing rated user {user_id} is absent from the archive.")
			continue
		activity = any(int(current.get(key) or 0) for key in ("wins", "losses", "draws", "streak"))
		activity = activity or current.get("last_ranked_match_at") is not None
		if activity:
			blockers.append(f"User {user_id} already has live rating activity.")
			continue
		if current.get("rating") is None:
			continue
		if (int(current["rating"]) != int(incoming.get("rating") or 0)
		        or int(current.get("deviation") or 0) != int(incoming.get("deviation") or 0)):
			blockers.append(f"User {user_id} has a rating different from the archive snapshot.")
	return blockers[:20]


async def _pubobot_migration_preview(context, payload):
	parsed = pubobot_migration.parse_archive(payload)
	pickup = context.channel
	import_id = pubobot_migration.migration_digest(
		pickup.channel_id, context.target_channel_id, parsed)
	already = await db.select_one(
		["import_id", "created_at"], "community_imports", where={"import_id": import_id})
	match_count = await db.fetchone(
		"SELECT COUNT(*) AS n FROM matches WHERE channel_id=%s", [pickup.channel_id])
	history_count = await db.fetchone(
		"SELECT COUNT(*) AS n FROM rating_history WHERE channel_id=%s", [context.target_channel_id])
	existing_ratings = await db.select(
		["user_id", "rating", "deviation", "wins", "losses", "draws", "streak", "last_ranked_match_at"],
		"player_ratings", where={"channel_id": context.target_channel_id})
	blockers = []
	if context.target_channel_id != pickup.channel_id:
		blockers.append("Full history migration requires the pickup channel to own its rating table.")
	if int((match_count or {}).get("n") or 0):
		blockers.append("Target pickup channel already contains recorded matches.")
	if int((history_count or {}).get("n") or 0):
		blockers.append("Target rating channel already contains rating history.")
	blockers.extend(_migration_rating_blockers(existing_ratings, parsed["players"]))
	if already:
		blockers.append("This exact archive has already been imported into this channel.")
	if not parsed["matches"] or not parsed["players"]:
		blockers.append("Archive must contain at least one match and one player.")
	if any(
		int(getattr(getattr(match, "qc", None), "id", 0)) == pickup.channel_id
		for match in pickup.queue_channel.app.active_matches):
		blockers.append("A match is currently active in the target channel.")

	return {
		"community": pickup.admin.payload(),
		"target": {
			"channel_id": str(pickup.channel_id),
			"channel_name": pickup.channel.name,
			"rating_channel_id": str(context.target_channel_id),
		},
		"source": parsed["source_name"],
		"timezone": parsed["timezone"],
		"summary": {
			"matches": len(parsed["matches"]),
			"players": len(parsed["players"]),
			"player_matches": len(parsed["match_players"]),
			"rating_history": len(parsed["rating_history"]),
		},
		"preflight": {
			"status": "ready" if not blockers else "blocked",
			"blockers": blockers,
			"existing_seed_rows": len(existing_ratings or []),
			"match_ids": "Source IDs will be remapped into one atomically reserved global range.",
		},
		"digest": import_id,
		"can_apply": not blockers,
	}, parsed


async def _pubobot_migration_request(request):
	session = await _get_session(request)
	if not session:
		return None, web.json_response({"error": "Not logged in"}, status=401)
	context, error = await _admin_rating_context(request, session)
	if error is not None:
		return None, error
	if not _check_csrf(request, session):
		return None, web.json_response({"error": "Invalid or missing CSRF token"}, status=403)
	try:
		payload = await request.json()
	except Exception:
		return None, web.json_response({"error": "Request body must be valid JSON"}, status=400)
	try:
		preview, parsed = await _pubobot_migration_preview(context, payload)
	except pubobot_migration.MigrationInputError as exc:
		return None, web.json_response({"error": str(exc)}, status=400)
	return (session, context, payload, preview, parsed), None


async def handle_api_pubobot_migration_preview(request):
	result, error = await _pubobot_migration_request(request)
	if error is not None:
		return error
	return web.json_response(result[3])


async def _migration_transaction_preflight(tx, context, parsed, import_id):
	pickup = context.channel
	if any(
		int(getattr(getattr(match, "qc", None), "id", 0)) == pickup.channel_id
		for match in pickup.queue_channel.app.active_matches):
		raise _MigrationPreflightError("A match started after migration preview.")
	if await tx.fetchone(
			"SELECT import_id FROM community_imports WHERE import_id=%s FOR UPDATE", [import_id]):
		raise _MigrationPreflightError("This exact archive has already been imported.")
	match_count = await tx.fetchone(
		"SELECT COUNT(*) AS n FROM matches WHERE channel_id=%s", [pickup.channel_id])
	if int((match_count or {}).get("n") or 0):
		raise _MigrationPreflightError("Target channel received a match after preview.")
	history_count = await tx.fetchone(
		"SELECT COUNT(*) AS n FROM rating_history WHERE channel_id=%s", [context.target_channel_id])
	if int((history_count or {}).get("n") or 0):
		raise _MigrationPreflightError("Target channel received rating history after preview.")
	existing = await tx.fetchall(
		"SELECT user_id, rating, deviation, wins, losses, draws, streak, last_ranked_match_at "
		"FROM player_ratings WHERE channel_id=%s FOR UPDATE",
		[context.target_channel_id])
	blockers = _migration_rating_blockers(existing, parsed["players"])
	if blockers:
		raise _MigrationPreflightError(blockers[0])


async def handle_api_pubobot_migration_apply(request):
	result, error = await _pubobot_migration_request(request)
	if error is not None:
		return error
	session, context, payload, preview, parsed = result
	pickup = context.channel
	provided_digest = payload.get("digest") if isinstance(payload, dict) else None
	if not isinstance(provided_digest, str) or not secrets.compare_digest(provided_digest, preview["digest"]):
		return web.json_response({"error": "Migration archive changed after preview; preview it again"}, status=409)
	if not preview["can_apply"]:
		return web.json_response({"error": "Migration preflight is blocked", "blockers": preview["preflight"]["blockers"]}, status=409)

	community_id = pickup.admin.community.community_id
	import_id = preview["digest"]
	now = int(time.time())
	try:
		async with pickup.queue_channel.app.match_creation_lock:
			async with db.transaction() as tx:
				counter = await tx.fetchone("SELECT next_id FROM match_counter FOR UPDATE")
				if counter is None:
					maximum = await tx.fetchone(
						"SELECT COALESCE(MAX(match_id) + 1, 0) AS next_id FROM matches")
					first_match_id = int((maximum or {}).get("next_id") or 0)
					await tx.insert("match_counter", {"next_id": first_match_id})
				else:
					first_match_id = int(counter["next_id"])
				await _migration_transaction_preflight(tx, context, parsed, import_id)

				source_ids = sorted(row["source_match_id"] for row in parsed["matches"])
				match_ids = {source_id: first_match_id + index for index, source_id in enumerate(source_ids)}
				next_id = first_match_id + len(source_ids)
				await tx.execute("UPDATE match_counter SET next_id=%s", [next_id])

				matches = [{
					"match_id": match_ids[row["source_match_id"]],
					"channel_id": pickup.channel_id,
					"queue_id": None,
					"queue_name": row["queue_name"],
					"reported_at": row["reported_at"],
					"alpha_name": "Alpha",
					"beta_name": "Beta",
					"ranked": 1,
					"winner": row["winner"],
					"alpha_score": row["alpha_score"],
					"beta_score": row["beta_score"],
					"maps": row["maps"],
				} for row in parsed["matches"]]
				players = [{
					"channel_id": context.target_channel_id,
					**row,
				} for row in parsed["players"]]
				nicks = {row["user_id"]: row["nick"] for row in parsed["players"]}
				match_players = [{
					"match_id": match_ids[row["source_match_id"]],
					"channel_id": pickup.channel_id,
					"user_id": row["user_id"],
					"nick": nicks[row["user_id"]],
					"team": row["team"],
				} for row in parsed["match_players"]]
				history = [{
					"channel_id": context.target_channel_id,
					"user_id": row["user_id"],
					"at": row["at"],
					"rating_before": row["rating_before"],
					"rating_change": row["rating_change"],
					"deviation_before": row["deviation_before"],
					"deviation_change": row["deviation_change"],
					"match_id": match_ids[row["source_match_id"]] if row["source_match_id"] is not None else None,
					"reason": row["reason"],
				} for row in parsed["rating_history"]]
				mapping = [{
					"import_id": import_id,
					"community_id": community_id,
					"channel_id": pickup.channel_id,
					"source_match_id": source_id,
					"match_id": target_id,
				} for source_id, target_id in match_ids.items()]

				await tx.insert_many("matches", matches)
				await tx.insert_many("player_ratings", players, on_duplicate="replace")
				await tx.insert_many("match_players", match_players)
				await tx.insert_many("rating_history", history)
				await tx.insert_many("community_import_match_map", mapping)
				await tx.insert("community_imports", {
					"import_id": import_id,
					"community_id": community_id,
					"channel_id": pickup.channel_id,
					"rating_channel_id": context.target_channel_id,
					"kind": "pubobot_full",
					"source_name": parsed["source_name"],
					"timezone": parsed["timezone"],
					"created_by": int(session["user_id"]),
					"created_at": now,
					"match_count": len(matches),
					"player_count": len(players),
				})
	except _MigrationPreflightError as exc:
		return web.json_response({"error": str(exc)}, status=409)
	except Exception as exc:
		log.error(
			f"Historical migration failed for community={community_id} channel={pickup.channel_id}: {exc}")
		return web.json_response({"error": "Historical migration could not be applied"}, status=500)
	return web.json_response({
		"ok": True,
		"import_id": import_id,
		"matches": len(parsed["matches"]),
		"players": len(parsed["players"]),
		"first_match_id": first_match_id,
		"next_match_id": next_id,
	})


async def handle_api_guilds(request):
	"""Legacy alias for the pre-community settings client."""
	session = await _get_session(request)
	if not session:
		return web.json_response({"error": "Not logged in"}, status=401)

	guilds = []
	for community in await _admin_communities_for_session(session):
		guilds.append({
			**community,
			"community_id": community["id"],
			"id": community["guild_id"],
		})
	return web.json_response({"guilds": guilds})


async def handle_api_channels(request):
	session = await _get_session(request)
	if not session:
		return web.json_response({"error": "Not logged in"}, status=401)

	guild_id = None
	if _explicit_community_id(request) is None:
		guild_id = _positive_route_id(request, "guild_id")
		if not guild_id:
			return web.json_response({"error": "Community not found"}, status=404)
		if dc.get_guild(guild_id) is None:
			return web.json_response({"error": "Guild not found"}, status=404)
	admin, error = await _admin_community_context(request, session, guild_id=guild_id)
	if error is not None:
		return error
	enrolled = await _community_channel_ids(admin.community.community_id)

	channels = []
	for ch_id, qc in dc.app.channels.items():
		if ch_id not in enrolled or int(qc.guild_id) != admin.community.guild_id:
			continue
		ch = dc.get_channel(ch_id)
		if ch is None or int(getattr(getattr(ch, "guild", None), "id", 0)) != admin.community.guild_id:
			continue
		channels.append({
			"id": str(ch_id),
			"name": ch.name if ch else f"unknown-{ch_id}",
			"queues": len(qc.queues),
			"is_admin": True,
		})
	return web.json_response({
		"community": admin.payload(len(channels)),
		"channels": channels,
	})


async def handle_api_channel_config(request):
	session = await _get_session(request)
	if not session:
		return web.json_response({"error": "Not logged in"}, status=401)

	context, error = await _admin_channel_context(request, session)
	if error is not None:
		return error
	qc = context.queue_channel
	channel = context.channel

	if request.method == "GET":
		readable = qc.cfg.readable()
		variables = {}
		for name, var in qc.cfg_factory.variables.items():
			if _should_skip(var):
				continue
			variables[name] = _var_meta(var, readable.get(name))
		return web.json_response({
			"community": context.admin.payload(),
			"channel_name": channel.name,
			"guild_name": channel.guild.name,
			"sections": qc.cfg_factory.sections,
			"variables": variables,
			"is_admin": True,
		})

	# POST — update config
	# The community/admin context above is read-only. Check CSRF before parsing
	# or mutating config. Pre-CSRF this endpoint accepted any POST
	# with a valid session cookie, so a malicious page could rewrite a
	# logged-in admin's channel config with no interaction.
	if not _check_csrf(request, session):
		return web.json_response({"error": "Invalid or missing CSRF token"}, status=403)
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

	context, error = await _admin_channel_context(request, session)
	if error is not None:
		return error
	qc = context.queue_channel

	return web.json_response({
		"community": context.admin.payload(),
		"queues": [
			{"name": q.name, "size": q.cfg.size, "players": len(q.queue), "ranked": bool(q.cfg.ranked)}
			for q in qc.queues
		],
	})


async def handle_api_queue_config(request):
	session = await _get_session(request)
	if not session:
		return web.json_response({"error": "Not logged in"}, status=401)

	queue_name = request.match_info["queue_name"]
	context, error = await _admin_channel_context(request, session)
	if error is not None:
		return error
	qc = context.queue_channel

	queue = next((q for q in qc.queues if q.name.lower() == queue_name.lower()), None)
	if not queue:
		return web.json_response({"error": f"Queue '{queue_name}' not found"}, status=404)

	if request.method == "GET":
		readable = queue.cfg.readable()
		variables = {}
		for name, var in queue.cfg_factory.variables.items():
			if _should_skip(var):
				continue
			variables[name] = _var_meta(var, readable.get(name))
		return web.json_response({
			"community": context.admin.payload(),
			"queue_name": queue.name,
			"sections": queue.cfg_factory.sections,
			"variables": variables,
			"is_admin": True,
		})

	# POST
	# CSRF check first — see handle_api_channel_config for rationale.
	if not _check_csrf(request, session):
		return web.json_response({"error": "Invalid or missing CSRF token"}, status=403)
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
	# Full onboarding archives are base64 JSON (5 MB compressed becomes about
	# 6.7 MB on the wire). Keep an explicit ceiling above that payload while
	# still rejecting unbounded request bodies before any parser runs.
	app = web.Application(client_max_size=8 * 1024 * 1024)
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
	# Explicit public tenant routes. The unprefixed forms above are temporary
	# aliases for the configured flagship; these are the product contract.
	app.router.add_get('/api/communities/{community_id}/civ-stats', handle_civ_stats)
	app.router.add_get('/api/communities/{community_id}/strategies', handle_strategies)
	app.router.add_get('/api/communities/{community_id}/match-stats', handle_match_stats)
	app.router.add_get('/api/communities/{community_id}/leaderboard', handle_leaderboard)
	app.router.add_get('/api/communities/{community_id}/player-stats', handle_player_stats)
	app.router.add_get('/api/me', handle_api_me)
	# Tenant-aware admin/settings API. Every route below resolves the explicit
	# community and verifies Manage Guild (or owner) authority before reading.
	app.router.add_get('/api/admin/communities', handle_api_communities)
	app.router.add_get('/api/admin/communities/{community_id}', handle_api_community)
	app.router.add_get(
		'/api/admin/communities/{community_id}/overview',
		handle_api_community_overview)
	app.router.add_post(
		'/api/admin/communities/{community_id}/identities/import/preview',
		handle_api_identity_import_preview)
	app.router.add_post(
		'/api/admin/communities/{community_id}/identities/import/apply',
		handle_api_identity_import_apply)
	app.router.add_get('/api/admin/communities/{community_id}/channels', handle_api_channels)
	app.router.add_get(
		'/api/admin/communities/{community_id}/channels/{channel_id}/config',
		handle_api_channel_config)
	app.router.add_post(
		'/api/admin/communities/{community_id}/channels/{channel_id}/config',
		handle_api_channel_config)
	app.router.add_get(
		'/api/admin/communities/{community_id}/channels/{channel_id}/queues',
		handle_api_queues)
	app.router.add_post(
		'/api/admin/communities/{community_id}/channels/{channel_id}/ratings/seed/preview',
		handle_api_rating_seed_preview)
	app.router.add_post(
		'/api/admin/communities/{community_id}/channels/{channel_id}/ratings/seed/apply',
		handle_api_rating_seed_apply)
	app.router.add_post(
		'/api/admin/communities/{community_id}/channels/{channel_id}/migration/pubobot/preview',
		handle_api_pubobot_migration_preview)
	app.router.add_post(
		'/api/admin/communities/{community_id}/channels/{channel_id}/migration/pubobot/apply',
		handle_api_pubobot_migration_apply)
	app.router.add_get(
		'/api/admin/communities/{community_id}/channels/{channel_id}/queues/{queue_name}/config',
		handle_api_queue_config)
	app.router.add_post(
		'/api/admin/communities/{community_id}/channels/{channel_id}/queues/{queue_name}/config',
		handle_api_queue_config)
	# Legacy settings aliases. These now resolve through the same community
	# boundary and remain only for old dashboard clients/bookmarks.
	app.router.add_get('/api/guilds', handle_api_guilds)
	app.router.add_get('/api/guilds/{guild_id}/channels', handle_api_channels)
	app.router.add_get('/api/channels/{channel_id}/config', handle_api_channel_config)
	app.router.add_post('/api/channels/{channel_id}/config', handle_api_channel_config)
	app.router.add_get('/api/channels/{channel_id}/queues', handle_api_queues)
	app.router.add_get('/api/channels/{channel_id}/queues/{queue_name}/config', handle_api_queue_config)
	app.router.add_post('/api/channels/{channel_id}/queues/{queue_name}/config', handle_api_queue_config)
	# SPA routes. Keep after API/auth/lobby routes so refresh preserves app views without shadowing endpoints.
	#
	# `/luck` is deliberately absent and must not come back: the luck page's rows
	# rested on `luck_baseline`, which fires for every player in every valid Nomad
	# game and is stored in no table (nammaoe2bot/derived/game_labels.kind_for), so the
	# "% of valid starts" denominator every figure on it was quoted against no
	# longer exists. An unregistered route 404s, which is the honest answer to a
	# bookmarked link — re-adding it would serve the SPA shell for a page whose
	# data source is gone.
	app.router.add_get('/strategies', handle_index)
	app.router.add_get('/match-stats', handle_index)
	app.router.add_get('/leaderboard', handle_index)
	app.router.add_get('/civ-stats', handle_index)
	app.router.add_get('/dashboard', handle_index)
	app.router.add_get('/player/{user_id}', handle_index)
	app.router.add_get('/player-profile/{user_id}', handle_index)
	app.router.add_get('/c/{community_id}', handle_index)
	app.router.add_get('/c/{community_id}/strategies', handle_index)
	app.router.add_get('/c/{community_id}/match-stats', handle_index)
	app.router.add_get('/c/{community_id}/leaderboard', handle_index)
	app.router.add_get('/c/{community_id}/civ-stats', handle_index)
	app.router.add_get('/c/{community_id}/dashboard', handle_index)
	app.router.add_get('/c/{community_id}/player/{user_id}', handle_index)
	app.router.add_get('/c/{community_id}/player-profile/{user_id}', handle_index)
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
