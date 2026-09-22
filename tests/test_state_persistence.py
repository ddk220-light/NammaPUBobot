import asyncio
import json
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


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
		channels=channels, state_restored=True, state_restore_lock=asyncio.Lock(),
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


def test_all_save_entrypoints_preserve_existing_snapshot_before_restoration(tmp_path, monkeypatch):
	_reset_baselines(monkeypatch)
	path = tmp_path / "state.json"
	path.write_text("previous active matches")
	monkeypatch.setattr(state, "STATE_PATH", str(path))
	database = SimpleNamespace(insert=AsyncMock())
	monkeypatch.setattr(state, "db", database)
	app = _app()
	app.state_restored = False
	assert state.save_state(app) is False
	assert asyncio.run(state.save_state_db(app)) is False
	assert asyncio.run(state.save_state_if_changed(app)) == (False, False)
	assert path.read_text() == "previous active matches"
	database.insert.assert_not_awaited()


def _restore_setup(tmp_path, monkeypatch, data):
	app = _app()
	app.state_restored = False
	monkeypatch.setattr(state, "STATE_PATH", str(tmp_path / "state.json"))
	database = SimpleNamespace(select_one=AsyncMock(return_value={"data": json.dumps(data)}))
	monkeypatch.setattr(state, "db", database)
	monkeypatch.setattr(state.stats, "reserve_restored_ids", AsyncMock())
	monkeypatch.setattr(state.expire, "load_json", AsyncMock(), raising=False)
	return app, database


def test_failed_db_read_does_not_fall_back_to_stale_local_state(tmp_path, monkeypatch):
	app, database = _restore_setup(tmp_path, monkeypatch, {})
	(tmp_path / "state.json").write_text('{"queues":[],"matches":[]}')
	database.select_one.side_effect = ConnectionError("unavailable")
	with pytest.raises(ConnectionError):
		asyncio.run(state.load_state(app))
	assert not app.state_restored
	assert asyncio.run(state.save_state_if_changed(app)) == (False, False)


@pytest.mark.parametrize("payload", ["{", "null", "{}", '{"queues":[],"matches":null}'])
def test_invalid_durable_state_never_enables_snapshots(tmp_path, monkeypatch, payload):
	app, database = _restore_setup(tmp_path, monkeypatch, {})
	database.select_one.return_value = {"data": payload}
	with pytest.raises((ValueError, state.Exc.ValueError)):
		asyncio.run(state.load_state(app))
	assert not app.state_restored


def test_fresh_database_and_missing_local_file_enable_empty_state(tmp_path, monkeypatch):
	app, database = _restore_setup(tmp_path, monkeypatch, {})
	database.select_one.return_value = None
	asyncio.run(state.load_state(app))
	assert app.state_restored


def test_restore_race_blocks_save_until_every_match_is_loaded(tmp_path, monkeypatch):
	data = {"queues": [], "matches": [{"match_id": 81}], "expire": []}
	app, database = _restore_setup(tmp_path, monkeypatch, data)
	database.select_one.side_effect = [{"data": json.dumps(data)}, None]

	async def run():
		entered, release = asyncio.Event(), asyncio.Event()

		async def restore(_data):
			entered.set()
			await release.wait()

		monkeypatch.setattr(state.Match, "from_json", restore, raising=False)
		task = asyncio.create_task(state.load_state(app))
		await entered.wait()
		assert not app.state_restored
		assert await state.save_state_if_changed(app) == (False, False)
		release.set()
		await task
		assert app.state_restored

	asyncio.run(run())
	state.stats.reserve_restored_ids.assert_awaited_once_with([81])


def test_partial_restore_failure_keeps_durable_source_protected(tmp_path, monkeypatch):
	data = {"queues": [], "matches": [{"match_id": 81}, {"match_id": 82}]}
	app, database = _restore_setup(tmp_path, monkeypatch, data)
	database.select_one.side_effect = [{"data": json.dumps(data)}, None, None]
	monkeypatch.setattr(state.Match, "from_json", AsyncMock(side_effect=[None, state.Exc.ValueError("missing guild")]), raising=False)
	with pytest.raises(state.Exc.ValueError):
		asyncio.run(state.load_state(app))
	assert not app.state_restored


def test_committed_match_in_old_snapshot_is_not_restored(tmp_path, monkeypatch):
	md = dict(match_id=81, channel_id=1, queue_id=2, players=[3, 4], teams=[[3], [4], []])
	data = dict(queues=[], matches=[md])
	app, database = _restore_setup(tmp_path, monkeypatch, data)
	database.select_one.side_effect = [{"data": json.dumps(data)}, dict(match_id=81, channel_id=1, queue_id=2, ranked=1)]
	database.fetchall = AsyncMock(side_effect=[
		[dict(user_id=3, team=0, channel_id=1), dict(user_id=4, team=1, channel_id=1)],
		[dict(user_id=3), dict(user_id=4)]])
	restore = AsyncMock()
	monkeypatch.setattr(state.Match, "from_json", restore, raising=False)
	asyncio.run(state.load_state(app))
	assert app.state_restored
	restore.assert_not_awaited()
	assert app.active_matches == []


def test_incomplete_legacy_result_blocks_restore_instead_of_rerating(tmp_path, monkeypatch):
	md = dict(match_id=81, channel_id=1, queue_id=2, players=[3, 4], teams=[[3], [4], []])
	app, database = _restore_setup(tmp_path, monkeypatch, dict(queues=[], matches=[md]))
	database.select_one.side_effect = [{"data": json.dumps(dict(queues=[], matches=[md]))}, dict(match_id=81, channel_id=1, queue_id=2, ranked=1)]
	database.fetchall = AsyncMock(return_value=[])
	with pytest.raises(state.Exc.ValueError, match="inconsistent stored roster"):
		asyncio.run(state.load_state(app))
	assert not app.state_restored


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
