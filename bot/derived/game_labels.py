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

# The classifier emits strategy rows and luck/spawn rows in one undifferentiated
# stream (cls_results mixed them in one table with no category column), so what
# to STORE has to be stated explicitly. The two tuples below have different
# provenance, and conflating them silently drops labels:
#
#   STRATEGY_KEYS is the 17 strategy classifications. It is the ONLY copy of that
#   list now: bot/replay_stats/card_query.py used to keep an identical one to
#   constrain its own cls_results query, and stage 5c deleted it when the cards
#   moved to this table -- a reader asks for `kind`, which is this allowlist's
#   answer, already stored.
#
#   SPAWN_KEYS is NOT card_query's SPAWN_PHRASES. SPAWN_PHRASES holds only the 3
#   spawn facts worth rendering as a sentence on a card; these 11 are every luck
#   classification in utils/classifications/defs/luck.py except luck_baseline
#   (which fires for every player in every valid Nomad game and is deliberately
#   stored nowhere). Storing is not displaying: "re-syncing" this tuple with
#   card_query would throw away 8 spawn labels with no error anywhere.
#
# tests/test_game_labels.py guards both directions against the upstream
# registry, so a NEW classifier key added there fails the build here rather than
# being silently dropped by label_rows.
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

# The subset of SPAWN_KEYS that describes WHERE a player started relative to the
# other players, as opposed to what was near them. The three are near-exclusive
# and together cover the one spawn fact worth reading a win rate against: a
# player who does well next to the enemy and badly across the map from everyone
# is telling you something about how they play.
#
# The other eight are resource and layout facts (gold_poor, near_stone,
# tight_villagers, ...). They are stored -- storing is not displaying, see the
# comment above -- and simply have no place in a sentence that reads "wins most
# when spawning X": "wins most when spawning stone-poor" is a statement about the
# map generator, not the player.
#
# The SAME three keys, in the same priority order, back
# bot/replay_stats/card_query.SPAWN_PHRASES, which pairs them with card-specific
# wording. Deliberately two tuples and not one import: the card picks ONE phrase
# per player and needs a priority order, while the scouting report ranks all
# three on their records and needs none. tests/test_game_labels.py pins the two
# key sets equal, so the duplication cannot drift into disagreement.
POSITION_KEYS = ("spawn_near_enemy", "spawn_isolated", "spawn_near_ally")


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


# The column order every payload row is emitted in. core/DBAdapters/mysql.py's
# insert_many takes its column list from the FIRST row's keys and then zips each
# subsequent row's .values() against it -- so two rows whose dicts carry the same
# keys in a DIFFERENT order write values into the wrong columns, silently, with
# no error from MySQL as long as the types happen to be compatible. Every caller
# is safe by construction today (both build rows from the same literal), but
# nothing enforced it. Normalising here makes the payload order a property of
# this function rather than of its callers' dict-literal order.
_COLUMNS = ("replay_match_id", "player_number", "label", "kind", "evidence", "played_at")


async def write(replay_match_id, rows, db_adapter=None):
	"""Idempotent per-match write: DELETE this match's rows, then insert what
	label_rows returned, stamping replay_match_id onto each row (the pure
	function above deliberately never sees it -- see its docstring).

	Mirrors bot/derived/game_stats.py's write for the same reason: a match
	can be re-ingested (parser bump, manual retry, or the stage-3.4 backfill
	correcting a stale row) and the stored set must exactly match the latest
	compute, never accumulate leftovers from a run with a different label
	set.

	`db_adapter` overrides the module-global adapter for callers that write a
	whole match through one specific connection --
	bot/replay_stats/classification_sync.py's sync_match takes one and passes it
	straight down, so a caller that owns a connection has this match's labels
	written through it rather than through the bot's global adapter.

	ACCEPTED TRADEOFF, deliberate: the DELETE and the INSERT are not one
	transaction, because the adapter runs in autocommit (see
	core/DBAdapters/mysql.py's connect) and has no transaction surface to reach
	for. If the insert fails after the delete succeeded, this match is briefly
	label-less -- and bot/derived/backfill.py's reconciliation loop notices the
	set difference and rewrites it within POLL_INTERVAL. Making this atomic
	means giving the adapter transactions, which is a change to every writer in
	the bot, for a window that already self-heals. Do not "fix" it here.
	"""
	dbw = db_adapter or db
	await dbw.execute("DELETE FROM game_labels WHERE replay_match_id=%s", [replay_match_id])
	if not rows:
		return
	payload = []
	for r in rows:
		row = dict(r)
		row["replay_match_id"] = replay_match_id
		row["evidence"] = json.dumps(row.get("evidence") or {}, sort_keys=True)
		if set(row) != set(_COLUMNS):
			# Loud, not coerced. A row with an unexpected key set means the
			# compute and the table have diverged, and quietly dropping or
			# defaulting the difference is how a column silently stops being
			# written. Both callers are best-effort-guarded, so this logs.
			raise ValueError(
				f"game_labels row for match {replay_match_id} has keys {sorted(row)}, "
				f"expected exactly {sorted(_COLUMNS)}")
		payload.append({c: row[c] for c in _COLUMNS})
	await dbw.insert_many("game_labels", payload, on_duplicate="replace")
