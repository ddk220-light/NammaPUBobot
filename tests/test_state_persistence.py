import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace


def _load_state_module():
	"""Load state.py with only its restore-time classes stubbed.

	The real PickupQueue class constructs configuration tables at import time;
	that is intentionally outside this persistence unit test and requires a live
	database loop. Restore the global module cache immediately after loading so
	other tests remain free to import the real classes.
	"""
	queue_module = types.ModuleType("nammaoe2bot.pickup.queue")
	queue_module.PickupQueue = type("PickupQueue", (), {})
	match_module = types.ModuleType("nammaoe2bot.pickup.match.match")
	match_module.Match = type("Match", (), {})
	expire_module = types.ModuleType("nammaoe2bot.pickup.expire")
	expire_module.expire = SimpleNamespace(serialize=lambda: [])
	stubs = {
		"nammaoe2bot.pickup.queue": queue_module,
		"nammaoe2bot.pickup.match.match": match_module,
		"nammaoe2bot.pickup.expire": expire_module,
	}
	previous = {name: sys.modules.get(name) for name in stubs}
	try:
		sys.modules.update(stubs)
		path = Path(__file__).resolve().parent.parent / "nammaoe2bot" / "state.py"
		spec = importlib.util.spec_from_file_location("test_state_module", path)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
		return module
	finally:
		for name, original in previous.items():
			if original is None:
				sys.modules.pop(name, None)
			else:
				sys.modules[name] = original


state = _load_state_module()


class _Queue:
	def __init__(self, row):
		self.row = row
		self.length = 1

	def serialize(self):
		return self.row


class _Match:
	def __init__(self, row):
		self.row = row

	def serialize(self):
		return self.row


def _app(queue_rows=(), match_rows=()):
	channels = {
		index: SimpleNamespace(queues=[_Queue(row)])
		for index, row in enumerate(queue_rows)
	}
	return SimpleNamespace(
		channels=channels,
		active_matches=[_Match(row) for row in match_rows],
	)


def _reset_baselines(monkeypatch):
	monkeypatch.setattr(state, "_last_local_payload", None)
	monkeypatch.setattr(state, "_last_db_payload", None)


def test_canonical_payload_ignores_container_and_mapping_order(monkeypatch):
	monkeypatch.setattr(
		state.expire,
		"serialize",
		lambda: [{"deadline": 20, "id": 2}, {"id": 1, "deadline": 10}],
	)
	first = _app(
		queue_rows=[{"id": 2, "players": [3]}, {"players": [1], "id": 1}],
		match_rows=[{"id": 7, "state": "ready"}, {"state": "forming", "id": 5}],
	)
	payload_a = state.canonical_state_payload(first)

	monkeypatch.setattr(
		state.expire,
		"serialize",
		lambda: [{"deadline": 10, "id": 1}, {"id": 2, "deadline": 20}],
	)
	second = _app(
		queue_rows=[{"id": 1, "players": [1]}, {"players": [3], "id": 2}],
		match_rows=[{"state": "forming", "id": 5}, {"state": "ready", "id": 7}],
	)

	assert state.canonical_state_payload(second) == payload_a


def test_local_snapshot_writes_only_when_payload_changes(tmp_path, monkeypatch):
	_reset_baselines(monkeypatch)
	monkeypatch.setattr(state, "STATE_PATH", str(tmp_path / "state.json"))
	monkeypatch.setattr(state.expire, "serialize", lambda: [])
	app = _app(queue_rows=[{"id": 1}], match_rows=[])

	assert state.save_state(app) is True
	first = (tmp_path / "state.json").read_text()
	assert state.save_state(app) is False
	assert (tmp_path / "state.json").read_text() == first

	app.channels[0].queues[0].row["players"] = [42]
	assert state.save_state(app) is True
	assert (tmp_path / "state.json").read_text() != first


def test_failed_db_snapshot_retries_without_advancing_baseline(monkeypatch):
	class _FlakyDB:
		def __init__(self):
			self.calls = 0

		async def insert(self, *_args, **_kwargs):
			self.calls += 1
			if self.calls == 1:
				raise RuntimeError("temporary outage")

	database = _FlakyDB()
	_reset_baselines(monkeypatch)
	monkeypatch.setattr(state, "db", database)
	monkeypatch.setattr(state.expire, "serialize", lambda: [])
	app = _app(match_rows=[{"id": 9}])

	assert asyncio.run(state.save_state_db(app)) is False
	assert state._last_db_payload is None
	assert asyncio.run(state.save_state_db(app)) is True
	assert asyncio.run(state.save_state_db(app)) is False
	assert database.calls == 2


def test_combined_snapshot_computes_once_and_tracks_destinations_independently(
		tmp_path, monkeypatch):
	class _FlakyDB:
		def __init__(self):
			self.calls = 0

		async def insert(self, *_args, **_kwargs):
			self.calls += 1
			if self.calls == 1:
				raise RuntimeError("temporary outage")

	database = _FlakyDB()
	_reset_baselines(monkeypatch)
	monkeypatch.setattr(state, "db", database)
	monkeypatch.setattr(state, "STATE_PATH", str(tmp_path / "state.json"))
	monkeypatch.setattr(state.expire, "serialize", lambda: [])
	app = _app(queue_rows=[{"id": 1}])

	assert asyncio.run(state.save_state_if_changed(app)) == (True, False)
	assert asyncio.run(state.save_state_if_changed(app)) == (False, True)
	assert asyncio.run(state.save_state_if_changed(app)) == (False, False)
	assert database.calls == 2
