"""Unit tests for the derived-global game_labels pure compute + write
(bot/derived/game_labels.py)."""

import asyncio
import json

import bot.derived.game_labels as game_labels
from bot.derived import game_labels as gl


# ── label_rows() -- brief's verbatim fixtures ───────────────────────────

def test_luck_baseline_is_dropped():
	rows = [dict(key="luck_baseline", player_number=1),
	        dict(key="scout_rush", player_number=1)]
	out = gl.label_rows(rows, [], 500)
	assert [r["label"] for r in out] == ["scout_rush"]


def test_kinds_are_assigned_from_the_allowlist():
	rows = [dict(key="knight_rush", player_number=1),
	        dict(key="spawn_near_enemy", player_number=1),
	        dict(key="tight_villagers", player_number=2)]
	out = gl.label_rows(rows, [], 500)
	kinds = {r["label"]: r["kind"] for r in out}
	assert kinds == {"knight_rush": "strategy", "spawn_near_enemy": "spawn",
	                 "tight_villagers": "spawn"}


def test_unknown_key_is_dropped_not_stored_with_a_guessed_kind():
	out = gl.label_rows([dict(key="brand_new_thing", player_number=1)], [], 500)
	assert out == []


def test_evidence_is_gathered_per_player_and_label():
	rows = [dict(key="scout_rush", player_number=1)]
	metrics = [dict(key="scout_rush", player_number=1, metric="first_scout_s", value=310),
	           dict(key="scout_rush", player_number=2, metric="first_scout_s", value=999),
	           dict(key="knight_rush", player_number=1, metric="other", value=1)]
	out = gl.label_rows(rows, metrics, 500)
	assert out[0]["evidence"] == {"first_scout_s": 310}


def test_played_at_is_stamped_on_every_row():
	out = gl.label_rows([dict(key="ram_push", player_number=3)], [], 777)
	assert out[0]["played_at"] == 777


# ── allowlists + kind_for() ─────────────────────────────────────────────

def test_strategy_and_spawn_keys_match_the_spec_verbatim():
	assert game_labels.STRATEGY_KEYS == (
		"archer_rush", "scout_rush", "maa_rush", "knight_rush", "crossbow_rush",
		"cav_archer_rush", "camel_rush", "ram_push", "forward_castle", "safe_castle",
		"late_knight", "late_crossbow", "late_cav_archer", "late_camel",
		"late_unique", "late_ram", "boom_to_imp",
	)
	assert game_labels.SPAWN_KEYS == (
		"spawn_near_enemy", "spawn_isolated", "spawn_near_ally", "spawn_near_gold",
		"spawn_gold_poor", "spawn_near_stone", "spawn_stone_poor", "spawn_near_food",
		"spawn_food_poor", "tight_villagers", "scattered_villagers",
	)


def test_allowlists_do_not_overlap_and_exclude_luck_baseline():
	overlap = set(game_labels.STRATEGY_KEYS) & set(game_labels.SPAWN_KEYS)
	assert overlap == set()
	assert "luck_baseline" not in game_labels.STRATEGY_KEYS
	assert "luck_baseline" not in game_labels.SPAWN_KEYS


def test_kind_for_every_strategy_and_spawn_key():
	for key in game_labels.STRATEGY_KEYS:
		assert game_labels.kind_for(key) == "strategy", key
	for key in game_labels.SPAWN_KEYS:
		assert game_labels.kind_for(key) == "spawn", key


def test_kind_for_unknown_and_luck_baseline_is_none():
	assert game_labels.kind_for("luck_baseline") is None
	assert game_labels.kind_for("brand_new_thing") is None


# ── write() ──────────────────────────────────────────────────────────────
# No pytest-asyncio in this repo, so this is a plain sync test driving the
# coroutine with asyncio.run() -- never `async def test_...` (which pytest
# would silently skip and falsely report as passing).
class _RecordingDB:
	def __init__(self):
		self.calls = []

	async def execute(self, sql, args=None):
		self.calls.append(("execute", sql, list(args) if args else []))

	async def insert_many(self, table, rows, on_duplicate=None):
		self.calls.append(("insert_many", table, list(rows), on_duplicate))


def test_write_deletes_before_insert_stamps_id_and_serialises_evidence():
	recorder = _RecordingDB()
	original_db = game_labels.db
	game_labels.db = recorder
	try:
		rows = [
			dict(player_number=1, label="scout_rush", kind="strategy",
			     evidence={"first_scout_s": 310}, played_at=500),
			dict(player_number=2, label="spawn_near_enemy", kind="spawn",
			     evidence={}, played_at=500),
		]
		asyncio.run(game_labels.write(555, rows))
	finally:
		game_labels.db = original_db

	# (a) DELETE fires before the insert.
	assert [c[0] for c in recorder.calls] == ["execute", "insert_many"]
	_, delete_sql, delete_args = recorder.calls[0]
	assert "DELETE" in delete_sql.upper()
	assert "game_labels" in delete_sql
	assert delete_args == [555]

	_, table, payload, on_duplicate = recorder.calls[1]
	assert table == "game_labels"
	assert on_duplicate == "replace"
	assert len(payload) == 2

	# (b) replay_match_id stamped onto EVERY row.
	assert all(r["replay_match_id"] == 555 for r in payload)

	# (c) evidence serialised the way the adapter expects: a JSON string
	# (MEDIUMTEXT column), not the raw dict label_rows returned.
	assert isinstance(payload[0]["evidence"], str)
	assert json.loads(payload[0]["evidence"]) == {"first_scout_s": 310}
	assert json.loads(payload[1]["evidence"]) == {}


def test_write_with_no_rows_still_deletes_but_never_inserts():
	recorder = _RecordingDB()
	original_db = game_labels.db
	game_labels.db = recorder
	try:
		asyncio.run(game_labels.write(555, []))
	finally:
		game_labels.db = original_db

	assert [c[0] for c in recorder.calls] == ["execute"]
