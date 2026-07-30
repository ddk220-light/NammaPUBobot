# -*- coding: utf-8 -*-
"""Per-match reads that only the Match Cards embed needs.

Every fetch is independently guarded, so a missing table or a failing query
degrades that one signal to empty rather than costing the whole card.

All joins key on (aoe2_match_id, player_number). profile_id is a nullable
denormalisation on every long-form rs_*/cls_* table, and NULL never matches in
a join, so joining on it silently drops players.
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


async def _buildings(aoe2_match_id):
	rows = await db.fetchall(
		"SELECT player_number, building, count FROM rs_player_buildings "
		"WHERE aoe2_match_id=%s AND building IN (%s, %s)",
		[aoe2_match_id, FARM_BUILDING, TC_BUILDING])
	out = {}
	for r in rows or []:
		entry = out.setdefault(r["player_number"], {"farms": 0, "tcs": 0})
		field = "farms" if r["building"] == FARM_BUILDING else "tcs"
		entry[field] = int(r.get("count") or 0)
	return out


async def _clicks(aoe2_match_id):
	rows = await db.fetchall(
		"SELECT player_number, t_s FROM rs_player_events "
		"WHERE aoe2_match_id=%s AND t_s IS NOT NULL "
		"ORDER BY player_number, t_s",
		[aoe2_match_id])
	out = {}
	for r in rows or []:
		out.setdefault(r["player_number"], []).append(int(r["t_s"]))
	for times in out.values():
		times.sort()
	return out


async def _strategies(aoe2_match_id):
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
		[aoe2_match_id, *STRATEGY_KEYS])
	out = {}
	for r in rows or []:
		label = r.get("title") or str(r.get("ckey") or "").replace("_", " ").title()
		if label:
			out.setdefault(r["player_number"], []).append(label)
	return out


async def _spawn(aoe2_match_id):
	keys = [k for k, _ in SPAWN_PHRASES]
	placeholders = ",".join(["%s"] * len(keys))
	rows = await db.fetchall(
		"SELECT player_number, `key` AS ckey FROM cls_results "
		f"WHERE aoe2_match_id=%s AND `key` IN ({placeholders})",
		[aoe2_match_id, *keys])
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


async def _peak_eapm(aoe2_match_id):
	"""Max per-minute bucket per player. Peak is not stored — it is derived.

	rs_player_apm is forward-only, so this is empty for every match ingested
	before the eAPM pipeline deployed. Callers must omit the figure rather than
	render a zero. Never average these buckets: rows are absent for zero-action
	minutes, so any mean over them divides by active minutes instead of whole
	game minutes — the parity-preserving average is rs_player_games.eapm.
	"""
	rows = await db.fetchall(
		"SELECT player_number, MAX(actions) AS peak FROM rs_player_apm "
		"WHERE aoe2_match_id=%s GROUP BY player_number",
		[aoe2_match_id])
	return {r["player_number"]: int(r["peak"]) for r in rows or []
	        if r.get("peak") is not None}


async def fetch_card_signals(aoe2_match_id, match_end_s=None):
	"""Every Match Card signal outside rs_player_games, keyed by player_number.

	``match_end_s`` is accepted so callers can pass the match duration alongside;
	bucketing itself happens in card_scoring.production_coverage.
	"""
	return {
		"buildings": await _safe(_buildings(aoe2_match_id), {}, "buildings"),
		"clicks": await _safe(_clicks(aoe2_match_id), {}, "events"),
		"strategies": await _safe(_strategies(aoe2_match_id), {}, "strategies"),
		"spawn": await _safe(_spawn(aoe2_match_id), {}, "spawn"),
		"peak_eapm": await _safe(_peak_eapm(aoe2_match_id), {}, "peak eapm"),
	}
