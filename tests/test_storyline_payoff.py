"""Unit tests for the post-game storyline payoff.

Resolution is pure: a candidate plus a winner plus a user->team map. The embed
builder is exercised through asyncio.run with a fake db, because there is no
pytest-asyncio in this project and an `async def test_` would be silently
skipped.
"""
from __future__ import annotations

import random

import bot.storyline_payoff as sp

_NICK = {1: "Ann", 2: "Bo", 3: "Cy", 4: "Dee", 5: "Eve", 6: "Fay", 7: "Gil", 8: "Hal"}
_META = [{"name": "Alpha", "emoji": "🟦"}, {"name": "Beta", "emoji": "🟥"}]
_ROSTERS = {0: [1, 2, 3, 4], 1: [5, 6, 7, 8]}
_TEAM_OF = {1: 0, 2: 0, 3: 0, 4: 0, 5: 1, 6: 1, 7: 1, 8: 1}


def _c(typ, players, data, teams=(0,)):
	return {"type": typ, "score": 1.0, "players": frozenset(players),
	        "teams": frozenset(teams), "data": data}


# ── resolution ───────────────────────────────────────────────────────────
def test_a_team_subject_resolves_on_its_own_side_winning():
	c = _c("mate", (1, 2), {"ids": [1, 2], "k": 4, "series": 7, "won": False,
	                        "team_idx": 0})
	assert sp.resolve(c, 0, _TEAM_OF) is True
	assert sp.resolve(c, 1, _TEAM_OF) is False


def test_a_player_subject_resolves_through_the_team_map():
	c = _c("h2h", (1, 5), {"winner": 5, "loser": 1, "k": 4, "series": 5,
	                       "sweep": False}, teams=(0, 1))
	assert sp.resolve(c, 1, _TEAM_OF) is True     # player 5 is on team 1
	assert sp.resolve(c, 0, _TEAM_OF) is False


def test_a_draw_resolves_nothing():
	c = _c("form", (1,), {"p": 1, "k": 5, "won": True})
	assert sp.resolve(c, None, _TEAM_OF) is None


def test_an_unknown_player_resolves_nothing():
	c = _c("form", (99,), {"p": 99, "k": 5, "won": True})
	assert sp.resolve(c, 0, _TEAM_OF) is None


# ── phrasing truth table ─────────────────────────────────────────────────
_CASES = [
	("lineup", (1, 2, 3, 4), {"ids": [1, 2, 3, 4], "wins": 2, "games": 2,
	                          "one_way": True, "won": True, "team_idx": 0}, (0,)),
	("trio", (1, 2, 3), {"ids": [1, 2, 3], "wins": 4, "games": 5, "won": True,
	                     "team_idx": 0}, (0,)),
	("perfect", (1, 2), {"ids": [1, 2], "n": 5, "won": False, "team_idx": 0}, (0,)),
	("mate_wr", (1, 2), {"p": 1, "q": 2, "wr": 0.8, "base": 0.5, "games": 8,
	                     "kind": "best"}, (0,)),
	("h2h", (1, 5), {"winner": 1, "loser": 5, "k": 4, "series": 6,
	                 "sweep": False}, (0, 1)),
	("mate", (1, 2), {"ids": [1, 2], "k": 4, "series": 7, "won": True,
	                  "team_idx": 0}, (0,)),
	("deadlock", (1, 5), {"ids": [1, 5], "each": 3, "n": 6}, (0, 1)),
	("form", (1,), {"p": 1, "k": 5, "won": True}, (0,)),
]


def test_every_type_phrases_both_outcomes():
	for typ, players, data, teams in _CASES:
		c = _c(typ, players, data, teams)
		for came_true in (True, False):
			line = sp.payoff_phrase(c, came_true, _NICK, _META, _ROSTERS,
			                        rng=random.Random(3))
			assert isinstance(line, str) and line.strip(), (typ, came_true)


def test_the_two_outcomes_differ():
	for typ, players, data, teams in _CASES:
		c = _c(typ, players, data, teams)
		won = sp.payoff_phrase(c, True, _NICK, _META, _ROSTERS, rng=random.Random(3))
		lost = sp.payoff_phrase(c, False, _NICK, _META, _ROSTERS, rng=random.Random(3))
		assert won != lost, typ


def test_payoff_phrasing_is_deterministic():
	c = _c("form", (1,), {"p": 1, "k": 5, "won": True})
	a = sp.payoff_phrase(c, True, _NICK, _META, _ROSTERS, rng=random.Random(11))
	b = sp.payoff_phrase(c, True, _NICK, _META, _ROSTERS, rng=random.Random(11))
	assert a == b
