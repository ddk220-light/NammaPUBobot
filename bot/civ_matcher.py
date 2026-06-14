# -*- coding: utf-8 -*-
"""Record civs for a completed bot match by matching it to its aoe2companion game.

AOE2LobbyBOT doesn't reliably post result embeds in this server, so the live
civ_sync path captures nothing. Instead — the same way utils/civ_analysis.py
built all the historical civ data — we query the aoe2companion API for the
match participants' recent games, find the one that lines up by time + player
overlap, and store each player's civ in qc_match_civs (linked to the bot match).

Triggered from bot/stats/stats.py when a match is reported/completed. Because
the API lags a few minutes behind a finished game, we retry on a short backoff
until it appears (or give up). Runs as a background task — the /report command
returns immediately.
"""
import asyncio
import csv
import os
from datetime import datetime

import aiohttp

from core.console import log
from core.database import db

AOE2_API = "https://data.aoe2companion.com/api"
_PROFILE_MAP_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "player_profile_map.csv")

# Base URL of the replay visualizer (Railway). Overridable via env so we don't
# hard-code the host. Trailing slash stripped — links append "/?match=...".
VISUALIZER_URL = os.environ.get(
	"REPLAY_VISUALIZER_URL", "https://aoe2-replay-visualizer-production.up.railway.app"
).rstrip("/")

# Time window: bot match `at` (report time) minus API game start time should be
# positive and within a few hours (game duration + report delay).
_MAX_DIFF_SECONDS = 3 * 3600
# Retry schedule (seconds) — the API usually has the game within a few minutes.
_RETRY_DELAYS = (60, 180, 420)

# Keep references so create_task'd coroutines aren't garbage-collected mid-run.
_pending = set()


def _load_profile_map():
	"""nick -> [profile_id, ...] from data/player_profile_map.csv."""
	nick_to_pids = {}
	if not os.path.exists(_PROFILE_MAP_PATH):
		return nick_to_pids
	with open(_PROFILE_MAP_PATH, newline="") as f:
		for row in csv.DictReader(f):
			nick = row.get("nick")
			pids_raw = (row.get("profile_id") or "").strip()
			if not nick or not pids_raw:
				continue
			for p in pids_raw.split("/"):
				p = p.strip()
				if p.isdigit():
					nick_to_pids.setdefault(nick, []).append(int(p))
	return nick_to_pids


def _load_profile_uid_map():
	"""Discord user_id -> [profile_id, ...] from data/player_profile_map.csv.

	Keyed on the stable Discord user_id (the CSV's user_id column) rather than
	the nick. A player renaming used to silently break civ matching — the
	nick-keyed lookup is why most matches went un-recorded. user_id never drifts.
	"""
	uid_to_pids = {}
	if not os.path.exists(_PROFILE_MAP_PATH):
		return uid_to_pids
	with open(_PROFILE_MAP_PATH, newline="") as f:
		for row in csv.DictReader(f):
			uid = (row.get("user_id") or "").strip()
			pids_raw = (row.get("profile_id") or "").strip()
			if not uid.isdigit() or not pids_raw:
				continue
			for p in pids_raw.split("/"):
				p = p.strip()
				if p.isdigit():
					uid_to_pids.setdefault(int(uid), []).append(int(p))
	return uid_to_pids


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


async def _find_and_record(channel_id, bot_match_id, players, winner, match_at, post_replay=True):
	"""Return True if civs were recorded (or already present), False to retry."""
	nick_to_pids = _load_profile_map()
	uid_to_pids = _load_profile_uid_map()

	# Map this match's players to AoE2 profile ids. Prefer the stable user_id
	# (survives nick changes); fall back to nick for rows without a user_id.
	player_info = {}   # user_id -> (nick, team, [pids])
	active_pids = set()
	for user_id, nick, team in players:
		pids = uid_to_pids.get(user_id) or nick_to_pids.get(nick, [])
		if pids:
			player_info[user_id] = (nick, team, pids)
			active_pids.update(pids)
	if len(player_info) < 2:
		return True  # not enough mapped players to ever match — don't keep retrying

	# Already recorded?
	if await db.fetchone("SELECT 1 AS x FROM qc_match_civs WHERE bot_match_id=%s LIMIT 1", [bot_match_id]):
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
			channel_id=channel_id, aoe2_match_id=aoe2_match_id, aoe2_name="",
			civ=civ, at=match_at, bot_match_id=bot_match_id,
			user_id=user_id, nick=nick, team=team, result=result,
		))
	if not rows:
		return False

	await db.insert_many("qc_match_civs", rows)
	log.info(
		f"Civ match: bot match {bot_match_id} -> aoe2 {aoe2_match_id}, "
		f"recorded {len(rows)} civs (overlap {best_overlap})."
	)

	# Now that the aoe2 match is resolved, post a "watch replay" link (live path
	# only — the reconcile sweep passes post_replay=False to avoid spamming old
	# matches). Pick a download perspective that's actually a participant.
	if post_replay:
		link_pid = next((pid for pid in active_pids if pid in pid_civ), None)
		await _post_replay_link(channel_id, aoe2_match_id, link_pid, best)
	return True


async def _post_replay_link(channel_id, aoe2_match_id, profile_id, api_match):
	"""Post a clickable replay link to the channel (browser viewer + raw download).

	Best-effort: any failure here must not affect civ recording.
	"""
	if not aoe2_match_id or not profile_id:
		return
	try:
		from core.client import dc
		from nextcord import Embed, Colour

		channel = dc.get_channel(channel_id)
		if channel is None:
			return

		watch = f"{VISUALIZER_URL}/?match={aoe2_match_id}&profile={profile_id}"
		download = f"https://aoe.ms/replay/?gameId={aoe2_match_id}&profileId={profile_id}"

		map_name = ""
		if isinstance(api_match, dict):
			m = api_match.get("map")
			map_name = (m.get("name") if isinstance(m, dict) else None) or api_match.get("mapName") or ""

		embed = Embed(
			title="🎬 Replay ready",
			description=f"[▶ Watch in browser]({watch})\n[⬇ Download .aoe2record]({download})",
			colour=Colour(0x5865F2),
		)
		embed.set_footer(text=(f"{map_name} · aoe2 match {aoe2_match_id}" if map_name else f"aoe2 match {aoe2_match_id}"))
		await channel.send(embed=embed)
	except Exception as e:
		log.error(f"Replay link post failed (aoe2 {aoe2_match_id}): {e}")


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
