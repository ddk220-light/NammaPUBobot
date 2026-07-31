# -*- coding: utf-8 -*-
"""Per-match reads that only the Match Cards embed needs.

Every fetch is independently guarded, so a missing table or a failing query
degrades that one signal to empty rather than costing the whole card.

All joins key on (match id, player_number). profile_id is a nullable
denormalisation on every long-form replay_*/cls_* table, and NULL never matches
in a join, so joining on it silently drops players.

That match-id column is spelled `replay_match_id` on the raw replay_* tables and
still `aoe2_match_id` on the legacy cls_* ones: 007_raw_renames renamed the
former and deliberately left the latter alone, because stage 6 retires cls_*
wholesale rather than carrying it forward under a new name.
"""

from core.database import db

# cls_results mixes strategy rows and luck/spawn rows in one table with no
# category column — luck_baseline alone fires for every player in every valid
# Nomad game — so the card must constrain by an explicit allowlist or it will
# render "All valid spawns (baseline)" as somebody's strategy.
STRATEGY_KEYS = (
	"archer_rush", "scout_rush", "maa_rush", "knight_rush", "crossbow_rush",
	"cav_archer_rush", "camel_rush", "ram_push", "forward_castle", "safe_castle",
	"late_knight", "late_crossbow", "late_cav_archer", "late_camel",
	"late_unique", "late_ram", "boom_to_imp",
)

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
	from core.console import log
	try:
		return await coro
	except Exception as e:
		log.error(f"Match card signal '{what}' failed: {e}")
		return default


async def _buildings(replay_match_id):
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
	"""Every strategy classification that fired, per player, as display labels.

	Labels come from cls_classifications.title — the same source /insights uses.
	Four competing label maps already exist in this codebase; this adds none.
	"""
	placeholders = ",".join(["%s"] * len(STRATEGY_KEYS))
	rows = await db.fetchall(
		"SELECT c.player_number, c.`key` AS ckey, r.title FROM cls_results c "
		"LEFT JOIN cls_classifications r ON r.`key`=c.`key` "
		f"WHERE c.aoe2_match_id=%s AND c.`key` IN ({placeholders}) "
		"ORDER BY c.player_number, c.`key`",
		[replay_match_id, *STRATEGY_KEYS])
	out = {}
	for r in rows or []:
		label = r.get("title") or str(r.get("ckey") or "").replace("_", " ").title()
		if label:
			out.setdefault(r["player_number"], []).append(label)
	return out


async def _spawn(replay_match_id):
	keys = [k for k, _ in SPAWN_PHRASES]
	placeholders = ",".join(["%s"] * len(keys))
	rows = await db.fetchall(
		"SELECT player_number, `key` AS ckey FROM cls_results "
		f"WHERE aoe2_match_id=%s AND `key` IN ({placeholders})",
		[replay_match_id, *keys])
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
	render a zero. Never average these buckets: rows are absent for zero-action
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
