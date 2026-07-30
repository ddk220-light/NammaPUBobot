# -*- coding: utf-8 -*-
"""Scoring model for the Match Cards embed only.

A deliberate fork of scoring.py. That module is consumed by the stored
rs_player_game_tags table, the web profile API and the Tale of the Tape embed,
so changing it in place would strand historical tag rows and shift surfaces this
revamp does not own. scoring.py is therefore left byte-identical and this module
is imported only by the Match Cards path.

Differences from scoring.py:
  * No timing component. feudal_s / castle_s / imperial_s feed nothing.
  * Impact is army/eco only, renormalised from scoring.py's 0.45 / 0.32.
  * Missing values are excluded and the remaining weights renormalised, instead
    of being scored as match-average (a None must not read as "exactly average").
  * Age-derived terms are dropped when the parser flagged ages unreliable, and
    the pre-Imperial term is dropped for players who never clicked Imperial —
    extract.py counts the whole game in that case, which would score a
    Castle-Age turtle as though their entire army were early.

ECO_MIX and ARMY_MIX are intentionally identical to scoring.py's, so the
percentile anchors calibrated against those distributions in July 2026 still
hold and TH is inherited verbatim rather than re-derived.

Pure: no DB, no core imports, no relative imports — so it stays unit-testable
under the CI import shim and loadable standalone by path.
"""

ECO_MIX = (("villagers", 0.55), ("vil_pre_castle", 0.45))
ARMY_MIX = (("military", 0.55), ("mil_pre_imperial", 0.25), ("mil_pre_castle", 0.20))

# Renormalised from scoring.py's (army 0.45, eco 0.32) with timing removed.
IMPACT_WEIGHTS = (("army", 0.58), ("eco", 0.42))

# Every rs_player_games column this module reads. Callers that SELECT explicit
# column lists must include all of these (tests/test_post_game.py enforces it).
REQUIRED_COLUMNS = tuple(dict.fromkeys(
	[k for k, _ in ECO_MIX] + [k for k, _ in ARMY_MIX]
	+ ["age_reliable", "imperial_s", "eapm"]))

# Inherited verbatim from scoring.py. ECO_MIX and ARMY_MIX are unchanged, so the
# distributions these percentile anchors describe are unchanged too.
TH = {
	"all_in_army": 64,
	"all_in_eco_max": 48,
	"reboom_score": 70,
	"reboom_early_eco_max": 46,
	"reboom_eco_min": 55,
	# New, and the only threshold here that was chosen rather than calibrated:
	# share of two-minute buckets containing at least one production click.
	"production_coverage": 0.75,
}

# Terms that only carry information when the parser's age clicks are trustworthy.
_AGE_DEPENDENT = frozenset((
	"vil_pre_feudal", "vil_pre_castle", "vil_pre_imperial",
	"mil_pre_feudal", "mil_pre_castle", "mil_pre_imperial"))


def _usable(row, key):
	"""Whether ``key`` on ``row`` carries real information for this player."""
	if row.get(key) is None:
		return False
	if key in _AGE_DEPENDENT and not row.get("age_reliable"):
		return False
	# extract.py documents that "before age X when never reached X counts the
	# whole game", so for these players the term is their whole army, not their
	# early army, and including it inflates them against teammates who did age up.
	if key == "mil_pre_imperial" and row.get("imperial_s") is None:
		return False
	if key == "vil_pre_imperial" and row.get("imperial_s") is None:
		return False
	return True


def _avg(rows, key):
	vals = [float(r[key]) for r in rows if _usable(r, key)]
	return sum(vals) / len(vals) if vals else None


def _std(rows, key):
	vals = [float(r[key]) for r in rows if _usable(r, key)]
	if len(vals) < 2:
		return 1.0
	mean = sum(vals) / len(vals)
	return max((sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5, 1.0)


def _z(row, rows, key):
	"""Clamped z-score, or None when the value cannot be scored honestly."""
	if not _usable(row, key):
		return None
	mean = _avg(rows, key)
	if mean is None:
		return None
	return max(-2.0, min(2.0, (float(row[key]) - mean) / _std(rows, key)))


def _mix(row, group, mix):
	"""Weighted mean over the z-scores that are actually available.

	An absent term contributes nothing and its weight is redistributed across
	the rest, so a partially parsed replay cannot yield a confident mid score
	built out of missing data.
	"""
	total = 0.0
	weight = 0.0
	for key, w in mix:
		z = _z(row, group, key)
		if z is None:
			continue
		total += w * z
		weight += w
	return total / weight if weight else 0.0


def _score_component(value):
	return max(0, min(100, round(50 + value * 15)))


def component_scores(row, group):
	"""0-100 component + impact scores for one rs_player_games row, relative to
	``group`` (every player in the same match).

	Scores are internal only — they drive sort order, the carry crown and tag
	thresholds, and are never displayed.
	"""
	eco_z = _mix(row, group, ECO_MIX)
	army_z = _mix(row, group, ARMY_MIX)
	early_eco_z = _z(row, group, "vil_pre_castle") or 0.0
	early_army_z = _z(row, group, "mil_pre_castle") or 0.0
	vil_z = _z(row, group, "villagers") or 0.0
	reboom_z = max(-2.0, min(2.0, vil_z - early_eco_z))
	scores = {
		"eco": _score_component(eco_z),
		"army": _score_component(army_z),
		"early_eco": _score_component(early_eco_z),
		"early_army": _score_component(early_army_z),
		"reboom": _score_component(reboom_z),
	}
	scores["impact"] = round(sum(scores[k] * w for k, w in IMPACT_WEIGHTS))
	return scores


def carry_sort_key(payload):
	"""Deterministic ordering inside a team: highest impact first, army then eco
	break ties, nick keeps it stable when everything ties."""
	return (
		-(payload.get("impact_score") or 0),
		-(payload.get("army_score") or 0),
		-(payload.get("eco_score") or 0),
		str(payload.get("nick") or ""),
	)


# ── Medals ───────────────────────────────────────────────────────────────
# Medals rank on raw counts, deliberately distinct from the component scores.
# The medal answers "who made the most", which is a plain fact; the component
# score answers "who contributed most overall".
MEDAL_AXES = (("military_medal", "military", "villagers"),
              ("villager_medal", "villagers", "military"))

PRODUCTION_BUCKET_S = 120

TAG_NAMES = {
	"all_in_pressure": "Low-eco pressure",
	"reboom": "Recovery",
	"constant_production": "Constant production",
}


def assign_medals(payloads):
	"""Top-three medals per axis, ranked across every player in the match.

	Returns a list parallel to ``payloads``, each entry
	``{"military_medal": 1|2|3|None, "villager_medal": 1|2|3|None}``.

	Players with no production data are excluded entirely rather than ranked
	last — showing them bare would read as "played badly" when the truth is
	"not measured".
	"""
	out = [{"military_medal": None, "villager_medal": None} for _ in payloads]
	for field, primary, secondary in MEDAL_AXES:
		ranked = sorted(
			(i for i, p in enumerate(payloads) if p.get("has_production")),
			key=lambda i: (
				-(payloads[i].get(primary) or 0),
				-(payloads[i].get(secondary) or 0),
				str(payloads[i].get("nick") or ""),
			))
		for place, i in enumerate(ranked[:3], start=1):
			out[i][field] = place
	return out


def production_coverage(click_times_s, match_end_s):
	"""Share of two-minute buckets between the first click and match end that
	contain at least one production click. None when there is no timeline.

	Bucketing rather than gap-measuring because production clicks are batched —
	one click can queue five villagers, covering roughly two minutes. A
	gap-based metric would read efficient batch-queuing as idleness, and
	correcting for that needs per-unit train times the parser does not store.
	"""
	times = sorted(t for t in (click_times_s or []) if t is not None)
	if not times or not match_end_s or match_end_s <= times[0]:
		return None
	first = times[0]
	span = match_end_s - first
	total = int(span // PRODUCTION_BUCKET_S)
	if span % PRODUCTION_BUCKET_S:
		total += 1
	total = max(1, total)
	hit = {int((t - first) // PRODUCTION_BUCKET_S) for t in times if t <= match_end_s}
	return min(1.0, len(hit) / total)


def _tag_candidates(p):
	"""(tag key, ranking score) for every tag this player clears the bar for."""
	found = []
	if (p.get("army_score") or 0) >= TH["all_in_army"] \
			and (p.get("eco_score") or 0) <= TH["all_in_eco_max"]:
		found.append(("all_in_pressure", p.get("army_score") or 0))
	if (p.get("reboom_score") or 0) >= TH["reboom_score"] \
			and (p.get("early_eco_score") or 0) <= TH["reboom_early_eco_max"] \
			and (p.get("eco_score") or 0) >= TH["reboom_eco_min"]:
		found.append(("reboom", p.get("reboom_score") or 0))
	cov = p.get("production_coverage")
	if cov is not None and cov >= TH["production_coverage"]:
		found.append(("constant_production", cov))
	return found


def assign_team_tags(payloads):
	"""Award each tag to the single highest-scoring player on the team who clears
	its absolute threshold; nobody gets it if nobody on that team clears.

	Returns a list parallel to ``payloads`` of display-name lists.

	Tags describe game *shape*, which medals cannot express. Note the deliberate
	scope mix: medals are match-wide, tags are team-scoped — the tag answers
	"what was the shape of this player's game relative to their team".
	"""
	out = [[] for _ in payloads]
	by_team = {}
	for i, p in enumerate(payloads):
		if not p.get("has_production"):
			continue
		by_team.setdefault(p.get("team"), []).append(i)
	for members in by_team.values():
		best = {}
		for i in members:
			for key, score in _tag_candidates(payloads[i]):
				held = best.get(key)
				if held is None or score > held[1] or (
						score == held[1]
						and str(payloads[i].get("nick") or "")
						< str(payloads[held[0]].get("nick") or "")):
					best[key] = (i, score)
		for key, (i, _score) in best.items():
			out[i].append(TAG_NAMES[key])
	for names in out:
		names.sort()
	return out
