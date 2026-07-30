# -*- coding: utf-8 -*-
"""Unit tests for bot/lobby/profile_map.py.

``eliminate`` is pure inference logic and untouched by task 2.3. ``known_for``
and ``link`` used to wrap the qc_profile_map table directly — empty in
production (0 rows) — and are re-pointed at bot/identity.py (the single
identity resolver) instead: known_for -> identity.user_for_profile, link ->
identity.learn(source='learned'). qc_profile_map is no longer read or written
here at all.
"""
import asyncio

from bot.lobby import profile_map


def test_eliminate_pins_lone_leftover():
	# users {10,20,30}; slots {101,102,103}; 101,102 known -> 103 must be user 30
	known = {101: 10, 102: 20}
	assert profile_map.eliminate([10, 20, 30], [101, 102, 103], known) == [(30, 103)]


def test_eliminate_no_pin_when_two_unknown():
	known = {101: 10}
	assert profile_map.eliminate([10, 20, 30], [101, 102, 103], known) == []


def test_eliminate_no_pin_when_all_known():
	known = {101: 10, 102: 20, 103: 30}
	assert profile_map.eliminate([10, 20, 30], [101, 102, 103], known) == []


def test_eliminate_empty_inputs():
	assert profile_map.eliminate([], [], {}) == []


def test_eliminate_single_player_match():
	# 1v1-ish: one user, one unknown profile -> pinned
	assert profile_map.eliminate([42], [777], {}) == [(42, 777)]


# ─── known_for / link -> identity resolver ──────────────────────────────

class _FakeIdentity:
	def __init__(self, owners=None):
		self.owners = dict(owners or {})   # profile_id -> user_id
		self.user_for_profile_calls = []
		self.learn_calls = []

	async def user_for_profile(self, profile_id):
		self.user_for_profile_calls.append(profile_id)
		return self.owners.get(profile_id)

	async def learn(self, profile_id, user_id, source, aoe2_name=None):
		self.learn_calls.append((profile_id, user_id, source, aoe2_name))


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


def test_link_routes_through_identity_learn(monkeypatch):
	fake = _FakeIdentity()
	monkeypatch.setattr(profile_map, "identity", fake)

	asyncio.run(profile_map.link(555, 999, "SomeName"))

	assert fake.learn_calls == [(999, 555, "learned", "SomeName")]


def test_link_never_learns_with_a_manual_source_from_automated_code(monkeypatch):
	# link()'s own default must not be able to forge a human correction.
	fake = _FakeIdentity()
	monkeypatch.setattr(profile_map, "identity", fake)

	asyncio.run(profile_map.link(555, 999, "SomeName"))

	assert fake.learn_calls[0][2] == "learned"


def test_link_treats_empty_name_as_unknown_not_a_clobber(monkeypatch):
	# An empty capture name must not overwrite an existing aoe2_name with "".
	fake = _FakeIdentity()
	monkeypatch.setattr(profile_map, "identity", fake)

	asyncio.run(profile_map.link(555, 999, ""))

	assert fake.learn_calls == [(999, 555, "learned", None)]


def test_link_swallows_write_errors(monkeypatch):
	class _Boom:
		async def learn(self, *a, **k):
			raise RuntimeError("db down")

	monkeypatch.setattr(profile_map, "identity", _Boom())

	# Must not raise — link() is best-effort, callers don't expect it to.
	asyncio.run(profile_map.link(555, 999, "SomeName"))
