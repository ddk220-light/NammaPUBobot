"""Guards for the lightweight product mode that pauses replay analysis."""
import ast
import asyncio
from pathlib import Path

import nammaoe2bot.community as community
import nammaoe2bot.derived.civ_stats as civ_stats
from nammaoe2bot.features.civs import matcher, recorded
from nammaoe2bot.features.lobby import completed


ROOT = Path(__file__).resolve().parents[1]


def _dotted(node):
	if isinstance(node, ast.Name):
		return node.id
	if isinstance(node, ast.Attribute):
		base = _dotted(node.value)
		return f"{base}.{node.attr}" if base else node.attr
	return ""


def _awaited_calls(nodes):
	return {
		_dotted(node.value.func)
		for parent in nodes
		for node in ast.walk(parent)
		if isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
	}


def test_replay_workers_are_only_scheduled_inside_the_product_gate():
	tree = ast.parse((ROOT / "nammaoe2bot/discord/events.py").read_text())
	on_think = next(
		n for n in tree.body
		if isinstance(n, ast.AsyncFunctionDef) and n.name == "on_think")
	gate = next(
		n for n in on_think.body
		if isinstance(n, ast.If) and isinstance(n.test, ast.Call)
		and _dotted(n.test.func) == "replay_pipeline_available")

	replay_jobs = {
		"ingest.jobs.think",
		"derived.jobs.think",
		"derived.sweeper_jobs.think",
	}
	assert replay_jobs <= _awaited_calls(gate.body)
	assert not (replay_jobs & _awaited_calls([
		n for n in on_think.body if n is not gate
	])), "a replay worker escaped the disabled-mode gate"


def test_paused_boot_does_not_import_the_process_pool_or_parser():
	tree = ast.parse((ROOT / "nammaoe2bot/ingest/jobs.py").read_text())
	top_imports = {
		node.module for node in tree.body if isinstance(node, ast.ImportFrom)
	}
	assert "parse" not in top_imports and "procpool" not in top_imports
	ingest_one = next(
		n for n in ast.walk(tree)
		if isinstance(n, ast.AsyncFunctionDef) and n.name == "ingest_one")
	assert any(
		isinstance(n, ast.ImportFrom) and n.module == "parse"
		for n in ast.walk(ingest_one)), "self-hosted replay mode must remain reversible"


def test_default_image_excludes_replay_and_chart_packages():
	core = (ROOT / "requirements.txt").read_text().lower()
	optional = (ROOT / "requirements-replay.txt").read_text().lower()
	for package in ("matplotlib", "mgz", "aocref", "requests", "tqdm"):
		assert package not in core
		assert package in optional
	dockerfile = (ROOT / "Dockerfile").read_text()
	assert "ARG INSTALL_REPLAY_DEPS=false" in dockerfile


def test_missing_replay_switch_fails_closed(monkeypatch):
	monkeypatch.setattr(community.cfg, "DEPLOYMENT_MODE", "self_hosted", raising=False)
	monkeypatch.delattr(community.cfg, "REPLAY_INGEST_ENABLED", raising=False)
	assert community.replay_pipeline_available() is False


def test_civ_finalize_keeps_linkage_and_summary_without_replay(monkeypatch):
	calls = []

	async def _link(bot_match_id, aoe2_match_id):
		calls.append(("link", bot_match_id, aoe2_match_id))
		return True

	async def _refresh(channel_id, computed_at, db_adapter=None):
		calls.append(("civs", channel_id, computed_at, db_adapter))
		return True

	monkeypatch.setattr(community, "link_match_replay", _link)
	monkeypatch.setattr(civ_stats, "refresh_for_channel", _refresh)
	monkeypatch.setattr(recorded.time, "time", lambda: 78)

	asyncio.run(recorded.finalize(
		900, bot_match_id=12, aoe2_match_id=34, computed_at=56))

	assert calls == [("link", 12, 34), ("civs", 900, 78, None)]


def test_existing_civs_recover_the_match_link_after_result_is_stored(monkeypatch):
	linked = []

	class _Db:
		async def fetchone(self, sql, args):
			assert "replay_match_id IS NOT NULL" in sql
			assert args == [12]
			return {"replay_match_id": 34}

	async def _link(bot_match_id, aoe2_match_id, **kwargs):
		linked.append((bot_match_id, aoe2_match_id, kwargs))
		return True

	monkeypatch.setattr(recorded, "db", _Db())
	monkeypatch.setattr(recorded, "link", _link)

	assert asyncio.run(recorded.link_existing(12)) is True
	assert linked == [(12, 34, {"db_adapter": None})]


def test_post_result_matcher_recovers_existing_link_without_network_work(monkeypatch):
	linked = []

	async def _link_existing(bot_match_id):
		linked.append(bot_match_id)
		return True

	async def _must_not_find(*_args, **_kwargs):
		raise AssertionError("existing civ rows must skip identity/API work")

	monkeypatch.setattr(recorded, "link_existing", _link_existing)
	monkeypatch.setattr(matcher, "_find_and_record", _must_not_find)

	assert asyncio.run(matcher._record_with_retry(900, 12, [], 0, 56)) is None
	assert linked == [12]


def test_known_lobby_game_is_linked_even_when_no_civ_rows_can_be_written(monkeypatch):
	linked = []

	async def _link(bot_match_id, aoe2_match_id, **_kwargs):
		linked.append((bot_match_id, aoe2_match_id))
		return True

	class _Db:
		async def fetchone(self, *_args, **_kwargs):
			return None

	monkeypatch.setattr(recorded, "link", _link)
	monkeypatch.setattr(completed, "db", _Db())

	result = asyncio.run(completed.record_civs_by_id(
		900, 12, {"matchId": 34, "teams": []}, [], 0, 56))

	assert result is False
	assert linked == [(12, 34)]
