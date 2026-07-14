# -*- coding: utf-8 -*-
"""Single source of truth for replay impact scores and impact-tag derivation.

Consumed by bot/replay_stats/player_tags.py (stored rs_player_game_tags rows),
bot/post_game.py (Match Cards / Tale of the Tape embeds), and bot/web.py
(player profile + match stats API). Pure functions, no DB and no core imports,
so tests and offline calibration tooling can import this file standalone.

Scores are z-scores relative to the players in the same match, mapped onto a
0-100 scale where 50 = match average and each 15 points = one standard
deviation (see _score_component).

Weights and thresholds were calibrated offline against the full live history
(1061 parsed matches / 8371 player-games, July 2026) via utils/tag_calibration.py.
The headline fix over the previous formula: the recovery ("reboom") component
no longer feeds the impact score. It used to enter unclamped at 0.18 weight
while also double-counting final villagers, which made early-game economy
contribute *negatively* to impact overall — so a player who idled early and
reboomed later frequently outscored the teammate who actually carried the
early game (36.6%% of team carries were reboom-driven; 17.8%% of "Eco carry"
tags went to players with below-match-average early eco). Reboom is now a
tag-only signal gated on a genuinely weak early eco, and the carry tags
require an early-eco floor.
"""

# Component mixes (weights over per-match z-scores).
ECO_MIX = (("villagers", 0.55), ("vil_pre_castle", 0.45))
ARMY_MIX = (("military", 0.65), ("mil_pre_castle", 0.35))
TIMING_MIX = (("feudal_s", 0.35), ("castle_s", 0.45), ("imperial_s", 0.20))  # inverted: earlier = better

# Impact = weighted mix of the three component scores. Reboom intentionally absent.
IMPACT_WEIGHTS = (("army", 0.45), ("eco", 0.35), ("timing", 0.20))

# Tag thresholds (0-100 component scale). Percentile anchors from calibration:
# army/eco p90=61 p95=66; timing p90=62 p95=64; impact p90=57 p95=60;
# early_eco p85=62; reboom p85=66 p90=72.
TH = {
	"all_in_army": 64,          # army needed for All-in pressure...
	"all_in_eco_max": 48,       # ...with economy clearly sacrificed
	"map_pressure_army": 64,
	"boom_carry_eco": 62,
	"boom_carry_early_eco": 60,     # genuinely boom-first: strong eco *before* castle
	"boom_carry_early_army_max": 52,
	"eco_carry_eco": 64,
	"eco_carry_early_eco_min": 50,  # floor keeps pure reboomers out of carry tags
	"age_up_timing": 63,
	"reboom_score": 70,
	"reboom_early_eco_max": 46,     # reboom means the early eco was actually weak...
	"reboom_eco_min": 55,           # ...and the recovery actually landed
	"high_impact": 58,
}

# Canonical tag keys -> per-surface display names. "stored" is what
# rs_player_game_tags persists (player_tags.py), "payload" is what the
# post-game embeds and the web API historically emit.
TAG_NAMES = {
	"all_in_pressure": {"stored": "All-in pressure", "payload": "Low-eco pressure"},
	"map_pressure": {"stored": "Map pressure", "payload": "Army pressure"},
	"boom_carry": {"stored": "Boom carry", "payload": "Boom carry"},
	"eco_carry": {"stored": "Eco carry", "payload": "Eco carry"},
	"age_up_tempo": {"stored": "Age-up tempo", "payload": "Timing edge"},
	"reboom": {"stored": "Reboom", "payload": "Recovery"},
	"high_impact": {"stored": "High impact", "payload": "High impact"},
}


def _avg(rows, key):
	vals = [float(r[key]) for r in rows if r.get(key) is not None]
	return sum(vals) / len(vals) if vals else None


def _std(rows, key):
	vals = [float(r[key]) for r in rows if r.get(key) is not None]
	if len(vals) < 2:
		return 1.0
	mean = sum(vals) / len(vals)
	return max((sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5, 1.0)


def _z(row, rows, key, invert=False):
	if row.get(key) is None:
		return 0.0
	mean = _avg(rows, key)
	if mean is None:
		return 0.0
	val = float(row[key])
	score = (mean - val if invert else val - mean) / _std(rows, key)
	return max(-2.0, min(2.0, score))


def _score_component(value):
	return max(0, min(100, round(50 + value * 15)))


def impact_scores(row, group):
	"""0-100 component + impact scores for one rs_player_games row, relative to
	``group`` (all players in the same match)."""
	eco_z = sum(w * _z(row, group, k) for k, w in ECO_MIX)
	army_z = sum(w * _z(row, group, k) for k, w in ARMY_MIX)
	timing_z = sum(w * _z(row, group, k, invert=True) for k, w in TIMING_MIX)
	early_eco_z = _z(row, group, "vil_pre_castle")
	early_army_z = _z(row, group, "mil_pre_castle")
	# Clamped like every other z — previously this difference ranged +-4 and
	# swung the score across the whole 0-100 scale.
	reboom_z = max(-2.0, min(2.0, _z(row, group, "villagers") - early_eco_z))
	scores = {
		"eco": _score_component(eco_z),
		"army": _score_component(army_z),
		"timing": _score_component(timing_z),
		"early_eco": _score_component(early_eco_z),
		"early_army": _score_component(early_army_z),
		"reboom": _score_component(reboom_z),
	}
	scores["impact"] = round(sum(scores[k] * w for k, w in IMPACT_WEIGHTS))
	return scores


def derive_impact_tags(scores):
	"""Canonical impact tags for one player's component scores.

	Returns ``[{"key", "score"}, ...]`` — specific style tags first, the
	generic "high_impact" last so surfaces that cap displayed tags keep the
	most descriptive ones.
	"""
	s = scores
	tags = []
	if s["army"] >= TH["all_in_army"] and s["eco"] <= TH["all_in_eco_max"]:
		tags.append({"key": "all_in_pressure", "score": s["army"]})
	elif s["army"] >= TH["map_pressure_army"]:
		tags.append({"key": "map_pressure", "score": s["army"]})
	if (s["eco"] >= TH["boom_carry_eco"] and s["early_eco"] >= TH["boom_carry_early_eco"]
			and s["early_army"] <= TH["boom_carry_early_army_max"]):
		tags.append({"key": "boom_carry", "score": s["eco"]})
	elif s["eco"] >= TH["eco_carry_eco"] and s["early_eco"] >= TH["eco_carry_early_eco_min"]:
		tags.append({"key": "eco_carry", "score": s["eco"]})
	if s["timing"] >= TH["age_up_timing"]:
		tags.append({"key": "age_up_tempo", "score": s["timing"]})
	if (s["reboom"] >= TH["reboom_score"] and s["early_eco"] <= TH["reboom_early_eco_max"]
			and s["eco"] >= TH["reboom_eco_min"]):
		tags.append({"key": "reboom", "score": s["reboom"]})
	if s["impact"] >= TH["high_impact"]:
		tags.append({"key": "high_impact", "score": s["impact"]})
	return tags


def impact_tag_names(scores, style="payload"):
	"""Display names for the derived tags, in derivation order."""
	return [TAG_NAMES[t["key"]][style] for t in derive_impact_tags(scores)]


def carry_sort_key(payload):
	"""Deterministic 'carry' ordering for a team: highest impact first, army
	then eco break ties, nick keeps it stable when everything ties."""
	return (
		-(payload.get("impact_score") or 0),
		-(payload.get("army_score") or 0),
		-(payload.get("eco_score") or 0),
		str(payload.get("nick") or ""),
	)


def strength_glyphs(scores):
	"""Compact qualitative read of army/eco/timing vs the match average —
	no raw numbers (players shouldn't see internal component scores).
	One-std-above -> up arrow, one-std-below -> down arrow, else a dot."""
	def glyph(v):
		if v >= 61:
			return "▲"
		if v <= 39:
			return "▼"
		return "·"
	return "⚔{} 🌾{} ⏱{}".format(glyph(scores["army"]), glyph(scores["eco"]), glyph(scores["timing"]))
