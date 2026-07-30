"""Unit tests for the post-game storyline payoff.

Resolution is pure: a candidate plus a winner plus a user->team map. The embed
builder is exercised through asyncio.run with a fake db, because there is no
pytest-asyncio in this project and an `async def test_` would be silently
skipped.
"""
from __future__ import annotations

import random

import pytest

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


# ── frame tone follows tonight's result, not the stored pre-game direction ──
def test_an_unbeaten_pair_that_loses_tonight_gets_a_negative_frame():
	"""d["won"]=True is the pre-game direction (unbeaten). came_true=False means
	tonight broke that streak, which is bad news for the whole side -- the frame
	must not read as a congratulation."""
	c = _c("perfect", (1, 2), {"ids": [1, 2], "n": 5, "won": True, "team_idx": 0})
	frame = sp._payoff_frame(c, False, _NICK, _META, _ROSTERS, rng=random.Random(3))
	assert ("went down with them" in frame) or ("rough one for" in frame), frame


def test_a_cursed_pair_that_finally_wins_tonight_gets_a_positive_frame():
	"""d["won"]=False is the pre-game direction (cursed). came_true=True means the
	curse broke tonight, which is good news for the whole side -- the frame must
	not read as commiseration."""
	c = _c("perfect", (1, 2), {"ids": [1, 2], "n": 5, "won": False, "team_idx": 0})
	frame = sp._payoff_frame(c, True, _NICK, _META, _ROSTERS, rng=random.Random(3))
	assert ("got to enjoy it" in frame) or ("good night to be" in frame), frame


# ── payoff_phrase must fail loud on an unrenderable candidate type ──────────
def test_payoff_phrase_raises_for_an_unknown_candidate_type():
	c = _c("bogus", (1,), {"p": 1, "k": 5, "won": True})
	with pytest.raises(ValueError):
		sp.payoff_phrase(c, True, _NICK, _META, _ROSTERS, rng=random.Random(0))


# ── sentence case (defect 1, mirrored past-tense frame) ──────────────────
class _FirstChoice:
	"""Deterministic stand-in for ``random`` that always takes the first pooled
	variant, so a test can pin down which template renders without brute-forcing
	a real seed."""

	def choice(self, seq):
		return seq[0]


class _CountingChoice:
	"""Wraps ``_FirstChoice`` while counting ``.choice`` calls, so a test can
	assert _payoff_frame burns exactly one RNG draw per invocation."""

	def __init__(self):
		self.calls = 0

	def choice(self, seq):
		self.calls += 1
		return seq[0]


_SMALL_ROSTERS = {0: [1, 2, 3], 1: [5, 6, 7]}


def test_a_large_complement_payoff_frame_does_not_lowercase_after_the_period():
	"""Same defect as bot/team_insights.py's _frame: complement_of collapses to
	'the rest of {name}' past two uninvolved players, and _payoff_frame has its
	own sentence-initial templates that must not leak the lowercase phrase."""
	c = _c("form", (1,), {"p": 1, "k": 5, "won": True})
	out = sp._payoff_frame(c, False, _NICK, _META, _ROSTERS, rng=_FirstChoice())
	assert ". the rest of" not in out
	assert out.startswith("The rest of Alpha")


# ── rival backers (defect 2, mirrored past-tense frame) ──────────────────
def test_an_h2h_payoff_frame_names_the_subjects_own_backers_when_it_came_true():
	"""subject_of(h2h) picks the pre-game favourite (d['winner']); when their
	streak holds (came_true=True) the frame is short -- just their own side's
	backers, not a roll call of both sides."""
	c = _c("h2h", (1, 5), {"winner": 1, "loser": 5, "k": 4, "series": 6,
	                       "sweep": False}, teams=(0, 1))
	out = sp._payoff_frame(c, True, _NICK, _META, _SMALL_ROSTERS, rng=_FirstChoice())
	assert out not in ("Their teammates had their own night.",
	                    "The other six were along for the ride.")
	assert "Bo" in out and "Cy" in out


def test_an_h2h_payoff_frame_flips_the_winning_side_when_the_underdog_wins():
	"""subject_of(h2h) picks the pre-game favourite (d['winner']); when the
	streak breaks (came_true=False) the *other* rival's backers are the ones
	who actually got their way tonight."""
	c = _c("h2h", (1, 5), {"winner": 1, "loser": 5, "k": 4, "series": 6,
	                       "sweep": False}, teams=(0, 1))
	won_true = sp._payoff_frame(c, True, _NICK, _META, _SMALL_ROSTERS, rng=_FirstChoice())
	won_false = sp._payoff_frame(c, False, _NICK, _META, _SMALL_ROSTERS, rng=_FirstChoice())
	assert won_true != won_false
	assert "Fay" in won_false and "Gil" in won_false


def test_an_h2h_payoff_frame_uses_the_team_name_once_backers_collapse():
	"""Same bug as the pre-game frame: in a 4v4 the complement always collapses
	to "the rest of {team}". The payoff frame must say just the team name."""
	c = _c("h2h", (1, 5), {"winner": 1, "loser": 5, "k": 4, "series": 6,
	                       "sweep": False}, teams=(0, 1))
	out = sp._payoff_frame(c, True, _NICK, _META, _ROSTERS, rng=_FirstChoice())
	assert "the rest of" not in out
	assert "Alpha" in out


def test_h2h_payoff_frame_falls_back_to_generic_when_rival_backers_is_none():
	c = _c("h2h", (1, 99), {"winner": 1, "loser": 99, "k": 4, "series": 6,
	                        "sweep": False}, teams=(0, 1))
	out = sp._payoff_frame(c, True, _NICK, _META, _ROSTERS, rng=_FirstChoice())
	assert out in ("Their teammates had their own night.", "The other six were along for the ride.")


def test_payoff_frame_consumes_exactly_one_rng_choice_call_per_branch():
	"""build_payoff_embed re-runs ti._phrase to burn the pre-game RNG draws so
	its variant picks stay aligned -- every _payoff_frame branch must consume
	exactly one rng.choice call or that alignment breaks."""
	cases = [
		("h2h", (1, 5), {"winner": 1, "loser": 5, "k": 4, "series": 6, "sweep": False},
		 (0, 1), _SMALL_ROSTERS, True),
		("h2h", (1, 99), {"winner": 1, "loser": 99, "k": 4, "series": 6, "sweep": False},
		 (0, 1), _ROSTERS, True),
		("lineup", (1, 2, 3, 4), {"ids": [1, 2, 3, 4], "wins": 2, "games": 2,
		                          "one_way": True, "won": True, "team_idx": 0},
		 (0,), _ROSTERS, True),
		("form", (1,), {"p": 1, "k": 5, "won": True}, (0,), _ROSTERS, True),
		("form", (1,), {"p": 1, "k": 5, "won": True}, (0,), _ROSTERS, False),
	]
	for typ, players, data, teams, rosters, came_true in cases:
		c = _c(typ, players, data, teams)
		counter = _CountingChoice()
		sp._payoff_frame(c, came_true, _NICK, _META, rosters, rng=counter)
		assert counter.calls == 1, (typ, data, came_true)


# ── embed assembly ───────────────────────────────────────────────────────
class _P:
	def __init__(self, uid):
		self.id = uid


class _T(list):
	def __init__(self, ids, name, emoji):
		super().__init__(_P(i) for i in ids)
		self.name = name
		self.emoji = emoji


class _M:
	def __init__(self, winner):
		self.id = 500
		self.winner = winner
		self.teams = [_T([1, 2, 3, 4], "Alpha", "🟦"), _T([5, 6, 7, 8], "Beta", "🟥")]
		self.qc = type("QC", (), {"id": 77})()


def _history_rows():
	"""Players 1 & 2 have lost their last four as teammates, of seven together.

	Order matters: the three wins are the OLDER match ids and the four losses
	trail, so _trailing_streak sees a 4-game losing run. Seven games clears
	MATE_MIN_TOGETHER=6 and the run clears MATE_MIN_STREAK=4.
	"""
	rows = []
	for mid in range(1, 4):          # 1-3: team 0 wins
		for uid, team in ((1, 0), (2, 0), (5, 1), (6, 1)):
			rows.append({"match_id": mid, "user_id": uid, "nick": f"u{uid}",
			             "team": team, "winner": 0})
	for mid in range(4, 8):          # 4-7: team 0 loses
		for uid, team in ((1, 0), (2, 0), (5, 1), (6, 1)):
			rows.append({"match_id": mid, "user_id": uid, "nick": f"u{uid}",
			             "team": team, "winner": 1})
	return rows


def _run_build(monkeypatch, winner, rows):
	import asyncio
	import sys
	import types

	import bot.team_insights as ti

	class _DB:
		async def fetchall(self, sql, params=None):
			return rows

	monkeypatch.setattr(ti, "db", _DB())

	fake_utils = types.ModuleType("core.utils")
	fake_utils.get_nick = lambda p: f"u{p.id}"
	monkeypatch.setitem(sys.modules, "core.utils", fake_utils)

	# build_payoff_embed defers `from nextcord import Colour, Embed` specifically so
	# the pure helpers import cleanly under the CI test shim (see the module
	# docstring) -- CI never installs nextcord (see ci.yml). A candidate-producing
	# fixture reaches that import, so it needs the same treatment as core.utils
	# above: a minimal fake standing in for the real package.
	class _FakeEmbed:
		def __init__(self, *, title=None, colour=None, description=None):
			self.title = title
			self.colour = colour
			self.description = description
			self.footer_text = None

		def set_footer(self, *, text=None):
			self.footer_text = text

	fake_nextcord = types.ModuleType("nextcord")
	fake_nextcord.Embed = _FakeEmbed
	fake_nextcord.Colour = lambda value: value
	monkeypatch.setitem(sys.modules, "nextcord", fake_nextcord)
	return asyncio.run(sp.build_payoff_embed(_M(winner)))


def test_a_draw_builds_no_embed(monkeypatch):
	assert _run_build(monkeypatch, None, _history_rows()) is None


def test_no_history_builds_no_embed(monkeypatch):
	assert _run_build(monkeypatch, 0, []) is None


def test_a_decisive_result_builds_a_titled_embed(monkeypatch):
	embed = _run_build(monkeypatch, 0, _history_rows())
	assert embed is not None
	assert embed.title == "⚔️ Final Tale of the Tape"
	assert embed.description.strip()


def test_the_current_match_is_excluded_from_its_own_prior(monkeypatch):
	"""Match 500 in history must not resolve itself."""
	rows = _history_rows()
	rows += [{"match_id": 500, "user_id": u, "nick": f"u{u}", "team": t, "winner": 0}
	         for u, t in ((1, 0), (2, 0), (5, 1), (6, 1))]
	with_current = _run_build(monkeypatch, 0, rows)
	without = _run_build(monkeypatch, 0, _history_rows())
	assert with_current.description == without.description
