"""Unit tests for bot/civ_matcher's player -> AoE2-profile resolution.

The civ recorder used to read data/player_profile_map.csv from disk on every
civ-match attempt, keyed on Discord user_id with a nick fallback for rows the
CSV lacked a user_id for. Task 2.3 re-points this at bot/identity.py (the
single identity resolver seeded from all known sources at boot) and drops the
nick fallback: every row in the CSV already carried a user_id, and every live
caller (bot/stats/stats.py, bot/civ_reconcile.py) always supplies one, so the
fallback could never resolve anyone the user_id path couldn't.
"""
from __future__ import annotations

import asyncio

import bot.civ_matcher as cm


class _FakeIdentity:
	"""Fakes bot.identity.profiles_for_users — records the call and returns a
	canned {user_id: [profile_id, ...]} map."""

	def __init__(self, mapping):
		self.mapping = mapping
		self.calls = []

	async def profiles_for_users(self, user_ids):
		self.calls.append(set(user_ids))
		wanted = set(user_ids)
		return {uid: pids for uid, pids in self.mapping.items() if uid in wanted}


def test_map_players_to_profiles_consults_identity_resolver(monkeypatch):
	fake = _FakeIdentity({111: [612690]})
	monkeypatch.setattr(cm, "identity", fake)

	player_info, active_pids = asyncio.run(
		cm._map_players_to_profiles([(111, "ddk", 0)])
	)

	assert fake.calls == [{111}]
	assert player_info == {111: ("ddk", 0, [612690])}
	assert active_pids == {612690}


def test_map_players_to_profiles_drops_unmapped_players(monkeypatch):
	fake = _FakeIdentity({111: [612690]})
	monkeypatch.setattr(cm, "identity", fake)

	player_info, active_pids = asyncio.run(
		cm._map_players_to_profiles([(111, "ddk", 0), (222, "nobody", 1)])
	)

	assert 222 not in player_info
	assert player_info == {111: ("ddk", 0, [612690])}
	assert active_pids == {612690}


def test_map_players_to_profiles_no_nick_fallback(monkeypatch):
	# A player with no known profile must stay unmapped even if their nick
	# happens to collide with something — there is no nick-keyed lookup left.
	fake = _FakeIdentity({})
	monkeypatch.setattr(cm, "identity", fake)

	player_info, active_pids = asyncio.run(
		cm._map_players_to_profiles([(333, "ddk", 0)])
	)

	assert player_info == {}
	assert active_pids == set()


def test_map_players_to_profiles_combines_alt_accounts(monkeypatch):
	fake = _FakeIdentity({222: [17841676, 2885693]})
	monkeypatch.setattr(cm, "identity", fake)

	player_info, active_pids = asyncio.run(
		cm._map_players_to_profiles([(222, "thelivi", 1)])
	)

	assert player_info == {222: ("thelivi", 1, [17841676, 2885693])}
	assert active_pids == {17841676, 2885693}


def test_csv_loaders_and_path_are_gone():
	# The disk-read-on-every-attempt loaders are retired entirely, along with
	# the path constant that pointed at data/player_profile_map.csv.
	assert not hasattr(cm, "_load_profile_map")
	assert not hasattr(cm, "_load_profile_uid_map")
	assert not hasattr(cm, "_PROFILE_MAP_PATH")
