"""Substitutions under a live betting book.

/subauto has refunded and re-opened the book since the day it shipped
(nammaoe2bot/pickup/match/substitution.py). /subfor and /subforce — which route to the same
`Draft.sub_for` — did not, and that is not a race: sub_for is permitted in
CHECK_IN, DRAFT and WAITING_REPORT, and the book stays open for ten minutes,
so it is ordinary operation.

WHAT THAT COST. A spectator who staked on Alpha could be substituted onto
Bravo and would then profit by LOSING — exactly the position Amendment 1 §A
declares impossible ("because a participant can never take the opposing side,
no participant can ever profit by losing"), which is the whole reason letting
players bet was judged safe. They could not correct it either: the composite
primary key on prediction_bets IS the side lock, so the other side is refused.
And `is_player` stays at the value captured when the bet was placed — 0 — so
the post-match report would not even name them as a participant.

Nothing here mocks Draft, and nothing mocks the lifecycle either: the fake
queue channel carries a real Application, bot/wiring.py subscribes the real
handlers to it, and only nammaoe2bot.features.betting' hook is swapped for a recorder. One
test leaves even that alone and drives the REAL restart_for_match, to prove a
book failure cannot break a substitution.

The call is one step further away than it was -- Draft emits `roster_changed`
and wiring's _restart_book answers it -- so these tests are also what pins that
the emit reaches betting at all.

No pytest-asyncio in this repo, so every coroutine is driven with asyncio.run().
"""
from __future__ import annotations

import asyncio
import types

import nammaoe2bot.features.betting as predictions
from nammaoe2bot.pickup.match.substitution import Draft
from bot.wiring import wire_match_lifecycle


# ── the fake match ───────────────────────────────────────────────────────
class FakeTeam(list):
	"""A Team is a list of members that also carries a name and an index —
	which is why Draft can do `player in team` and `team[team.index(p)] = q`."""

	def __init__(self, name, idx, players=()):
		super().__init__(players)
		self.name = name
		self.idx = idx


class FakeMember:
	def __init__(self, uid):
		self.id = uid
		self.mention = f"<@{uid}>"

	def __repr__(self):
		return f"<member {self.id}>"


class FakeQueueChannel:
	def __init__(self):
		self.id = 900
		self.removed = []
		self.rating = types.SimpleNamespace(get_players=self._get_players)
		# Every real QueueChannel carries the Application, and the substitution
		# path reaches live queue state through it (app.remove_players sweeps
		# app.active_queues). A fake without one is a different shape from
		# production and the AttributeError it raises here is the fixture's
		# fault, not the code's.
		from bot.app import Application
		self.app = Application(client=None)

	async def _get_players(self, user_ids):
		return [dict(user_id=uid, rating=1500) for uid in user_ids]

	async def remove_members(self, *members, **_kw):
		self.removed.extend(members)


class FakeCtx:
	def __init__(self):
		self.notices, self.successes = [], []

	async def notice(self, content=None, embed=None):
		self.notices.append(embed if content is None else content)

	async def success(self, content=None, embed=None):
		self.successes.append(embed if content is None else content)


class FakeMatch:
	CHECK_IN, DRAFT, WAITING_REPORT = 0, 1, 2

	def __init__(self, ranked=True, state=DRAFT):
		self.id = 77
		self.ranked = ranked
		self.state = state
		self.states = []
		self.cfg = {"pick_teams": "matchmaking", "team_size": 2, "predictions_enabled": True}
		self.teams = [
			FakeTeam("Alpha", 0, [FakeMember(1), FakeMember(2)]),
			FakeTeam("Bravo", 1, [FakeMember(3), FakeMember(4)]),
			FakeTeam("unpicked", 2, []),
		]
		self.players = [*self.teams[0], *self.teams[1]]
		self.ratings = {}
		self.qc = FakeQueueChannel()
		self.queue = types.SimpleNamespace(queue=[])
		self.embeds = types.SimpleNamespace(
			final_message=lambda: "final-message", draft=lambda: "draft-card")
		self.check_in = types.SimpleNamespace(refresh=self._refresh_check_in)
		self.check_ins = 0
		self.rebalanced = 0
		self.advanced = 0

	async def _refresh_check_in(self):
		self.check_ins += 1

	async def next_state(self, _ctx):
		self.advanced += 1

	def gt(self, text):
		return text

	def init_teams(self, _mode):
		self.rebalanced += 1


def wire(monkeypatch, match):
	"""Subscribe the real features to this match's Application, with betting's
	restart hook swapped for a recorder. Returns the list of matches whose book
	was restarted.

	The swap is on the PACKAGE attribute, not on wiring: wiring._restart_book
	looks the function up at call time, so patching nammaoe2bot.features.betting is what a
	test can reach and what production actually resolves.

	It used to also patch `remove_players`, `active_matches` and `Exc` onto the
	`bot` package shim, because draft.py reached all three through
	bot/__init__.py. It reaches none of them that way now — Exc is a direct
	import, active_matches is Application state, and remove_players is an
	Application method the fake channel's real Application answers."""
	restarted = []

	async def _restart_for_match(m):
		restarted.append(m)

	monkeypatch.setattr(predictions, "restart_for_match", _restart_for_match)
	wire_match_lifecycle(match.qc.app)
	return restarted


def draft_for(match):
	return Draft(match, captains_role_id=None)


# ── /subfor and /subforce ────────────────────────────────────────────────
class TestSubForRestartsTheBook:
	def test_a_swap_under_a_live_book_refunds_and_re_opens_it(self, monkeypatch):
		""" THE DEFECT. sub_for rewrote the roster and made no prediction call
		at all, so the book kept running on teams that no longer existed. """
		match = FakeMatch()
		restarted = wire(monkeypatch, match)
		out_player, sub_in = match.teams[0][1], FakeMember(9)

		asyncio.run(draft_for(match).sub_for(FakeCtx(), out_player, sub_in, force=True))

		assert restarted == [match], "the book was left running on a roster that changed"
		# ...and the swap really happened, so the assertion above is about the
		# book rather than about an early return.
		assert list(match.teams[0]) == [match.teams[0][0], sub_in]
		assert out_player not in match.players and sub_in in match.players

	def test_every_state_the_command_permits_restarts_the_book(self, monkeypatch):
		""" sub_for is legal in CHECK_IN, DRAFT and WAITING_REPORT, and the book
		is open for ten minutes — it can be live in any of them. """
		for state in (FakeMatch.CHECK_IN, FakeMatch.DRAFT, FakeMatch.WAITING_REPORT):
			match = FakeMatch(state=state)
			restarted = wire(monkeypatch, match)
			asyncio.run(draft_for(match).sub_for(
				FakeCtx(), match.teams[1][0], FakeMember(9), force=True))
			assert restarted == [match], f"no book restart in state {state}"

	def test_an_unranked_match_has_no_book_to_restart(self, monkeypatch):
		""" Unranked matches never get a card posted, so there is nothing to
		refund — and the guard has to match the one open_for_match applies. """
		match = FakeMatch(ranked=False)
		restarted = wire(monkeypatch, match)
		asyncio.run(draft_for(match).sub_for(
			FakeCtx(), match.teams[0][0], FakeMember(9), force=True))
		assert restarted == []

	def test_the_substitution_still_completes_when_the_book_fails(self, monkeypatch):
		""" A sub that died half-way would leave the roster inconsistent with the
		queue, so a betting failure must not reach it. Two things now stop it --
		restart_for_match swallows its own errors, and MatchLifecycle.emit
		isolates every handler -- and this drives the whole path with a store
		that raises rather than asserting the property against a stand-in, so it
		holds whichever of the two is doing the work. """
		class _Exploding:
			async def live_for_match(self, _match_id):
				raise RuntimeError("database down")

		monkeypatch.setattr(predictions.flow, "store", _Exploding())
		match = FakeMatch()
		# The real handlers, and nammaoe2bot.features.betting deliberately NOT swapped —
		# wiring's _restart_book calls the genuine restart_for_match, which
		# reaches the store above and raises inside itself.
		wire_match_lifecycle(match.qc.app)
		sub_in = FakeMember(9)
		ctx = FakeCtx()
		asyncio.run(draft_for(match).sub_for(ctx, match.teams[0][0], sub_in, force=True))

		assert sub_in in match.players, "the substitution was rolled back by a betting failure"
		assert ctx.notices == ["draft-card"], "and the draft card was still refreshed"


# ── /subauto, which already did this ─────────────────────────────────────
class TestSubAutoStillRestartsTheBook:
	def test_a_rebalance_refunds_and_re_opens(self, monkeypatch):
		""" The behaviour /subfor was missing, pinned so the pair cannot drift
		apart again. """
		match = FakeMatch()
		restarted = wire(monkeypatch, match)
		match.queue.queue = [FakeMember(9)]
		asyncio.run(draft_for(match).sub_auto(FakeCtx(), match.teams[0][0]))

		assert restarted == [match]
		assert match.rebalanced == 1, "both teams are re-cut, which is why the book cannot survive"

	def test_an_unranked_rebalance_touches_no_book(self, monkeypatch):
		match = FakeMatch(ranked=False)
		restarted = wire(monkeypatch, match)
		match.queue.queue = [FakeMember(9)]
		asyncio.run(draft_for(match).sub_auto(FakeCtx(), match.teams[0][0]))
		assert restarted == []
