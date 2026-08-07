"""The in-game name a replay ingest reports must come from the REPLAY.

This is the fix at the centre of identity v2. extract_match used to be handed a
`resolved` map loaded from a generated CSV and compute
``identity = nick or aoe2_name``, so a player's DISCORD nickname won whenever
one was mapped. That value flowed into the replay-stats tables AND into
identity.learn(aoe2_name=...), which is how `identities.aoe2_name` — a column
whose entire job is "what is this account called in the game" — ended up
holding Discord nicknames for most of its production rows.

These tests pin both halves of the corrected path end to end:

  1. extract_match reports the parsed player's own name, with no way left to
     pass it anything else (the `resolved` parameter is gone, not defaulted).
  2. store._learn_from_ingest hands exactly that value to identity.learn().

mgz is not installed in CI (it is a vendored fork used only to parse replays
offline), so a minimal fake stands in for it. The fake only has to satisfy what
extract_match reads; the point of the test is the name, not the parsing.
"""
import asyncio
import inspect
import sys
from datetime import timedelta
from types import ModuleType, SimpleNamespace

import pytest

from nammaoe2bot.ingest import store
from utils.replay.extract import extract_match

REPLAY_NAME = "TheInGameName"
DISCORD_NICK = "SomeDiscordNick"     # must never appear in any output below
PROFILE_ID = 4242


class _FakePlayer:
	def __init__(self, number, name, profile_id):
		self.number = number
		self.name = name
		self.profile_id = profile_id
		self.civilization = "Franks"
		self.team_id = 1
		self.winner = True
		self.eapm = 60
		self.objects = []


def _install_fake_mgz(monkeypatch, players):
	"""Register a `mgz.model` whose parse_match returns a canned match."""
	match = SimpleNamespace(
		players=players,
		uptimes=[],
		actions=[],
		gaia=[],
		duration=timedelta(seconds=900),
		save_version=67.2,
		map=SimpleNamespace(name="Arabia"),
	)
	model = ModuleType("mgz.model")
	model.parse_match = lambda fh: match
	mgz = ModuleType("mgz")
	mgz.model = model
	monkeypatch.setitem(sys.modules, "mgz", mgz)
	monkeypatch.setitem(sys.modules, "mgz.model", model)


@pytest.fixture
def replay_path(tmp_path):
	# extract_match takes the aoe2 match id from the filename.
	p = tmp_path / "12345.aoe2record"
	p.write_bytes(b"")
	return str(p)


def test_extract_reports_the_replay_name(monkeypatch, replay_path):
	_install_fake_mgz(monkeypatch, [_FakePlayer(1, REPLAY_NAME, PROFILE_ID)])

	out = extract_match(replay_path, {})

	assert out["players"][0]["identity"] == REPLAY_NAME
	assert out["players"][0]["profile_id"] == PROFILE_ID


def test_extract_takes_no_identity_map_at_all(monkeypatch, replay_path):
	"""The `resolved` parameter is DELETED, not merely defaulted to empty.

	A parameter left in place with a harmless default is an invitation to start
	passing it again, and the caller that used to pass it (the live ingest job)
	is the one whose data this fix repairs.
	"""
	assert list(inspect.signature(extract_match).parameters) == ["path", "date_map"]

	_install_fake_mgz(monkeypatch, [_FakePlayer(1, REPLAY_NAME, PROFILE_ID)])
	with pytest.raises(TypeError):
		extract_match(replay_path, {PROFILE_ID: ("", DISCORD_NICK, "seed")}, {})


def test_no_extractor_side_loader_survives():
	"""load_resolved() opened the generated CSV. It is gone from the extractor;
	the offline quiz pipeline that still wants display nicknames owns its own
	copy and applies it AFTER extraction (utils/replay/build_db.py)."""
	import utils.replay.extract as extract_mod
	assert not hasattr(extract_mod, "load_resolved")


class _RecordingIdentity:
	def __init__(self):
		self.learn_calls = []

	async def learn(self, profile_id, user_id, source, aoe2_name=None):
		self.learn_calls.append((profile_id, user_id, source, aoe2_name))


def test_the_name_reaching_the_resolver_is_the_replay_name(monkeypatch, replay_path):
	"""End to end: parsed replay -> extract_match -> _learn_from_ingest ->
	identity.learn(aoe2_name=...). This is the exact hop that polluted
	production."""
	_install_fake_mgz(monkeypatch, [_FakePlayer(1, REPLAY_NAME, PROFILE_ID)])
	extracted = extract_match(replay_path, {})

	fake = _RecordingIdentity()
	monkeypatch.setattr(store, "resolver", fake)
	asyncio.run(store._learn_from_ingest(extracted["players"], {PROFILE_ID: 777}))

	assert fake.learn_calls == [(PROFILE_ID, 777, "learned", REPLAY_NAME)]
