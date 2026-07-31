"""Unit tests for bot/civ_sync.py parser and matcher helpers.

Scope: pure, non-IO functions only. We test:

- parse_lobby_embed(message) — AOE2LobbyBOT Discord embed → structured dict
- buffer_lobby_result(parsed) — in-memory rolling buffer semantics
- find_matching_lobby(elo_parsed, ts) — time-window + team-shape match

We do NOT test link_and_write() — it appends to data/ CSVs and is integration
territory.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.civ_sync import (
	MAX_BUFFER,
	_lobby_buffer,
	buffer_lobby_result,
	consume_lobby_result,
	find_matching_lobby,
	parse_lobby_embed,
)


# ─── Embed/message fakes ─────────────────────────────────────────────

class _FakeField:
	def __init__(self, name, value):
		self.name = name
		self.value = value


class _FakeEmbed:
	def __init__(self, description='', fields=None):
		self.description = description
		self.fields = fields or []


class _FakeMessage:
	def __init__(self, embed, created_at_ts=1700000000, msg_id=1):
		self.embeds = [embed] if embed else []
		# parse_lobby_embed only calls message.created_at.timestamp() —
		# mirror that shape without needing a real datetime.
		self.created_at = SimpleNamespace(timestamp=lambda: created_at_ts)
		self.id = msg_id


# A realistic AOE2LobbyBOT match-complete embed. Trophy glyphs mark the
# winner, black square marks the loser. Player link format is aoe2insights
# URLs. "Civ" header starts the per-player civ list.
LOBBY_4V4_DESCRIPTION = """Map: Arabia
Duration: 42 min

Team 1 :trophy:
[thelivi](https://www.aoe2insights.com/user/relic/100001/)
[guruGreatest](https://www.aoe2insights.com/user/relic/100002/)
[bob](https://www.aoe2insights.com/user/relic/100003/)
[newPlayer](https://www.aoe2insights.com/user/relic/100004/)
Civ
Britons
Franks
Mongols
Aztecs

Team 2 :black_large_square:
[M1k3](https://www.aoe2insights.com/user/relic/200001/)
[steve](https://www.aoe2insights.com/user/relic/200002/)
[rando](https://www.aoe2insights.com/user/relic/200003/)
[someone](https://www.aoe2insights.com/user/relic/200004/)
Civ
Vikings
Turks
Persians
Huns

Rec
[Download replay](https://aoe.ms/replay/?gameId=999888)
"""


# ─── parse_lobby_embed ───────────────────────────────────────────────

class TestParseLobbyEmbedHappyPath:
	def test_extracts_map_and_duration(self):
		msg = _FakeMessage(_FakeEmbed(description=LOBBY_4V4_DESCRIPTION))
		parsed = parse_lobby_embed(msg)
		assert parsed is not None
		assert parsed['map'] == 'Arabia'
		assert parsed['duration'] == 42

	def test_extracts_aoe2_match_id_from_replay_link(self):
		msg = _FakeMessage(_FakeEmbed(description=LOBBY_4V4_DESCRIPTION))
		parsed = parse_lobby_embed(msg)
		assert parsed['aoe2_match_id'] == 999888

	def test_extracts_two_teams(self):
		msg = _FakeMessage(_FakeEmbed(description=LOBBY_4V4_DESCRIPTION))
		parsed = parse_lobby_embed(msg)
		assert len(parsed['teams']) == 2

	def test_winner_and_loser_marked_correctly(self):
		msg = _FakeMessage(_FakeEmbed(description=LOBBY_4V4_DESCRIPTION))
		parsed = parse_lobby_embed(msg)
		# Team 1 had :trophy: → winner
		assert parsed['teams'][0]['is_winner'] is True
		# Team 2 had :black_large_square: → loser
		assert parsed['teams'][1]['is_winner'] is False

	def test_player_profile_ids_parsed(self):
		msg = _FakeMessage(_FakeEmbed(description=LOBBY_4V4_DESCRIPTION))
		parsed = parse_lobby_embed(msg)
		winners = parsed['teams'][0]['players']
		assert len(winners) == 4
		assert winners[0]['aoe2_name'] == 'thelivi'
		assert winners[0]['profile_id'] == 100001
		assert winners[-1]['profile_id'] == 100004

	def test_civs_zipped_with_players_in_order(self):
		msg = _FakeMessage(_FakeEmbed(description=LOBBY_4V4_DESCRIPTION))
		parsed = parse_lobby_embed(msg)
		winners = parsed['teams'][0]['players']
		assert winners[0]['civ'] == 'Britons'
		assert winners[1]['civ'] == 'Franks'
		assert winners[2]['civ'] == 'Mongols'
		assert winners[3]['civ'] == 'Aztecs'

		losers = parsed['teams'][1]['players']
		assert losers[0]['civ'] == 'Vikings'
		assert losers[-1]['civ'] == 'Huns'


class TestParseLobbyEmbedRejection:
	def test_none_when_message_has_no_embeds(self):
		msg = SimpleNamespace(embeds=[], created_at=SimpleNamespace(timestamp=lambda: 0), id=1)
		assert parse_lobby_embed(msg) is None

	def test_none_when_no_map(self):
		# No "Map:" anywhere → unparseable.
		desc = "Team 1 :trophy:\n[alice](https://www.aoe2insights.com/user/relic/1/)\n"
		assert parse_lobby_embed(_FakeMessage(_FakeEmbed(description=desc))) is None

	def test_none_when_no_game_id(self):
		desc = "Map: Arabia\nTeam 1 :trophy:\n[alice](https://www.aoe2insights.com/user/relic/1/)\nCiv\nBritons\n"
		# No gameId=NNNN anywhere — the match id extraction fails, so
		# parser returns None (we only write civ data when we have an
		# aoe2 match id to key it against).
		assert parse_lobby_embed(_FakeMessage(_FakeEmbed(description=desc))) is None


# ─── buffer_lobby_result ─────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_lobby_buffer():
	"""Reset the module-level lobby buffer before every test so
	leftovers from one test never leak into the next."""
	_lobby_buffer.clear()
	yield
	_lobby_buffer.clear()


class TestBufferLobbyResult:
	def test_appends_to_buffer(self):
		buffer_lobby_result({'aoe2_match_id': 1, 'timestamp': 1000})
		assert len(_lobby_buffer) == 1

	def test_evicts_oldest_when_max_exceeded(self):
		for i in range(MAX_BUFFER + 5):
			buffer_lobby_result({'aoe2_match_id': i, 'timestamp': 1000 + i})
		# Buffer caps at MAX_BUFFER
		assert len(_lobby_buffer) == MAX_BUFFER
		# FIFO eviction — first 5 are gone, last MAX_BUFFER are kept
		assert _lobby_buffer[0]['aoe2_match_id'] == 5
		assert _lobby_buffer[-1]['aoe2_match_id'] == MAX_BUFFER + 4


# ─── find_matching_lobby ─────────────────────────────────────────────

def _elo_parsed(nicks_by_team):
	"""Build a minimal Pubobot ELO parse that find_matching_lobby can
	consume. nicks_by_team is a list of lists: [[team0 nicks], [team1 nicks]]."""
	return {
		'match_id': 1,
		'queue_name': '4v4',
		'teams': [
			{
				'index': idx,
				'name': f'T{idx}',
				'avg_before': 1000,
				'avg_after': 1000,
				'players': [{'nick': n, 'before': 1000, 'after': 1000} for n in nicks],
			}
			for idx, nicks in enumerate(nicks_by_team)
		],
	}


def _lobby_parsed(nicks_by_team, aoe2_match_id=42, timestamp=1000, is_winner_for_team_0=True):
	"""Build a minimal lobby parse matching the find_matching_lobby shape."""
	return {
		'aoe2_match_id': aoe2_match_id,
		'map': 'Arabia',
		'duration': 40,
		'timestamp': timestamp,
		'message_id': 1,
		'teams': [
			{
				'is_winner': (is_winner_for_team_0 if idx == 0 else not is_winner_for_team_0),
				'players': [
					{'aoe2_name': n, 'profile_id': i + 1, 'civ': 'Britons'}
					for i, n in enumerate(nicks)
				],
			}
			for idx, nicks in enumerate(nicks_by_team)
		],
	}


class TestFindMatchingLobby:
	"""The matcher pairs a Pubobot ELO result with a LobbyBOT result using only
	signals neither side has to agree on a person for: how long ago the lobby
	was reported, and the team shape (per-team player counts).

	It used to translate the ELO message's Discord nicks into AoE2 names through
	data/player_profile_map.csv and require an 8-player name overlap. That CSV is
	no longer read anywhere at runtime — `identities` is the single identity
	store and it is keyed by AoE2 profile_id, which a Pubobot ELO message never
	carries — so these tests no longer patch a profile map, and none of them
	touch the filesystem.
	"""

	def test_returns_none_when_buffer_empty(self):
		elo = _elo_parsed([['alice', 'bob', 'carol', 'dave'], ['eve', 'frank', 'grace', 'henry']])
		assert find_matching_lobby(elo, elo_timestamp=1000) is None

	def test_returns_match_within_time_window_and_shape(self):
		all_nicks = ['alice', 'bob', 'carol', 'dave', 'eve', 'frank', 'grace', 'henry']
		elo = _elo_parsed([all_nicks[:4], all_nicks[4:]])
		# Lobby 60 seconds before the ELO message — well within the 2h window.
		buffer_lobby_result(_lobby_parsed([all_nicks[:4], all_nicks[4:]], timestamp=940))
		match = find_matching_lobby(elo, elo_timestamp=1000)
		assert match is not None
		assert match['aoe2_match_id'] == 42

	def test_pairs_by_shape_not_by_who_played(self):
		"""The lobby's names have nothing in common with the ELO nicks and it is
		still the right pairing: the two sides name the same humans differently
		(Discord nick vs AoE2 account name), which is precisely why matching on
		names needed a CSV translation table in the first place."""
		elo = _elo_parsed([['alice', 'bob', 'carol', 'dave'], ['eve', 'frank', 'grace', 'henry']])
		buffer_lobby_result(_lobby_parsed(
			[['XxAlicexX', 'B0B', 'C4R0L', 'D_A_V_E'], ['3v3', 'FrankTheTank', 'Gr4ce', 'Henry_']],
			timestamp=940))
		assert find_matching_lobby(elo, elo_timestamp=1000)['aoe2_match_id'] == 42

	def test_rejects_lobby_older_than_2_hours(self):
		all_nicks = ['alice', 'bob', 'carol', 'dave', 'eve', 'frank', 'grace', 'henry']
		elo = _elo_parsed([all_nicks[:4], all_nicks[4:]])
		# 2h + 1s before the ELO message → outside the window
		buffer_lobby_result(_lobby_parsed([all_nicks[:4], all_nicks[4:]], timestamp=1000 - 7201))
		assert find_matching_lobby(elo, elo_timestamp=1000) is None

	def test_rejects_lobby_in_the_future(self):
		# time_diff < 0 means the lobby message was AFTER the ELO
		# message — implausible causality, so the matcher skips it.
		all_nicks = ['alice', 'bob', 'carol', 'dave', 'eve', 'frank', 'grace', 'henry']
		elo = _elo_parsed([all_nicks[:4], all_nicks[4:]])
		buffer_lobby_result(_lobby_parsed([all_nicks[:4], all_nicks[4:]], timestamp=2000))
		assert find_matching_lobby(elo, elo_timestamp=1000) is None

	def test_rejects_lobby_of_a_different_shape(self):
		# A 3v3 lobby cannot be this 4v4's game, however close in time.
		all_nicks = ['alice', 'bob', 'carol', 'dave', 'eve', 'frank', 'grace', 'henry']
		elo = _elo_parsed([all_nicks[:4], all_nicks[4:]])
		buffer_lobby_result(_lobby_parsed([all_nicks[:3], all_nicks[3:6]], timestamp=940))
		assert find_matching_lobby(elo, elo_timestamp=1000) is None

	def test_pairs_nothing_when_two_lobbies_are_equally_good(self):
		"""THE point of the rewrite. Time + shape cannot separate two 4v4s
		reported in the same two hours, and guessing would attach one game's
		civs and result to the other match — a false fact every downstream
		layer then treats as true. An unpaired match costs one match's civ
		detail; a wrong pairing costs correctness. So: pair neither."""
		all_nicks = ['alice', 'bob', 'carol', 'dave', 'eve', 'frank', 'grace', 'henry']
		elo = _elo_parsed([all_nicks[:4], all_nicks[4:]])
		buffer_lobby_result(_lobby_parsed([all_nicks[:4], all_nicks[4:]],
										  aoe2_match_id=42, timestamp=940))
		buffer_lobby_result(_lobby_parsed([all_nicks[:4], all_nicks[4:]],
										  aoe2_match_id=43, timestamp=900))
		assert find_matching_lobby(elo, elo_timestamp=1000) is None

	def test_an_out_of_window_rival_does_not_block_the_pairing(self):
		"""Only candidates that actually qualify create ambiguity — otherwise a
		stale lobby in the buffer would veto every pairing forever."""
		all_nicks = ['alice', 'bob', 'carol', 'dave', 'eve', 'frank', 'grace', 'henry']
		elo = _elo_parsed([all_nicks[:4], all_nicks[4:]])
		buffer_lobby_result(_lobby_parsed([all_nicks[:4], all_nicks[4:]],
										  aoe2_match_id=42, timestamp=940))
		buffer_lobby_result(_lobby_parsed([all_nicks[:4], all_nicks[4:]],
										  aoe2_match_id=43, timestamp=1000 - 7201))
		assert find_matching_lobby(elo, elo_timestamp=1000)['aoe2_match_id'] == 42

	def test_1v1_shape_is_matched_too(self):
		# parse_elo_message synthesizes one player per team for 1v1 results.
		elo = _elo_parsed([['alice'], ['bob']])
		buffer_lobby_result(_lobby_parsed([['Alice_AoE'], ['Bob_AoE']], timestamp=940))
		assert find_matching_lobby(elo, elo_timestamp=1000)['aoe2_match_id'] == 42


class TestPairedLobbiesAreConsumed:
	"""The buffer used to be append-only: a paired lobby stayed a candidate
	forever, so the matcher degraded the longer a session ran. With a past-only
	window and shape-only signals, a night of 4v4s poisoned itself — game 1
	paired, game 2 saw TWO same-shape candidates still in the window and paired
	nothing, game 3 saw three. "Ambiguity means pair nothing" has to be a
	statement about genuinely concurrent games, not about how many have been
	played."""

	def test_a_paired_lobby_leaves_the_buffer(self):
		all_nicks = ['alice', 'bob', 'carol', 'dave', 'eve', 'frank', 'grace', 'henry']
		elo = _elo_parsed([all_nicks[:4], all_nicks[4:]])
		buffer_lobby_result(_lobby_parsed([all_nicks[:4], all_nicks[4:]],
										  aoe2_match_id=42, timestamp=940))

		assert find_matching_lobby(elo, elo_timestamp=1000)['aoe2_match_id'] == 42
		assert _lobby_buffer == [], "one lobby describes one game; it is spoken for now"

	def test_a_second_game_of_the_same_shape_still_pairs(self):
		"""The regression itself, end to end: two 4v4s an hour apart, each
		buffered then reported. Both must pair. Before the fix the second saw
		the first still sitting in the buffer, called it ambiguous, and paired
		nothing — and every later game of that shape inherited the problem."""
		all_nicks = ['alice', 'bob', 'carol', 'dave', 'eve', 'frank', 'grace', 'henry']
		elo = _elo_parsed([all_nicks[:4], all_nicks[4:]])

		buffer_lobby_result(_lobby_parsed([all_nicks[:4], all_nicks[4:]],
										  aoe2_match_id=42, timestamp=940))
		assert find_matching_lobby(elo, elo_timestamp=1000)['aoe2_match_id'] == 42

		buffer_lobby_result(_lobby_parsed([all_nicks[:4], all_nicks[4:]],
										  aoe2_match_id=43, timestamp=4500))
		assert find_matching_lobby(elo, elo_timestamp=4600)['aoe2_match_id'] == 43

	def test_an_unpaired_lobby_stays_in_the_buffer(self):
		"""Consumption is on a HIT only. An ambiguous or out-of-window read must
		leave every candidate where it is — the ELO message it could not be
		matched to is not evidence that any of them is finished with."""
		all_nicks = ['alice', 'bob', 'carol', 'dave', 'eve', 'frank', 'grace', 'henry']
		elo = _elo_parsed([all_nicks[:4], all_nicks[4:]])
		buffer_lobby_result(_lobby_parsed([all_nicks[:4], all_nicks[4:]],
										  aoe2_match_id=42, timestamp=940))
		buffer_lobby_result(_lobby_parsed([all_nicks[:4], all_nicks[4:]],
										  aoe2_match_id=43, timestamp=900))

		assert find_matching_lobby(elo, elo_timestamp=1000) is None
		assert sorted(lobby['aoe2_match_id'] for lobby in _lobby_buffer) == [42, 43]

	def test_consuming_a_lobby_that_was_never_buffered_is_a_noop(self):
		"""The channel-history path pairs from a freshly scanned list and then
		clears any buffered copy of the same game; usually there is none."""
		buffer_lobby_result(_lobby_parsed([['alice'], ['bob']], aoe2_match_id=42))

		consume_lobby_result({'aoe2_match_id': 999})

		assert [lobby['aoe2_match_id'] for lobby in _lobby_buffer] == [42]


class TestProfileMapIsGone:
	def test_no_csv_backed_profile_map_helpers_remain(self):
		"""Both are deleted, not merely unused: load_profile_map read the
		hand-maintained CSV on every ELO result, and _auto_add_profile_mappings
		APPENDED to it with an empty user_id — rows nothing could ever read back
		into `identities`, on a filesystem that Railway resets on every deploy."""
		import bot.civ_sync as cs
		assert not hasattr(cs, 'load_profile_map')
		assert not hasattr(cs, '_auto_add_profile_mappings')


if __name__ == '__main__':
	pytest.main([__file__, '-v'])
