"""The betting lifecycle — freeze, resolve, void — driven for real.

tests/test_predictions_wiring.py pins the SHAPE of bot/predictions/flow.py (what
the package exports, which object the call sites hold). This file pins what the
lifecycle DOES now that a book of gold, rather than a set of reactions, is the
thing being settled:

* freeze reads prediction_bets, never the message's reactions;
* a one-sided book is 'no_action' with every stake refunded, not a frozen post
  with impossible odds;
* resolve pays the winners out of the whole pot and grants the playing faucet
  independently of any post;
* every terminal no-settle path (draw, cancelled match, teams rebalanced) hands
  the gold back exactly once.

Nothing here mocks the function under test — store, gold and the Discord side
are fakes, the flow is real. No pytest-asyncio in this repo, so every coroutine
is driven with asyncio.run().
"""
from __future__ import annotations

import asyncio
import sys
import types

import bot.predictions.flow as flow


# ── the fake Discord side ────────────────────────────────────────────────
class FakeMessage:
	def __init__(self):
		self.edits = []             # (embed, view)

	async def edit(self, embed=None, view="<untouched>", **_kw):
		self.edits.append((embed, view))


class FakeChannel:
	def __init__(self):
		self.message = FakeMessage()
		self.sent = []              # embeds announced to the channel

	async def fetch_message(self, _message_id):
		return self.message

	async def send(self, embed=None, **_kw):
		self.sent.append(embed)
		return types.SimpleNamespace(id=555)


class FakeBank:
	"""Records every movement the flow asks for, in order. Refunds and payouts
	are keyed by post so a double-settle would show up as two entries."""

	def __init__(self):
		self.refunds = []           # (community_id, [user_ids], post_id)
		self.payouts = []           # (community_id, {user_id: amount}, post_id)
		self.seeded = []            # (community_id, user_id)
		self.rewards = []           # (community_id, user_id, match_id)

	async def refund_post(self, community_id, bets, post_id, _now):
		self.refunds.append((community_id, [b["user_id"] for b in bets], post_id))
		return len(bets)

	async def pay_post(self, community_id, paid, post_id, _now):
		self.payouts.append((community_id, dict(paid), post_id))
		return len(paid)

	async def ensure_seeded(self, community_id, user_id, _now):
		self.seeded.append((community_id, user_id))
		return False

	async def grant_match_reward(self, community_id, user_id, match_id, _now):
		self.rewards.append((community_id, user_id, match_id))
		return 10


class FakeStore:
	def __init__(self, bets=(), post=None):
		self._bets = list(bets)
		self._post = post
		self.frozen = []            # (post_id, bettors0, bettors1)
		self.no_actioned = []
		self.voided = []
		self.resolved = []          # (post_id, winner_idx)

	async def bets_for(self, _post_id):
		return list(self._bets)

	async def live_for_match(self, _match_id):
		return self._post

	async def freeze(self, post_id, votes0, votes1):
		self.frozen.append((post_id, votes0, votes1))

	async def no_action(self, post_id, _now):
		self.no_actioned.append(post_id)

	async def void(self, post_id, _now):
		self.voided.append(post_id)

	async def resolve(self, post_id, winner_idx, _now):
		self.resolved.append((post_id, winner_idx))


class RecordingLog:
	def __init__(self):
		self.errors, self.warnings, self.infos = [], [], []

	def error(self, data):
		self.errors.append(str(data))

	def warning(self, data):
		self.warnings.append(str(data))

	def info(self, data):
		self.infos.append(str(data))


POST = dict(
	id=12, channel_id=900, match_id=77, message_id=4242,
	team0_name="Alpha", team1_name="Bravo",
	opened_at=1_000, freezes_at=1_600, status="open")

TWO_SIDED = [
	dict(user_id=1, nick="anu", side=0, stake=150),
	dict(user_id=2, nick="bala", side=0, stake=50),
	dict(user_id=3, nick="chetan", side=1, stake=100),
]

ONE_SIDED = [
	dict(user_id=1, nick="anu", side=0, stake=150),
	dict(user_id=2, nick="bala", side=0, stake=50),
]


def post(**overrides):
	p = dict(POST)
	p.update(overrides)
	return p


def wire(monkeypatch, *, bets=(), the_post=None, community_id=5):
	"""Stand up store, the bank, the channel and the community lookup.

	Returns (store, bank, channel, log).
	"""
	store = FakeStore(bets=bets, post=the_post)
	bank = FakeBank()
	channel = FakeChannel()
	log = RecordingLog()
	monkeypatch.setattr(flow, "store", store)
	monkeypatch.setattr(flow, "gold", bank)
	monkeypatch.setattr(flow, "log", log)

	class _Client:
		def get_channel(self, _channel_id):
			return channel

	monkeypatch.setattr(sys.modules["core.client"], "dc", _Client())

	async def _community_for_channel(_channel_id):
		return community_id

	# `from bot import community` resolves the package attribute before it tries
	# to import the submodule, so this keeps the real module's DB access away.
	monkeypatch.setattr(sys.modules["bot"], "community",
						types.SimpleNamespace(community_for_channel=_community_for_channel),
						raising=False)
	monkeypatch.setattr(sys.modules["bot"], "active_matches", [], raising=False)
	return store, bank, channel, log


def text(embed):
	return embed.description or ""


# ── the defect this cutover closes ───────────────────────────────────────
def test_flow_no_longer_calls_anything_the_bet_era_deleted():
	""" Tasks 2 and 5 deleted six functions (scoring.resolve_ballots /
	tally / grade, store.save_ballots / ballots_for / mark_correct) that flow.py
	still called. Every one of those call sites was an AttributeError waiting on
	the first freeze or the first report, inside a best-effort guard that would
	have logged it and moved on — the same shape of silent death this feature
	already suffered once. """
	import inspect
	source = inspect.getsource(flow)
	for gone in ("resolve_ballots", "scoring.tally", "scoring.grade",
				 "save_ballots", "ballots_for", "mark_correct", "TEAM_EMOJIS"):
		assert gone not in source, f"flow.py still references {gone}, which no longer exists"


# ── freeze ───────────────────────────────────────────────────────────────
class TestFreeze:
	def test_a_two_sided_book_locks_with_bettor_counts_and_no_refunds(self, monkeypatch):
		""" votes0/votes1 now count BETTORS per side, not gold: 2 on Alpha, 1 on
		Bravo. The pots (200 vs 100) belong on the card, not in those columns. """
		store, bank, channel, log = wire(monkeypatch, bets=TWO_SIDED)
		asyncio.run(flow._freeze(post(), 2_000))

		assert store.frozen == [(12, 2, 1)]
		assert store.no_actioned == [] and store.voided == []
		assert bank.refunds == [], "a settleable book keeps its stakes"
		body = text(channel.message.edits[0][0])
		assert "200" in body and "100" in body
		assert log.errors == []

	def test_the_locked_card_drops_the_buttons(self, monkeypatch):
		""" Freezing is terminal for betting: leaving the six buttons live would
		invite presses the router then refuses one by one. """
		_store, _bank, channel, _log = wire(monkeypatch, bets=TWO_SIDED)
		asyncio.run(flow._freeze(post(), 2_000))
		assert channel.message.edits[0][1] is None

	def test_a_one_sided_book_is_refunded_and_never_frozen(self, monkeypatch):
		""" Nobody took the other side, so there are no odds. Every stake goes
		back at freeze time and the post lands on 'no_action' — a status distinct
		from void so the audit trail says what actually happened. """
		store, bank, channel, log = wire(monkeypatch, bets=ONE_SIDED)
		asyncio.run(flow._freeze(post(), 2_000))

		assert store.no_actioned == [12]
		assert store.frozen == [], "a one-sided book must never reach 'frozen'"
		assert bank.refunds == [(5, [1, 2], 12)]
		assert "refund" in text(channel.message.edits[0][0]).lower()
		assert log.errors == []

	def test_an_empty_book_is_no_action_without_touching_the_bank(self, monkeypatch):
		store, bank, channel, log = wire(monkeypatch, bets=[])
		asyncio.run(flow._freeze(post(), 2_000))

		assert store.no_actioned == [12]
		assert store.frozen == []
		assert bank.refunds == [], "there is nothing to refund"
		assert log.errors == []

	def test_a_one_sided_book_with_no_community_shouts_rather_than_losing_gold(self, monkeypatch):
		""" The refund cannot be applied without a community to apply it in. The
		post still closes — but silently swallowing real stakes is the one
		outcome that must never pass unnoticed. """
		store, bank, _channel, log = wire(monkeypatch, bets=ONE_SIDED, community_id=None)
		asyncio.run(flow._freeze(post(), 2_000))

		assert bank.refunds == []
		assert store.no_actioned == [12]
		assert len(log.errors) == 1 and "refund" in log.errors[0].lower()

	def test_freeze_reads_the_book_not_the_message(self, monkeypatch):
		""" The reaction era is over: a message that cannot be fetched no longer
		costs the freeze anything, because the DB is the source of truth. """
		store, _bank, _channel, log = wire(monkeypatch, bets=TWO_SIDED)

		class _Blind:
			def get_channel(self, _channel_id):
				return None

		monkeypatch.setattr(sys.modules["core.client"], "dc", _Blind())
		asyncio.run(flow._freeze(post(), 2_000))

		assert store.frozen == [(12, 2, 1)]
		assert log.errors == []


# ── resolve ──────────────────────────────────────────────────────────────
class TestResolve:
	def match(self, winner=0, players=(1, 2, 3, 4)):
		return types.SimpleNamespace(
			id=77, winner=winner, qc=types.SimpleNamespace(id=900),
			players=[types.SimpleNamespace(id=uid) for uid in players])

	def test_winners_are_paid_the_post_resolves_and_the_report_is_posted(self, monkeypatch):
		""" The spec's worked example: 150+50 on Alpha vs 100 on Bravo, Alpha
		wins, the whole 300 pot splits by stake. """
		store, bank, channel, log = wire(monkeypatch, bets=TWO_SIDED, the_post=post(status="frozen"))
		asyncio.run(flow.resolve_for_match(self.match(winner=0)))

		assert bank.payouts == [(5, {1: 225, 2: 75}, 12)]
		assert store.resolved == [(12, 0)]
		assert store.voided == []
		assert len(channel.sent) == 1
		body = text(channel.sent[0])
		assert "Alpha" in body and "anu" in body and "225" in body
		assert log.errors == []

	def test_playing_pays_the_faucet_for_every_player(self, monkeypatch):
		_store, bank, _channel, log = wire(monkeypatch, bets=TWO_SIDED, the_post=post(status="frozen"))
		asyncio.run(flow.resolve_for_match(self.match(winner=1, players=(1, 2))))

		assert bank.seeded == [(5, 1), (5, 2)]
		assert bank.rewards == [(5, 1, 77), (5, 2, 77)]
		assert log.errors == []

	def test_the_faucet_pays_even_when_nobody_opened_a_book(self, monkeypatch):
		""" The reward is a fact about playing a ranked match, not about betting:
		a match whose card was never posted still tops its players up. """
		_store, bank, channel, log = wire(monkeypatch, the_post=None)
		asyncio.run(flow.resolve_for_match(self.match(winner=0, players=(1, 2))))

		assert bank.rewards == [(5, 1, 77), (5, 2, 77)]
		assert channel.sent == [], "no post, no report"
		assert log.errors == []

	def test_a_match_with_no_winner_refunds_instead_of_settling(self, monkeypatch):
		""" A draw or a cancelled report grades nobody. Every stake goes back and
		nothing is paid — including the faucet, which needs a result. """
		store, bank, channel, log = wire(monkeypatch, bets=TWO_SIDED, the_post=post(status="frozen"))
		asyncio.run(flow.resolve_for_match(self.match(winner=None)))

		assert bank.refunds == [(5, [1, 2, 3], 12)]
		assert bank.payouts == [] and bank.rewards == []
		assert store.voided == [12] and store.resolved == []
		assert "refunded" in text(channel.message.edits[0][0]).lower()
		assert log.errors == []

	def test_a_one_sided_book_that_reaches_resolve_is_refunded_not_settled(self, monkeypatch):
		""" Unreachable through the freeze rule, which is exactly why it is
		guarded: scoring.payouts() answers {} for a one-sided book, and paying
		nobody while marking the post resolved would keep the gold. """
		store, bank, _channel, log = wire(monkeypatch, bets=ONE_SIDED, the_post=post(status="frozen"))
		asyncio.run(flow.resolve_for_match(self.match(winner=0)))

		assert bank.refunds == [(5, [1, 2], 12)]
		assert bank.payouts == []
		assert store.voided == [12] and store.resolved == []

	def test_winners_with_no_community_shout_rather_than_paying_nobody_quietly(self, monkeypatch):
		""" A bet cannot be placed in a channel with no community, so this needs
		the channel to have been un-enrolled mid-match. The freeze and void paths
		both shout when they cannot apply a movement; settlement, which is the
		one that owes people money, must not be the quiet one. """
		_store, bank, _channel, log = wire(
			monkeypatch, bets=TWO_SIDED, the_post=post(status="frozen"), community_id=None)
		asyncio.run(flow.resolve_for_match(self.match(winner=0)))

		assert bank.payouts == []
		assert len(log.errors) == 1 and "payout" in log.errors[0].lower()

	def test_a_bank_failure_never_escapes_into_the_match_flow(self, monkeypatch):
		""" resolve_for_match is awaited straight out of report_win. It has to
		swallow its own failures, exactly as the open path does. """
		_store, bank, _channel, log = wire(monkeypatch, bets=TWO_SIDED, the_post=post(status="frozen"))

		async def _boom(*_a, **_k):
			raise RuntimeError("database down")

		monkeypatch.setattr(bank, "pay_post", _boom)
		asyncio.run(flow.resolve_for_match(self.match(winner=0)))    # must not raise
		assert len(log.errors) == 1


# ── void and restart ─────────────────────────────────────────────────────
class TestVoid:
	def test_a_cancelled_match_hands_every_stake_back(self, monkeypatch):
		store, bank, channel, log = wire(monkeypatch, bets=TWO_SIDED, the_post=post())
		asyncio.run(flow.void_for_match(77))

		assert bank.refunds == [(5, [1, 2, 3], 12)]
		assert store.voided == [12]
		assert "refunded" in text(channel.message.edits[0][0]).lower()
		assert channel.message.edits[0][1] is None, "a voided card keeps no live buttons"
		assert log.errors == []

	def test_no_live_post_is_a_no_op(self, monkeypatch):
		store, bank, _channel, log = wire(monkeypatch, the_post=None)
		asyncio.run(flow.void_for_match(77))

		assert bank.refunds == [] and store.voided == []
		assert log.errors == []

	def test_a_rebalance_refunds_the_old_book_before_opening_the_new_one(self, monkeypatch):
		""" /subauto rewrites both teams, so the sides people staked on stop
		existing. The old book is refunded rather than carried over. """
		store, bank, _channel, log = wire(monkeypatch, bets=TWO_SIDED, the_post=post())
		opened = []

		async def _open_for_match(match):
			opened.append(match.id)

		monkeypatch.setattr(flow, "open_for_match", _open_for_match)
		match = types.SimpleNamespace(id=77, ranked=True, cfg={"predictions_enabled": True})
		asyncio.run(flow.restart_for_match(match))

		assert bank.refunds == [(5, [1, 2, 3], 12)]
		assert store.voided == [12]
		assert opened == [77]
		assert log.errors == []
