# -*- coding: utf-8 -*-
"""Player persona derivation — a community-flavored archetype from replay stats.

A persona is Style x Team-Role:

  * Style — how they play, from average impact-component deviations vs the
    50-baseline plus confirming tag rates (aggressor / boomer / tempo /
    phoenix / slowcooker / flex).
  * Role — what they are to a team, from how often they top their team's
    impact chart and how volatile their game-to-game impact is
    (carry / engine / wildcard / support / anchor).

Pure module (no DB, no Discord): nammaoe2bot/web/server.py feeds it aggregates from
_player_impact_profile, and anything else (e.g. Discord embeds) can reuse it.
Thresholds are calibrated against the live history via
utils/persona_calibration.py — the axis scale factors roughly equalize the
spread of each component across the player pool so no single axis dominates by
construction.

Naming rule: a persona may only claim what the replay parser actually records.
The parser counts units *created* — never kills, razes, or damage — so the
"aggressor" axis is military *production*, not aggression that landed. Impact
carries no win term either (it predicted the winning team in 62% of a 350-match
sample, barely above a coin flip), so no name or tagline here asserts that a
player won anything. Copy that implied kills ("Villager Menace") or outcomes
("Designated Carry", "donates Elo") was retired for that reason.

Two conditions guard against confident-sounding noise, both calibrated on the
350-match / 2778 player-game replay sample:

  * style_margin — the top style must beat the runner-up by a real gap.
    Taking a bare max() named a style for 10 of 33 players on a margin under
    2.0, and 7 of those on a margin under 1.0 (one was 8.0 vs 7.9). Those are
    coin flips, and they now resolve to slowcooker/flex instead.
  * engine_sd / wildcard_sd — impact_sd is far more compressed than the
    original thresholds assumed (pool p25=5.6, p50=6.0, p75=6.3). At the old
    5.0/5.9 cut points "Coinflip Enjoyer" swallowed a third of the pool. They
    now sit on the pool's own quartiles.
  * engine_carry — carry_rate and impact_sd are inversely related here, so the
    old 22% floor sat above every steady player and "Diesel Engine" could not
    be reached at all. It now sits just above support_carry.
"""

MIN_GAMES = 10

STYLES = {
	"aggressor": {
		# Military *production*, not kills — the parser cannot see a raid land.
		"name": "Army Printer",
		"tagline": "More military out of the buildings than anyone else in the lobby.",
	},
	"boomer": {
		"name": "Farm Enjoyer",
		"tagline": "Fifty farms by thirty minutes. The fight can wait.",
	},
	"tempo": {
		# feudal_s + castle_s + imperial_s, so name all three ages, not just Imp.
		"name": "Age-Up Speedrunner",
		"tagline": "Hits Feudal, Castle and Imp ahead of the lobby clock.",
	},
	"phoenix": {
		# Final villagers vs early villagers — a shape, not a rescue from 3 vils.
		"name": "Late Bloomer",
		"tagline": "Opens light on villagers and still finishes with an economy.",
	},
	"slowcooker": {
		"name": "Slow Cooker",
		"tagline": "Slow ages, long games — somehow still cooking.",
	},
	"flex": {
		"name": "Certified Flex",
		"tagline": "No lane clearly ahead of the rest — reads the game, then picks one.",
	},
	"unscouted": {
		"name": "Mystery Box",
		"tagline": "Not enough parsed replays to scout this one yet.",
	},
}

# `read` fills the second half of the tagline. It describes board position and
# consistency only — impact has no win term, so nothing here claims a result.
ROLES = {
	"carry": {"name": "Board Topper", "read": "leads the team's impact board in {carry}% of games"},
	"engine": {"name": "Diesel Engine", "read": "same output game after game"},
	"wildcard": {"name": "Coinflip Enjoyer", "read": "output swings hard from game to game"},
	"support": {"name": "Squad Glue", "read": "rarely tops the board, steady underneath it"},
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

# Role thresholds (percent / score points), from pool percentiles measured on
# the 350-match replay sample: carry_rate p25=10 p50=27 p75=40;
# impact_sd p25=5.6 p50=6.0 p75=6.3.
TH = {
	"carry_rate": 38,
	# Just above support_carry: steadiness and board-topping are inversely
	# related in this pool, so every steady player sits low on carry_rate. At
	# the old floor of 22 the engine branch was unreachable — see _pick_role.
	"engine_carry": 13,
	"engine_sd": 5.6,       # pool p25 — below this is genuinely steady output
	"wildcard_sd": 6.3,     # pool p75 — above this is genuinely swingy
	"support_carry": 12,
	"style_min": 1.5,       # weakest scaled deviation that still names a style
	"style_margin": 2.0,    # ...and it must beat the runner-up style by this much
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


def _comp(stats, key):
	# Explicit None check: a legitimate 0 score must stay 0, not turn neutral.
	v = _num(stats.get(key))
	return 50.0 if v is None else v


def _style_scores(stats, tag_rates):
	army = _comp(stats, "avg_army")
	eco = _comp(stats, "avg_eco")
	timing = _comp(stats, "avg_timing")
	reboom = _comp(stats, "avg_recovery")
	# Scale factors equalize each component's spread across the player pool
	# (army varies least per player, reboom the most).
	return {
		"aggressor": (army - 50) * 2.2 + _tag_rate(tag_rates, "pressure") * 0.20 + _tag_rate(tag_rates, "all_in") * 0.25,
		"boomer": (eco - 50) * 1.3 + _tag_rate(tag_rates, "boom") * 0.15 + _tag_rate(tag_rates, "eco") * 0.15,
		"tempo": (timing - 50) * 1.1 + _tag_rate(tag_rates, "tempo") * 0.12,
		"phoenix": (reboom - 50) * 0.75 + _tag_rate(tag_rates, "reboom") * 0.25,
	}


def _pick_style(stats, tag_rates):
	"""Winning style, but only when the win is decisive.

	A bare max() will always name something, so two axes a tenth of a point
	apart used to produce a confident archetype. The style has to clear
	style_min *and* beat the runner-up by style_margin; otherwise the player
	genuinely has no dominant lane and falls through to slowcooker/flex.
	"""
	scores = _style_scores(stats, tag_rates)
	ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
	style, top = ranked[0]
	margin = top - ranked[1][1] if len(ranked) > 1 else None
	if top >= TH["style_min"] and (margin is None or margin >= TH["style_margin"]):
		return style, scores
	timing = _num(stats.get("avg_timing"))
	if timing is not None and (50 - timing) >= TH["slow_timing_dev"]:
		return "slowcooker", scores
	return "flex", scores


def _pick_role(carry_rate, impact_sd):
	# "engine" (steady output, more board presence than a pure support) is the
	# rare one, and it used to be impossible. carry_rate and impact_sd are
	# inversely related in the sample: the steadiest players (sd 4.2-5.5) all
	# sit at 0-16% carry, while everyone above the old engine floor of 22%
	# bottoms out at sd 5.61. The floor now sits just above support_carry,
	# which is where steady players actually live — it selects 1 of 33, and
	# leaves the sub-13% steady players in "support" where they belong.
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
