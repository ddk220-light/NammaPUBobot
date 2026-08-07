# -*- coding: utf-8 -*-
"""The storyline-stash clearing contract, in the two places it now lives.

bot/team_insights.py's build_insights_embed stashes match.storyline_ctx the
moment it *computes* a tease -- but "computed" is not "posted". If the
subsequent ctx.notice(embed=insights_embed) send then fails, nobody in the
channel ever saw the tease, and the stash must be dropped so
bot/storyline_payoff.py's build_payoff_embed -- which trusts the stash to mean
"a tease was shown" -- never answers a tease nobody saw.

tests/test_storyline_payoff.py's test_no_storyline_ctx_at_all_builds_no_embed
already proves the payoff half of that contract (no stash -> no payoff embed).
This file proves the other half.

IT USED TO BE ONE END-TO-END TEST, because Match.final_message both posted the
tease and cleared the stash. The domain no longer knows storylines exist: it
posts the teams and emits `teams_posted`, and bot/wiring.py subscribes the
storyline handler. So the contract splits in two, and both halves are here --
that final_message really announces, and that the handler really clears. The
third link, that wiring subscribes something to `teams_posted` at all, is
tests/test_match_lifecycle.py.

Importing the real bot.match.match module pulls in nextcord and the rest of
its transitive import chain (nammaoe2bot.discord.client, nammaoe2bot.runtime.utils, bot.match.check_in,
bot.match.draft, bot.match.embeds), none of which is installed in this test
environment (CI installs only pytest; nextcord and prettytable are absent).
This module fakes just enough of that chain -- the same sys.modules-injection
trick tests/conftest.py already uses for nammaoe2bot.runtime.config/console/database -- to
import the real match.py and exercise the real final_message body end to end.
bot.team_insights itself is NOT faked: it is imported for real, and only its
build_insights_embed function is monkeypatched, so the test exercises the
actual handshake between the two modules rather than a re-description of it.
"""
from __future__ import annotations

import asyncio
import sys
import types


def _install_fakes(monkeypatch):
	"""Minimal stand-ins for match.py's import chain, registered in
	sys.modules so ``import bot.match.match`` (and the check_in/draft/embeds
	submodules it pulls in) succeed without the real nextcord/prettytable
	packages installed. bot.match.subbing has no external imports of its own
	and is left to import for real."""

	class _DiscordException(Exception):
		pass

	fake_nextcord = types.ModuleType("nextcord")
	fake_nextcord.DiscordException = _DiscordException
	fake_nextcord.Embed = object
	fake_nextcord.Colour = object
	fake_nextcord.Streaming = object
	fake_nextcord.Member = object
	fake_nextcord_errors = types.ModuleType("nextcord.errors")
	fake_nextcord_errors.DiscordException = _DiscordException
	fake_nextcord.errors = fake_nextcord_errors
	# match.py now imports SystemContext from bot.context.context, which
	# annotates against nextcord.abc.GuildChannel. Annotation only -- that file
	# declares `from __future__ import annotations` -- but the import is real.
	fake_nextcord_abc = types.ModuleType("nextcord.abc")
	fake_nextcord_abc.GuildChannel = object
	fake_nextcord.abc = fake_nextcord_abc
	monkeypatch.setitem(sys.modules, "nextcord", fake_nextcord)
	monkeypatch.setitem(sys.modules, "nextcord.errors", fake_nextcord_errors)
	monkeypatch.setitem(sys.modules, "nextcord.abc", fake_nextcord_abc)

	fake_core_client = types.ModuleType("nammaoe2bot.discord.client")
	fake_core_client.dc = types.SimpleNamespace(user=None)
	fake_core_client.FakeMember = object      # Context.get_member's `name@id` path
	monkeypatch.setitem(sys.modules, "nammaoe2bot.discord.client", fake_core_client)

	fake_core_config = types.ModuleType("nammaoe2bot.runtime.config")
	fake_core_config.cfg = types.SimpleNamespace(DC_OWNER_ID=0)
	monkeypatch.setitem(sys.modules, "nammaoe2bot.runtime.config", fake_core_config)

	fake_core_utils = types.ModuleType("nammaoe2bot.runtime.utils")
	fake_core_utils.find = lambda *a, **k: None
	fake_core_utils.get = lambda *a, **k: None
	fake_core_utils.iter_to_dict = lambda *a, **k: {}
	fake_core_utils.join_and = lambda xs: ", ".join(str(x) for x in xs)
	fake_core_utils.get_nick = lambda p: getattr(p, "name", str(p))
	fake_core_utils.error_embed = lambda *a, **k: None
	fake_core_utils.ok_embed = lambda *a, **k: None
	monkeypatch.setitem(sys.modules, "nammaoe2bot.runtime.utils", fake_core_utils)


class _FakeEmbeds:
	def final_message(self):
		return "final-message-embed"


class _RaisingNotice:
	"""ctx.notice stand-in: the Nth call onward raises, modelling the kind of
	Discord send failure D1 is about -- a 5xx, an expired interaction token, a
	momentary Forbidden on embed links. final_message calls notice() up to
	twice: once for the final-score embed, once for the storyline tease."""

	def __init__(self, fail_from_call, exc=None):
		self.calls = []
		self.fail_from_call = fail_from_call
		self.exc = exc or RuntimeError("send failed")

	async def notice(self, *, embed=None):
		self.calls.append(embed)
		if len(self.calls) >= self.fail_from_call:
			raise self.exc


def _post_team_insights(monkeypatch, *, tease_send_fails):
	"""Drive the REAL storyline handler out of bot/wiring.py."""
	import bot.team_insights as ti
	import bot.wiring as wiring

	match = types.SimpleNamespace(id=42, storyline_ctx=None)
	stashed_ctx = {"since": 0, "seed": 42, "rosters": {0: [1], 1: [2]}}

	async def _fake_build_insights_embed(m):
		# Mirrors the real function's contract: the stash lands on the match
		# the moment a tease is computed, before the caller ever tries to
		# send it (bot/team_insights.py build_insights_embed's own docstring).
		m.storyline_ctx = stashed_ctx
		return "tease-embed"

	monkeypatch.setattr(ti, "build_insights_embed", _fake_build_insights_embed)

	ctx = _RaisingNotice(fail_from_call=1 if tease_send_fails else 999)
	asyncio.run(wiring._post_team_insights(match, ctx))
	return match, stashed_ctx


def test_the_storyline_handler_clears_the_stash_when_the_tease_send_fails(monkeypatch):
	"""D1: build_insights_embed stashes match.storyline_ctx before the tease is
	ever sent. If ctx.notice(embed=insights_embed) then raises, nobody in the
	channel saw the tease -- so the stash must not survive, or a later
	build_payoff_embed answers a tease that was never shown."""
	match, _ = _post_team_insights(monkeypatch, tease_send_fails=True)
	assert match.storyline_ctx is None


def test_the_storyline_handler_keeps_the_stash_when_the_tease_send_succeeds(monkeypatch):
	"""Contrast case: the clearing above is conditioned on the send actually
	failing, not an unconditional wipe after every tease."""
	match, stashed_ctx = _post_team_insights(monkeypatch, tease_send_fails=False)
	assert match.storyline_ctx == stashed_ctx


def test_final_message_posts_the_teams_then_announces(monkeypatch):
	"""The other half: the domain sends the teams embed and THEN emits
	`teams_posted`, carrying both the match and the ctx the handler needs to
	post into. Emitting first would put the tease above the teams it teases."""
	_install_fakes(monkeypatch)

	import bot.match.match as match_mod
	from bot.match.events import MatchLifecycle

	announced = []

	async def _record(m, c):
		announced.append((m, c, list(ctx.calls)))

	events = MatchLifecycle()
	events.on("teams_posted", _record)

	match = match_mod.Match.__new__(match_mod.Match)
	match.id = 42
	match.embeds = _FakeEmbeds()
	match.qc = types.SimpleNamespace(app=types.SimpleNamespace(match_events=events))

	ctx = _RaisingNotice(fail_from_call=999)
	asyncio.run(match.final_message(ctx))

	assert len(announced) == 1, "final_message did not announce teams_posted"
	got_match, got_ctx, sends_before = announced[0]
	assert got_match is match and got_ctx is ctx
	assert sends_before == ["final-message-embed"], (
		"the teams embed must already be on screen when the handler runs")


def test_a_failed_teams_embed_still_announces(monkeypatch):
	"""The teams-embed send is wrapped in `except DiscordException: pass` and
	always was. A Discord hiccup there must not silently cost the channel its
	storyline too."""
	_install_fakes(monkeypatch)

	import bot.match.match as match_mod
	from bot.match.events import MatchLifecycle

	announced = []
	events = MatchLifecycle()
	events.on("teams_posted", lambda m, c: _noop(announced))

	match = match_mod.Match.__new__(match_mod.Match)
	match.id = 42
	match.embeds = _FakeEmbeds()
	match.qc = types.SimpleNamespace(app=types.SimpleNamespace(match_events=events))

	# match_mod's OWN DiscordException: the module is cached across tests in
	# this file, so its `except DiscordException` is bound to whichever fake
	# nextcord was installed when it first imported. Raising a fresh one from
	# sys.modules would sail straight past the guard under test.
	ctx = _RaisingNotice(fail_from_call=1, exc=match_mod.DiscordException("503"))
	asyncio.run(match.final_message(ctx))
	assert announced == ["ran"]


async def _noop(sink):
	sink.append("ran")
