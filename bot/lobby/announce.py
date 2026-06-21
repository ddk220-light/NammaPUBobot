# -*- coding: utf-8 -*-
"""Standalone lobby announcer for the /lobby2 command.

Subscribes to the live lobby socket filtered to ONE game id, posts a rich,
self-updating lobby card (the AOE2LobbyBOT look) in the channel, and keeps editing
it as players join until the game launches or the watch expires. Independent of the
ranked-match flow (no match, no roster-confirm, no profile heal) — it just renders a
lobby by id. Strictly best-effort and fully isolated: every Discord/socket error is
logged, never raised, so it can't affect anything else.

Imports nextcord, so it is imported lazily (by bot.commands.matches.lobby2), never at
package import or under the unit tests.
"""
import asyncio
import time

from nextcord import DiscordException

from core.console import log

from . import buttons, embeds, reducer, socket, view

HARD_TTL = 90 * 60          # absolute cap on an announcer's life (seconds)
NOT_FOUND_GRACE = 25        # if the lobby never appears in the feed within this, say so
EDIT_DEBOUNCE = 3.0         # min seconds between live card edits

active = {}                 # game_id -> announcer (dedupe concurrent /lobby2 for one id)
_pending = set()            # keep create_task'd jobs from being GC'd


class LobbyAnnouncer:

	def __init__(self, channel, game_id):
		self.channel = channel
		self.game_id = game_id
		self.state = reducer.new_state()
		self.message = None
		self.task = None
		self.launched = False
		self.seen = False                     # have we received any data for this lobby?
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
		# Seed a loading card immediately so the channel shows something at once.
		await self._safe_send(embeds.simple_embed(
			f"🔎 Lobby `{self.game_id}`", body="Looking up the live lobby…"))
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
		if self.message is not None and (now - self._last_edit) < EDIT_DEBOUNCE:
			return
		await self._safe_edit(embeds.lobby_embed(entry, self.game_id),
							   view=buttons.link_view(self.game_id))
		self._last_text = rendered
		self._last_edit = now

	async def _finish(self):
		entry = self.state.get(self.game_id, {"lobby": {}, "slots": {}})
		name = (entry.get("lobby") or {}).get("name") or self.game_id
		if self.launched:
			await self._safe_edit(embeds.simple_embed(
				f"🎮 `{name}` — game in progress",
				body="The lobby has started.", footer=f"game {self.game_id}"),
				view=buttons.link_view(self.game_id, join=False, spectate=True))
		elif not self.seen:
			await self._safe_edit(embeds.simple_embed(
				f"Lobby `{self.game_id}` not found",
				body="No open lobby with that id — check the id (the number in "
					 "`aoe2de://0/<id>`) and that it hasn't started yet.", greyed=True), view=None)
		else:
			await self._safe_edit(embeds.simple_embed(
				f"Lobby `{name}` closed", body="Tracking ended.", greyed=True), view=None)

	# ── discord helpers (bulletproof) ────────────────────────────────────
	async def _safe_send(self, embed, view=None):
		try:
			self.message = await self.channel.send(embed=embed, view=view)
		except DiscordException as e:
			log.warning(f"LobbyAnnouncer({self.game_id}) send failed: {e}")

	async def _safe_edit(self, embed, view=None):
		if self.message is None:
			return await self._safe_send(embed, view=view)
		try:
			await self.message.edit(embed=embed, view=view)
		except DiscordException as e:
			log.warning(f"LobbyAnnouncer({self.game_id}) edit failed: {e}")


def start(channel, game_id):
	"""Spawn (or reuse) an announcer for game_id in channel. Returns the announcer, or
	the existing one if /lobby2 was already tracking this id."""
	existing = active.get(game_id)
	if existing:
		return existing
	announcer = LobbyAnnouncer(channel, game_id)
	announcer.start()
	return announcer
