# -*- coding: utf-8 -*-
"""Persisted player personas — computed once per match, read everywhere.

Personas used to be derived on every web request from the full replay history.
This module materializes them into ``rs_player_personas`` keyed
``(user_id, period)``: after each ingested match the personas of everyone in
that match are recomputed for every period window, so the stored row always
reflects the latest game. Readers (web API today, Discord embeds tomorrow) get
a cheap primary-key lookup; the live computation remains only as a fallback
for rows that have not been materialized yet.
"""
import json
import time
from collections import defaultdict

from core.database import db

try:
	from . import persona, scoring
except ImportError:
	# Loaded standalone by file path (utils/backfill_personas.py) — resolve the
	# sibling modules from this directory, same pattern as player_tags.py.
	import importlib.util as _ilu
	import os as _os
	_here = _os.path.dirname(_os.path.abspath(__file__))

	def _load(_name):
		_spec = _ilu.spec_from_file_location("rs_{}_standalone".format(_name), _os.path.join(_here, _name + ".py"))
		_mod = _ilu.module_from_spec(_spec)
		_spec.loader.exec_module(_mod)
		return _mod
	scoring = _load("scoring")
	persona = _load("persona")

# Mirrors bot/web.py MATCH_STAT_PERIODS — every period the profile UI offers.
PERIODS = {
	"all": None,
	"year": 365,
	"month6": 183,
	"month3": 92,
	"month": 30,
	"week": 7,
}


def _period_starts(now=None):
	now = now or int(time.time())
	return {p: (None if days is None else now - days * 86400) for p, days in PERIODS.items()}


def aggregate_player_stats(match_groups, user_id, period_start):
	"""Pure aggregation of one player's rows into derive_persona() input.

	``match_groups`` is ``[(played_at, [rs_player_games rows of one match])]``.
	Returns None when the player has no rows in the window.
	"""
	uid = str(user_id)
	n = 0
	sums = {"army": 0.0, "eco": 0.0, "timing": 0.0, "reboom": 0.0}
	impacts = []
	carry = 0
	tag_counts = defaultdict(int)
	for played_at, group in match_groups:
		if period_start is not None and (played_at or 0) < period_start:
			continue
		mine = [r for r in group if str(r.get("user_id") or "") == uid]
		if not mine:
			continue
		scored = [(r, scoring.impact_scores(r, group)) for r in group]
		by_team = defaultdict(list)
		for r, s in scored:
			if r.get("team") is not None:
				by_team[str(r["team"])].append((r, s))
		tops = set()
		for members in by_team.values():
			top = min(members, key=lambda m: scoring.carry_sort_key({
				"impact_score": m[1]["impact"], "army_score": m[1]["army"],
				"eco_score": m[1]["eco"], "nick": m[0].get("identity") or "",
			}))
			tops.add(id(top[0]))
		for r, s in scored:
			if str(r.get("user_id") or "") != uid:
				continue
			n += 1
			for k in sums:
				sums[k] += s[k]
			impacts.append(s["impact"])
			if id(r) in tops:
				carry += 1
			for name in scoring.impact_tag_names_with_fallback(s, r):
				tag_counts[name] += 1
	if not n:
		return None
	mean = sum(impacts) / n
	sd = round((sum((x - mean) ** 2 for x in impacts) / n) ** 0.5, 1) if n >= 2 else None
	return {
		"matches": n,
		"avg_army": sums["army"] / n,
		"avg_eco": sums["eco"] / n,
		"avg_timing": sums["timing"] / n,
		"avg_recovery": sums["reboom"] / n,
		"impact_sd": sd,
		"carry_rate": round(100.0 * carry / n),
		"tag_rates": {k: 100.0 * v / n for k, v in tag_counts.items()},
	}


async def _match_groups_for_user(user_id):
	rows = await db.fetchall(
		"SELECT g.*, m.at AS played_at "
		"FROM rs_player_games g "
		"JOIN rs_matches rm ON rm.aoe2_match_id=g.aoe2_match_id "
		"LEFT JOIN qc_matches m ON m.match_id=rm.bot_match_id "
		"WHERE g.aoe2_match_id IN ("
		"SELECT aoe2_match_id FROM rs_player_games WHERE user_id=%s)",
		[user_id])
	by_match = defaultdict(list)
	for r in rows or []:
		by_match[r["aoe2_match_id"]].append(r)
	return [(group[0].get("played_at"), group) for group in by_match.values()]


async def refresh_user(user_id, now=None):
	"""Recompute + upsert this player's persona for every period window."""
	groups = await _match_groups_for_user(user_id)
	now = now or int(time.time())
	rows = []
	for period, start in _period_starts(now).items():
		stats = aggregate_player_stats(groups, user_id, start)
		p = persona.derive_persona(stats or {"matches": 0})
		rows.append({
			"user_id": int(user_id),
			"period": period,
			"persona_key": p["key"],
			"style": p["style"],
			"role": p["role"],
			"name": p["name"],
			"epithet": p["epithet"],
			"tagline": p["tagline"],
			"evidence_json": json.dumps(p["evidence"], sort_keys=True),
			"carry_rate": (stats or {}).get("carry_rate"),
			"impact_sd": (stats or {}).get("impact_sd"),
			"matches": (stats or {}).get("matches") or 0,
			"computed_at": now,
		})
	await db.insert_many("rs_player_personas", rows, on_dublicate="replace")
	return len(rows)


async def refresh_match_users(aoe2_match_id):
	"""Post-ingest hook: refresh everyone who played this match."""
	rows = await db.fetchall(
		"SELECT DISTINCT user_id FROM rs_player_games "
		"WHERE aoe2_match_id=%s AND user_id IS NOT NULL",
		[aoe2_match_id])
	for r in rows or []:
		await refresh_user(r["user_id"])
	return len(rows or [])


async def get_persona(user_id, period):
	"""Stored persona payload for the API, or None if not materialized."""
	if period not in PERIODS:
		return None
	row = await db.fetchone(
		"SELECT * FROM rs_player_personas WHERE user_id=%s AND period=%s",
		[user_id, period])
	if not row:
		return None
	try:
		evidence = json.loads(row.get("evidence_json") or "[]")
	except (TypeError, ValueError):
		evidence = []
	return {
		"key": row.get("persona_key"),
		"name": row.get("name"),
		"epithet": row.get("epithet"),
		"tagline": row.get("tagline"),
		"style": row.get("style"),
		"role": row.get("role"),
		"evidence": evidence,
		"stored": True,
		"computed_at": row.get("computed_at"),
	}
