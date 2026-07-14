# -*- coding: utf-8 -*-
"""Derived per-player match tags from replay-stats rows.

These tags are facts from rs_* tables: age timing, eco/army profile, unit comp, and tech path.
They are persisted so leaderboards and post-game summaries read stable evidence instead of
recomputing broad labels only.
"""
import json
import time
from collections import defaultdict

from core.database import db

try:
	from . import scoring
except ImportError:
	# utils/backfill_player_game_tags.py loads this file standalone via
	# spec_from_file_location (no parent package), so the relative import has
	# nothing to resolve against. Load scoring.py from the same directory.
	import importlib.util as _importlib_util
	import os as _os
	_spec = _importlib_util.spec_from_file_location(
		"rs_scoring_standalone",
		_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "scoring.py"))
	scoring = _importlib_util.module_from_spec(_spec)
	_spec.loader.exec_module(scoring)


TAG_META = {
	"All-in pressure": ("impact", "All-in pressure"),
	"Map pressure": ("impact", "Map pressure"),
	"Boom carry": ("impact", "Boom carry"),
	"Eco carry": ("impact", "Eco carry"),
	"Age-up tempo": ("impact", "Age-up tempo"),
	"Reboom": ("impact", "Reboom"),
	"High impact": ("impact", "High impact"),
	"Naked FC": ("style", "Naked FC"),
	"Greedy boom": ("style", "Greedy boom"),
	"Feudal all-in": ("style", "Feudal all-in"),
	"Fast Imp": ("style", "Fast Imp"),
	"Army spammer": ("style", "Army spammer"),
	"Tech greedy": ("tech", "Tech greedy"),
	"Upgrade timer": ("tech", "Upgrade timer"),
	"Knight-heavy comp": ("composition", "Knight-heavy comp"),
	"Monk support": ("composition", "Monk support"),
	"Trash switch": ("composition", "Trash switch"),
	"One-trick comp": ("composition", "One-trick comp"),
	"Mixed comp": ("composition", "Mixed comp"),
}

ARCHER_UPGRADES = {"Fletching", "Bodkin Arrow", "Bracer", "Thumb Ring", "Ballistics"}
CAV_UPGRADES = {"Bloodlines", "Husbandry", "Scale Barding Armor", "Chain Barding Armor", "Plate Barding Armor"}
ECO_UPGRADES = {"Wheelbarrow", "Hand Cart", "Double-Bit Axe", "Bow Saw", "Two-Man Saw", "Horse Collar", "Heavy Plow"}


def _tag(tag, score, evidence):
	category, label = TAG_META[tag]
	return {"tag": tag, "label": label, "category": category, "score": int(max(0, min(100, round(score)))),
	        "evidence": evidence}


def derive_tags(row, group, units=None, techs=None):
	units = units or []
	techs = techs or []
	scores = scoring.impact_scores(row, group)
	tags = []
	base_evidence = {
		"impact": scores["impact"],
		"army": scores["army"],
		"eco": scores["eco"],
		"timing": scores["timing"],
		"villagers": row.get("villagers"),
		"military": row.get("military"),
		"pre_castle_villagers": row.get("vil_pre_castle"),
		"pre_castle_military": row.get("mil_pre_castle"),
	}
	for impact_tag in scoring.derive_impact_tags(scores):
		tags.append(_tag(scoring.TAG_NAMES[impact_tag["key"]]["stored"], impact_tag["score"], base_evidence))

	castle_m = (row.get("castle_s") or 0) / 60 if row.get("castle_s") else None
	imp_m = (row.get("imperial_s") or 0) / 60 if row.get("imperial_s") else None
	mil_pc = row.get("mil_pre_castle") or 0
	vil_pc = row.get("vil_pre_castle") or 0
	if castle_m and castle_m <= 18.5 and mil_pc <= 3:
		tags.append(_tag("Naked FC", 78, {"castle_min": round(castle_m, 1), "pre_castle_military": mil_pc}))
	if vil_pc >= 30 and mil_pc <= 6:
		tags.append(_tag("Greedy boom", min(90, 55 + vil_pc), {"pre_castle_villagers": vil_pc, "pre_castle_military": mil_pc}))
	if mil_pc >= 14 and (not castle_m or castle_m >= 19.5):
		tags.append(_tag("Feudal all-in", min(92, 50 + mil_pc * 2), {"pre_castle_military": mil_pc, "castle_min": castle_m}))
	if imp_m and imp_m <= 36:
		tags.append(_tag("Fast Imp", max(62, 96 - imp_m), {"imperial_min": round(imp_m, 1)}))
	if (row.get("military") or 0) >= 140:
		tags.append(_tag("Army spammer", min(96, (row.get("military") or 0) / 2), {"military": row.get("military")}))

	early_eco_techs = [t for t in techs if t["tech"] in ECO_UPGRADES and t.get("click_s") and t["click_s"] <= 35 * 60]
	early_army_techs = [t for t in techs if t["tech"] in ARCHER_UPGRADES | CAV_UPGRADES and t.get("click_s") and t["click_s"] <= 35 * 60]
	if len(early_eco_techs) >= 3 and mil_pc <= 8:
		tags.append(_tag("Tech greedy", 58 + len(early_eco_techs) * 5, {"early_eco_techs": [t["tech"] for t in early_eco_techs[:5]]}))
	if len(early_army_techs) >= 2:
		tags.append(_tag("Upgrade timer", 58 + len(early_army_techs) * 5, {"early_army_techs": [t["tech"] for t in early_army_techs[:5]]}))

	by_cat = defaultdict(int)
	for u in units:
		if u.get("is_military"):
			by_cat[u.get("category") or ""] += int(u.get("total") or 0)
	total_military = sum(by_cat.values())
	if total_military:
		top_cat, top_total = max(by_cat.items(), key=lambda x: x[1])
		share = top_total / total_military
		comp_evidence = {"top_category": top_cat, "top_total": top_total, "military_total": total_military}
		if top_cat == "knight_line" and top_total >= 45:
			tags.append(_tag("Knight-heavy comp", min(95, 45 + top_total / 2), comp_evidence))
		if by_cat.get("monk", 0) >= 3:
			tags.append(_tag("Monk support", min(88, 55 + by_cat["monk"] * 4), {"monks": by_cat["monk"]}))
		trash = by_cat.get("spearman_line", 0) + by_cat.get("skirmisher", 0) + by_cat.get("scout", 0)
		if trash >= 60 and trash / total_military >= 0.45:
			tags.append(_tag("Trash switch", min(92, 45 + trash / 2), {"trash_units": trash, "military_total": total_military}))
		if share >= 0.62 and top_total >= 60:
			tags.append(_tag("One-trick comp", min(92, 45 + share * 60), comp_evidence))
		elif len([v for v in by_cat.values() if v >= 20]) >= 3:
			tags.append(_tag("Mixed comp", 70, {"categories": dict(by_cat)}))

	# Stable unique tags, highest score wins if duplicate category logic added same tag twice.
	seen = {}
	for tag in tags:
		if tag["tag"] not in seen or tag["score"] > seen[tag["tag"]]["score"]:
			seen[tag["tag"]] = tag
	return sorted(seen.values(), key=lambda t: (-t["score"], t["tag"]))


async def ensure_table():
	exists = await db.fetchone("SHOW TABLES LIKE 'rs_player_game_tags'")
	if not exists:
		await db.execute(
			"CREATE TABLE IF NOT EXISTS rs_player_game_tags ("
			"aoe2_match_id BIGINT NOT NULL, player_number BIGINT NOT NULL, tag VARCHAR(191) NOT NULL, "
			"tag_label VARCHAR(191), category VARCHAR(191), score FLOAT, evidence_json MEDIUMTEXT, "
			"played_at BIGINT, created_at BIGINT, user_id BIGINT, profile_id BIGINT, identity VARCHAR(191), "
			"civ VARCHAR(191), team VARCHAR(191), winner TINYINT(1), "
			"PRIMARY KEY (aoe2_match_id, player_number, tag))")
	await _ensure_index("idx_rs_player_game_tags_tag_time", "tag, category, played_at")
	await _ensure_index("idx_rs_player_game_tags_user_time", "user_id, profile_id, played_at")


async def _ensure_index(name, columns):
	row = await db.fetchone("SHOW INDEX FROM rs_player_game_tags WHERE Key_name=%s", [name])
	if row:
		return
	await db.execute("CREATE INDEX `{}` ON rs_player_game_tags ({})".format(name, columns))


async def write_match_tags(aoe2_match_id):
	await ensure_table()
	players = await db.fetchall(
		"SELECT g.*, m.at AS played_at "
		"FROM rs_player_games g JOIN rs_matches rm ON rm.aoe2_match_id=g.aoe2_match_id "
		"LEFT JOIN qc_matches m ON m.match_id=rm.bot_match_id "
		"WHERE g.aoe2_match_id=%s",
		[aoe2_match_id])
	if not players:
		return 0
	units = await db.fetchall("SELECT * FROM rs_player_units WHERE aoe2_match_id=%s", [aoe2_match_id])
	techs = await db.fetchall("SELECT * FROM rs_player_techs WHERE aoe2_match_id=%s", [aoe2_match_id])
	units_by_player = defaultdict(list)
	techs_by_player = defaultdict(list)
	for row in units or []:
		units_by_player[int(row["player_number"])].append(row)
	for row in techs or []:
		techs_by_player[int(row["player_number"])].append(row)
	now = int(time.time())
	rows = []
	for player in players:
		pnum = int(player["player_number"])
		for tag in derive_tags(player, players, units_by_player[pnum], techs_by_player[pnum]):
			rows.append({
				"aoe2_match_id": int(aoe2_match_id),
				"player_number": pnum,
				"tag": tag["tag"],
				"tag_label": tag["label"],
				"category": tag["category"],
				"score": tag["score"],
				"evidence_json": json.dumps(tag["evidence"], sort_keys=True),
				"played_at": player.get("played_at"),
				"created_at": now,
				"user_id": player.get("user_id"),
				"profile_id": player.get("profile_id"),
				"identity": player.get("identity"),
				"civ": player.get("civ"),
				"team": player.get("team"),
				"winner": player.get("winner"),
			})
	await db.execute("DELETE FROM rs_player_game_tags WHERE aoe2_match_id=%s", [aoe2_match_id])
	if rows:
		await db.insert_many("rs_player_game_tags", rows, on_dublicate="replace")
	return len(rows)
