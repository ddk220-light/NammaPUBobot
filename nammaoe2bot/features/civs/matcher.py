# -*- coding: utf-8 -*-
"""Record civs for a completed bot match by matching it to its aoe2companion game.

AOE2LobbyBOT doesn't reliably post result embeds in this server, so the live
civ_sync path captures nothing. Instead — the same way utils/civ_analysis.py
built all the historical civ data — we query the aoe2companion API for the
match participants' recent games, find the one that lines up by time + player
overlap, and store each player's civ in civ_picks (linked to the bot match).

Triggered from nammaoe2bot/pickup/stats.py when a match is reported/completed. Because
the API lags a few minutes behind a finished game, we retry on a short backoff
until it appears (or give up). Runs as a background task — the /report command
returns immediately.
"""
import asyncio
from datetime import datetime

import aiohttp

from nammaoe2bot.features.identity import resolver
from nammaoe2bot.runtime.console import log
from nammaoe2bot.runtime.database import db

AOE2_API = "https://data.aoe2companion.com/api"

# Time window: bot match `at` (report time) minus API game start time should be
# positive and within a few hours (game duration + report delay).
_MAX_DIFF_SECONDS = 3 * 3600
# Retry schedule (seconds) — the API usually has the game within a few minutes.
_RETRY_DELAYS = (60, 180, 420)

# Keep references so create_task'd coroutines aren't garbage-collected mid-run.
_pending = set()


async def _map_players_to_profiles(players):
	"""user_id -> (nick, team, [pids]) for players with a known AoE2 profile, via
	the identity resolver (nammaoe2bot/features/identity/resolver.py) — the single source of truth for
	profile_id<->user_id, seeded at boot from every legacy store. Returns the
	map plus the union of all mapped profile ids.

	No nick fallback: the old CSV-only nick-keyed lookup never resolved anyone
	the user_id path couldn't (every row in the hand-maintained profile-map CSV
	already carried a user_id), and both live callers (nammaoe2bot/pickup/stats.py's Discord
	member ids, nammaoe2bot/features/civs/reconcile.py's match_players.user_id) always supply a
	real user_id, never None.
	"""
	uid_to_pids = await resolver.profiles_for_users([user_id for user_id, _nick, _team in players])

	player_info = {}   # user_id -> (nick, team, [pids])
	active_pids = set()
	for user_id, nick, team in players:
		pids = uid_to_pids.get(user_id, [])
		if pids:
			player_info[user_id] = (nick, team, pids)
			active_pids.update(pids)
	return player_info, active_pids


def _iso_to_unix(s):
	try:
		return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
	except (ValueError, AttributeError, TypeError):
		return None


async def _fetch_recent(session, sem, pid, pool):
	url = f"{AOE2_API}/matches?profile_ids={pid}&count=20&page=1"
	async with sem:
		try:
			async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
				if resp.status != 200:
					return
				data = await resp.json()
		except (aiohttp.ClientError, TimeoutError, ValueError):
			return
	for m in data.get("matches", []):
		mid = m.get("matchId")
		if mid and mid not in pool:
			pool[mid] = m


async def _find_and_record(channel_id, bot_match_id, players, winner, match_at):
	"""Return True if civs were recorded (or already present), False to retry."""
	player_info, active_pids = await _map_players_to_profiles(players)
	if len(player_info) < 2:
		# The observable symptom of a degraded/empty identity resolver (see
		# nammaoe2bot/features/identity/resolver.py and nammaoe2bot/runtime/migrations.py's 003_seed_identities): if
		# `identities` is unexpectedly empty, every match lands here forever
		# with no exception and no retry, so civ stats silently stop
		# accruing. Log it so that failure mode is at least visible.
		log.info(
			f"Civ match: only {len(player_info)}/{len(players)} players for bot match "
			f"{bot_match_id} resolved to a known AoE2 profile (need >= 2); skipping."
		)
		return True  # not enough mapped players to ever match — don't keep retrying

	# Already recorded?
	if await db.fetchone("SELECT 1 AS x FROM civ_picks WHERE bot_match_id=%s LIMIT 1", [bot_match_id]):
		return True

	# Build a pool of the participants' recent API games.
	pool = {}
	async with aiohttp.ClientSession(headers={"User-Agent": "NammaPUBobot/1.0"}) as session:
		sem = asyncio.Semaphore(5)
		await asyncio.gather(*(_fetch_recent(session, sem, pid, pool) for pid in active_pids))
	if not pool:
		return False

	# Pick the API game with the most participant overlap inside the time window.
	best, best_overlap = None, 0
	for m in pool.values():
		api_unix = _iso_to_unix(m.get("started"))
		if api_unix is None:
			continue
		diff = match_at - api_unix
		if not (0 < diff < _MAX_DIFF_SECONDS):
			continue
		api_pids = set()
		for t in m.get("teams", []):
			for p in t.get("players", []):
				if p.get("profileId"):
					api_pids.add(p["profileId"])
		overlap = len(active_pids & api_pids)
		if overlap > best_overlap:
			best_overlap, best = overlap, m

	threshold = max(2, min(4, len(player_info)))
	if best is None or best_overlap < threshold:
		return False

	# Map profile_id -> civ for the chosen game, then build per-player rows.
	pid_civ = {}
	for t in best.get("teams", []):
		for p in t.get("players", []):
			if p.get("profileId") and p.get("civName"):
				pid_civ[p["profileId"]] = p["civName"]

	aoe2_match_id = best.get("matchId")
	rows = []
	for user_id, (nick, team, pids) in player_info.items():
		civ = next((pid_civ[pid] for pid in pids if pid in pid_civ), None)
		if not civ:
			continue
		result = ("W" if team == winner else "L") if (winner is not None and team is not None) else None
		rows.append(dict(
			channel_id=channel_id, replay_match_id=aoe2_match_id, aoe2_name="",
			civ=civ, at=match_at, bot_match_id=bot_match_id,
			user_id=user_id, nick=nick, team=team, result=result,
		))
	if not rows:
		return False

	await db.insert_many("civ_picks", rows)
	log.info(
		f"Civ match: bot match {bot_match_id} -> aoe2 {aoe2_match_id}, "
		f"recorded {len(rows)} civs (overlap {best_overlap})."
	)
	return True


async def _record_with_retry(channel_id, bot_match_id, players, winner, match_at):
	for delay in _RETRY_DELAYS:
		await asyncio.sleep(delay)
		try:
			if await _find_and_record(channel_id, bot_match_id, players, winner, match_at):
				return
		except Exception as e:
			log.error(f"Civ match error for bot match {bot_match_id}: {e}")
			return
	log.info(f"Civ match: no aoe2companion game found for bot match {bot_match_id} after retries.")


def schedule(channel_id, bot_match_id, players, winner, match_at):
	"""Fire-and-forget civ recording for a completed match.

	players: iterable of (user_id, nick, team). Safe to call from a command
	handler — returns immediately; recording happens in the background.
	"""
	task = asyncio.create_task(
		_record_with_retry(channel_id, bot_match_id, list(players), winner, match_at)
	)
	_pending.add(task)
	task.add_done_callback(_pending.discard)
