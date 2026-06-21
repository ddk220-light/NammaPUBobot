# -*- coding: utf-8 -*-
"""Standalone lobby announcer for the /lobby2 command.

Subscribes to the live lobby socket filtered to ONE game id and live-edits the
command's own response message into a rich, self-updating lobby card (the
AOE2LobbyBOT look) until the game launches or the watch expires. Independent of the
ranked-match flow (no match, no roster-confirm, no profile heal) — it just renders a
lobby by id. On launch it leaves an `in_progress` qc_lobbies row (match_id NULL) so
LobbyJobs posts the post-game results card. Strictly best-effort and fully isolated:
every Discord/socket/DB error is logged, never raised.

Imports nextcord, so it is imported lazily (by bot.commands.matches.lobby2), never at
package import or under the unit tests.
"""
import asyncio
import time

from nextcord import DiscordException

from core.console import log
from core.database import db

from . import buttons, embeds, reducer, socket, view

HARD_TTL = 90 * 60          # absolute cap on an announcer's life (seconds)
NOT_FOUND_GRACE = 25        # if the lobby never appears in the feed within this, say so
EDIT_DEBOUNCE = 3.0         # min seconds between live card edits

active = {}                 # game_id -> announcer (dedupe concurrent /lobby2 for one id)
_pending = set()            # keep create_task'd jobs from being GC'd


def loading_embed(game_id):
	"""The card the command posts immediately as its response, before socket data."""
	return embeds.simple_embed(f"🔎 Lobby `{game_id}`", body="Looking up the live lobby…")


class LobbyAnnouncer:

	def __init__(self, message, game_id, requested_by=None):
		self.message = message          # the command's own response message (we edit it)
		self.channel = getattr(message, "channel", None)
		self.game_id = game_id
		self.requested_by = requested_by
		self.state = reducer.new_state()
		self.task = None
		self.launched = False
		self.seen = False               # have we received any data for this lobby?
		self.started_at = time.monotonic()
		self._last_edit = 0.0
		self._last_text = None

	def start(self):
		self.task = asyncio.create_task(self._guard())
		active[self.game_id] = self
		_pending.add(self.task)
		self.task.add_done_callback(lambda t: (_pending.discard(t), active.pop(self.game_id, None)))

	async def _guard(self):
		try:
			await self._run()
		except asyncio.CancelledError:
			raise
		except Exception as e:
			log.error(f"LobbyAnnouncer({self.game_id}) crashed: {e}")

	async def _run(self):
		async for events in socket.iter_frames(match_id=self.game_id):
			for ev in events:
				reducer.apply_event(self.state, ev)
				if (ev.get("type") == "lobbyRemoved"
						and (ev.get("data") or {}).get("matchId") == self.game_id):
					self.launched = True
			if self.game_id in self.state:
				self.seen = True
			await self._render()
			if self.launched or self._expired() or self._not_found():
				break
		await self._finish()

	def _expired(self):
		return (time.monotonic() - self.started_at) > HARD_TTL

	def _not_found(self):
		return not self.seen and (time.monotonic() - self.started_at) > NOT_FOUND_GRACE

	async def _render(self):
		entry = self.state.get(self.game_id)
		if not entry:
			return
		rendered = "\n".join(view.lobby_card_lines(entry, self.game_id))
		if rendered == self._last_text:
			return
		now = time.monotonic()
		if (now - self._last_edit) < EDIT_DEBOUNCE and self._last_text is not None:
			return
		await self._safe_edit(embeds.lobby_embed(entry, self.game_id),
							   view=buttons.link_view(self.game_id))
		self._last_text = rendered
		self._last_edit = now

	async def _finish(self):
		entry = self.state.get(self.game_id, {"lobby": {}, "slots": {}})
		name = (entry.get("lobby") or {}).get("name") or self.game_id
		if self.launched:
			await self._persist_in_progress(entry)
			await self._safe_edit(embeds.simple_embed(
				f"🎮 `{name}` — game in progress",
				body="The result will be posted here when the game ends.",
				footer=f"game {self.game_id}"),
				view=buttons.link_view(self.game_id, join=False, spectate=True))
		elif not self.seen:
			await self._safe_edit(embeds.simple_embed(
				f"Lobby `{self.game_id}` not found",
				body="No open lobby with that id — check the id (the number in "
					 "`aoe2de://0/<id>`) and that it hasn't started yet.", greyed=True), view=None)
		else:
			await self._safe_edit(embeds.simple_embed(
				f"Lobby `{name}` closed", body="Tracking ended.", greyed=True), view=None)

	async def _persist_in_progress(self, entry):
		"""Leave a bare (match_id NULL) in_progress row so LobbyJobs posts the results
		card after the game finishes. Only inserts if no row exists for this lobby — a
		ranked /lobby2 link already created its own row and owns the result flow."""
		try:
			existing = await db.select_one(
				["id"], "qc_lobbies",
				where={"channel_id": getattr(self.channel, "id", None), "aoe2_game_id": self.game_id})
			if existing:
				return
			lob = entry.get("lobby") or {}
			now = int(time.time())
			await db.insert("qc_lobbies", dict(
				aoe2_game_id=self.game_id, channel_id=getattr(self.channel, "id", None),
				message_id=getattr(self.message, "id", None), completed_message_id=None,
				match_id=None, status="in_progress", lobby_name=(lob.get("name") or "(lobby)"),
				map_name=lob.get("mapName"), server=lob.get("server"),
				profile_ids=",".join(str(p) for p in sorted(reducer.profile_ids(entry))),
				created_at=now, last_edit_at=0, requested_by=self.requested_by))
		except Exception as e:
			log.error(f"LobbyAnnouncer({self.game_id}) persist failed: {e}")

	# ── discord helpers (bulletproof) ────────────────────────────────────
	async def _safe_edit(self, embed, view=None):
		if self.message is None:
			return
		try:
			await self.message.edit(embed=embed, view=view)
		except DiscordException as e:
			log.warning(f"LobbyAnnouncer({self.game_id}) edit failed: {e}")


def start(message, game_id, requested_by=None):
	"""Spawn (or reuse) an announcer that live-edits `message` for game_id. Returns the
	announcer, or the existing one if /lobby2 is already tracking this id."""
	existing = active.get(game_id)
	if existing:
		return existing
	announcer = LobbyAnnouncer(message, game_id, requested_by)
	announcer.start()
	return announcer
