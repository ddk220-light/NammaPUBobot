# -*- coding: utf-8 -*-
"""Pure unit tests for bot/lobby/results.py (post-game results card text)."""
from bot.lobby import results

_MATCH = {
	"matchId": 486986844,
	"mapName": "Arabia",
	"server": "ukwest",
	"started": "2026-06-20T10:00:00.000Z",
	"finished": "2026-06-20T10:32:30.000Z",
	"teams": [
		{"teamId": 1, "players": [
			{"profileId": 1, "name": "ddk220", "civName": "Mongols", "won": True, "replay": True},
			{"profileId": 2, "name": "Shadeslayer", "civName": "Franks", "won": True, "replay": False},
		]},
		{"teamId": 2, "players": [
			{"profileId": 3, "name": "Kaipullae", "civName": "Britons", "won": False, "replay": True},
			{"profileId": 4, "name": "rusher", "civName": "Mayans", "won": False, "replay": False},
		]},
	],
}


def test_fmt_duration():
	assert results.fmt_duration(None) == "?"
	assert results.fmt_duration(125) == "2:05"
	assert results.fmt_duration(3725) == "1:02:05"


def test_results_lines_meta_winner_and_civs():
	body = "\n".join(results.results_lines(_MATCH))
	assert "Map: Arabia" in body and "Server: ukwest" in body
	assert "Duration: 32:30" in body
	assert "🏆 **Team 1** — winner" in body
	assert "**Team 2**" in body and "🏆 **Team 2**" not in body
	assert "• ddk220 — Mongols" in body and "• Kaipullae — Britons" in body


def test_results_lines_draw_when_no_clear_winner():
	draw = {"teams": [{"teamId": 1, "players": [{"profileId": 1, "name": "a", "won": True}]},
					  {"teamId": 2, "players": [{"profileId": 2, "name": "b", "won": True}]}]}
	body = "\n".join(results.results_lines(draw))
	assert "draw / not determined" in body


def test_replay_links_only_for_recorded_players():
	links = results.replay_links(_MATCH)
	names = [n for n, _ in links]
	assert names == ["ddk220", "Kaipullae"]            # only replay==True players
	assert all("aoe.ms/replay/?gameId=486986844&profileId=" in url for _, url in links)
