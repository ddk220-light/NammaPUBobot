"""Pytest bootstrap — install minimal fakes for the `core.*` modules so
test files can import the bot's parser helpers without a live MySQL
connection, a Discord client, or a real ``config.cfg``.

Why this is needed: the bot's module-load wiring is aggressive.
``nammaoe2bot/features/elo_sync.py`` does ``from nammaoe2bot.runtime.database import db`` at module
load, and ``nammaoe2bot/runtime/database/__init__.py`` in turn does
``db = init_db(cfg.DB_URI)`` at module load — constructing the DB
adapter from ``config.cfg`` the moment anything reaches into the core
layer. That's fine in production but means the first line of any
unit-test run would blow up trying to read ``config.cfg`` (which
doesn't exist in CI) and dial MySQL.

pytest imports ``conftest.py`` before it imports any test module, so
as long as the fakes here land in ``sys.modules`` before
``from nammaoe2bot.features.elo_sync import ...`` executes, every downstream ``import``
of the real module is short-circuited and gets the fake back.

This file intentionally does NOT import from the real ``core`` package
at module level — doing so would defeat the whole point (it would
trigger the same config-and-db chain we're trying to avoid). The one
real-``core`` import lives inside the ``adapter_module`` fixture at the
bottom, which runs per-test and only after its own stubs are in place.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


# Make the repo root importable so ``from nammaoe2bot.features.elo_sync import ...``
# works regardless of which directory pytest was invoked from.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(_REPO_ROOT))


# ─── nammaoe2bot.runtime.config ─────────────────────────────────────────────────────
# The real module loads config.cfg via SourceFileLoader and exposes it
# as `cfg`. Tests only need a tiny subset of attributes — whatever the
# parsers happen to look up. Use SimpleNamespace so getattr-with-default
# works naturally.
_fake_core_config = types.ModuleType('nammaoe2bot.runtime.config')
_fake_core_config.cfg = types.SimpleNamespace(
	DB_URI='mysql://test:test@localhost:3306/test',
	DC_OWNER_ID=0,
	PUBOBOT_USER_ID=0,
	LOBBYBOT_USER_ID=0,
	STATUS='',
	HELP='',
	FLAGSHIP_GUILD_IDS=[],
	DEPLOYMENT_MODE='self_hosted',
	# nammaoe2bot/ingest/store.is_enabled() reads this (it replaced the single-row
	# ops table 007_raw_renames dropped). Its own getattr default is True, so this
	# is here to make the test environment's answer explicit, not incidental.
	REPLAY_INGEST_ENABLED=True,
)
sys.modules['nammaoe2bot.runtime.config'] = _fake_core_config


# ─── nammaoe2bot.runtime.console ────────────────────────────────────────────────────
# log.info / log.error / log.debug / log.warning are called from many
# places in the parsers. A null logger that swallows every call is all
# we need for unit tests. `alive` is a bool attribute `think()` reads
# but no parser touches it — kept here for completeness in case future
# tests reach for it.
class _NullLog:
	def __getattr__(self, _name):
		def _noop(*_a, **_k):
			return None
		return _noop


_fake_core_console = types.ModuleType('nammaoe2bot.runtime.console')
_fake_core_console.log = _NullLog()
_fake_core_console.alive = True
sys.modules['nammaoe2bot.runtime.console'] = _fake_core_console


# ─── nammaoe2bot.runtime.database ───────────────────────────────────────────────────
# Every DB method raises — unit tests must not hit the database. If a
# test needs to exercise a function that writes to DB, it should either
# monkeypatch the method or use a proper integration-test harness.
# `types` mimics the MySQL adapter's type registry that `bot/stats/*`
# references at import time for ensure_table() schema definitions.
class _RaisingDB:
	class types:
		int = 'BIGINT'
		bool = 'TINYINT(1)'
		str = 'VARCHAR(191)'
		text = 'VARCHAR(2000)'
		float = 'FLOAT'
		dict = 'MEDIUMTEXT'

	def ensure_table(self, *_a, **_k):
		# No-op: nammaoe2bot/pickup/stats.py calls this at import time. In a
		# production boot this actually creates tables; in tests we just
		# let the call pass so the module-level statements succeed.
		return None

	async def _unexpected(self, *_a, **_k):
		raise RuntimeError(
			'nammaoe2bot.runtime.database.db method called during unit test — mock it '
			'in the test, or move the function under test to a pure '
			'helper that does not touch the DB.'
		)

	select_one = _unexpected
	select = _unexpected
	insert = _unexpected
	insert_many = _unexpected
	update = _unexpected
	delete = _unexpected
	execute = _unexpected
	executemany = _unexpected
	fetchone = _unexpected
	fetchall = _unexpected


_fake_core_database = types.ModuleType('nammaoe2bot.runtime.database')
_fake_core_database.db = _RaisingDB()
# __path__ so the stub does not hide its own submodules. The adapter tests
# import nammaoe2bot.runtime.database.mysql for real (that IS the code under
# test), and a plain ModuleType here answers "not a package" — which is a
# collection error, not a skip, so it would have been loud rather than silent.
# It became possible only when core/database.py and core/DBAdapters/ merged
# into one package; before that the stub and the adapters were siblings.
_fake_core_database.__path__ = [str(_REPO_ROOT / 'nammaoe2bot' / 'runtime' / 'database')]
sys.modules['nammaoe2bot.runtime.database'] = _fake_core_database


# ─── aiohttp (stub) ──────────────────────────────────────────────────
# nammaoe2bot/features/civs/matcher.py does `import aiohttp` at module load, but only USES it
# (ClientSession, ClientError, ...) inside async functions the unit tests never
# run. CI's pytest job installs only pytest, so a bare stub lets civ_matcher
# import for its pure-helper tests (_load_profile_uid_map) without pulling in
# the full aiohttp runtime dependency. Same trick as the core.* fakes above.
_fake_aiohttp = types.ModuleType('aiohttp')
sys.modules['aiohttp'] = _fake_aiohttp


# ─── aiohttp.web (stub) ──────────────────────────────────────────────
# `nammaoe2bot/web/server.py` does `from aiohttp import web` and every handler in it ends in
# `web.json_response(...)`. Until stage 5d the only way to test that file was to
# parse it with `ast` and assert on its source (see test_web_identity.py), which
# is how a web endpoint could read a retired table for two stages without a test
# noticing: a source-level assertion cannot tell you what an endpoint RETURNS.
#
# json_response here keeps the payload and the status on the object it returns
# instead of serialising them, so a test can drive a handler and assert on the
# actual response. That is the whole point — these are the real handlers, the
# real SQL and the real payload shaping, with only the transport faked.
class _FakeResponse:
	""" A response object, not a bag: the SPA branches on `status` (401 sends it
	to the login screen, 404 to "not found"), so a fake that let a caller's
	status evaporate would make an auth regression returning {"error": "Not
	logged in"} with status 200 completely invisible. `status` is therefore
	validated here rather than merely stored, and tests/test_web_repoint.py
	asserts on it for every error branch it drives. """

	def __init__(self, payload=None, status=200, text=None, content_type=None):
		if not isinstance(status, int) or isinstance(status, bool):
			raise TypeError(f"HTTP status must be an int, got {status!r}")
		self.payload = payload
		self.status = status
		self.text = text
		self.content_type = content_type
		# aiohttp's Response carries the cookie jar the auth handlers write to.
		self.cookies = {}
		self.deleted_cookies = []

	def set_cookie(self, name, value, **kwargs):
		self.cookies[name] = dict(value=value, **kwargs)

	def del_cookie(self, name, **kwargs):
		self.deleted_cookies.append(name)
		self.cookies.pop(name, None)


class _FakeRouter:
	def __init__(self):
		self.routes = []

	def add_get(self, path, handler):
		self.routes.append(('GET', path, handler))

	def add_post(self, path, handler):
		self.routes.append(('POST', path, handler))


class _FakeApplication:
	def __init__(self, **kwargs):
		self.router = _FakeRouter()
		self.client_max_size = kwargs.get("client_max_size")


def _fake_json_response(payload=None, status=200):
	return _FakeResponse(payload=payload, status=status)


_fake_aiohttp_web = types.ModuleType('aiohttp.web')
_fake_aiohttp_web.json_response = _fake_json_response
_fake_aiohttp_web.Response = _FakeResponse
_fake_aiohttp_web.Application = _FakeApplication
_fake_aiohttp_web.AppRunner = object
_fake_aiohttp_web.TCPSite = object


# ─── aiohttp.web's HTTP exceptions ───────────────────────────────────────
# These were ONE class wearing five names, which did not merely leave the OAuth
# path untested — it made it untestable. `isinstance(HTTPFound('/x'),
# HTTPNotFound)` was True, so pytest.raises could not fail on any of them;
# `location` was read from kwargs while all three call sites in nammaoe2bot/web/server.py pass
# it positionally, so it was always None; and set_cookie/del_cookie were absent
# entirely, so login and logout AttributeError'd the moment a test touched them.
#
# In real aiohttp these exceptions ARE responses (HTTPException subclasses
# Response), which is why nammaoe2bot/web/server.py does `resp = web.HTTPFound("/")`,
# `resp.set_cookie(...)`, `raise resp`. The shapes below keep that: distinct
# types, the real positional signatures, a status per class, and the cookie jar.
class _FakeHTTPException(_FakeResponse, Exception):
	status_code = 500

	def __init__(self, *, headers=None, reason=None, body=None, text=None, content_type=None):
		_FakeResponse.__init__(self, payload=None, status=self.status_code,
		                       text=text, content_type=content_type)
		Exception.__init__(self, text or reason or type(self).__name__)
		self.reason = reason
		self.body = body
		self.headers = dict(headers or {})


class _FakeHTTPRedirection(_FakeHTTPException):
	def __init__(self, location, **kwargs):
		super().__init__(**kwargs)
		# Positional, and required. aiohttp raises ValueError on a missing
		# location; a fake that quietly stored None is how "the redirect goes
		# nowhere" ships green.
		if not location:
			raise ValueError("HTTP redirect requires a location")
		self.location = str(location)
		self.headers['Location'] = self.location


class _FakeHTTPFound(_FakeHTTPRedirection):
	status_code = 302


class _FakeHTTPBadRequest(_FakeHTTPException):
	status_code = 400


class _FakeHTTPUnauthorized(_FakeHTTPException):
	status_code = 401


class _FakeHTTPForbidden(_FakeHTTPException):
	status_code = 403


class _FakeHTTPNotFound(_FakeHTTPException):
	status_code = 404


_fake_aiohttp_web.HTTPException = _FakeHTTPException
_fake_aiohttp_web.HTTPBadRequest = _FakeHTTPBadRequest
_fake_aiohttp_web.HTTPFound = _FakeHTTPFound
_fake_aiohttp_web.HTTPForbidden = _FakeHTTPForbidden
_fake_aiohttp_web.HTTPNotFound = _FakeHTTPNotFound
_fake_aiohttp_web.HTTPUnauthorized = _FakeHTTPUnauthorized
sys.modules['aiohttp.web'] = _fake_aiohttp_web
_fake_aiohttp.web = _fake_aiohttp_web


# ─── nextcord (stub) ─────────────────────────────────────────────────
# Reached only through nammaoe2bot/runtime/cfg_factory.py (`from nextcord import Guild`, for an
# isinstance check) and nammaoe2bot/runtime/utils.py (Embed + three utils helpers), both of
# which nammaoe2bot/web/server.py imports. Permissive on purpose: nothing under test calls into
# it, so a stand-in class that accepts anything is enough, and pinning a fuller
# shape would be inventing an API contract this repo does not own.
class _NextcordStub:
	def __init__(self, *_a, **_k):
		pass

	def __getattr__(self, _name):
		return _NextcordStub()


class FakeEmbed:
	""" Faithful enough to assert on, unlike the permissive stub above, and the
	ONLY definition of it in the suite.

	This one is NOT a rubber stamp on purpose. nammaoe2bot/runtime/utils.py's error_embed /
	ok_embed build an Embed at import-of-nammaoe2bot.runtime.utils time from whatever
	sys.modules['nextcord'] holds, and nammaoe2bot.runtime.utils is imported (via
	nammaoe2bot.runtime.cfg_factory, via nammaoe2bot/web/server.py) during collection of
	tests/test_web_repoint.py — before any test's own nextcord fake is
	installed. A stub that swallowed the kwargs would make `embed.title` a stub
	object and every copy assertion in tests/test_identity.py would compare a
	stub to a string.

	Which fake ends up behind nammaoe2bot.runtime.utils therefore depends on COLLECTION ORDER,
	and for a while the answer was "whichever of two hand-maintained copies got
	there first" — tests/test_identity.py and tests/test_scouting_report.py each
	carried their own, kept in step by a comment. Both now import this class
	(`from tests.conftest import FakeEmbed`), so there is one definition, no
	copy to drift, and nothing for an ordering to pick between. """

	def __init__(self, title=None, description=None, colour=None, color=None, **_kw):
		self.title = title
		self.description = description
		self.colour = colour if colour is not None else color
		self.color = self.colour
		self.fields = []
		self.footer_text = None

	def add_field(self, name=None, value=None, inline=True):
		self.fields.append(dict(name=name, value=value, inline=inline))
		return self

	def set_footer(self, text=None, **_kw):
		# RECORDED, not swallowed. A footer is not decoration here: it is where
		# the boards state their window, their sample floor and how many players
		# cleared it, and a fake that dropped it left every one of those claims
		# unassertable while the embed still "rendered".
		self.footer_text = text
		return self


class _InteractionType:
	""" The component-interaction discriminator, and the ONE piece of nextcord a
	button router needs at runtime.

	`nammaoe2bot/features/betting/interactions.py` (and `nammaoe2bot/features/quiz/interactions.py`, and
	`nammaoe2bot/derived/classifications/interactions.py`) all open with

	    if interaction.type != nextcord.InteractionType.component: return

	which is the first line of the guard chain in front of the gold. Without
	these five names that line is an AttributeError, the whole handler is
	untestable, and the money entry point ships with no test at all — which is
	exactly what happened.

	The values are Discord's own wire numbers, not invented ones: a fake
	interaction carrying `type = 3` is carrying what the gateway sends. """

	ping = 1
	application_command = 2
	component = 3
	application_command_autocomplete = 4
	modal_submit = 5


class _FakeButton:
	""" The three attributes a bet button IS: which side and stake it stakes
	(carried in `custom_id`, the only thing that survives a redeploy), which row
	it sits on, and what it says. Kept faithful because `custom_id` is the wire
	protocol between nammaoe2bot/features/betting/embeds.bet_view and the router that parses
	it back — a stub that swallowed it would let the two drift apart. """

	def __init__(self, *, style=None, row=None, label=None, emoji=None, custom_id=None, **_kw):
		self.style = style
		self.row = row
		self.label = label
		self.emoji = emoji
		self.custom_id = custom_id


class _FakeView:
	""" `timeout` and `auto_defer` are recorded rather than swallowed for the
	same reason nammaoe2bot/features/betting/embeds.py sets them explicitly: timeout=None is
	what makes the buttons outlive the process, and auto_defer=False is what
	stops nextcord acking a press before the global router sees it. """

	def __init__(self, timeout=None, auto_defer=True, **_kw):
		self.timeout = timeout
		self.auto_defer = auto_defer
		self.children = []

	def add_item(self, item):
		self.children.append(item)
		return item


class _ButtonStyleStub:
	""" nextcord's ButtonStyle is an enum; every member answers to its own name
	here, which is enough to tell blue from red without inventing values. """

	def __getattr__(self, name):
		return name


class _FakeSelectOption:
	""" nextcord.SelectOption, one per lettered choice on a multi-answer quiz
	vote. `label` is the thing a test can actually see ("A. Knight"); kept
	faithful for the same reason _FakeButton is — nammaoe2bot/features/quiz/embeds.vote_view's
	StringSelect IS its options, so a stub that swallowed them would make the
	multi-answer path untestable rather than merely unasserted. """

	def __init__(self, *, label=None, value=None, description=None, emoji=None, default=False, **_kw):
		self.label = label
		self.value = value
		self.description = description
		self.emoji = emoji
		self.default = default


class _FakeStringSelect:
	""" nextcord.ui.StringSelect — the multi-answer quiz vote control
	(nammaoe2bot/features/quiz/embeds.vote_view, multi=True). `custom_id` is the wire protocol
	back to the router exactly as it is for _FakeButton, and `max_values` is
	what proves "select ALL that apply" actually allows more than one pick. """

	def __init__(self, *, custom_id=None, placeholder=None, min_values=1, max_values=1,
			options=None, row=None, **_kw):
		self.custom_id = custom_id
		self.placeholder = placeholder
		self.min_values = min_values
		self.max_values = max_values
		self.options = list(options or [])
		self.row = row


class _UiStub:
	""" `nextcord.ui`, reached because nammaoe2bot/features/betting/embeds.py and
	nammaoe2bot/features/quiz/embeds.py import it at module level to build their views. View,
	Button and StringSelect are real enough to assert on (see above) so
	bet_view()/vote_view() can be DRIVEN rather than merely imported — the card
	shipping with no buttons at all was invisible to the suite while this was a
	rubber stamp. Everything else stays permissive. """

	View = _FakeView
	Button = _FakeButton
	StringSelect = _FakeStringSelect

	def __getattr__(self, _name):
		return _NextcordStub


class _FakeDiscordException(Exception):
	""" nextcord.DiscordException, the base of every error the library raises.
	nammaoe2bot/pickup/match/substitution.py imports it at module load to swallow a failed embed
	send, so without it that module — and everything reachable through it —
	cannot be imported by a test at all. """


class _FakeHTTPError(_FakeDiscordException):
	""" nextcord.HTTPException — the base of every FAILED API call, and the
	class the quiz resolve names by hand around the fetch-and-edit of the poll
	card it is about to lock. The real hierarchy is
	DiscordException -> HTTPException -> {NotFound, Forbidden,
	DiscordServerError}, and it is reproduced rather than flattened because the
	whole point of that except clause is the SET of failures it covers: a 404
	(card deleted) and a 403 (permissions revoked) both have to reach it, and a
	stub where Forbidden was not an HTTPException would make the persistent-
	failure case look handled when it was not. """

	def __init__(self, response=None, message=None):
		super().__init__(message or "HTTP error")
		self.response = response
		self.status = None


class _FakeNotFound(_FakeHTTPError):
	""" nextcord.NotFound — a 404 from the API. A permissive stub in its place
	turned the resolve's except clause into an AttributeError the moment a test
	drove the branch, so the "someone deleted the card" path was untestable
	rather than merely unasserted. The real signature takes (response,
	message); nothing here reads either, so both stay optional. """

	def __init__(self, response=None, message=None):
		super().__init__(response, message or "Not Found")
		self.status = 404


class _FakeForbidden(_FakeHTTPError):
	""" nextcord.Forbidden — a 403. Unlike a 404 this is PERSISTENT: the bot
	lost Manage Messages / Read Message History and every retry will fail the
	same way, which is exactly why the resolve may not let it block the close.
	"""

	def __init__(self, response=None, message=None):
		super().__init__(response, message or "Forbidden")
		self.status = 403


def _find(predicate, seq):
	""" The REAL nextcord.utils.find, not a rubber stamp.

	`lambda *_a, **_k: None` was cheap while nothing under test called it, but
	nammaoe2bot/pickup/match/substitution.py resolves a player's team with it on every /subfor
	(`find(lambda t: player1 in t, self.m.teams)`), and a version that always
	answers None turns "which team is this player on" into an unconditional
	crash — which would make the sub paths untestable rather than merely
	unasserted. Six lines of real behaviour costs nothing and invents no API:
	this is what the library does. """
	for element in seq:
		if predicate(element):
			return element
	return None


def _get(iterable, **attrs):
	""" The real nextcord.utils.get: first element whose attributes all match.
	Nested lookups (`channel__name=...`) use the library's double-underscore
	spelling. """
	def _matches(element):
		for key, expected in attrs.items():
			value = element
			for part in key.split('__'):
				value = getattr(value, part, None)
			if value != expected:
				return False
		return True
	return _find(_matches, iterable)


class _FakeColour:
	""" nextcord.Colour, in BOTH calling conventions this repo actually uses:
	`Colour(0xRRGGBB)` directly (nammaoe2bot/features/betting/embeds.py) and the named
	classmethods — `.blurple()` / `.gold()` / `.green()` / `.purple()` —
	nammaoe2bot/features/quiz/embeds.py calls to pick each embed kind's colour. The plain
	`lambda value=0: value` this used to be answered `Colour(x)` fine but had
	no `.blurple` etc., so the first test to actually drive a quiz embed
	(rather than merely import the module) AttributeError'd before Embed() was
	ever reached — nothing about vote_view/poll_embed's own logic was wrong,
	the stub just never had to support this half of the real API before. """

	def __init__(self, value=0):
		self.value = value

	@classmethod
	def blurple(cls):
		return cls(0x5865F2)

	@classmethod
	def gold(cls):
		return cls(0xF1C40F)

	@classmethod
	def green(cls):
		return cls(0x2ECC71)

	@classmethod
	def purple(cls):
		return cls(0x9B59B6)


_fake_nextcord = types.ModuleType('nextcord')
# The names the REAL pickup/match chain imports. tests/test_match_lifecycle_e2e.py
# drives an actual Match through an actual lifecycle, so match.py, embeds.py,
# checkin.py and substitution.py all have to import for real.
for _name in ('Guild', 'Member', 'TextChannel', 'Role', 'Client', 'Intents',
              'Streaming', 'Forbidden', 'Message', 'Interaction', 'ChannelType',
              'Activity', 'ActivityType'):
	setattr(_fake_nextcord, _name, _NextcordStub)
_fake_nextcord.DiscordException = _FakeDiscordException
_fake_nextcord.HTTPException = _FakeHTTPError
_fake_nextcord.NotFound = _FakeNotFound
_fake_nextcord.Forbidden = _FakeForbidden
_fake_nextcord.Embed = FakeEmbed
_fake_nextcord.Colour = _FakeColour
_fake_nextcord.Color = _fake_nextcord.Colour
_fake_nextcord.SelectOption = _FakeSelectOption
_fake_nextcord.InteractionType = _InteractionType
_fake_nextcord.ui = _UiStub()
_fake_nextcord.ButtonStyle = _ButtonStyleStub()
# `nextcord.abc`, reached by nammaoe2bot/discord/context.py for the abc.GuildChannel
# annotation on Context.__init__. Annotation only — that file declares
# `from __future__ import annotations`, so the attribute is never evaluated —
# but `from nextcord import abc` is a real module-level import and has to
# resolve to something.
_fake_nextcord.abc = types.ModuleType('nextcord.abc')
_fake_nextcord.abc.GuildChannel = _NextcordStub
# `nextcord.errors`, imported by nammaoe2bot/pickup/match/checkin.py. Same
# class object as nextcord.DiscordException — the real library re-exports it,
# and an `except DiscordException` in one module has to catch what another
# module raises.
_fake_nextcord.errors = types.ModuleType('nextcord.errors')
_fake_nextcord.errors.DiscordException = _FakeDiscordException
_fake_nextcord_utils = types.ModuleType('nextcord.utils')
_fake_nextcord_utils.get = _get
_fake_nextcord_utils.find = _find
_fake_nextcord_utils.escape_markdown = lambda s: s


class _MissingSentinel:
	""" Stands in for nextcord.utils.MISSING — the "argument not supplied at
	all" sentinel real nextcord tests with `is not MISSING` before touching an
	optional kwarg like `view`. `None` is a real, different value to that
	check, which is exactly the distinction nammaoe2bot/features/betting/interactions.py's
	`_eph` depends on: a caller that means "no view" must pass this sentinel
	(the default), because passing `None` crashes in production the same way
	FakeResponse/FakeFollowup below are written to crash on it too. """

	__slots__ = ()

	def __repr__(self):
		return "..."

	def __bool__(self):
		return False


_fake_nextcord_utils.MISSING = _MissingSentinel()
_fake_nextcord.utils = _fake_nextcord_utils
sys.modules['nextcord'] = _fake_nextcord
sys.modules['nextcord.abc'] = _fake_nextcord.abc
sys.modules['nextcord.errors'] = _fake_nextcord.errors
sys.modules['nextcord.utils'] = _fake_nextcord_utils

# nammaoe2bot/runtime/cfg_factory.py's only other third-party import.
_fake_emoji = types.ModuleType('emoji')
_fake_emoji.emojize = lambda s, **_k: s
_fake_emoji.demojize = lambda s, **_k: s
_fake_emoji.EMOJI_DATA = {}
sys.modules['emoji'] = _fake_emoji


# ─── nammaoe2bot.runtime.client (stub) ──────────────────────────────────────────────
# The real module subclasses nextcord.Client and builds an Intents object at
# import time, so it is faked outright rather than run against the permissive
# nextcord stub above. nammaoe2bot/web/server.py reaches `dc` for three things only: looking up
# a user's avatar, walking the guild list, and the readiness flag the health
# endpoint reports. All three answer "nothing" here, which is the state a test
# with no Discord connection is actually in.
class _FakeDiscordClient:
	guilds = ()

	def get_user(self, _user_id):
		return None

	def get_channel(self, _channel_id):
		return None

	def get_guild(self, _guild_id):
		# nammaoe2bot/web/server.py's dashboard-config endpoints call this; without it they
		# AttributeError on contact rather than taking their "Guild not found"
		# branch, which is the branch a test with no Discord connection wants.
		return None

	def is_ready(self):
		return False

	# The bot's own user, which pickup/match/embeds.py reaches for an avatar to
	# stamp on the footer of every match embed. `avatar = None` takes the
	# `if dc.user.avatar else None` branch, which is the state a client with no
	# Discord connection is genuinely in.
	user = types.SimpleNamespace(id=0, name="nammaoe2bot", avatar=None)


class _FakeMember:
	""" nammaoe2bot.runtime.client.FakeMember — the stand-in the bot builds for a player who
	is named by `name@id` rather than by a real Discord mention. Only
	nammaoe2bot/discord/context.py imports it, and only inside Context.get_member. """

	def __init__(self, guild, user_id, name):
		self.id = user_id
		self.name = name
		self.nick = None
		self.roles = []
		self.bot = True


_fake_core_client = types.ModuleType('nammaoe2bot.runtime.client')
_fake_core_client.dc = _FakeDiscordClient()
_fake_core_client.FakeMember = _FakeMember

# The real entrypoint does `dc.app = Application(client=dc)` at boot, and the
# live state that used to be bot/__init__.py globals now lives there. Without
# this the stub is a DIFFERENT SHAPE from production: any path a test reaches
# that touches dc.app raises AttributeError, which reads as a broken test
# rather than as the missing wiring it would actually be.
#
# Loaded BY PATH, not as `from nammaoe2bot.app import ...`: importing it through the
# package runs bot/__init__.py, which pulls the whole nextcord-heavy import
# graph and fails here. Deliberately NOT wrapped in try/except — if this stops
# working the stub silently reverts to the wrong shape and every test that
# touches app state starts passing for the wrong reason.
_app_spec = importlib.util.spec_from_file_location(
	"_bot_app_for_tests", Path(__file__).resolve().parent.parent / "nammaoe2bot" / "app.py")
_app_mod = importlib.util.module_from_spec(_app_spec)
_app_spec.loader.exec_module(_app_mod)
_fake_core_client.dc.app = _app_mod.Application(client=_fake_core_client.dc)

sys.modules['nammaoe2bot.runtime.client'] = _fake_core_client


# ─── bot (package shim) ──────────────────────────────────────────────
# Pre-register an empty `bot` package in sys.modules with an explicit
# __path__, so `from nammaoe2bot.features.elo_sync import X` resolves the submodule without
# running bot/__init__.py.
#
# THE REASON CHANGED, AND THE SHIM STAYED. It used to be load-bearing:
# bot/__init__.py re-exported half the codebase and imported every feature
# package for its ensure_table side effects, so reaching one parser pulled in
# nextcord, aiomysql and the whole graph. That file is now a docstring and
# nothing else — the side-effect imports moved to nammaoe2bot/bootstrap.py, which
# only the entrypoint calls — so importing it for real would be harmless.
#
# It is kept because tests monkeypatch submodules ONTO this object:
# `monkeypatch.setattr(sys.modules["nammaoe2bot"], "community", fake)` is what makes a
# function-local `from nammaoe2bot import community` hand back a stub instead of
# opening a DB connection (see tests/test_predictions_flow.py). Patching a
# package attribute is the supported way to do that, and it wants a package
# object that belongs to the test run rather than to the import system.
_fake_bot = types.ModuleType('bot')
_fake_bot.__path__ = [str(_REPO_ROOT / 'bot')]
sys.modules['bot'] = _fake_bot


# ─── nammaoe2bot.runtime.database.mysql (real module, stubbed drivers) ────────────
# Unlike everything above, this hands back the REAL adapter module — the
# thing under test — with only its two driver imports faked. It is a
# fixture rather than a module-level stub because the fake drivers must
# not sit in sys.modules for the rest of the run.
#
# Both drivers are stubbed unconditionally: CI installs pytest ONLY (see
# .github/workflows/ci.yml), so importing the adapter for real is not an
# option, and stubbing conditionally would mean the adapter tests
# exercised different code on a developer machine than in CI.
#
# It lives here, not in a test file, because "which DB drivers are absent"
# is a fact about the environment rather than about any one test — the
# duplicate-fixture alternative grows another copy with every adapter test.
@pytest.fixture
def adapter_module(monkeypatch):
	fake_aiomysql = types.ModuleType("aiomysql")
	fake_aiomysql.Pool = object
	fake_aiomysql.cursors = types.SimpleNamespace(DictCursor=object)
	fake_aiomysql.create_pool = None
	monkeypatch.setitem(sys.modules, "aiomysql", fake_aiomysql)

	# DISTINCT classes, not five aliases of Exception. Adapter.wrap_exc is a
	# chain of `e.__class__ == mysqlErr.X` tests whose whole job is to map one
	# driver error to one of the adapter's own types — and nammaoe2bot/features/betting/gold.py
	# catches exactly one of them (IntegrityError) to implement the side lock, so
	# "which branch fired" is a money question. Aliased to Exception, every
	# branch matched every error and the mapping was unassertable; two of the
	# names (IntegrityError, ProgrammingError) were missing outright, so touching
	# those branches raised AttributeError out of the error handler itself.
	class _PyMysqlError(Exception):
		pass

	fake_pymysql = types.ModuleType("pymysql")
	fake_pymysql.err = types.SimpleNamespace(
		Error=_PyMysqlError,
		**{name: type(name, (_PyMysqlError,), {}) for name in (
			"InternalError", "OperationalError", "DataError",
			"IntegrityError", "ProgrammingError")})
	monkeypatch.setitem(sys.modules, "pymysql", fake_pymysql)
	monkeypatch.setitem(sys.modules, "pymysql.err", fake_pymysql.err)

	monkeypatch.delitem(sys.modules, "nammaoe2bot.runtime.database.mysql", raising=False)
	import nammaoe2bot.runtime.database.mysql as mysql
	monkeypatch.delitem(sys.modules, "nammaoe2bot.runtime.database.mysql", raising=False)
	return mysql
