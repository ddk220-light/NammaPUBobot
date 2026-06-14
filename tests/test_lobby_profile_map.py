# -*- coding: utf-8 -*-
"""Pure unit tests for bot/lobby/profile_map.eliminate — the safe by-elimination
inference that backfills the discord<->aoe2 map only when exactly one user and
one profileId remain unmatched."""
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
