"""Unit tests for bot/elo_sync.py parser and resolver helpers.

Scope: pure, non-DB functions only —

- parse_elo_message(content) — Pubobot ELO result → structured dict
- _resolve_user_id(message, nick) — guild member lookup with
  synthetic-id fallback

The tests do NOT exercise process_elo_sync() itself (that's an async
DB writer — integration territory). Any future refactor of the DB
writes should still keep these parser tests passing; if they fail,
the Pubobot message format has either changed upstream or a parser
edit regressed its shape guarantees.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.elo_sync import _resolve_user_id, parse_elo_message


# ─── Fixtures ────────────────────────────────────────────────────────

# Real Pubobot 4v4 ELO message format. Captured from the live sync
# integration — don't reformat the whitespace, the parser is strict
# about the "> nick before ⟼ after" arrow glyph and indentation.
ELO_4V4_MESSAGE = """```markdown
4v4(1354050) results
-------------
0. A 1053 ⟼ 1075
> thelivi 1590 ⟼ 1612
> guruGreatest 1126 ⟼ 1148
> bob_the_builder 900 ⟼ 920
> newPlayer 500 ⟼ 530
1. B 1056 ⟼ 1034
> M1k3 1735 ⟼ 1713
> steveTheLegend 1200 ⟼ 1180
> randoPlayer 800 ⟼ 780
> someone 489 ⟼ 463
```"""

# Pubobot 1v1 format: team name doubles as player nick, no "> " lines.
ELO_1V1_MESSAGE = """```markdown
1v1(9000001) results
-------------
0. PlayerOne 1200 ⟼ 1220
1. PlayerTwo 1180 ⟼ 1160
```"""


# ─── parse_elo_message ───────────────────────────────────────────────

class TestParseEloMessage4v4:
	def test_extracts_match_id_and_queue_name(self):
		parsed = parse_elo_message(ELO_4V4_MESSAGE)
		assert parsed is not None
		assert parsed['match_id'] == 1354050
		assert parsed['queue_name'] == '4v4'

	def test_extracts_two_teams_with_four_players_each(self):
		parsed = parse_elo_message(ELO_4V4_MESSAGE)
		assert len(parsed['teams']) == 2
		assert len(parsed['teams'][0]['players']) == 4
		assert len(parsed['teams'][1]['players']) == 4

	def test_team_index_and_name(self):
		parsed = parse_elo_message(ELO_4V4_MESSAGE)
		assert parsed['teams'][0]['index'] == 0
		assert parsed['teams'][0]['name'] == 'A'
		assert parsed['teams'][1]['index'] == 1
		assert parsed['teams'][1]['name'] == 'B'

	def test_team_averages(self):
		# Team rating deltas are used by the civ linker and by any
		# downstream MMR heuristics; must survive a parser regression.
		parsed = parse_elo_message(ELO_4V4_MESSAGE)
		assert parsed['teams'][0]['avg_before'] == 1053
		assert parsed['teams'][0]['avg_after'] == 1075
		assert parsed['teams'][1]['avg_before'] == 1056
		assert parsed['teams'][1]['avg_after'] == 1034

	def test_player_rating_deltas_preserved(self):
		parsed = parse_elo_message(ELO_4V4_MESSAGE)
		first = parsed['teams'][0]['players'][0]
		assert first['nick'] == 'thelivi'
		assert first['before'] == 1590
		assert first['after'] == 1612
		last = parsed['teams'][1]['players'][-1]
		assert last['nick'] == 'someone'
		assert last['before'] == 489
		assert last['after'] == 463

	def test_winner_is_team_at_index_zero(self):
		# Pubobot convention: winning team is always index 0. The
		# elo-sync writer encodes `winner=0` based on this; if the
		# parser ever starts sorting teams differently, the writer's
		# qc_matches rows will silently lie about who won.
		parsed = parse_elo_message(ELO_4V4_MESSAGE)
		team0_gained = parsed['teams'][0]['avg_after'] > parsed['teams'][0]['avg_before']
		team1_lost = parsed['teams'][1]['avg_after'] < parsed['teams'][1]['avg_before']
		assert team0_gained
		assert team1_lost


class TestParseEloMessage1v1:
	def test_1v1_treats_team_name_as_player_nick(self):
		parsed = parse_elo_message(ELO_1V1_MESSAGE)
		assert parsed is not None
		assert parsed['match_id'] == 9000001
		assert parsed['queue_name'] == '1v1'
		assert len(parsed['teams']) == 2
		# Each 1v1 "team" gets one synthetic player whose nick is the team name
		assert parsed['teams'][0]['players'][0]['nick'] == 'PlayerOne'
		assert parsed['teams'][0]['players'][0]['before'] == 1200
		assert parsed['teams'][0]['players'][0]['after'] == 1220
		assert parsed['teams'][1]['players'][0]['nick'] == 'PlayerTwo'


class TestParseEloMessageRejection:
	def test_none_when_no_markdown_block(self):
		assert parse_elo_message('just some plain text, no codeblock') is None

	def test_none_when_header_doesnt_match(self):
		garbage = """```markdown
random text not matching queue(id) results header
```"""
		assert parse_elo_message(garbage) is None

	def test_none_when_block_empty(self):
		# Header-only with no teams is treated as unparseable.
		empty = """```markdown
4v4(1) results
-------------
```"""
		assert parse_elo_message(empty) is None


# ─── _resolve_user_id ────────────────────────────────────────────────

class _FakeMember:
	def __init__(self, id_, display_name, name):
		self.id = id_
		self.display_name = display_name
		self.name = name


class _FakeGuild:
	def __init__(self, members):
		self.members = members


def _message_with_guild(*members):
	guild = _FakeGuild(list(members)) if members else None
	return SimpleNamespace(guild=guild)


def _message_no_guild():
	return SimpleNamespace()  # no .guild attribute at all


class TestResolveUserIdReal:
	def test_resolves_by_display_name(self):
		m = _FakeMember(11111111111111, 'thelivi', 'thelivi_raw')
		msg = _message_with_guild(m)
		assert _resolve_user_id(msg, 'thelivi') == 11111111111111

	def test_resolves_by_name_when_display_name_differs(self):
		# Discord global usernames and per-guild display names diverge;
		# the resolver must match on either.
		m = _FakeMember(22222222222222, 'ServerNickname', 'actual_username')
		msg = _message_with_guild(m)
		assert _resolve_user_id(msg, 'actual_username') == 22222222222222

	def test_real_id_is_positive(self):
		m = _FakeMember(33333333333333, 'bob', 'bob')
		assert _resolve_user_id(_message_with_guild(m), 'bob') > 0


class TestResolveUserIdSyntheticFallback:
	def test_unknown_nick_returns_negative_id(self):
		# The synthetic id is the defence against the "unresolved
		# players all collapse to user_id=0" primary-key collision bug.
		msg = _message_with_guild(_FakeMember(1, 'knownPlayer', 'knownPlayer'))
		assert _resolve_user_id(msg, 'unknownPlayer') < 0

	def test_message_without_guild_returns_synthetic(self):
		assert _resolve_user_id(_message_no_guild(), 'anything') < 0

	def test_synthetic_id_is_never_zero(self):
		# We add +1 inside _resolve_user_id specifically to exclude the
		# one input (crc32 == 0) that would otherwise collapse to the
		# legacy collision value. Belt + braces.
		# crc32(b'') is 0 — this is the only adversarial case we have
		# to hand-verify.
		got = _resolve_user_id(_message_no_guild(), '')
		assert got != 0
		assert got < 0

	def test_synthetic_id_is_deterministic_across_calls(self):
		first = _resolve_user_id(_message_no_guild(), 'consistent_nick')
		second = _resolve_user_id(_message_no_guild(), 'consistent_nick')
		assert first == second

	def test_different_nicks_give_different_ids(self):
		# This is THE property the whole synthetic-id design is meant
		# to enforce: two different unresolved players must not collide
		# on the qc_player_matches (match_id, user_id) primary key.
		a = _resolve_user_id(_message_no_guild(), 'playerOne')
		b = _resolve_user_id(_message_no_guild(), 'playerTwo')
		assert a != b

	def test_resolved_and_synthetic_never_overlap(self):
		# Discord snowflakes are positive 64-bit ints, synthetic ids
		# are strictly negative. A real player and their synthetic
		# fallback-in-waiting can never collide.
		real_member = _FakeMember(44444444444444, 'realPerson', 'realPerson')
		real_id = _resolve_user_id(_message_with_guild(real_member), 'realPerson')
		synth_id = _resolve_user_id(_message_no_guild(), 'realPerson')
		assert real_id > 0
		assert synth_id < 0


if __name__ == '__main__':
	pytest.main([__file__, '-v'])
