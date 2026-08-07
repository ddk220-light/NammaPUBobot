# -*- coding: utf-8 -*-
"""Per-match reads that only the Match Cards embed needs.

Every fetch is independently guarded, so a missing table or a failing query
degrades that one signal to empty rather than costing the whole card.

All joins key on (match id, player_number). profile_id is a nullable
denormalisation on every long-form replay_* table, and NULL never matches in a
join, so joining on it silently drops players.

FOUR OF THE SIX SIGNALS BELOW READ A SWEEPABLE TABLE, and that is an accepted,
deliberate trade rather than an oversight. replay_events, replay_buildings and
replay_apm are all retention="sweepable" (nammaoe2bot/runtime/data_registry.py):
nammaoe2bot/derived/sweeper.py deletes their rows for a community that opted out of
keeping raw replay detail, once the derived summaries that stand in for them
have been computed. When that happens these queries return no rows and the
card renders WITHOUT those elements — _stats_text omits each one silently
rather than printing a zero, so the card degrades to the facts that survive
(civ, strategy, spawn, villager/military counts, medals) instead of erroring or
claiming a measurement of 0. tests/test_card_query.py pins the empty-source
behaviour; the sweeper still ships with DRY_RUN = True, and this note is part
of what has to be true before anyone flips it. Do NOT add a fallback that
recomputes a swept signal from somewhere else — the whole point of the sweep is
that those rows are gone.

The two signals that are NOT affected are the two this file no longer derives:
strategies and spawn now come from game_labels, which is derived-global and kept
forever, so a lean community keeps its strategy chips after its raw events are
gone.
"""

from nammaoe2bot.runtime.database import db

# The 3 spawn facts worth rendering as a sentence on a card — a DISPLAY subset,
# and emphatically not a copy of what game_labels stores. nammaoe2bot/derived/
# game_labels.py's SPAWN_KEYS holds all 11 spawn classifications, because what to
# STORE and what to SAY are different questions; "re-syncing" the two would throw
# 8 stored labels away in one direction or put 8 unsayable ones on the card in the
# other. tests/test_game_labels.py guards the distinction in both directions.
#
# Ordered by how much the phrase is worth saying: a nearby enemy is the most
# consequential spawn fact, so it wins when several fire for one player
# (near_ally and near_enemy are not mutually exclusive).
SPAWN_PHRASES = (
	("spawn_near_enemy", "spawned next to enemy"),
	("spawn_isolated", "spawned alone"),
	("spawn_near_ally", "spawned with team"),
)

FARM_BUILDING = "Farm"
TC_BUILDING = "Town Center"


async def _safe(coro, default, what):
	from nammaoe2bot.runtime.console import log
	try:
		return await coro
	except Exception as e:
		log.error(f"Match card signal '{what}' failed: {e}")
		return default


async def _buildings(replay_match_id):
	# SWEPT SOURCE (replay_buildings): empty for a lean community whose raw rows
	# have aged out — the card then omits the farm/TC figures. See the module note.
	rows = await db.fetchall(
		"SELECT player_number, building, count FROM replay_buildings "
		"WHERE replay_match_id=%s AND building IN (%s, %s)",
		[replay_match_id, FARM_BUILDING, TC_BUILDING])
	out = {}
	for r in rows or []:
		entry = out.setdefault(r["player_number"], {"farms": 0, "tcs": 0})
		field = "farms" if r["building"] == FARM_BUILDING else "tcs"
		entry[field] = int(r.get("count") or 0)
	return out


async def _clicks(replay_match_id):
	# SWEPT SOURCE (replay_events): empty for a lean community whose raw rows have
	# aged out — card_scoring.production_coverage then has no series to bucket and
	# the coverage bar is omitted. See the module note.
	rows = await db.fetchall(
		"SELECT player_number, t_s FROM replay_events "
		"WHERE replay_match_id=%s AND t_s IS NOT NULL "
		"ORDER BY player_number, t_s",
		[replay_match_id])
	out = {}
	for r in rows or []:
		out.setdefault(r["player_number"], []).append(int(r["t_s"]))
	for times in out.values():
		times.sort()
	return out


async def _composition(replay_match_id):
	"""Military production per unit category, and how much of it came after the
	Imperial click.

	The Imperial split matters because no classification covers post-Imperial
	play, so a player whose whole army arrived in Imperial Age otherwise shows no
	strategy at all. imperial_s is per player, hence the join.

	SWEPT SOURCE (replay_events): empty for a lean community whose raw rows have
	aged out — the army-composition line disappears from the card. See the module
	note. replay_players, the other side of the join, is retained forever.
	"""
	rows = await db.fetchall(
		"SELECT e.player_number, e.category, e.name, SUM(e.amount) AS total, "
		"SUM(CASE WHEN g.imperial_s IS NOT NULL AND e.t_s >= g.imperial_s "
		"         THEN e.amount ELSE 0 END) AS post_imp "
		"FROM replay_events e "
		"JOIN replay_players g ON g.replay_match_id=e.replay_match_id "
		"                      AND g.player_number=e.player_number "
		"WHERE e.replay_match_id=%s AND e.is_military=1 "
		"GROUP BY e.player_number, e.category, e.name",
		[replay_match_id])
	out = {}
	for r in rows or []:
		entry = out.setdefault(r["player_number"], {
			"composition": {}, "unit_names": {}, "post_imperial": 0, "_top": {}})
		cat = r["category"]
		total = int(r.get("total") or 0)
		if total:
			entry["composition"][cat] = entry["composition"].get(cat, 0) + total
			# Keep the most-produced unit name per category, for the categories
			# whose label alone says nothing. Ties break on name for stability.
			name = r.get("name")
			best = entry["_top"].get(cat)
			if best is None or (total, str(name or "")) > (best[0], str(best[1] or "")):
				entry["_top"][cat] = (total, name)
		entry["post_imperial"] += int(r.get("post_imp") or 0)
	for entry in out.values():
		entry["unit_names"] = {c: n for c, (_t, n) in entry.pop("_top").items() if n}
	return out


async def _strategies(replay_match_id):
	"""Every strategy label the player earned, per player, as display labels.

	Read from game_labels, whose `kind` column IS the strategy allowlist: the 17
	keys were decided once, at ingest, by nammaoe2bot/derived/game_labels.py's
	STRATEGY_KEYS, so asking for kind='strategy' asks that same question back
	instead of restating the list here. This file used to carry a verbatim copy of
	those 17 keys to constrain the old cls_results read (that table mixed strategy
	and luck rows with no category column); the copy is gone with the read.

	Labels come from cls_classifications.title — the same source /insights uses.
	Four competing label maps already exist in this codebase; this adds none. The
	join is on `key` alone, so it is unaffected by cls_results still spelling the
	match id `aoe2_match_id` while everything else now says `replay_match_id`.
	"""
	rows = await db.fetchall(
		"SELECT l.player_number, l.label AS ckey, r.title FROM game_labels l "
		"LEFT JOIN cls_classifications r ON r.`key`=l.label "
		"WHERE l.replay_match_id=%s AND l.kind=%s "
		"ORDER BY l.player_number, l.label",
		[replay_match_id, "strategy"])
	out = {}
	for r in rows or []:
		label = r.get("title") or str(r.get("ckey") or "").replace("_", " ").title()
		if label:
			out.setdefault(r["player_number"], []).append(label)
	return out


async def _spawn(replay_match_id):
	# kind='spawn' AND an explicit key list, not one or the other: the kind picks
	# the stored category, the list narrows it to the 3 facts SPAWN_PHRASES can
	# actually say. The other 8 stored spawn labels are real and are simply not
	# rendered here.
	keys = [k for k, _ in SPAWN_PHRASES]
	placeholders = ",".join(["%s"] * len(keys))
	rows = await db.fetchall(
		"SELECT player_number, label AS ckey FROM game_labels "
		f"WHERE replay_match_id=%s AND kind=%s AND label IN ({placeholders})",
		[replay_match_id, "spawn", *keys])
	found = {}
	for r in rows or []:
		found.setdefault(r["player_number"], set()).add(r.get("ckey"))
	out = {}
	for pnum, present in found.items():
		for key, phrase in SPAWN_PHRASES:
			if key in present:
				out[pnum] = phrase
				break
	return out


async def _peak_eapm(replay_match_id):
	"""Max per-minute bucket per player. Peak is not stored — it is derived.

	replay_apm is forward-only, so this is empty for every match ingested
	before the eAPM pipeline deployed. Callers must omit the figure rather than
	render a zero. It is also a SWEPT SOURCE, which reaches the same place from
	the other end of time: a lean community's aged-out matches lose their buckets
	and their peak with them (the average survives — it is replay_players.eapm).
	See the module note. Never average these buckets: rows are absent for zero-action
	minutes, so any mean over them divides by active minutes instead of whole
	game minutes — the parity-preserving average is replay_players.eapm.
	"""
	rows = await db.fetchall(
		"SELECT player_number, MAX(actions) AS peak FROM replay_apm "
		"WHERE replay_match_id=%s GROUP BY player_number",
		[replay_match_id])
	return {r["player_number"]: int(r["peak"]) for r in rows or []
	        if r.get("peak") is not None}


async def fetch_card_signals(replay_match_id, match_end_s=None):
	"""Every Match Card signal outside replay_players, keyed by player_number.

	``match_end_s`` is accepted so callers can pass the match duration alongside;
	bucketing itself happens in card_scoring.production_coverage.
	"""
	return {
		"buildings": await _safe(_buildings(replay_match_id), {}, "buildings"),
		"clicks": await _safe(_clicks(replay_match_id), {}, "events"),
		"composition": await _safe(_composition(replay_match_id), {}, "composition"),
		"strategies": await _safe(_strategies(replay_match_id), {}, "strategies"),
		"spawn": await _safe(_spawn(replay_match_id), {}, "spawn"),
		"peak_eapm": await _safe(_peak_eapm(replay_match_id), {}, "peak eapm"),
	}
