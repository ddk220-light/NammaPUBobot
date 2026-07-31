# -*- coding: utf-8 -*-
"""Derived-global game_stats: per-player facts computed once at ingest instead
of re-derived every time a card renders. compute_game_stats is pure -- no DB,
no I/O -- so the exact same function drives both the live write below (called
from bot/replay_stats/store.py right after a match parses) and the stage-3.4
reconciliation loop that backfills the 1126 already-ingested historical
matches. Neither caller teaches the function anything about where its input
rows came from, which is what lets one implementation serve both."""
import json

from core.database import db


def compute_game_stats(players, units, apm, computed_at):
	"""Per-player derived facts for one match. Pure: no DB, no I/O.

	`players` are rs_player_games-shaped dicts, `units` rs_player_units-shaped,
	`apm` rs_player_apm-shaped. Returns one row per player, keyed by
	player_number, with NO replay_match_id -- the caller stamps that, so the
	same function serves the live ingest and the backfill without either one
	teaching it where the id comes from.
	"""
	from bot.replay_stats import card_scoring

	payloads = [dict(
		player_number=p.get("player_number"),
		military=p.get("military"),
		villagers=p.get("villagers"),
		has_production=bool((p.get("villagers") or 0) + (p.get("military") or 0)),
	) for p in players]
	medals = card_scoring.assign_medals(payloads)

	# Peak eAPM is a plain MAX over the per-minute buckets -- genuinely "eAPM"
	# because apm_buckets already applies mgz's effective-APM filter before
	# storing a bucket. Absent entirely for historical rows: the bucket
	# feature shipped after the last pre-backfill match was played, so the
	# backfill's peak_eapm is legitimately None, not a bug.
	peak = {}
	for a in apm:
		pn, n = a.get("player_number"), a.get("actions") or 0
		if pn is not None and n > peak.get(pn, -1):
			peak[pn] = n

	# top_units is military-only: unfiltered, "top 3 by total" is Villager
	# plus two others for every player alive, which says nothing.
	tops = {}
	for u in units:
		if not u.get("is_military"):
			continue
		tops.setdefault(u.get("player_number"), []).append(u)
	for lst in tops.values():
		lst.sort(key=lambda u: (-(u.get("total") or 0), str(u.get("unit") or "")))

	rows = []
	for i, p in enumerate(players):
		pn = p.get("player_number")
		rows.append(dict(
			player_number=pn,
			profile_id=p.get("profile_id"),
			civ=p.get("civ"),
			team=p.get("team"),
			winner=p.get("winner"),
			# rs_player_games.eapm passed through unchanged -- never a mean of
			# the buckets above. Bucket rows are absent for zero-action
			# minutes, so averaging only the buckets that exist overstates;
			# bot/replay_stats/apm_query.py computes a deliberately different
			# `mean_active` for charts and documents that the two must not be
			# conflated.
			avg_eapm=p.get("eapm"),
			peak_eapm=peak.get(pn),
			military_medal=medals[i]["military_medal"],
			villager_medal=medals[i]["villager_medal"],
			top_units=[dict(unit=u.get("unit"), category=u.get("category"),
			                total=u.get("total")) for u in tops.get(pn, [])[:3]],
			computed_at=computed_at,
		))
	return rows


async def write(replay_match_id, rows):
	"""Idempotent per-match write: DELETE this match's rows, then insert what
	compute_game_stats returned, stamping replay_match_id onto each row (the
	pure function above deliberately never sees it -- see its docstring).

	Mirrors bot/replay_stats/store.py's write_match delete-then-insert for the
	same reason: a match can be re-ingested (parser bump, manual retry, or the
	stage-3.4 backfill correcting a stale row) and the stored set must exactly
	match the latest compute, never accumulate leftovers from a run with a
	different player count.
	"""
	await db.execute("DELETE FROM game_stats WHERE replay_match_id=%s", [replay_match_id])
	if not rows:
		return
	payload = []
	for r in rows:
		row = dict(r)
		row["replay_match_id"] = replay_match_id
		row["top_units"] = json.dumps(row.get("top_units") or [], sort_keys=True)
		payload.append(row)
	await db.insert_many("game_stats", payload, on_duplicate="replace")
