import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nammaoe2bot.app import Application
from nammaoe2bot.pickup import stats
from nammaoe2bot.pickup.match import match as match_module
from nammaoe2bot.pickup.match.match import Match
from tests.test_match_lifecycle_e2e import build_match
from tests.test_match_counter import CounterTransaction


@pytest.mark.parametrize("counter,expected", [(50, 82), (100, None), (None, 82)])
def test_restored_id_floor_never_consumes_an_id_or_moves_backwards(monkeypatch, counter, expected):
	database = CounterTransaction(counter=counter)
	monkeypatch.setattr(stats, "db", database)
	asyncio.run(stats.reserve_restored_ids([80, 81]))
	if expected is None:
		assert len(database.calls) == 1
	elif counter is None:
		assert database.calls[-1] == ("insert", "match_counter", {"next_id": expected})
	else:
		assert database.calls[-1] == ("execute", "UPDATE match_counter SET next_id=%s", [expected])


def test_boot_counter_repair_does_not_lower_a_restored_floor(monkeypatch):
	database = CounterTransaction(counter=500, maximum=100)
	monkeypatch.setattr(stats, "db", database)
	asyncio.run(stats.check_match_id_counter())
	assert all(call[0] == "fetchone" for call in database.calls)
	assert "FOR UPDATE" in database.calls[0][1]


def test_missing_counter_recovery_accounts_for_both_saved_and_recorded_ids(monkeypatch):
	database = CounterTransaction(counter=None, maximum=500)
	monkeypatch.setattr(stats, "db", database)
	asyncio.run(stats.reserve_restored_ids([80, 81]))
	assert database.calls[-1] == ("insert", "match_counter", {"next_id": 500})


@pytest.mark.parametrize("stage", [Match.CHECK_IN, Match.WAITING_REPORT])
@pytest.mark.parametrize("legacy", [False, True])
def test_match_roundtrip_preserves_original_id_and_checkin_or_report_state(monkeypatch, stage, legacy):
	app = Application(client=None)
	match = build_match(app)
	match.state = stage
	match.states = [Match.WAITING_REPORT] if stage == Match.CHECK_IN else []
	match.check_in.ready_players = {match.players[0]}
	match.winner, match.scores = 1, [0, 1]
	match.streaks = {p.id: i - 2 for i, p in enumerate(match.players)}
	data = json.loads(json.dumps(match.serialize()))
	if legacy:
		for key in ("winner", "scores", "start_time", "streaks"):
			data.pop(key)
	source = deepcopy(data)
	qc = match.qc
	qc.guild_id, qc.queues = 123, [match.queue]
	app.channels = {qc.id: qc}
	app.active_matches.clear()
	guild = SimpleNamespace(get_member=lambda uid: next(p for p in match.players if p.id == uid))
	monkeypatch.setattr(match_module, "dc", SimpleNamespace(app=app, get_guild=lambda _id: guild))
	monkeypatch.setattr(match_module, "SystemContext", lambda qc: object())
	checkin = AsyncMock()
	monkeypatch.setattr(match_module.CheckIn, "start", checkin)
	reserve = AsyncMock(side_effect=AssertionError("restoration must not allocate a new ID"))
	monkeypatch.setattr(stats, "next_match", reserve)
	asyncio.run(Match.from_json(data))
	restored, = app.active_matches
	assert restored.id == source["match_id"]
	assert restored.state == stage
	assert restored.states == source["states"]
	assert [[p.id for p in team] for team in restored.teams] == source["teams"]
	assert {p.id for p in restored.check_in.ready_players} == set(source["ready_players"])
	assert restored.maps == source["maps"]
	assert restored.scores == ([0, 0] if legacy else [0, 1])
	assert restored.winner == (None if legacy else 1)
	assert restored.restored is True
	assert restored.streaks == ({p.id: 0 for p in match.players} if legacy else match.streaks)
	assert checkin.await_count == int(stage == Match.CHECK_IN)
	assert data == source, "restoration mutated its input and made retries unsafe"


def test_new_match_preserves_streaks_from_the_existing_rating_read(monkeypatch):
	app = Application(client=None)
	template = build_match(app)
	rows = [dict(user_id=p.id, rating=1500, streak=i - 3) for i, p in enumerate(template.players)]
	read = AsyncMock(return_value=rows)
	template.qc.rating.get_players = read
	monkeypatch.setattr(stats, 'next_match', AsyncMock(return_value=9000))
	asyncio.run(Match.new(SimpleNamespace(qc=template.qc), template.queue, template.players,
		ranked=True, pick_teams='matchmaking', team_size=4))
	created = app.active_matches[-1]
	assert read.await_count == 1
	assert created.streaks == {row['user_id']: row['streak'] for row in rows}
	assert created.restored is False


def test_failed_checkin_restoration_removes_partial_match(monkeypatch):
	app = Application(client=None)
	match = build_match(app)
	match.state = Match.CHECK_IN
	data = match.serialize()
	qc = match.qc
	qc.guild_id, qc.queues = 123, [match.queue]
	app.channels = {qc.id: qc}
	app.active_matches.clear()
	guild = SimpleNamespace(get_member=lambda uid: next(p for p in match.players if p.id == uid))
	monkeypatch.setattr(match_module, "dc", SimpleNamespace(app=app, get_guild=lambda _id: guild))
	monkeypatch.setattr(match_module, "SystemContext", lambda qc: object())
	monkeypatch.setattr(match_module.CheckIn, "start", AsyncMock(side_effect=RuntimeError("Discord unavailable")))
	with pytest.raises(RuntimeError):
		asyncio.run(Match.from_json(data))
	assert app.active_matches == []
