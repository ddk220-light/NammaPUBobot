# -*- coding: utf-8 -*-
"""Discord link-button views for joinable AoE2 lobbies.

Runtime-only (imports nextcord), so it is imported lazily by watcher.py and the
/lobby2 command — never at package import or under the unit tests. The pure URL
builders live in bot/lobby/view.py so they unit-test without Discord.

Discord only permits http(s)/discord schemes on link buttons, so the buttons point
at our own https redirect (bot/web.py /join|/spectate/<id>) which bounces the
browser to the `aoe2de://` deep link that launches the game.
"""
import nextcord

from core.config import cfg

from . import view


def link_view(game_id, *, join=True, spectate=True):
	"""A View with Join and/or Spectate link buttons for an AoE2 game id, or None when
	no public base URL (cfg.WS_ROOT_URL) is configured — without it we can't build the
	https redirect a Discord link button requires. `join`/`spectate` toggle which
	buttons appear (e.g. spectate-only once the game has launched)."""
	base = (getattr(cfg, "WS_ROOT_URL", "") or "").strip()
	if not base:
		return None
	v = nextcord.ui.View(timeout=None)   # link buttons are stateless -> redeploy-safe
	if join:
		v.add_item(nextcord.ui.Button(
			style=nextcord.ButtonStyle.link, label="Join lobby",
			emoji="\U0001F3AE", url=view.join_url(base, game_id)))
	if spectate:
		v.add_item(nextcord.ui.Button(
			style=nextcord.ButtonStyle.link, label="Spectate",
			emoji="\U0001F441️", url=view.spectate_url(base, game_id)))
	return v if v.children else None
