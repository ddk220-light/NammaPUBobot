# -*- coding: utf-8 -*-
"""Player persona derivation — a community-flavored archetype from replay stats.

A persona is Style x Team-Role:

  * Style — how they play, from average impact-component deviations vs the
    50-baseline plus confirming tag rates (aggressor / boomer / tempo /
    phoenix / slowcooker / flex).
  * Role — what they are to a team, from how often they top their team's
    impact chart and how volatile their game-to-game impact is
    (carry / engine / wildcard / support / anchor).

Pure module (no DB, no Discord): bot/web.py feeds it aggregates from
_player_impact_profile, and anything else (Discord embeds, offline commentary)
can reuse it. Thresholds are calibrated against the July-2026 live history
(42 players with >=30 parsed games) via utils/persona_calibration.py — the
axis scale factors roughly equalize the spread of each component across the
player pool so no single axis dominates by construction.
"""

MIN_GAMES = 10

STYLES = {
	"aggressor": {
		"name": "Villager Menace",
		"tagline": "Your villagers are never safe. Neither are your house walls.",
	},
	"boomer": {
		"name": "Farm Enjoyer",
		"tagline": "Fifty farms by thirty minutes. The fight can wait.",
	},
	"tempo": {
		"name": "Imp Speedrunner",
		"tagline": "Clicks up before you've even scouted them.",
	},
	"phoenix": {
		"name": "Comeback Merchant",
		"tagline": "Down to three villagers? Rude to count them out.",
	},
	"slowcooker": {
		"name": "Slow Cooker",
		"tagline": "Slow ages, long games — somehow still cooking.",
	},
	"flex": {
		"name": "Certified Flex",
		"tagline": "No fixed plan: reads the game, then picks a lane.",
	},
	"unscouted": {
		"name": "Mystery Box",
		"tagline": "Not enough parsed replays to scout this one yet.",
	},
}

ROLES = {
	"carry": {"name": "Designated Carry", "read": "tops the team impact chart in {carry}% of games"},
	"engine": {"name": "Diesel Engine", "read": "same output every game, rain or shine"},
	"wildcard": {"name": "Coinflip Enjoyer", "read": "either hard-carries or donates Elo — no in-between"},
	"support": {"name": "Squad Glue", "read": "does the quiet work that wins team games"},
	"anchor": {"name": "Steady Hands", "read": "dependable middle of the lineup"},
}

# Tag labels differ between stored rows and web payloads; normalize both.
_TAG_GROUPS = {
	"pressure": {"map pressure", "army pressure"},
	"all_in": {"all-in pressure", "low-eco pressure"},
	"boom": {"boom carry"},
	"eco": {"eco carry"},
	"tempo": {"age-up tempo", "timing edge"},
	"reboom": {"reboom", "recovery"},
}

# Role thresholds (percent / score points), from pool percentiles:
# carry_rate p50=25 p75=38; impact_sd p25=4.8 p75=5.8.
TH = {
	"carry_rate": 38,
	"engine_carry": 22,
	"engine_sd": 5.0,
	"wildcard_sd": 5.9,
	"support_carry": 12,
	"style_min": 1.5,       # weakest scaled deviation that still names a style
	"slow_timing_dev": 4,   # 50 - avg_timing needed for Slow Cooker
}


def _num(v):
	try:
		return float(v)
	except (TypeError, ValueError):
		return None


def _tag_rate(tag_rates, group):
	labels = _TAG_GROUPS[group]
	return sum(rate for label, rate in (tag_rates or {}).items()
	           if str(label).strip().lower() in labels)


def _style_scores(stats, tag_rates):
	army = _num(stats.get("avg_army")) or 50.0
	eco = _num(stats.get("avg_eco")) or 50.0
	timing = _num(stats.get("avg_timing")) or 50.0
	reboom = _num(stats.get("avg_recovery")) or 50.0
	# Scale factors equalize each component's spread across the player pool
	# (army varies least per player, reboom the most).
	return {
		"aggressor": (army - 50) * 2.2 + _tag_rate(tag_rates, "pressure") * 0.20 + _tag_rate(tag_rates, "all_in") * 0.25,
		"boomer": (eco - 50) * 1.3 + _tag_rate(tag_rates, "boom") * 0.15 + _tag_rate(tag_rates, "eco") * 0.15,
		"tempo": (timing - 50) * 1.1 + _tag_rate(tag_rates, "tempo") * 0.12,
		"phoenix": (reboom - 50) * 0.75 + _tag_rate(tag_rates, "reboom") * 0.25,
	}


def _pick_style(stats, tag_rates):
	scores = _style_scores(stats, tag_rates)
	style = max(scores, key=lambda k: scores[k])
	if scores[style] >= TH["style_min"]:
		return style, scores
	timing = _num(stats.get("avg_timing"))
	if timing is not None and (50 - timing) >= TH["slow_timing_dev"]:
		return "slowcooker", scores
	return "flex", scores


def _pick_role(carry_rate, impact_sd):
	if carry_rate is None:
		return "anchor"
	if carry_rate >= TH["carry_rate"]:
		return "carry"
	if impact_sd is not None and carry_rate >= TH["engine_carry"] and impact_sd <= TH["engine_sd"]:
		return "engine"
	if impact_sd is not None and impact_sd >= TH["wildcard_sd"]:
		return "wildcard"
	if carry_rate <= TH["support_carry"]:
		return "support"
	return "anchor"


def _evidence(stats, tag_rates, style, carry_rate, impact_sd):
	out = []
	comps = [("army", "avg_army"), ("eco", "avg_eco"), ("age-up", "avg_timing"), ("reboom", "avg_recovery")]
	for label, key in comps:
		v = _num(stats.get(key))
		if v is not None and abs(v - 50) >= 2:
			out.append("{} {} ({}{} vs match average)".format(label, round(v), "+" if v >= 50 else "", round(v - 50)))
	top_tag = max((tag_rates or {}).items(), key=lambda kv: kv[1], default=None)
	if top_tag and top_tag[1] >= 5:
		out.append("{} in {}% of games".format(top_tag[0], round(top_tag[1])))
	if carry_rate is not None and carry_rate >= 20:
		out.append("team-top impact in {}% of games".format(round(carry_rate)))
	if impact_sd is not None and impact_sd >= TH["wildcard_sd"]:
		out.append("impact swings hard game to game")
	return out[:4]


def derive_persona(stats):
	"""Persona dict for one player's aggregates, or the unscouted persona.

	``stats`` keys (all optional): matches, avg_army, avg_eco, avg_timing,
	avg_recovery, impact_sd, carry_rate (0-100), tag_rates ({label: percent}).
	"""
	stats = stats or {}
	matches = int(_num(stats.get("matches")) or 0)
	if matches < MIN_GAMES:
		style_meta = STYLES["unscouted"]
		return {
			"key": "unscouted",
			"name": style_meta["name"],
			"epithet": None,
			"tagline": style_meta["tagline"],
			"style": "unscouted",
			"role": None,
			"evidence": ["only {} parsed replay{}".format(matches, "" if matches == 1 else "s")],
		}
	tag_rates = stats.get("tag_rates") or {}
	carry_rate = _num(stats.get("carry_rate"))
	impact_sd = _num(stats.get("impact_sd"))
	style, _scores = _pick_style(stats, tag_rates)
	role = _pick_role(carry_rate, impact_sd)
	role_meta = ROLES[role]
	epithet = role_meta["name"]
	role_read = role_meta["read"].format(carry=round(carry_rate or 0))
	return {
		"key": "{}_{}".format(style, role),
		"name": STYLES[style]["name"],
		"epithet": epithet,
		"tagline": STYLES[style]["tagline"] + " " + role_read.capitalize() + ".",
		"style": style,
		"role": role,
		"evidence": _evidence(stats, tag_rates, style, carry_rate, impact_sd),
	}
