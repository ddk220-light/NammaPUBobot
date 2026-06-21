# -*- coding: utf-8 -*-
"""Shared nextcord embed builder for lobby cards (the AOE2LobbyBOT look).

Runtime-only (imports nextcord); both the ranked auto-watcher and the standalone
/lobby2 announcer render through here so the card is identical everywhere. The pure
text lives in bot/lobby/view.py so it unit-tests without Discord.
"""
import nextcord

from . import view

_GREEN = 0x50e3c2
_GREY = 0x4a4d52


def lobby_embed(entry, game_id, *, title=None, footer=None, colour=_GREEN):
	"""The full lobby card: title = lobby name, body = view.lobby_card_lines (join
	code block + settings + roster), and the map image as a thumbnail when present."""
	lob = entry.get("lobby") or {}
	embed = nextcord.Embed(
		title=title or lob.get("name") or "AoE2 lobby",
		colour=nextcord.Colour(colour),
		description="\n".join(view.lobby_card_lines(entry, game_id)) or None,
	)
	img = lob.get("mapImageUrl")
	if img:
		embed.set_thumbnail(url=img)
	if footer:
		embed.set_footer(text=footer)
	return embed


def simple_embed(title, body=None, footer=None, greyed=False):
	"""A plain status embed (loading / in-progress / closed) — no card body."""
	embed = nextcord.Embed(
		title=title, colour=nextcord.Colour(_GREY if greyed else _GREEN), description=body or None)
	if footer:
		embed.set_footer(text=footer)
	return embed
