# -*- coding: utf-8 -*-
"""Derived-global game_labels: the strategy and spawn labels a player earned
in one game, computed once at ingest instead of re-derived every time a card
renders. label_rows is pure -- no DB, no I/O -- so the exact same function
drives both the live write below (called from
bot/replay_stats/classification_sync.py's sync_match, right after the
classifier runs) and the stage-3.4 reconciliation loop that backfills the
already-ingested historical matches out of the legacy cls_results table.
Neither caller teaches the function anything about where its input rows came
from, which is what lets one implementation serve both."""
import json

from core.database import db

# cls_results mixes strategy rows and luck/spawn rows in one table with no
# category column -- copied verbatim from bot/replay_stats/card_query.py so
# the two allowlists never drift apart.
STRATEGY_KEYS = (
	"archer_rush", "scout_rush", "maa_rush", "knight_rush", "crossbow_rush",
	"cav_archer_rush", "camel_rush", "ram_push", "forward_castle", "safe_castle",
	"late_knight", "late_crossbow", "late_cav_archer", "late_camel",
	"late_unique", "late_ram", "boom_to_imp",
)

SPAWN_KEYS = (
	"spawn_near_enemy", "spawn_isolated", "spawn_near_ally", "spawn_near_gold",
	"spawn_gold_poor", "spawn_near_stone", "spawn_stone_poor", "spawn_near_food",
	"spawn_food_poor", "tight_villagers", "scattered_villagers",
)


def kind_for(label):
	"""'strategy' | 'spawn' | None. luck_baseline is in neither allowlist and
	therefore falls through to None by construction, not a special case --
	it fires for every player in every valid Nomad game and would otherwise
	become the most common "strategy" in the database. Any other unrecognised
	key falls through the same way: `kind` is a stored contract, not a
	guessed hint, so an unmapped label must be dropped by the caller rather
	than stored with a made-up kind."""
	if label in STRATEGY_KEYS:
		return "strategy"
	if label in SPAWN_KEYS:
		return "spawn"
	return None


def label_rows(result_rows, metric_rows, played_at):
	"""Per-(player, label) derived facts for one match. Pure: no DB, no I/O.

	`result_rows` are cls_results-shaped dicts (one per matched trigger, each
	carrying `key` and `player_number`); `metric_rows` are
	cls_result_metrics-shaped dicts (`key`, `player_number`, `metric`,
	`value`). Returns one row per (player_number, label) with NO
	replay_match_id -- the caller stamps that, so the same function serves
	the live ingest and the backfill without either one teaching it where
	the id comes from.

	A result row whose key is not in STRATEGY_KEYS or SPAWN_KEYS is dropped
	entirely (see kind_for's docstring). Evidence is scoped to the exact
	(key, player_number) pair -- a metric row for a different player, or for
	a different label the same player also earned, must never leak in."""
	rows = []
	for r in result_rows:
		label = r.get("key")
		kind = kind_for(label)
		if kind is None:
			continue
		pn = r.get("player_number")
		evidence = {
			m["metric"]: m["value"] for m in metric_rows
			if m.get("key") == label and m.get("player_number") == pn
		}
		rows.append(dict(
			player_number=pn,
			label=label,
			kind=kind,
			evidence=evidence,
			played_at=played_at,
		))
	return rows


async def write(replay_match_id, rows):
	"""Idempotent per-match write: DELETE this match's rows, then insert what
	label_rows returned, stamping replay_match_id onto each row (the pure
	function above deliberately never sees it -- see its docstring).

	Mirrors bot/derived/game_stats.py's write for the same reason: a match
	can be re-ingested (parser bump, manual retry, or the stage-3.4 backfill
	correcting a stale row) and the stored set must exactly match the latest
	compute, never accumulate leftovers from a run with a different label
	set.
	"""
	await db.execute("DELETE FROM game_labels WHERE replay_match_id=%s", [replay_match_id])
	if not rows:
		return
	payload = []
	for r in rows:
		row = dict(r)
		row["replay_match_id"] = replay_match_id
		row["evidence"] = json.dumps(row.get("evidence") or {}, sort_keys=True)
		payload.append(row)
	await db.insert_many("game_labels", payload, on_duplicate="replace")
