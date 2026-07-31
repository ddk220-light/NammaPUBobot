# -*- coding: utf-8 -*-
"""Unit tests for bot/lobby/profile_map.py.

The module is read-only since identity v2: ``known_for`` -> identity.
user_for_profile, and nothing else. Its two writers are gone — ``eliminate``
(pure by-elimination inference, no roster guard, could pin a wrong pair) and
``link`` (its only caller was the watcher's eliminate loop). Identity is now
deduced by bot/identity_solver.py; those tests live in
tests/test_identity_solver.py.
"""
import asyncio

from bot.lobby import profile_map


class _FakeIdentity:
	def __init__(self, owners=None):
		self.owners = dict(owners or {})   # profile_id -> user_id
		self.user_for_profile_calls = []

	async def user_for_profile(self, profile_id):
		self.user_for_profile_calls.append(profile_id)
		return self.owners.get(profile_id)


def test_known_for_consults_identity_user_for_profile(monkeypatch):
	fake = _FakeIdentity({101: 10, 103: 30})
	monkeypatch.setattr(profile_map, "identity", fake)

	result = asyncio.run(profile_map.known_for([101, 102, 103]))

	assert result == {101: 10, 103: 30}
	assert fake.user_for_profile_calls == [101, 102, 103]


def test_known_for_swallows_lookup_errors(monkeypatch):
	class _Boom:
		async def user_for_profile(self, profile_id):
			raise RuntimeError("db down")

	monkeypatch.setattr(profile_map, "identity", _Boom())

	assert asyncio.run(profile_map.known_for([1, 2])) == {}


def test_the_by_elimination_writers_are_gone():
	# Deleted deliberately (identity v2 spec section 4): eliminate() pinned a
	# pair on counts alone with no roster guard, so a lobby guest plus an absent
	# match player produced a WRONG global binding. If either name comes back,
	# it should come back with the solver's guards, not these.
	assert not hasattr(profile_map, "eliminate")
	assert not hasattr(profile_map, "link")
