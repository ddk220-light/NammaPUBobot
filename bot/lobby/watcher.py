# -*- coding: utf-8 -*-
"""LobbyWatcher — per-(ranked)-match live lobby detection + link.

Created when a ranked match enters WAITING_REPORT (bot/match/match.py). It
subscribes to the unfiltered lobby socket, keeps only lobbies named ``test123``,
shows a live-fill embed, and — when a full lobby with the right player count
appears — confirms the LINK: captures the gameId + slot profileIds, persists a
``qc_lobbies`` row tied to the match, and self-heals ``qc_profile_map`` by
elimination. Once the game launches it leaves a durable ``in_progress`` row and
stops; the captain-confirmed result loop is Phase 3.

Strictly best-effort + isolated: the whole run loop is wrapped, every Discord/DB
call is guarded, and match.py creates/destroys watchers inside try/except. Nothing
here can affect the core match or reporting flow. This module imports nextcord, so
it is only imported lazily at runtime (never at package import / under tests).
"""
import asyncio
import time

from nextcord import DiscordException

from core.console import log
from core.database import db

from . import buttons, embeds, reducer, socket, view, profile_map

TARGET_NAME = "test123"     # the announce join key (v1: fixed, single active match)
HARD_TTL = 90 * 60          # absolute cap on a watcher's life (seconds)
EDIT_DEBOUNCE = 3.0         # min seconds between live-fill embed edits

# Live watchers keyed by bot match id — lets match.py tear them down.
active = {}


class LobbyWatcher:

	def __init__(self, match, channel):
		self.match = match
		self.channel = channel
		self.match_size = len(match.players)
		self.state = reducer.new_state()
		self.task = None
		self.message = None
		self.linked = False
		self.game_id = None
		self.launched = False
		self.started_at = time.monotonic()
		self._last_edit = 0.0
		self._last_text = None
		self._stopped = False

	# ── lifecycle ────────────────────────────────────────────────────────
	def start(self):
		self.task = asyncio.create_task(self._guard())
		active[self.match.id] = self

	async def stop(self, status=None):
		if self._stopped:
			return
		self._stopped = True
		active.pop(self.match.id, None)
		if self.task:
			self.task.cancel()
		if status == "expired" and self.message and not self.linked:
			await self._safe_edit(
				embeds.simple_embed("Lobby tracking ended — no lobby detected.", greyed=True), view=None)

	async def _guard(self):
		try:
			await self._run()
		except asyncio.CancelledError:
			raise
		except Exception as e:
			log.error(f"LobbyWatcher({self.match.id}) crashed: {e}")

	async def _run(self):
		async for events in socket.iter_frames():   # unfiltered firehose
			self._ingest(events)
			await self._react()
			if self.launched or self._expired():
				break
		if self._expired() and not self.linked:
			await self.stop("expired")

	def _expired(self):
		return (time.monotonic() - self.started_at) > HARD_TTL

	# ── ingestion: keep only TARGET_NAME lobbies ─────────────────────────
	def _ingest(self, events):
		for ev in events:
			if not isinstance(ev, dict):
				continue
			etype = ev.get("type")
			data = ev.get("data")
			if not isinstance(data, dict):
				continue
			mid = data.get("matchId")
			if mid is None:
				continue
			if etype in ("lobbyAdded", "lobbyUpdated"):
				name = (data.get("name") or "").strip().lower()
				if name == TARGET_NAME:
					reducer.apply_event(self.state, ev)
				else:
					self.state.pop(mid, None)   # not (or no longer) one of ours
			elif etype == "lobbyRemoved":
				if mid in self.state:
					reducer.apply_event(self.state, ev)
					if mid == self.game_id:
						self.launched = True
			elif etype in ("slotAdded", "slotUpdated", "slotRemoved"):
				if mid in self.state:           # only slots for a tracked lobby
					reducer.apply_event(self.state, ev)

	# ── reactions ────────────────────────────────────────────────────────
	async def _react(self):
		if self.launched:
			await self._on_launch()
			return
		cand = view.pick_candidate(self.state, self.match_size)
		if cand is None:
			return
		mid, entry = cand
		await self._render(mid, entry)
		if not self.linked and view.link_ready(entry, self.match_size):
			await self._confirm(mid, entry)

	async def _render(self, mid, entry):
		title = (entry.get("lobby") or {}).get("name") or TARGET_NAME
		body = "\n".join(view.lobby_card_lines(entry, mid)) or "*waiting for players…*"
		rendered = title + "\n" + body
		if rendered == self._last_text:
			return
		now = time.monotonic()
		if self.message is not None and (now - self._last_edit) < EDIT_DEBOUNCE:
			return
		embed = embeds.lobby_embed(entry, mid)
		vw = buttons.link_view(mid)
		if self.message is None:
			await self._safe_send(embed, view=vw)
		else:
			await self._safe_edit(embed, view=vw)
		self._last_text = rendered
		self._last_edit = now

	async def _confirm(self, mid, entry):
		self.linked = True
		self.game_id = mid
		pids = sorted(reducer.profile_ids(entry))
		# Self-heal the profile map by elimination (safe: only the lone leftover).
		try:
			known = await profile_map.known_for(pids)
			names = {pid: nm for pid, nm, _t, _s in reducer.roster(entry)}
			match_uids = [p.id for p in self.match.players]
			for uid, pid in profile_map.eliminate(match_uids, pids, known):
				await profile_map.link(uid, pid, names.get(pid, ""))
		except Exception as e:
			log.error(f"LobbyWatcher({self.match.id}) profile-map heal failed: {e}")
		await self._persist("filling")
		await self._safe_edit(embeds.lobby_embed(
			entry, mid,
			footer=f"✅ Linked to match #{self.match.id} · {len(pids)} players",
		), view=buttons.link_view(mid))
		log.info(f"LobbyWatcher({self.match.id}) linked game {mid} ({len(pids)} players).")

	async def _on_launch(self):
		if self._stopped:
			return
		await self._persist("in_progress")
		# Game launched: the lobby is gone, so a Join button would be dead — keep only a
		# Spectate button (cleared automatically when no base URL is configured).
		await self._safe_edit(embeds.simple_embed(
			f"🎮 Game in progress — match #{self.match.id}",
			body="The result will sync when the game ends.",
			footer=f"game {self.game_id}",
		), view=buttons.link_view(self.game_id, join=False, spectate=True))
		log.info(f"LobbyWatcher({self.match.id}) game {self.game_id} launched.")
		await self.stop()   # leave the in_progress row; Phase 3 polls completion

	# ── persistence ──────────────────────────────────────────────────────
	async def _persist(self, status):
		if not self.game_id:
			return
		entry = self.state.get(self.game_id, {"lobby": {}, "slots": {}})
		lob = entry.get("lobby") or {}
		pids = sorted(reducer.profile_ids(entry))
		now = int(time.time())
		row = {
			"aoe2_game_id": self.game_id,
			"channel_id": self.match.qc.id,
			"message_id": self.message.id if self.message else None,
			"match_id": self.match.id,
			"status": status,
			"lobby_name": TARGET_NAME,
			"map_name": lob.get("mapName"),
			"server": lob.get("server"),
			"profile_ids": ",".join(str(p) for p in pids),
			"created_at": now,
			"last_edit_at": now,
			"requested_by": None,
		}
		try:
			existing = await db.select_one(
				["id"], "qc_lobbies",
				where={"channel_id": self.match.qc.id, "aoe2_game_id": self.game_id},
			)
			if existing:
				await db.update(
					"qc_lobbies",
					{k: v for k, v in row.items() if k not in ("aoe2_game_id", "channel_id", "created_at")},
					keys={"id": existing["id"]},
				)
			else:
				await db.insert("qc_lobbies", row)
		except Exception as e:
			log.error(f"LobbyWatcher({self.match.id}) persist failed: {e}")

	# ── discord helpers ──────────────────────────────────────────────────
	async def _safe_send(self, embed, view=None):
		try:
			self.message = await self.channel.send(embed=embed, view=view)
		except DiscordException as e:
			log.warning(f"LobbyWatcher({self.match.id}) send failed: {e}")

	async def _safe_edit(self, embed, view=None):
		if not self.message:
			return
		try:
			await self.message.edit(embed=embed, view=view)
		except DiscordException as e:
			log.warning(f"LobbyWatcher({self.match.id}) edit failed: {e}")


# ── module entry points used by match.py (best-effort) ───────────────────

def start_for(match, channel):
	"""Spin up a watcher for a ranked match. Returns it, or None on failure."""
	try:
		watcher = LobbyWatcher(match, channel)
		watcher.start()
		return watcher
	except Exception as e:
		log.error(f"Failed to start LobbyWatcher for match {getattr(match, 'id', '?')}: {e}")
		return None


async def stop_for(match_id, status=None):
	"""Tear down the watcher for a match, if any."""
	watcher = active.get(match_id)
	if watcher:
		await watcher.stop(status)
