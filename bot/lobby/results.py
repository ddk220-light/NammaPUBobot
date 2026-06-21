# -*- coding: utf-8 -*-
"""Post-game results card (the AOE2LobbyBOT match-result look).

Renders a finished aoe2companion match object into a Discord embed: map · server ·
duration, both teams with the winner trophied, civ per player, and per-player
recorded-game download links. The text builders are pure (no nextcord) so they
unit-test; results_embed wraps them (and lazy-imports nextcord).
"""
from . import api

_GOLD = 0xF1C40F


def fmt_duration(secs):
	"""Seconds -> `m:ss` (or `h:mm:ss`), `?` if unknown."""
	if secs is None:
		return "?"
	h, rem = divmod(int(secs), 3600)
	m, s = divmod(rem, 60)
	return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _player_name(p):
	return p.get("name") or (str(p["profileId"]) if p.get("profileId") else "?")


def results_lines(match):
	"""Body lines: a meta line (map · server · duration) then each team with a winner
	trophy and `• name — civ` per player. Pure."""
	win_tid = api.winning_teamid(match)
	meta = []
	if match.get("mapName"):
		meta.append(f"Map: {match['mapName']}")
	if match.get("server"):
		meta.append(f"Server: {match['server']}")
	meta.append(f"Duration: {fmt_duration(api.match_duration_seconds(match))}")
	lines = [" · ".join(meta)]
	for t in match.get("teams") or []:
		tid = t.get("teamId")
		won = win_tid is not None and tid == win_tid
		lines.append("")
		lines.append(f"{'🏆 ' if won else ''}**Team {tid}**" + (" — winner" if won else ""))
		for p in t.get("players") or []:
			lines.append(f"• {_player_name(p)} — {p.get('civName') or '?'}")
	if win_tid is None:
		lines += ["", "Result: draw / not determined"]
	return lines


def replay_links(match):
	"""[(name, aoe.ms-download-url), ...] for players who recorded the game. Pure."""
	mid = match.get("matchId")
	out = []
	for t in match.get("teams") or []:
		for p in t.get("players") or []:
			if p.get("replay") and p.get("profileId"):
				out.append((_player_name(p),
							f"https://aoe.ms/replay/?gameId={mid}&profileId={p['profileId']}"))
	return out


def results_embed(match):
	import nextcord   # lazy: keep the module import-light + unit-test-friendly

	title = "🏆 Match results"
	if match.get("mapName"):
		title += f" — {match['mapName']}"
	embed = nextcord.Embed(
		title=title, colour=nextcord.Colour(_GOLD),
		description="\n".join(results_lines(match)) or None)
	links = replay_links(match)
	if links:
		embed.add_field(
			name="Recorded games",
			value="\n".join(f"[{nm}]({url})" for nm, url in links[:10]), inline=False)
	if match.get("mapImageUrl"):
		embed.set_thumbnail(url=match["mapImageUrl"])
	return embed
