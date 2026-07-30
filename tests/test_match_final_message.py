# -*- coding: utf-8 -*-
"""Unit tests for Match.final_message's storyline-stash clearing behaviour.

bot/team_insights.py's build_insights_embed stashes match.storyline_ctx the
moment it *computes* a tease -- but "computed" is not "posted". If the
subsequent ctx.notice(embed=insights_embed) send then fails, nobody in the
channel ever saw the tease, and Match.final_message must drop the stash so
bot/storyline_payoff.py's build_payoff_embed -- which trusts the stash to mean
"a tease was shown" -- never answers a tease nobody saw.

tests/test_storyline_payoff.py's test_no_storyline_ctx_at_all_builds_no_embed
already proves the payoff half of that contract (no stash -> no payoff embed).
This file proves the other half: that final_message actually clears the stash
when the send fails, at the point the stash is set.

Importing the real bot.match.match module pulls in nextcord and the rest of
its transitive import chain (core.client, core.utils, bot.match.check_in,
bot.match.draft, bot.match.embeds), none of which is installed in this test
environment (CI installs only pytest; nextcord and prettytable are absent).
This module fakes just enough of that chain -- the same sys.modules-injection
trick tests/conftest.py already uses for core.config/console/database -- to
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
	monkeypatch.setitem(sys.modules, "nextcord", fake_nextcord)
	monkeypatch.setitem(sys.modules, "nextcord.errors", fake_nextcord_errors)

	fake_core_client = types.ModuleType("core.client")
	fake_core_client.dc = types.SimpleNamespace(user=None)
	monkeypatch.setitem(sys.modules, "core.client", fake_core_client)

	fake_core_utils = types.ModuleType("core.utils")
	fake_core_utils.find = lambda *a, **k: None
	fake_core_utils.get = lambda *a, **k: None
	fake_core_utils.iter_to_dict = lambda *a, **k: {}
	fake_core_utils.join_and = lambda xs: ", ".join(str(x) for x in xs)
	fake_core_utils.get_nick = lambda p: getattr(p, "name", str(p))
	monkeypatch.setitem(sys.modules, "core.utils", fake_core_utils)


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


def _run_final_message(monkeypatch, *, tease_send_fails):
	_install_fakes(monkeypatch)

	import bot.match.match as match_mod
	import bot.team_insights as ti

	match = match_mod.Match.__new__(match_mod.Match)
	match.id = 42
	match.embeds = _FakeEmbeds()

	stashed_ctx = {"since": 0, "seed": 42, "rosters": {0: [1], 1: [2]}}

	async def _fake_build_insights_embed(m):
		# Mirrors the real function's contract: the stash lands on the match
		# the moment a tease is computed, before the caller ever tries to
		# send it (bot/team_insights.py build_insights_embed's own docstring).
		m.storyline_ctx = stashed_ctx
		return "tease-embed"

	monkeypatch.setattr(ti, "build_insights_embed", _fake_build_insights_embed)

	# Call 2 is the tease send; fail it only when the scenario asks for it.
	ctx = _RaisingNotice(fail_from_call=2 if tease_send_fails else 999)
	asyncio.run(match.final_message(ctx))
	return match, stashed_ctx


def test_final_message_clears_the_stash_when_the_tease_send_fails(monkeypatch):
	"""D1: build_insights_embed stashes match.storyline_ctx before the tease is
	ever sent. If ctx.notice(embed=insights_embed) then raises, nobody in the
	channel saw the tease -- so the stash must not survive, or a later
	build_payoff_embed answers a tease that was never shown."""
	match, _ = _run_final_message(monkeypatch, tease_send_fails=True)
	assert match.storyline_ctx is None


def test_final_message_keeps_the_stash_when_the_tease_send_succeeds(monkeypatch):
	"""Contrast case: the clearing above is conditioned on the send actually
	failing, not an unconditional wipe after every tease."""
	match, stashed_ctx = _run_final_message(monkeypatch, tease_send_fails=False)
	assert match.storyline_ctx == stashed_ctx
