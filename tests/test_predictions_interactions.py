"""The bet button handler — the guard chain in front of the gold.

This is the money entry point: every press that ever moves a coin arrives
through `on_bet_interaction`, and until now it had no test at all. The reason
was mechanical rather than deliberate — the module does `import nextcord` at
load, nextcord is not installed in CI, and conftest's nextcord fake was missing
`InteractionType`, which is the very first thing the handler touches. So the
whole file was unreachable from pytest. conftest now stubs those five wire
constants (and `ui`/`ButtonStyle`, which bot/predictions/embeds.py imports at
module level), which unblocks `bot/quiz/interactions.py` too.

Nothing here mocks the function under test. The handler runs for real against a
fake interaction and fake collaborators; every assertion is on what the user is
told, which side of the transaction the failure landed on, and whether the bank
was touched at all. `flow._team_ids` in particular is NOT stubbed — the
own-team-only rule is binding, so the tests drive the real lookup against a
real-shaped `bot.active_matches`, three-entry `teams` list and all.

No pytest-asyncio in this repo: every coroutine is driven with asyncio.run().
"""
from __future__ import annotations

import asyncio
import math
import sys
import types

import nextcord

from bot.predictions import interactions, view

ALL_SENDS = math.inf            # every send fails, for as long as the test runs


# ── the fake Discord side ────────────────────────────────────────────────
class FakeResponse:
	""" nextcord's InteractionResponse, in the two states the handler branches
	on: not-yet-answered (send_message) and already-answered (followup). The
	one-shot rule is real — Discord rejects a second response to the same
	interaction — so `is_done()` flips on the first send and the handler's
	`if not interaction.response.is_done()` has something true to test.

	`fail_sends` counts down the sends that raise instead of landing, because
	the interesting failures are the PARTIAL ones. Failing everything hides the
	answer: to see which notice the catch-all picked, the delivery that drives
	the handler into the catch-all has to fail while the catch-all's own reply
	is allowed through. Set it to ALL_SENDS for the nothing-gets-out case. """

	def __init__(self):
		self.sent = []          # (text, ephemeral, view) — NEW ephemerals
		self.edits = []         # (text, view) — rewrites of the pressed message
		self._done = False
		self.fail_sends = 0

	def is_done(self):
		return self._done

	async def send_message(self, content=None, ephemeral=False, view=nextcord.utils.MISSING, **_kw):
		if self.fail_sends > 0:
			self.fail_sends -= 1
			raise RuntimeError("Discord 503 — interaction response failed")
		_check_view(view)
		self._done = True
		self.sent.append((content, ephemeral, view))

	async def edit_message(self, content=None, view=nextcord.utils.MISSING, **_kw):
		""" nextcord 2.6.0's InteractionResponse.edit_message — the
		component-interaction UPDATE_MESSAGE response, which rewrites the
		message the pressed component lives on.

		Recorded in its OWN list, because "rewrote the confirmation" and "sent
		a second ephemeral next to it" are the two behaviours Amendment 1 §B
		distinguishes, and a fake that merged them could not tell a test which
		one happened.

		`view=None` is accepted rather than dereferenced — the opposite of
		send_message above, and faithful: edit_message declares
		`view: Optional[View]` and maps None to `payload["components"] = []`,
		which is how the button is stripped. """
		if self.fail_sends > 0:
			self.fail_sends -= 1
			raise RuntimeError("Discord 503 — interaction response failed")
		self._done = True
		self.edits.append((content, view))


class FakeFollowup:
	def __init__(self, response):
		self._response = response

	async def send(self, content=None, ephemeral=False, view=nextcord.utils.MISSING, **_kw):
		_check_view(view)
		self._response.sent.append((content, ephemeral, view))


def _check_view(view):
	""" Faithful to nextcord 2.6.0's InteractionResponse.send_message /
	Webhook.send: both do `if view is not MISSING: view.to_components()` (or
	`view.timeout`), so an explicit `view=None` — a real, different value from
	"omitted" — dereferences None. A fake that swallowed `view` through
	`**_kw` would never notice a caller doing that; this is what caught task
	14's regression (_eph defaulting to `view=None` broke every ephemeral in
	the module that didn't pass a view). """
	if view is None:
		raise AttributeError("'NoneType' object has no attribute 'to_components'")


class FakeInteraction:
	COMPONENT = 3               # Discord's own wire value; see conftest

	def __init__(self, custom_id="bet:12:0:50", user_id=7, itype=COMPONENT):
		self.type = itype
		self.data = {"custom_id": custom_id}
		self.user = types.SimpleNamespace(id=user_id, display_name="Zed", name="zed")
		self.response = FakeResponse()
		self.followup = FakeFollowup(self.response)

	@property
	def replies(self):
		return [text for text, _ephemeral, _view in self.response.sent]

	@property
	def reply(self):
		assert len(self.response.sent) == 1, f"expected exactly one reply, got {self.response.sent}"
		return self.response.sent[0][0]

	@property
	def reply_view(self):
		""" The view attached to the single reply, or MISSING if none was. """
		assert len(self.response.sent) == 1, f"expected exactly one reply, got {self.response.sent}"
		return self.response.sent[0][2]

	@property
	def all_ephemeral(self):
		return all(ephemeral for _text, ephemeral, _view in self.response.sent)

	@property
	def edit(self):
		""" The text the pressed message was rewritten to — and there must be
		exactly one rewrite, because edit_message is a RESPONSE. """
		assert len(self.response.edits) == 1, f"expected exactly one rewrite, got {self.response.edits}"
		return self.response.edits[0][0]

	@property
	def edit_view(self):
		assert len(self.response.edits) == 1, f"expected exactly one rewrite, got {self.response.edits}"
		return self.response.edits[0][1]


class RecordingLog:
	""" Real assertions need to know whether the handler took a branch or CRASHED
	into one. Every guard below is expected to log nothing at all. """

	def __init__(self):
		self.errors, self.warnings, self.infos = [], [], []

	def error(self, data):
		self.errors.append(str(data))

	def warning(self, data):
		self.warnings.append(str(data))

	def info(self, data):
		self.infos.append(str(data))


class Wiring:
	""" What wire() hands back: the log the handler wrote to, and the bank calls
	it made. `errors`/`warnings`/`infos` read straight through to the log, so
	every `log = wire(...)` call site below still reads as it always did, and
	`placed` is what the own-team tests need — a press that is refused must
	leave it empty, and one that is allowed must carry the is_player flag the
	post-match report is later rendered from. """

	def __init__(self, log):
		self.log = log
		self.placed = []            # one dict per gold.place_bet call
		self.cancelled = []         # one dict per gold.cancel_bet call

	@property
	def errors(self):
		return self.log.errors

	@property
	def warnings(self):
		return self.log.warnings

	@property
	def infos(self):
		return self.log.infos


# ── the fixtures the handler reads ───────────────────────────────────────
OPEN_POST = dict(
	id=12, channel_id=900, match_id=77, message_id=None,
	team0_name="Alpha", team1_name="Bravo",
	opened_at=1_000, freezes_at=9_999_999_999, status="open")

EXPLODE = object()              # "this collaborator must not be reached"
_DEFAULT_POST = object()        # distinct from `the_post=None`, which means "deleted"


def post(**overrides):
	p = dict(OPEN_POST)
	p.update(overrides)
	return p


def wire(monkeypatch, *, the_post=_DEFAULT_POST, team0=(), team1=(), unpicked=(),
		 community_id=5, place_bet=("ok", 440), seeded=False, bets=(),
		 get_post_raises=None, bets_raise=None, bank=None,
		 cancel_bet=("nothing", 0), post_frozen=False):
	"""Stand the handler's collaborators up. Returns the Wiring record.

	`the_post=None` is a real value here — store.get_post answers None, i.e. the
	post is gone — which is why the default is a sentinel and not None.

	`team0`/`team1`/`unpicked` populate the live match's roster. They default to
	empty, i.e. every user is a spectator, which is what every test that is not
	about the roster wants.

	`bank=EXPLODE` makes both gold entry points raise: a guard that is supposed
	to reject a press before any money moves has to prove it never reached the
	bank, and an AssertionError from there surfaces as BOTH a logged error and
	the wrong message to the user, so no guard test can pass through it.
	"""
	log = RecordingLog()
	wiring = Wiring(log)
	monkeypatch.setattr(interactions, "log", log)
	the_post = post() if the_post is _DEFAULT_POST else the_post
	if post_frozen and the_post is not None:
		# status stays 'open' — the sweep hasn't run yet — but the deadline has
		# passed, exercising the `now >= freezes_at` clause specifically rather
		# than the `status != 'open'` one right next to it.
		the_post = dict(the_post, freezes_at=1)

	async def _get_post(_post_id):
		if get_post_raises is not None:
			raise get_post_raises
		return the_post

	async def _bets_for(_post_id):
		if bets_raise is not None:
			raise bets_raise
		return list(bets)

	monkeypatch.setattr(interactions, "store", types.SimpleNamespace(
		get_post=_get_post, bets_for=_bets_for))

	async def _ensure_seeded(_community_id, _user_id, _now):
		if bank is EXPLODE:
			raise AssertionError("ensure_seeded reached on a press that must be refused")
		return seeded

	async def _place_bet(_community_id, user_id, _post_id, side, stake, _nick, _now,
						 is_player=False):
		if bank is EXPLODE:
			raise AssertionError("place_bet reached on a press that must be refused")
		wiring.placed.append(dict(user_id=user_id, side=side, stake=stake, is_player=is_player))
		if isinstance(place_bet, Exception):
			raise place_bet
		return place_bet

	async def _cancel_bet(_community_id, user_id, post_id, _now):
		if bank is EXPLODE:
			raise AssertionError("cancel_bet reached on a press that must be refused")
		wiring.cancelled.append(dict(user_id=user_id, post_id=post_id))
		if isinstance(cancel_bet, Exception):
			raise cancel_bet
		return cancel_bet

	async def _balance(_community_id, _user_id):
		if bank is EXPLODE:
			raise AssertionError("balance reached on a press that must be refused")
		return 999

	monkeypatch.setattr(interactions, "gold", types.SimpleNamespace(
		ensure_seeded=_ensure_seeded, place_bet=_place_bet,
		cancel_bet=_cancel_bet, balance=_balance))

	async def _community_for_channel(_channel_id):
		return community_id

	# `from bot import community` resolves the package attribute before it tries
	# to import the submodule, so setting it here keeps the real community
	# module (and its DB access) out of the way.
	monkeypatch.setattr(sys.modules["bot"], "community",
						types.SimpleNamespace(community_for_channel=_community_for_channel),
						raising=False)
	# What flow._team_ids actually walks. Real function, real shape — including
	# the THIRD entry in `teams`, the "unpicked" pseudo-team with idx=-1, which
	# is exactly the trap a guard that iterates match.teams falls into.
	def _roster(uids):
		return [types.SimpleNamespace(id=uid) for uid in uids]

	matches = [] if the_post is None else [
		types.SimpleNamespace(
			id=the_post["match_id"],
			players=_roster([*team0, *team1, *unpicked]),
			teams=[_roster(team0), _roster(team1), _roster(unpicked)])]
	monkeypatch.setattr(sys.modules["bot"], "active_matches", matches, raising=False)
	return wiring


def run(interaction):
	asyncio.run(interactions.on_bet_interaction(interaction))
	return interaction


# ── foreign interactions fall straight through ───────────────────────────
class TestRouting:
	def test_a_non_component_interaction_is_ignored(self, monkeypatch):
		wire(monkeypatch, bank=EXPLODE)
		i = run(FakeInteraction(itype=2))       # application command
		assert i.replies == []

	def test_a_foreign_custom_id_is_ignored(self, monkeypatch):
		""" The quiz router and the insights router run off the same event. A bet
		handler that answered their clicks would double-respond. """
		wire(monkeypatch, bank=EXPLODE)
		assert run(FakeInteraction(custom_id="quiz:88:reveal")).replies == []

	def test_a_forged_stake_never_reaches_the_bank(self, monkeypatch):
		""" custom_ids come from the client. 250 is not a tier, so the route does
		not parse and the press is not even acknowledged. """
		log = wire(monkeypatch, bank=EXPLODE)
		assert run(FakeInteraction(custom_id="bet:12:0:250")).replies == []
		assert log.errors == []


# ── the closed book ──────────────────────────────────────────────────────
class TestBookIsClosed:
	CLOSED = "Betting on this match is closed."

	def test_a_frozen_post_is_refused(self, monkeypatch):
		log = wire(monkeypatch, the_post=post(status="frozen"), bank=EXPLODE)
		i = run(FakeInteraction())
		assert i.reply == self.CLOSED
		assert i.all_ephemeral
		assert log.errors == []

	def test_a_post_past_its_freeze_time_is_refused(self, monkeypatch):
		""" status is still 'open' here — the freeze sweep runs every 15s, so a
		press in that window has to be caught by the clock, not the status. """
		log = wire(monkeypatch, the_post=post(freezes_at=1_000), bank=EXPLODE)
		assert run(FakeInteraction()).reply == self.CLOSED
		assert log.errors == []

	def test_a_post_that_no_longer_exists_is_refused(self, monkeypatch):
		log = wire(monkeypatch, the_post=None, bank=EXPLODE)
		assert run(FakeInteraction()).reply == self.CLOSED
		assert log.errors == []

	def test_the_bank_closing_the_book_under_the_press_is_the_same_refusal(self, monkeypatch):
		""" The gate above reads the post ONCE, at the top of this handler; a
		freeze / void / settle sweep can flip it a moment later, while the press
		is still in flight. place_bet re-reads that row FOR UPDATE inside the
		transaction and answers ('closed', None) when it lost the race — the
		whole transaction rolls back, nothing is charged, and the user has to be
		told the same thing the early gate tells them rather than seeing a
		crash notice for a bet that was correctly refused. """
		log = wire(monkeypatch, place_bet=("closed", None),
				   bets_raise=AssertionError("kept processing after a refused bet"))
		i = run(FakeInteraction())
		assert i.reply == self.CLOSED
		assert i.all_ephemeral
		assert log.errors == []

	def test_a_press_the_bank_closed_is_not_treated_as_charged(self, monkeypatch):
		""" Nothing moved, so a failure to deliver that refusal is back to
		"nothing was charged" — the notice that invites a retry, correctly. """
		wire(monkeypatch, place_bet=("closed", None))
		i = FakeInteraction()
		i.response.fail_sends = 1
		run(i)
		assert i.reply == interactions.BET_FAILED_NOTICE


# ── own team only ────────────────────────────────────────────────────────
class TestOwnTeamOnly:
	""" Amendment 1 supersedes the spectators-only rule: a participant may bet,
	but only on the side they are actually playing. Driven through the real
	flow._team_ids. """

	def test_a_player_may_back_their_own_team(self, monkeypatch):
		""" The whole point of the amendment: a participant's gold can join the
		pool on the side they are actually playing. """
		bank = wire(monkeypatch, team0={7}, team1={8})
		i = FakeInteraction(user_id=7, custom_id="bet:12:0:50")
		run(i)
		assert bank.placed, "the bet never reached the bank"
		assert bank.placed[-1]["is_player"] is True

	def test_a_player_backing_the_other_team_is_refused(self, monkeypatch):
		""" A participant must never be able to hold a position against
		themselves — that is the only reason letting players bet is safe. """
		bank = wire(monkeypatch, team0={7}, team1={8})
		i = FakeInteraction(user_id=7, custom_id="bet:12:1:50")
		run(i)
		assert not bank.placed, "the bank was reached on a forbidden side"
		assert "only bet on yourself" in i.reply

	def test_a_spectator_may_back_either_side(self, monkeypatch):
		for side in (0, 1):
			bank = wire(monkeypatch, team0={7}, team1={8})
			i = FakeInteraction(user_id=99, custom_id=f"bet:12:{side}:50")
			run(i)
			assert bank.placed, f"spectator refused on side {side}"
			assert bank.placed[-1]["is_player"] is False

	def test_the_unpicked_pseudo_team_is_not_a_side(self, monkeypatch):
		""" Match.teams[2] is 'unpicked'. Someone sitting there is not playing
		either side and must be treated as a spectator, not silently blocked. """
		bank = wire(monkeypatch, team0={7}, team1={8}, unpicked={42})
		i = FakeInteraction(user_id=42, custom_id="bet:12:1:50")
		run(i)
		assert bank.placed
		assert bank.placed[-1]["is_player"] is False

	def test_the_refusal_is_a_decision_not_a_crash(self, monkeypatch):
		""" bank=EXPLODE turns any leak past the guard into a logged error and
		the wrong message, so neither assertion here can pass by accident. """
		log = wire(monkeypatch, team0={7}, team1={8}, bank=EXPLODE)
		i = run(FakeInteraction(user_id=8, custom_id="bet:12:0:100"))
		assert "only bet on yourself" in i.reply
		assert i.all_ephemeral
		assert log.errors == []


# ── the community gate ───────────────────────────────────────────────────
def test_a_channel_with_no_community_has_no_gold(monkeypatch):
	log = wire(monkeypatch, community_id=None, bank=EXPLODE)
	i = run(FakeInteraction())
	assert i.reply == "This channel keeps no stats — there is no gold here."
	assert log.errors == []


# ── what the bank answers ────────────────────────────────────────────────
class TestBankRejections:
	def test_insufficient_gold_reports_the_balance_and_stops(self, monkeypatch):
		""" `bets_raise` proves the stop: a handler that carried on to build a
		confirmation would hit it, and both assertions below would fail. """
		log = wire(monkeypatch, place_bet=("insufficient", 30),
				   bets_raise=AssertionError("kept processing after a refused bet"))
		i = run(FakeInteraction())
		assert "Not enough gold" in i.reply and "**30**" in i.reply
		assert "Playing matches tops you back up." in i.reply
		assert i.all_ephemeral
		assert log.errors == []

	def test_the_other_side_press_names_the_side_already_locked(self, monkeypatch):
		""" place_bet answers ('side_locked', <locked side index>), and the
		message has to name the team the user is ON, not the one they pressed.
		The press here is side 1 (Bravo) while the lock is side 0 (Alpha). """
		log = wire(monkeypatch, place_bet=("side_locked", 0),
				   bets_raise=AssertionError("kept processing after a refused bet"))
		i = run(FakeInteraction(custom_id="bet:12:1:50"))
		assert "You're on **Alpha** this match" in i.reply
		assert "Bravo" not in i.reply
		assert log.errors == []

	def test_a_lock_on_the_second_team_names_that_one(self, monkeypatch):
		wire(monkeypatch, place_bet=("side_locked", 1),
			 bets_raise=AssertionError("kept processing after a refused bet"))
		assert "You're on **Bravo** this match" in run(FakeInteraction()).reply


# ── the happy path ───────────────────────────────────────────────────────
class TestConfirmation:
	def test_a_placed_bet_confirms_side_stake_pools_and_balance(self, monkeypatch):
		log = wire(monkeypatch, place_bet=("ok", 440), bets=[
			dict(user_id=7, nick="Zed", side=0, stake=50),
			dict(user_id=8, nick="Ada", side=1, stake=100)])
		i = run(FakeInteraction(custom_id="bet:12:0:50", user_id=7))
		assert "Bet **50**" in i.reply
		assert "on **Alpha**" in i.reply
		assert "Pools: 50 vs 100" in i.reply
		assert "Your balance: **440**" in i.reply
		assert i.all_ephemeral, "a bet confirmation is private"
		assert log.errors == []
		# The confirmation carries a live Cancel button — task 14's whole
		# user-facing point — routable back through the real parser, not a
		# custom_id that merely looks right.
		from bot.predictions.scoring import parse_cancel_custom_id
		assert i.reply_view is not None and i.reply_view is not interactions.nextcord.utils.MISSING
		assert [b.custom_id for b in i.reply_view.children] == ["betcancel:12"]
		assert [parse_cancel_custom_id(b.custom_id) for b in i.reply_view.children] == [12]
		assert i.reply_view.timeout is None and i.reply_view.auto_defer is False

	def test_an_added_stake_shows_the_running_total(self, monkeypatch):
		""" Presses are additive: this one staked 50 on top of an earlier 10. """
		wire(monkeypatch, place_bet=("ok", 380), bets=[
			dict(user_id=7, nick="Zed", side=0, stake=60)])
		assert "(your total: 60)" in run(FakeInteraction(custom_id="bet:12:0:50")).reply

	def test_a_first_time_bettor_is_welcomed_before_the_confirmation(self, monkeypatch):
		wire(monkeypatch, seeded=True, place_bet=("ok", 450), bets=[
			dict(user_id=7, nick="Zed", side=0, stake=50)])
		lines = run(FakeInteraction()).reply.split("\n")
		assert lines[0].startswith("Welcome to the betting floor")
		assert "500" in lines[0]
		assert "Bet **50**" in lines[1]

	def test_a_returning_bettor_is_not_welcomed_again(self, monkeypatch):
		wire(monkeypatch, seeded=False, place_bet=("ok", 450), bets=[
			dict(user_id=7, nick="Zed", side=0, stake=50)])
		assert "Welcome to the betting floor" not in run(FakeInteraction()).reply


# ── the point of no return ───────────────────────────────────────────────
class TestFailureAfterTheCharge:
	""" gold.place_bet commits the deduction, the bet row and the ledger row
	together, so once it answers 'ok' the money HAS moved. Everything after it —
	re-reading the pools, rendering the confirmation, the Discord round-trip —
	can still fail, and the outer except is the only thing the user hears from.

	It used to say "nothing was charged if you didn't get a confirmation. Try
	again." on both sides of that commit. On this side the sentence is false and
	the instruction bills the user twice. """

	def test_a_failure_before_the_charge_still_reassures(self, monkeypatch):
		""" The message that was always right, on the path where it is right. """
		log = wire(monkeypatch, get_post_raises=RuntimeError("db blip"), bank=EXPLODE)
		i = run(FakeInteraction())
		assert "nothing was charged" in i.reply
		assert "Try again." in i.reply
		assert i.all_ephemeral
		assert len(log.errors) == 1

	def test_a_failure_after_the_charge_says_the_bet_landed(self, monkeypatch):
		""" The mirror of the rejection tests below: place_bet answered 'ok', so
		the money HAS moved and the notice must be the one that says so. Pinned
		against the module's own constants — a `charged` flag stuck on False
		would deliver the other one and fail here. """
		log = wire(monkeypatch, place_bet=("ok", 440),
				   bets_raise=RuntimeError("db blip reading the pools back"))
		i = run(FakeInteraction())
		assert i.reply == interactions.BET_LANDED_NOTICE
		assert i.reply != interactions.BET_FAILED_NOTICE
		assert i.all_ephemeral
		assert len(log.errors) == 1, "the underlying failure is still reported to the log"

	def test_a_failure_after_the_charge_never_invites_a_retry(self, monkeypatch):
		""" THE REGRESSION. A user who follows "try again" after a committed bet
		is charged a second time — no refund path, no cancel, tote rules. """
		wire(monkeypatch, place_bet=("ok", 440),
			 bets_raise=RuntimeError("db blip reading the pools back"))
		reply = run(FakeInteraction()).reply.lower()
		assert "nothing was charged" not in reply
		assert "try again" not in reply
		assert "don't press again" in reply

	def test_the_two_notices_disagree_about_the_only_thing_that_matters(self):
		""" Pins the pair rather than one string: whatever the copy becomes, the
		before-notice may promise no charge and the after-notice may not. """
		assert "nothing was charged" in interactions.BET_FAILED_NOTICE
		assert "nothing was charged" not in interactions.BET_LANDED_NOTICE
		assert "Try again" in interactions.BET_FAILED_NOTICE
		assert "try again" not in interactions.BET_LANDED_NOTICE.lower()

	def test_a_rejected_bet_is_not_treated_as_charged(self, monkeypatch):
		""" The flag must follow the transaction, not the attempt. place_bet
		rolls the deduction back on 'insufficient', so a later failure on that
		path is back to "nothing was charged".

		Only the FIRST send fails — the refusal, which is what drops the handler
		into the catch-all — and the catch-all's own reply lands, so the notice
		it chose is visible. Failing every send would hide it and the test would
		pass with `charged` hardwired True. """
		wire(monkeypatch, place_bet=("insufficient", 30))
		i = FakeInteraction()
		i.response.fail_sends = 1               # the refusal itself cannot be delivered
		run(i)
		assert i.reply == interactions.BET_FAILED_NOTICE
		assert i.reply != interactions.BET_LANDED_NOTICE
		assert i.all_ephemeral

	def test_a_side_locked_press_is_not_treated_as_charged(self, monkeypatch):
		""" The other rolled-back path. place_bet undoes the deduction here too,
		so a press refused for being on the locked side and then failing to
		deliver that refusal is still an uncharged user. """
		wire(monkeypatch, place_bet=("side_locked", 0))
		i = FakeInteraction(custom_id="bet:12:1:50")
		i.response.fail_sends = 1
		run(i)
		assert i.reply == interactions.BET_FAILED_NOTICE
		assert i.reply != interactions.BET_LANDED_NOTICE
		assert i.all_ephemeral

	def test_the_handler_never_raises_even_when_it_cannot_speak(self, monkeypatch):
		""" Self-isolating: this runs inside bot/events.py's on_interaction, ahead
		of nothing but after every other router — an exception escaping here is a
		traceback in the event loop for a button press. """
		log = wire(monkeypatch, place_bet=("ok", 440),
				   bets_raise=RuntimeError("db blip"))
		i = FakeInteraction()
		i.response.fail_sends = ALL_SENDS
		run(i)                                   # must not raise
		assert i.replies == []
		assert len(log.errors) == 1


# ── the cancel button ────────────────────────────────────────────────────
class TestCancelRoute:
	def test_cancelling_refunds_and_rewrites_the_private_message(self, monkeypatch):
		bank = wire(monkeypatch, cancel_bet=("ok", 60))
		i = FakeInteraction(user_id=99, custom_id="betcancel:12")
		run(i)
		# Identity: it is THIS user's bet on THIS post that got cancelled — not
		# the (post_id, user_id) pair transposed into the bank's call.
		assert bank.cancelled == [dict(user_id=99, post_id=12)]
		# The refund figure and the balance must land in their own slots — a
		# transposition (999 returned, 60 balance) reads as plausible prose and
		# was surviving on "60" in the reply alone.
		assert i.edit == "\n".join(view.bet_cancelled_lines(60, 999))

	def test_the_press_rewrites_its_own_confirmation_and_strips_the_button(self, monkeypatch):
		""" Amendment 1 §B, literally: "Pressing it rewrites that same private
		message into 'Bet cancelled — N gold returned' and strips the button."
		A NEW ephemeral instead leaves the original confirmation sitting on
		screen still advertising a live Cancel for a bet that no longer exists
		— and the next press of it answers "you have no bet on this match to
		cancel", which reads as a failure rather than as history. """
		wire(monkeypatch, cancel_bet=("ok", 60))
		i = FakeInteraction(user_id=99, custom_id="betcancel:12")
		run(i)
		assert i.response.sent == [], "a second ephemeral is not a rewrite"
		assert "cancelled" in i.edit.lower()
		assert i.edit_view is None, "the button is stripped, not left live"

	def test_cancelling_after_the_freeze_is_refused(self, monkeypatch):
		bank = wire(monkeypatch, cancel_bet=("ok", 60), post_frozen=True)
		i = FakeInteraction(user_id=99, custom_id="betcancel:12")
		run(i)
		assert not bank.cancelled, "gold was returned after the book locked"
		# AND THE USER IS ANSWERED. Without this the test passed for a guard
		# that simply returned, leaving the press hanging with no response of
		# any kind — Discord shows "This interaction failed" and the bettor is
		# left believing their gold is still staked.
		assert "closed" in i.edit.lower()
		assert i.edit_view is None, "a dead button comes off with the refusal"
		assert bank.errors == []

	def test_cancelling_with_no_bet_says_so_and_moves_no_gold(self, monkeypatch):
		wire(monkeypatch, cancel_bet=("nothing", 0))
		i = FakeInteraction(user_id=99, custom_id="betcancel:12")
		run(i)
		assert "no bet" in i.edit.lower()
		assert i.edit_view is None

	def test_a_channel_with_no_community_is_answered_too(self, monkeypatch):
		log = wire(monkeypatch, community_id=None, bank=EXPLODE)
		i = FakeInteraction(user_id=99, custom_id="betcancel:12")
		run(i)
		assert i.edit == "This channel keeps no stats — there is no gold here."
		assert log.errors == []

	def test_cancelling_refreshes_the_public_card(self, monkeypatch):
		""" The pools shown on the open card change the instant a bet is
		cancelled, so a successful cancel must still read them back with
		store.bets_for. Proven by making bets_for raise: if the post-cancel
		`bets_for`/`pools`/`_refresh_card` block were ever deleted (as the
		brief explicitly warns against), this exception would never fire and
		the log would stay clean instead. """
		log = wire(monkeypatch, cancel_bet=("ok", 60),
				   bets_raise=RuntimeError("bets_for reached after cancel"))
		i = FakeInteraction(user_id=99, custom_id="betcancel:12")
		run(i)
		assert len(log.errors) == 1
		assert "bets_for reached after cancel" in log.errors[0]
		# The refresh is best-effort and must not cost the user their reply —
		# the cancellation confirmation itself still has to have gone out.
		assert "cancel" in i.edit.lower()
		assert i.response.sent == [], "and no crash notice on top of a delivered reply"

	def test_a_foreign_custom_id_is_ignored(self, monkeypatch):
		bank = wire(monkeypatch)
		i = FakeInteraction(user_id=99, custom_id="quiz:12:reveal")
		run(i)
		assert not bank.cancelled and not bank.placed

	def test_the_bank_closing_the_book_under_the_press_is_refused_not_refunded(self, monkeypatch):
		""" The fast-path status/deadline check above can pass on a stale read —
		status still 'open', now still before freezes_at — while gold.cancel_bet's
		own FOR UPDATE read inside its transaction sees the freeze sweep already
		won the race. Its ('closed', 0) answer is authoritative: no bet row was
		deleted, no ledger row written, no gold moved. The handler must tell the
		user the book is closed, NOT run bet_cancelled_lines and claim a refund of
		zero gold. bets_raise proves the handler actually stops here instead of
		falling through to the balance/bets_for/_refresh_card tail — if the
		'closed' branch were ever deleted, that tail would run and either raise
		(logged) or, worse, report success. """
		bank = wire(monkeypatch, cancel_bet=("closed", 0),
					bets_raise=AssertionError("kept processing after a closed cancel"))
		i = FakeInteraction(user_id=99, custom_id="betcancel:12")
		run(i)
		assert bank.cancelled, "gold.cancel_bet was never called"
		assert "closed" in i.edit.lower()
		assert "returned" not in i.edit.lower(), "the 0-gold refund message must not be used"
		assert bank.errors == []


# ── the point of no return, on the cancel side ───────────────────────────
class TestFailureAfterTheRefund:
	""" gold.cancel_bet commits the row delete, the ledger row and the balance
	credit together, so once it answers 'ok' the gold IS back. The catch-all
	the cancel route used to fall into was the BET path's, whose `charged`
	flag is False for every cancel — so a failure on this side of that commit
	told the user "nothing was charged... Try again": false in both halves,
	and it leaves them believing a stake is still riding on the match. """

	def test_a_failure_before_the_refund_still_reassures(self, monkeypatch):
		log = wire(monkeypatch, get_post_raises=RuntimeError("db blip"), bank=EXPLODE)
		i = FakeInteraction(user_id=99, custom_id="betcancel:12")
		run(i)
		assert i.reply == interactions.CANCEL_FAILED_NOTICE
		assert i.all_ephemeral
		assert len(log.errors) == 1

	def test_a_failure_after_the_refund_says_the_gold_is_back(self, monkeypatch):
		""" Only the FIRST delivery fails — the rewrite, which is what drops the
		handler into the catch-all — so the catch-all's own reply lands and the
		notice it chose is visible. Failing every send would hide it and this
		would pass with `refunded` hardwired either way. """
		log = wire(monkeypatch, cancel_bet=("ok", 60))
		i = FakeInteraction(user_id=99, custom_id="betcancel:12")
		i.response.fail_sends = 1
		run(i)
		assert i.reply == interactions.CANCEL_LANDED_NOTICE
		assert i.reply != interactions.CANCEL_FAILED_NOTICE
		assert i.reply != interactions.BET_FAILED_NOTICE, "the bet path's notice is about a charge"
		assert len(log.errors) == 1

	def test_a_failure_after_the_refund_never_claims_nothing_moved(self, monkeypatch):
		""" THE REGRESSION. A bettor told "nothing was charged" over a refund
		that already landed believes their stake is still on the match. """
		wire(monkeypatch, cancel_bet=("ok", 60))
		i = FakeInteraction(user_id=99, custom_id="betcancel:12")
		i.response.fail_sends = 1
		run(i)
		reply = i.reply.lower()
		assert "nothing was charged" not in reply
		assert "untouched" not in reply
		assert "try again" not in reply

	def test_a_refused_cancel_is_not_treated_as_refunded(self, monkeypatch):
		""" The flag follows the transaction, not the attempt: 'nothing' and
		'closed' both move no gold at all. """
		for answer in (("nothing", 0), ("closed", 0)):
			wire(monkeypatch, cancel_bet=answer)
			i = FakeInteraction(user_id=99, custom_id="betcancel:12")
			i.response.fail_sends = 1
			run(i)
			assert i.reply == interactions.CANCEL_FAILED_NOTICE, answer

	def test_the_two_cancel_notices_disagree_about_the_only_thing_that_matters(self):
		""" Pins the pair rather than one string: whatever the copy becomes, the
		before-notice may promise the stake is untouched and the after-notice
		may not, and only the before-notice may invite a retry. """
		assert "untouched" in interactions.CANCEL_FAILED_NOTICE
		assert "untouched" not in interactions.CANCEL_LANDED_NOTICE
		assert "Try again" in interactions.CANCEL_FAILED_NOTICE
		assert "try again" not in interactions.CANCEL_LANDED_NOTICE.lower()
		assert "already back" in interactions.CANCEL_LANDED_NOTICE

	def test_the_cancel_route_never_raises_even_when_it_cannot_speak(self, monkeypatch):
		""" Self-isolating, like the bet route: this runs inside bot/events.py's
		on_interaction, and an exception escaping is a traceback in the event
		loop for a button press. """
		log = wire(monkeypatch, cancel_bet=("ok", 60))
		i = FakeInteraction(user_id=99, custom_id="betcancel:12")
		i.response.fail_sends = ALL_SENDS
		run(i)                                   # must not raise
		assert i.replies == []
		assert len(log.errors) == 1
