# -*- coding: utf-8 -*-
"""Derived player role tags from parsed replay stats.

The strategy classifier catches build-order moments (scouts, xbows, castles, siege).
These tags are broader per-game role reads, designed for profile summaries.
"""

STYLE_TAG_LABELS = {
	"role_fast_castle_pocket": "Fast Castle pocket",
	"role_greedy_boom": "Greedy boom",
	"role_opening_pressure": "Opening pressure",
	"role_knight_pocket": "Knight pocket",
	"role_archer_tempo": "Archer tempo",
	"role_camel_guard": "Camel guard",
	"role_ca_switch": "CA switch",
	"role_siege_closer": "Siege closer",
	"role_trash_stabilizer": "Trash stabilizer",
}


def _winner_int(w):
	return 1 if w in (1, True) else 0 if w in (0, False) else None


def _unit_totals(game, player_number):
	totals = {}
	for u in game.get("units", []):
		if u.get("player_number") != player_number:
			continue
		cat = u.get("category") or ""
		totals[cat] = totals.get(cat, 0) + int(u.get("total") or 0)
	return totals


def _has_tech(game, player_number, names):
	names = set(names)
	return any(t.get("player_number") == player_number and t.get("tech") in names for t in game.get("techs", []))


def _row(key, match_id, player, played_at):
	winner = player.get("winner")
	team = player.get("team")
	return {
		"key": key,
		"aoe2_match_id": int(match_id),
		"player_number": player.get("player_number"),
		"profile_id": player.get("profile_id"),
		"identity": player.get("identity"),
		"civ": player.get("civ"),
		"team": str(team) if team is not None else None,
		"winner": _winner_int(winner),
		"played_at": int(played_at or 0),
	}


def style_tag_rows(game, match_id, played_at):
	rows = []
	for p in game.get("players", []):
		pnum = p.get("player_number")
		if pnum is None:
			continue
		units = _unit_totals(game, pnum)
		castle = p.get("castle_s") or 0
		imp = p.get("imperial_s") or 0
		mil_pre_castle = int(p.get("mil_pre_castle") or 0)
		vil_pre_castle = int(p.get("vil_pre_castle") or 0)
		total_military = int(p.get("military") or 0)
		duration = int((game.get("match") or {}).get("duration_s") or 0)
		keys = []

		if castle and castle <= 18 * 60 and mil_pre_castle <= 4 and vil_pre_castle >= 22:
			keys.append("role_fast_castle_pocket")
		if vil_pre_castle >= 32 and mil_pre_castle <= 5 and (imp == 0 or imp >= 32 * 60):
			keys.append("role_greedy_boom")
		if mil_pre_castle >= 8 or units.get("scout", 0) >= 8 or units.get("archer_line", 0) >= 10:
			keys.append("role_opening_pressure")
		if units.get("knight_line", 0) >= 18 or (units.get("knight_line", 0) >= 10 and _has_tech(game, pnum, {"Bloodlines", "Scale Barding Armor", "Chain Barding Armor"})):
			keys.append("role_knight_pocket")
		if units.get("archer_line", 0) >= 18 or (units.get("archer_line", 0) >= 10 and _has_tech(game, pnum, {"Fletching", "Bodkin Arrow", "Thumb Ring"})):
			keys.append("role_archer_tempo")
		if units.get("camel", 0) >= 12:
			keys.append("role_camel_guard")
		if units.get("cav_archer", 0) >= 15:
			keys.append("role_ca_switch")
		if units.get("siege", 0) >= 8:
			keys.append("role_siege_closer")
		trash = units.get("spearman_line", 0) + units.get("skirmisher", 0)
		if trash >= 30 and (duration >= 40 * 60 or trash >= max(20, total_military * 0.35)):
			keys.append("role_trash_stabilizer")

		for key in dict.fromkeys(keys):
			rows.append(_row(key, match_id, p, played_at))
	return rows
