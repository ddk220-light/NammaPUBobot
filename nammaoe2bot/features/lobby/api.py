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

from nammaoe2bot.runtime.console import log

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


async def fetch_profile(profile_id):
	"""GET /profiles/{profile_id} -> ``(status, data)``, never raises.

	``status`` is one of:
	  "ok"          -- data is ``{"profile_id": int, "name": str}`` (name may be
	                   "" when the API omits it)
	  "not_found"   -- the id does not exist; data is None
	  "unavailable" -- anything else (non-200/404 status, timeout, network
	                   error, unreadable body); data is None

	The three-way result exists for `/link` (nammaoe2bot/features/identity/commands.py): "your id
	is wrong" and "the AoE2 service is down" must never be conflated, because
	only the first is the player's problem to fix. A bare None return could not
	tell them apart. Lazy aiohttp import, same as fetch_match_by_id.

	This function is I/O ONLY -- every response-to-outcome decision lives in the
	pure _classify() below, so the mapping that decides what a player is told is
	unit-testable without a network."""
	import aiohttp

	url = f"{AOE2_API}/profiles/{profile_id}"
	try:
		async with aiohttp.ClientSession(headers=_UA) as session:
			async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
				status = resp.status
				# Only a 200 body is ever decoded: "not_found" must rest on the
				# 404 STATUS alone, never on whether that body happens to parse.
				payload = await resp.json() if status == 200 else None
	except (aiohttp.ClientError, TimeoutError, ValueError) as e:
		# Every network failure lands here and MUST report "unavailable". An "ok"
		# here would write a binding nothing validated -- the single thing this
		# whole validation path exists to prevent.
		log.warning(f"fetch_profile({profile_id}) failed: {e}")
		return "unavailable", None

	result = _classify(status, payload)
	if result[0] == "unavailable":
		log.warning(f"fetch_profile({profile_id}) got an unusable HTTP {status} response")
	return result


async def fetch_profile_team_rating(profile_id):
	"""Fetch one profile's current ranked-team rating for onboarding.

	Returns ``(status, data)`` where status is ``ok``, ``unrated``,
	``not_found`` or ``unavailable``. This is intentionally separate from
	``fetch_profile``: identity validation needs only an observed id/name,
	while rating onboarding must fail closed when the ladder payload is not
	usable.
	"""
	import aiohttp

	url = f"{AOE2_API}/profiles/{profile_id}"
	try:
		async with aiohttp.ClientSession(headers=_UA) as session:
			async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
				status = resp.status
				payload = await resp.json() if status == 200 else None
	except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
		log.warning(f"fetch_profile_team_rating({profile_id}) failed: {exc}")
		return "unavailable", None

	result = _classify_team_rating(status, payload)
	if result[0] == "unavailable":
		log.warning(
			f"fetch_profile_team_rating({profile_id}) got an unusable HTTP {status} response")
	return result


# ── pure parsers (no I/O) ────────────────────────────────────────────────

def _classify(status, payload):
	"""(HTTP status, decoded 200 body) -> the ``(status, data)`` fetch_profile
	returns. Pure: no I/O, no logging.

	404 is the ONLY signal that means "no such profile". Anything else that is
	not a readable 200 -- a 500, a 400, an unexpected body -- proves nothing
	about the id, so it reports "unavailable" rather than blaming a number that
	may be perfectly good and sending the player hunting for a new one."""
	if status == 404:
		return "not_found", None
	if status != 200:
		return "unavailable", None
	if (parsed := _parse_profile(payload)) is None:
		return "unavailable", None
	return "ok", parsed


def _parse_profile(payload):
	"""The /profiles/{id} 200 body -> ``{"profile_id": int, "name": str}``, or
	None for anything that is not a profile.

	The 404 body echoes the requested id back (``{"success": false, "error":
	"profile couldn't be found", "profileId": 999999999}``), so the presence of
	``profileId`` proves nothing -- the explicit failure markers are checked
	first. ``name`` is the real in-game name; it is display-only (identities
	.aoe2_name) and never an input to matching."""
	if not isinstance(payload, dict):
		return None
	if payload.get("success") is False or payload.get("error"):
		return None
	try:
		profile_id = int(payload["profileId"])
	except (KeyError, TypeError, ValueError):
		return None
	return {"profile_id": profile_id, "name": str(payload.get("name") or "")}


def _parse_team_rating(payload):
	"""A profile payload -> its ranked-team rating observation, or None.

	A valid profile with no ``rm_team`` entry is represented by ``rating=None``
	and classified as ``unrated`` by the caller. Malformed ladder entries are
	unusable rather than guessed or coerced to zero.
	"""
	profile = _parse_profile(payload)
	if profile is None:
		return None
	leaderboards = payload.get("leaderboards")
	if not isinstance(leaderboards, list):
		return None
	for row in leaderboards:
		if not isinstance(row, dict) or row.get("leaderboardId") != "rm_team":
			continue
		try:
			rating = int(row["rating"])
		except (KeyError, TypeError, ValueError):
			return None
		if not 1 <= rating <= 9_999:
			return None
		return {**profile, "rating": rating, "leaderboard": "rm_team"}
	return {**profile, "rating": None, "leaderboard": "rm_team"}


def _classify_team_rating(status, payload):
	if status == 404:
		return "not_found", None
	if status != 200:
		return "unavailable", None
	parsed = _parse_team_rating(payload)
	if parsed is None:
		return "unavailable", None
	return ("ok" if parsed["rating"] is not None else "unrated"), parsed


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
