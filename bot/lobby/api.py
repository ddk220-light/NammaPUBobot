# -*- coding: utf-8 -*-
"""REST client + pure parsers for the aoe2companion finished-match API.

``GET https://data.aoe2companion.com/api/matches/{gameId}`` — PATH form (the
``?match_ids=`` query form returns HTTP 422). A non-empty User-Agent is mandatory
(403 without). The async fetch is isolated and lazy-imports aiohttp; the parsers
below are pure (no I/O) so they unit-test without the runtime dep.

Verified live (Phase 3 understand workflow):
  - started/finished are ISO-8601 strings (e.g. "2026-06-09T23:28:58.000Z").
    There is NO duration field — compute it.
  - The winner is per-player ``won`` (bool), consistent within a team; there is
    no team-level winner. Winning team = the team whose players are all won==True.
  - Per-team ``teamId`` is 1|2; players live under ``teams[].players[]`` with
    ``profileId`` + ``civName``.
"""
from datetime import datetime

from core.console import log

AOE2_API = "https://data.aoe2companion.com/api"
_UA = {"User-Agent": "NammaPUBobot/1.0"}
MIN_DURATION_SECONDS = 15 * 60


async def fetch_match_by_id(game_id):
	"""GET /matches/{game_id} (path form). Returns the match dict on 200, else
	None (404 lag / 4xx / network) — never raises. Lazy aiohttp import."""
	import aiohttp

	url = f"{AOE2_API}/matches/{game_id}"
	try:
		async with aiohttp.ClientSession(headers=_UA) as session:
			async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
				if resp.status != 200:
					return None
				return await resp.json()
	except (aiohttp.ClientError, TimeoutError, ValueError) as e:
		log.warning(f"fetch_match_by_id({game_id}) failed: {e}")
		return None


# ── pure parsers (no I/O) ────────────────────────────────────────────────

def parse_iso(s):
	"""ISO-8601 (optionally trailing 'Z') -> unix seconds (int), or None."""
	try:
		return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
	except (ValueError, AttributeError, TypeError):
		return None


def is_finished(match):
	"""True once the API reports a finished timestamp."""
	return bool((match or {}).get("finished"))


def match_duration_seconds(match):
	"""finished - started, in seconds; None if either is missing/unparseable."""
	if not isinstance(match, dict):
		return None
	start = parse_iso(match.get("started"))
	end = parse_iso(match.get("finished"))
	if start is None or end is None:
		return None
	return end - start


def _teams(match):
	return (match or {}).get("teams") or []


def winning_teamid(match):
	"""teamId of the team whose players ALL have won==True. None if ambiguous
	(mixed / all-None / empty / more than one qualifying team — e.g. a draw)."""
	winners = []
	for t in _teams(match):
		players = t.get("players") or []
		if players and {p.get("won") for p in players} == {True}:
			winners.append(t.get("teamId"))
	return winners[0] if len(winners) == 1 else None


def players_by_team(match):
	"""{teamId: [profileId, ...]} for occupied player slots."""
	out = {}
	for t in _teams(match):
		out[t.get("teamId")] = [
			p.get("profileId") for p in (t.get("players") or []) if p.get("profileId")
		]
	return out


def pid_civ_map(match):
	"""{profileId: civName} across all teams."""
	out = {}
	for t in _teams(match):
		for p in (t.get("players") or []):
			if p.get("profileId") and p.get("civName"):
				out[p["profileId"]] = p["civName"]
	return out
