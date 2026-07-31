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


# ── drift guard against the upstream registry ───────────────────────────
# The verbatim test above pins the allowlists against a hardcoded copy of
# themselves, so it catches an accidental EDIT here but is blind to the failure
# that actually costs data: a new classification appearing upstream. label_rows
# drops any key it does not recognise (deliberately -- see kind_for), so an
# unhandled new key means its cls_results rows are silently thrown away at
# ingest with no error, no log line, and no test failure anywhere. These
# compare against the real registry instead of a copy.

# Keys that exist upstream and are deliberately NOT stored. Adding to this is a
# decision, and has to be made here in writing rather than by an allowlist
# quietly not mentioning a key.
#   luck_baseline -- fires for every player in every valid Nomad game; it is the
#   50% reference cohort, not a fact about a player, and storing it would make
#   it the most common "strategy" in the database.
_DOCUMENTED_EXCLUSIONS = frozenset({"luck_baseline"})


def test_no_upstream_classification_key_is_silently_dropped():
	from utils.classifications.registry import REGISTRY

	handled = set(game_labels.STRATEGY_KEYS) | set(game_labels.SPAWN_KEYS) | _DOCUMENTED_EXCLUSIONS
	unhandled = sorted(set(REGISTRY) - handled)
	assert unhandled == [], (
		f"classification key(s) {unhandled} exist in utils/classifications/registry.py but are in "
		f"neither allowlist in bot/derived/game_labels.py nor _DOCUMENTED_EXCLUSIONS here. "
		f"label_rows drops them, so their cls_results rows are stored nowhere. Add each one to "
		f"STRATEGY_KEYS or SPAWN_KEYS, or to _DOCUMENTED_EXCLUSIONS with the reason.")


def test_the_allowlists_name_only_keys_that_still_exist_upstream():
	# The other direction: a key renamed or retired upstream leaves a dead entry
	# here that matches nothing, which reads as "we store this" and does not.
	from utils.classifications.registry import REGISTRY

	stale = sorted((set(game_labels.STRATEGY_KEYS) | set(game_labels.SPAWN_KEYS)) - set(REGISTRY))
	assert stale == [], f"allowlisted key(s) {stale} no longer exist in the classification registry"


def test_spawn_keys_are_the_luck_definitions_minus_the_baseline():
	# SPAWN_KEYS' actual provenance, asserted rather than described in a comment.
	from utils.classifications.defs import luck

	assert set(game_labels.SPAWN_KEYS) == {c.key for c in luck.CLASSIFICATIONS} - _DOCUMENTED_EXCLUSIONS


def test_strategy_keys_is_the_only_copy_of_the_list():
	# card_query used to keep a verbatim copy to constrain its own cls_results
	# query, and this test used to assert the two stayed identical. Stage 5c moved
	# the cards onto game_labels, where `kind` already answers "is this a strategy?"
	# -- so the copy went, and what is worth pinning now is that it stays gone. A
	# reader re-introducing a key list is re-introducing the drift.
	from bot.replay_stats import card_query

	assert not hasattr(card_query, "STRATEGY_KEYS")


def test_spawn_keys_are_deliberately_wider_than_card_querys_display_subset():
	# SPAWN_KEYS is NOT card_query.SPAWN_PHRASES, and must never be "re-synced"
	# with it: what to STORE and what to SAY are different questions, and
	# SPAWN_PHRASES answers only the second one (3 of the 11).
	from bot.replay_stats import card_query

	phrase_keys = {key for key, _phrase in card_query.SPAWN_PHRASES}
	assert phrase_keys < set(game_labels.SPAWN_KEYS)
	assert len(game_labels.SPAWN_KEYS) > len(phrase_keys)


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


# ── payload column order ─────────────────────────────────────────────────
# See the identical note in tests/test_game_stats.py: insert_many builds its
# column list from the FIRST row's keys and zips every other row's .values()
# against it, so divergent key order writes values into the wrong columns with
# no error. label/kind are both VARCHARs, which is exactly the pair that would
# swap silently.

def _written_payload(rows, replay_match_id=555):
	recorder = _RecordingDB()
	original_db = game_labels.db
	game_labels.db = recorder
	try:
		asyncio.run(game_labels.write(replay_match_id, rows))
	finally:
		game_labels.db = original_db
	return recorder.calls[1][2]


def _label_row(**kw):
	base = dict(player_number=1, label="scout_rush", kind="strategy", evidence={}, played_at=500)
	base.update(kw)
	return base


def test_every_written_row_uses_one_column_order():
	first = _label_row()
	second = {k: v for k, v in reversed(list(_label_row(player_number=2, label="ram_push").items()))}

	payload = _written_payload([first, second])

	assert list(payload[0].keys()) == list(game_labels._COLUMNS)
	assert list(payload[1].keys()) == list(game_labels._COLUMNS)
	assert payload[1]["label"] == "ram_push"
	assert payload[1]["kind"] == "strategy"


def test_a_row_with_an_unexpected_key_set_is_rejected_loudly():
	import pytest

	with pytest.raises(ValueError, match="expected exactly"):
		_written_payload([_label_row(surprise="unmapped")])

	with pytest.raises(ValueError, match="expected exactly"):
		_written_payload([{k: v for k, v in _label_row().items() if k != "kind"}])


def test_position_keys_are_a_subset_of_the_stored_spawn_keys():
	""" A display subset, never a replacement. The other eight spawn labels are
	stored exactly as before -- storing is not displaying -- and only the
	scouting report's summary narrows to these three. """
	assert set(game_labels.POSITION_KEYS) <= set(game_labels.SPAWN_KEYS)
	assert len(set(game_labels.POSITION_KEYS)) == 3


def test_position_keys_match_the_cards_spawn_phrases_exactly():
	""" bot/replay_stats/card_query.SPAWN_PHRASES pairs the SAME three keys with
	card-specific wording, in the same priority order. Deliberately two tuples
	and not one import -- the card picks ONE phrase per player and needs a
	priority, the scouting report ranks all three on their records and needs
	none -- so this test is the only thing stopping them drifting into two
	different ideas of what "position" means.

	Parsed as text rather than imported: bot/replay_stats/card_query.py reaches
	the database on import. """
	import ast
	import os

	root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	with open(os.path.join(root, "bot", "replay_stats", "card_query.py"), encoding="utf-8") as f:
		block = f.read().split("SPAWN_PHRASES = ", 1)[1]
	# Up to and including the closing paren of the literal.
	depth, end = 0, 0
	for i, ch in enumerate(block):
		depth += (ch == "(") - (ch == ")")
		if depth == 0 and i:
			end = i + 1
			break
	phrases = ast.literal_eval(block[:end])

	assert tuple(k for k, _phrase in phrases) == game_labels.POSITION_KEYS


def test_a_map_fact_is_stored_but_is_not_a_position():
	""" "Wins most when spawning stone-poor" is a claim about the map generator,
	not about the player. These stay in SPAWN_KEYS so game_labels keeps them. """
	for key in ("spawn_gold_poor", "spawn_near_stone", "tight_villagers"):
		assert game_labels.kind_for(key) == "spawn"
		assert key not in game_labels.POSITION_KEYS
