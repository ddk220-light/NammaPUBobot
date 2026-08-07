# -*- coding: utf-8 -*-
"""The quiz resolve path — grade → pay → results → close — driven for real.

THE ORDERING RULE IS THE WHOLE POINT. `store.close_post` is the LAST thing
`_reveal` does, and it does not happen at all if a payment failed. The only
retry mechanism this feature has is `_close_due`, a sweep that re-enters
`_reveal` for any post still `status='open'` past its `closes_at` — so "still
open" IS the retry ticket. Close the post and then fail to pay, and that
voter's gold is beyond the sweep forever: nothing will ever pay it and nothing
will ever notice. Re-running the whole resolve is always safe (grading is
deterministic, every grant is idem-keyed in bot/predictions/gold.py, the card
edit is harmless to repeat), so the correct failure mode is "do it again", not
"give up quietly". This is the same money-first / terminal-status-last rule
bot/predictions/flow.py follows.

Every fake here appends to ONE shared `calls` list, so the order assertions are
global across store, bank and channel rather than per-object — a close that
moved above the payment loop has to show up somewhere, and this is where.

Nothing below mocks the function under test: store, the gold bank, the
community lookup and the Discord side are fakes; `_reveal` and `_post_question`
are the real ones. No pytest-asyncio in this repo, so every coroutine is driven
with asyncio.run().
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import types

import pytest

import bot.predictions  # noqa: F401 — the package whose `gold` attribute the fixture swaps
import bot.quiz.jobs  # noqa: F401 — for the sys.modules lookup below
from bot.quiz import embeds

# NOT `import bot.quiz.jobs as jobs` / `from bot.quiz import jobs`. Both of
# those resolve the PACKAGE attribute `bot.quiz.jobs`, and bot/quiz/__init__.py
# ends with `from .jobs import jobs` — so that attribute is the QuizJobs
# INSTANCE, not the module, and every monkeypatch below would land on the
# singleton instead. This is the same shadowing that left the predictions
# feature dead for 3312 matches (see bot/predictions/__init__.py's tail);
# sys.modules is the unambiguous handle.
jobs = sys.modules["bot.quiz.jobs"]


# ── the rows ─────────────────────────────────────────────────────────────
POST = dict(
	id=41, channel_id=900, message_id=4242, category="combat", difficulty="medium",
	prompt="Which unit wins?", options_json='["Knight", "Pikeman"]',
	correct_index=0, correct_indices="[0]", explanation="Because armour.",
	opened_at=1_000, closes_at=87_400, status="open", seq=5, week=1, day=2, source="game")

MULTI_POST = dict(POST, category="techgaps", options_json='["a", "b", "c"]',
		correct_index=None, correct_indices="[0, 2]")

# A cast vote carries answered_at; the real answers_for_post filters on exactly
# that (`answered_at IS NOT NULL`), which is what keeps reveal-era ghost rows —
# a user who pressed "Reveal & start" and never answered — out of the payroll.
VOTE_RIGHT = dict(post_id=41, user_id=1, nick="Ann", choice_index=0,
		choice_indices=None, answered_at=1_500)
VOTE_WRONG = dict(post_id=41, user_id=2, nick="Bob", choice_index=1,
		choice_indices=None, answered_at=1_600)
GHOST = dict(post_id=41, user_id=3, nick="Ghost", choice_index=None,
		choice_indices=None, answered_at=None)


# ── the fakes ────────────────────────────────────────────────────────────
class FakeStore:
	"""bot.quiz.store, in memory. `answers_for_post` reproduces the real
	query's `answered_at IS NOT NULL` filter rather than handing back every
	row — the contract this module consumes is "cast votes only", and a fake
	that leaked ghosts would let the payroll widen without a test noticing."""

	def __init__(self, calls, votes=(), seq=1):
		self.calls = calls
		self.rows = [dict(v) for v in votes]
		self.posts = {}
		self.grades = []            # (post_id, user_id, is_correct)
		self.closed = []
		self.clamped = []           # (post_id, now) — the deadline pull-backs
		self.created = []           # (channel_id, q, opened_at, closes_at)
		self.message_ids = []
		self.configs = []
		self._seq = seq
		self._next_post_id = 41

	# — the resolve half —
	async def clamp_closes_at(self, post_id, now):
		"""LEAST(closes_at, now), same as the real UPDATE — never forward. The
		stored row is what the interaction router reads back, which is how the
		clamp actually shuts the vote gate."""
		self.calls.append("clamp_closes_at")
		self.clamped.append((post_id, now))
		row = self.posts.get(post_id)
		if row is not None:
			row["closes_at"] = min(int(row["closes_at"]), int(now))

	async def answers_for_post(self, post_id):
		self.calls.append("answers_for_post")
		return [dict(r) for r in self.rows
				if r["post_id"] == post_id and r.get("answered_at") is not None]

	async def write_grade(self, post_id, user_id, is_correct):
		self.calls.append("write_grade:{}".format(user_id))
		self.grades.append((post_id, user_id, bool(is_correct)))

	async def close_post(self, post_id):
		self.calls.append("close_post")
		self.closed.append(post_id)
		row = self.posts.get(post_id)
		if row is not None:
			row["status"] = "closed"

	async def due_to_close(self, now_ts):
		"""The retry query, reproduced exactly: status='open' AND closes_at<=now.
		Hands back COPIES — the sweep must re-read the row, not hold a reference
		to the one it is about to mutate."""
		self.calls.append("due_to_close")
		return [dict(p) for p in self.posts.values()
				if p["status"] == "open" and int(p["closes_at"]) <= now_ts]

	# — the vote half (drives bot.quiz.interactions against the same rows) —
	async def record_vote(self, post_id, user_id, nick, choice_index):
		"""The real one re-reads the post row FOR UPDATE inside its own
		transaction, reads the clock UNDER THAT LOCK, and writes only if the
		poll is still open — returning whether the vote landed. Reproduced here
		rather than assumed, both halves: the clamp's whole point is that the
		STORED row is what decides, so a fake that wrote unconditionally would
		let a press slip past the very gate this file exists to test, and a fake
		that judged that row against the ROUTER's press timestamp would let it
		slip past for the subtler reason — an arrival earlier than the clamp is
		still earlier than the clamped deadline.

		It takes no `now`, exactly as the real one does not. `time.time()` here
		is the same global clock `_reveal` reads, and the tests move it forward
		between the press's arrival and this call."""
		self.calls.append("record_vote:{}".format(user_id))
		now = int(time.time())
		post = self.posts.get(post_id)
		if post is None or post["status"] != "open" or now >= int(post["closes_at"]):
			return False
		self.rows = [r for r in self.rows
				if not (r["post_id"] == post_id and r["user_id"] == user_id)]
		self.rows.append(dict(post_id=post_id, user_id=user_id, nick=nick,
				choice_index=int(choice_index), choice_indices=None,
				is_correct=None, answered_at=now))
		return True

	def vote_of(self, post_id, user_id):
		for r in self.rows:
			if r["post_id"] == post_id and r["user_id"] == user_id:
				return r
		return None

	# — the post half —
	async def next_seq(self, _channel_id):
		self.calls.append("next_seq")
		return self._seq

	async def create_post(self, channel_id, q, opened_at, closes_at):
		self.calls.append("create_post")
		post_id = self._next_post_id
		self.created.append((channel_id, dict(q), opened_at, closes_at))
		self.posts[post_id] = dict(
			id=post_id, channel_id=channel_id, message_id=None, question_id=q["id"],
			category=q["category"], difficulty=q.get("difficulty"), prompt=q["prompt"],
			options_json=json.dumps(q["options"]), correct_index=q.get("correct_index"),
			correct_indices=json.dumps(q["correct_indices"]), explanation=q["explanation"],
			opened_at=opened_at, closes_at=closes_at, status="open",
			seq=q["seq"], week=q["week"], day=q["day"], source=q.get("source"))
		return post_id

	async def get_post(self, post_id):
		self.calls.append("get_post")
		return self.posts.get(post_id)

	async def set_message_id(self, post_id, message_id):
		self.calls.append("set_message_id")
		self.message_ids.append((post_id, message_id))

	async def upsert_config(self, channel_id, **fields):
		self.calls.append("upsert_config")
		self.configs.append((channel_id, fields))


class FakeBank:
	"""bot.predictions.gold, with a real (tiny) ledger behind it.

	`ledger` is keyed (quiz_post_id, user_id) exactly like the real
	`quiz:{post}:{user}` idem_key, so a repeated grant is a no-op returning 0 —
	the behaviour a resolve retried after a partial crash actually meets — and
	`quiz_paid_total` sums the ledger rather than whatever this run happened to
	move. `prepaid` stands up the "a previous run already paid" state."""

	def __init__(self, calls, fail_for=(), prepaid=None):
		self.calls = calls
		self.ledger = dict(prepaid or {})
		self.fail_for = set(fail_for)
		self.seeded = []            # (community_id, user_id)
		self.grants = []            # (community_id, user_id, post_id, correct)
		self.returned = []          # what each grant handed back

	async def ensure_seeded(self, community_id, user_id, _now):
		self.calls.append("ensure_seeded:{}".format(user_id))
		self.seeded.append((community_id, user_id))
		return False

	async def grant_quiz_reward(self, community_id, user_id, quiz_post_id, correct, _now):
		self.calls.append("grant:{}".format(user_id))
		self.grants.append((community_id, user_id, quiz_post_id, bool(correct)))
		if user_id in self.fail_for:
			raise RuntimeError("gold_balances row missing for {}/{}".format(community_id, user_id))
		key = (quiz_post_id, user_id)
		if key in self.ledger:
			self.returned.append(0)         # idem key already applied
			return 0
		amount = 50 if correct else 10
		self.ledger[key] = amount
		self.returned.append(amount)
		return amount

	async def quiz_paid_total(self, quiz_post_id):
		self.calls.append("quiz_paid_total")
		return sum(a for (pid, _uid), a in self.ledger.items() if pid == quiz_post_id)


class FakeMessage:
	def __init__(self, calls):
		self.calls = calls
		self.edits = []             # (embed, view)

	async def edit(self, embed=None, view="<untouched>", **_kw):
		self.calls.append("card_edit")
		self.edits.append((embed, view))


class FakeChannel:
	def __init__(self, calls, fetch_raises=None):
		self.calls = calls
		self.message = FakeMessage(calls)
		self.sent = []              # embeds announced to the channel
		self.sent_kwargs = []
		self._fetch_raises = fetch_raises

	async def fetch_message(self, _message_id):
		self.calls.append("fetch_message")
		if self._fetch_raises is not None:
			raise self._fetch_raises
		return self.message

	async def send(self, embed=None, **kw):
		self.calls.append("channel_send")
		self.sent.append(embed)
		self.sent_kwargs.append(kw)
		return types.SimpleNamespace(id=555)


@pytest.fixture
def env(monkeypatch):
	"""Factory: stand up store, the gold bank, the community lookup and the
	Discord side around a real QuizJobs, all sharing one ordered `calls` list."""

	def _wire(*, votes=(), community_id=7, fail_for=(), prepaid=None,
			channel=True, fetch_raises=None, seq=1):
		calls = []
		store = FakeStore(calls, votes=votes, seq=seq)
		bank = FakeBank(calls, fail_for=fail_for, prepaid=prepaid)
		chan = FakeChannel(calls, fetch_raises=fetch_raises) if channel else None

		monkeypatch.setattr(jobs, "store", store)
		# `from bot.predictions import gold as gold_bank` resolves the package
		# attribute before it would import the submodule, so setting it here
		# keeps the real bank (and its DB) out of the run.
		monkeypatch.setattr(sys.modules["bot.predictions"], "gold", bank, raising=False)

		async def _community_for_channel(_channel_id):
			calls.append("community_for_channel")
			return community_id

		monkeypatch.setattr(sys.modules["bot"], "community",
				types.SimpleNamespace(community_for_channel=_community_for_channel),
				raising=False)

		class _Client:
			def get_channel(self, _channel_id):
				return chan

		monkeypatch.setattr(sys.modules["nammaoe2bot.discord.client"], "dc", _Client())
		return types.SimpleNamespace(calls=calls, store=store, bank=bank,
				channel=chan, job=jobs.QuizJobs(), post=dict(POST))

	return _wire


def text(embed):
	return embed.description or ""


def phases(calls):
	"""The call trace with the per-user suffixes stripped — the shape of the
	resolve, in order."""
	return [c.split(":")[0] for c in calls]


# ── the ordering rule ────────────────────────────────────────────────────
def test_resolve_grades_pays_then_closes_in_order(env):
	e = env(votes=[VOTE_RIGHT, VOTE_WRONG])
	asyncio.run(e.job._reveal(e.post, fresh=True))

	assert e.store.grades == [(41, 1, True), (41, 2, False)]
	assert e.bank.grants == [(7, 1, 41, True), (7, 2, 41, False)]
	assert e.store.closed == [41]

	# Seeding precedes the grant it enables — a voter who never touched gold
	# has no balances row, and grant_quiz_reward raises without one.
	assert e.calls.index("ensure_seeded:1") < e.calls.index("grant:1")
	assert e.calls.index("ensure_seeded:2") < e.calls.index("grant:2")

	# THE assertion. close_post is last, once, and after every payment and
	# every render. Anything that moves it earlier fails here.
	assert e.calls[-1] == "close_post"
	assert e.calls.count("close_post") == 1
	assert e.calls.index("close_post") > e.calls.index("grant:2")
	assert e.calls.index("close_post") > e.calls.index("card_edit")
	assert e.calls.index("close_post") > e.calls.index("channel_send")

	# ...and the whole trace, spelled out: clamp → grade → pay → results → close.
	assert phases(e.calls) == [
		"clamp_closes_at",
		"answers_for_post",
		"write_grade", "write_grade",
		"community_for_channel",
		"ensure_seeded", "grant", "ensure_seeded", "grant", "quiz_paid_total",
		"fetch_message", "card_edit", "channel_send",
		"close_post",
	]


# ── the clamp: the snapshot has to be final before it is taken ───────────
# _close_due only ever feeds _reveal posts already past closes_at, so that path
# was never racy. _reveal_previous is the hole: /quiz reveal_now and the daily
# cadence both reach _reveal while `now < closes_at`, and the vote gate in
# bot/quiz/interactions.py refuses a press only when `status != 'open' or now >=
# closes_at`. Mid-resolve the row still says open on BOTH counts — status flips
# last by design (money first, terminal status last) — so a press in that window
# was accepted and written AFTER the snapshot, then shut out by the close
# forever: never graded, never paid, and undetectable, because the ledger and
# the balance cache still agree. store.clamp_closes_at, called before the
# snapshot, is what makes the gate honest.
def test_reveal_clamps_the_deadline_before_it_snapshots_the_votes(env, monkeypatch):
	# The /quiz reveal_now case: the poll is still nominally open — the clock
	# is frozen at 50_000 and the post does not close until 99_000.
	monkeypatch.setattr(time, "time", lambda: 50_000)
	e = env(votes=[VOTE_RIGHT])
	e.store.posts[41] = dict(POST, closes_at=99_000)
	asyncio.run(e.job._reveal(dict(e.store.posts[41]), fresh=True))

	# THE assertion: the door shuts before the votes are read, not after.
	assert "clamp_closes_at" in e.calls, "the resolve never clamped the deadline"
	assert e.calls.index("clamp_closes_at") < e.calls.index("answers_for_post")
	assert e.calls[0] == "clamp_closes_at", "nothing may await ahead of the clamp"

	# ...and it clamped to NOW, on the stored row the vote gate reads back.
	assert e.store.clamped == [(41, 50_000)]
	assert e.store.posts[41]["closes_at"] == 50_000


def test_the_clamp_never_pushes_a_deadline_forward(env, monkeypatch):
	# LEAST(closes_at, now), not an assignment. _close_due re-enters _reveal for
	# posts that are ALREADY past their deadline, sometimes days later; moving
	# closes_at up to now would re-open a poll that has been shut for a week.
	monkeypatch.setattr(time, "time", lambda: 50_000)
	e = env(votes=[VOTE_RIGHT])
	e.store.posts[41] = dict(POST, closes_at=1_000)
	asyncio.run(e.job._reveal(dict(e.store.posts[41]), fresh=False))
	assert e.store.posts[41]["closes_at"] == 1_000


class _Clock:
	"""An advancing fake wall clock. `_reveal` reads it, and so does the
	store's own vote gate; the ROUTER reads its own frozen reading (the instant
	the button was pressed) instead, which is what lets a press ARRIVE before
	the clamp and reach the DB after it — the shape of the real failure."""

	def __init__(self, t):
		self.t = int(t)

	def __call__(self):
		return self.t

	def at(self, t):
		self.t = int(t)
		return self.t


def test_a_press_during_the_resolve_is_refused(env, monkeypatch):
	"""The race itself, driven end to end against the real router.

	The press is fired from inside the card edit — i.e. after the vote
	snapshot and after the payments, while the post is still status='open'
	because the close is deliberately last. That is precisely the window the
	clamp exists to close, and it is the only place a press can do the damage:
	the row the router reads back is the one the clamp left.

	THREE DISTINCT INSTANTS, and they have to be distinct or the test proves
	nothing. The press ARRIVES at 49_999, strictly before the resolve — so its
	own fast-path check is honest and PASSES, which is what puts the whole
	burden on the store. The resolve runs at 50_000 and clamps the deadline
	there. The press's write reaches the store at 50_002, having spent the gap
	queued behind the resolve. A single frozen clock (which this test used to
	use) collapses all three into one instant, where `now >= closes_at` is true
	by equality and the ROUTER refuses the press before the store is ever
	asked — green, and blind to a gate that judges a clamped deadline against
	the caller's older reading."""
	from bot.quiz import interactions
	from tests.test_quiz_interactions import FakeInteraction

	clock = _Clock(50_000)
	monkeypatch.setattr(time, "time", clock)
	e = env(votes=[VOTE_RIGHT])
	e.store.posts[41] = dict(POST, closes_at=99_000)
	monkeypatch.setattr(interactions, "store", e.store)
	# The router's clock is its OWN — frozen at the press's arrival, which is
	# before the clamp. Swapping the module attribute (not `time.time`) is what
	# keeps the two clocks apart.
	monkeypatch.setattr(interactions, "time", types.SimpleNamespace(time=lambda: 49_999))

	latecomer = FakeInteraction(cid="quiz:41:ans:0", user_id=88, nick="Late",
			message_id=4242, now=49_999)
	real_edit = e.channel.message.edit

	async def _edit_and_race(embed=None, view="<untouched>", **kw):
		clock.at(50_002)               # the press's transaction finally gets the row
		await interactions.on_quiz_interaction(latecomer)
		await real_edit(embed=embed, view=view, **kw)

	e.channel.message.edit = _edit_and_race
	asyncio.run(e.job._reveal(dict(e.store.posts[41]), fresh=True))

	# 49_999 is well inside the post's original 99_000 window AND inside the
	# clamped 50_000 one, so the router's fast path passes and the store is the
	# only thing that can refuse this press.
	assert "record_vote:88" in e.calls, "the press got past the fast path, as it must"
	assert e.store.vote_of(41, 88) is None, "a vote was recorded after the snapshot"
	assert latecomer.response.sent is not None
	assert "closed" in latecomer.response.sent["content"].lower()
	assert latecomer.response.edited is None

	# The consequence the clamp actually prevents, asserted directly: no cast
	# vote may survive the close ungraded — an ungraded row is never paid, and
	# nothing downstream can tell it apart from a row that was.
	graded = {uid for _pid, uid, _ok in e.store.grades}
	voters = {r["user_id"] for r in e.store.rows if r.get("answered_at") is not None}
	assert voters <= graded, "a vote survived the close ungraded — it will never be paid"
	assert e.store.closed == [41]


def test_resolve_reruns_idempotently(env):
	# _close_due re-enters _reveal for any post still open past closes_at, so a
	# resolve interrupted anywhere runs again from the top. Nothing may double.
	e = env(votes=[VOTE_RIGHT, VOTE_WRONG])
	asyncio.run(e.job._reveal(e.post, fresh=True))
	first_grades = list(e.store.grades)
	first_total = text(e.channel.sent[0])

	e.calls.clear()
	asyncio.run(e.job._reveal(e.post, fresh=True))

	assert e.store.grades == first_grades * 2, "grading is deterministic — same verdicts"
	assert e.bank.grants[2:] == [(7, 1, 41, True), (7, 2, 41, False)], "grants re-attempted"
	assert e.bank.returned[2:] == [0, 0], "the idem key no-ops them in gold.py"
	assert e.store.closed == [41, 41]
	assert e.calls[-1] == "close_post"
	# The announced figure is the ledger's, so the second announcement reads
	# the same 60 the first did — not the 0 this run actually moved.
	assert "60" in text(e.channel.sent[1])
	assert text(e.channel.sent[1]) == first_total


def test_payment_failure_leaves_the_post_open(env):
	# The retry ticket IS status='open'. Closing past an unpaid voter puts
	# their gold beyond _close_due forever, and the ledger/balance cache still
	# agree, so nothing downstream can detect the loss either.
	e = env(votes=[VOTE_RIGHT, VOTE_WRONG], fail_for=(1,))
	raised = None
	try:
		asyncio.run(e.job._reveal(e.post, fresh=True))
	except Exception as exc:                                    # noqa: BLE001
		raised = exc

	# Deliberately NOT pytest.raises: that would short-circuit the moment the
	# raise went missing, and "it didn't raise" is the less important half. The
	# claim under test is that the post is STILL OPEN, so that is asserted
	# first and on its own — a mutation that swallows the failure and closes
	# anyway has to fail here, not merely on the missing exception.
	assert "close_post" not in e.calls
	assert e.store.closed == []
	assert isinstance(raised, RuntimeError), "the failure must propagate, not be logged and forgotten"
	# The loop does not abort on the first failure — the second voter is still
	# paid, and the raise comes after the whole loop.
	assert e.bank.grants == [(7, 1, 41, True), (7, 2, 41, False)]
	assert e.bank.ledger.get((41, 2)) == 10
	assert e.bank.ledger.get((41, 1)) is None
	# Grading already happened and is safe to redo on the retry.
	assert e.store.grades == [(41, 1, True), (41, 2, False)]


def test_close_due_is_the_retry_and_it_pays_the_missed_voter(env):
	"""The retry mechanism itself, end to end — not by proxy.

	Every other test here asserts a HALF of the rule: the post is left open on
	a failure, or the sweep query says status='open'. Neither one drives the
	loop that turns "still open" into "paid on the next pass", and that loop is
	the entire reason _reveal closes last. This test fails a payment, sweeps,
	clears the failure, sweeps again with the SAME query at the SAME clock, and
	checks the gold actually landed."""
	e = env(votes=[VOTE_RIGHT, VOTE_WRONG], fail_for=(1,))
	e.store.posts[41] = dict(POST, closes_at=1_000)        # already past its deadline

	# Sweep 1 — Ann's grant blows up. _close_due logs and moves on; nothing closes.
	asyncio.run(e.job._close_due(9_000))
	assert e.bank.ledger.get((41, 2)) == 10, "Bob was paid before the failure"
	assert e.bank.ledger.get((41, 1)) is None, "Ann was not"
	assert e.store.closed == []
	assert e.store.posts[41]["status"] == "open", "the retry ticket must survive the failure"
	assert [p["id"] for p in asyncio.run(e.store.due_to_close(9_000))] == [41]

	# Sweep 2 — the transient failure has cleared. The post is still in the
	# query (unchanged clock), so the same sweep re-enters _reveal.
	e.bank.fail_for = set()
	e.calls.clear()
	asyncio.run(e.job._close_due(9_000))

	assert "due_to_close" in e.calls and "answers_for_post" in e.calls, "the sweep re-entered _reveal"
	assert e.bank.ledger[(41, 1)] == 50, "the previously-unpaid voter is paid on the retry"
	assert e.bank.ledger[(41, 2)] == 10, "and the already-paid one is not paid twice"
	assert e.store.closed == [41]
	assert e.store.posts[41]["status"] == "closed"
	assert e.calls[-1] == "close_post"
	# The ticket is spent exactly once: the post is out of the query now.
	assert asyncio.run(e.store.due_to_close(9_000)) == []
	# Re-grading on the retry is safe and produced the same verdicts.
	assert e.store.grades == [(41, 1, True), (41, 2, False)] * 2


def test_payment_failure_skips_the_results_too(env):
	# The raise is what leaves the post open, so nothing after the pay step
	# runs — no card edit, no announcement claiming a payout that half-landed.
	e = env(votes=[VOTE_RIGHT], fail_for=(1,))
	raised = None
	try:
		asyncio.run(e.job._reveal(e.post, fresh=True))
	except Exception as exc:                                    # noqa: BLE001
		raised = exc
	assert e.channel.sent == []
	assert e.channel.message.edits == []
	assert isinstance(raised, RuntimeError)


# ── the gold note ────────────────────────────────────────────────────────
def test_gold_note_reads_the_ledger_total(env):
	# A previous, crashed run already paid both voters, so every grant this run
	# makes is an idem no-op returning 0. The announced figure must still be
	# 60 — which is only true if it is read back from the ledger. A loop
	# accumulator would announce nothing at all here.
	e = env(votes=[VOTE_RIGHT, VOTE_WRONG], prepaid={(41, 1): 50, (41, 2): 10})
	asyncio.run(e.job._reveal(e.post, fresh=True))

	assert e.bank.returned == [0, 0], "the fixture's point: this run moved nothing"
	body = text(e.channel.sent[0])
	assert "60" in body
	assert "\U0001FA99" in body


def test_gold_note_states_the_rule(env):
	e = env(votes=[VOTE_RIGHT, VOTE_WRONG])
	asyncio.run(e.job._reveal(e.post, fresh=True))
	body = text(e.channel.sent[0])
	assert "50" in body and "10" in body and "500" in body


def test_no_payment_means_no_gold_note(env):
	# Nobody voted: nothing to pay, and a "0 gold paid out" line would be noise.
	e = env(votes=[])
	asyncio.run(e.job._reveal(e.post, fresh=True))
	assert e.bank.grants == []
	assert "\U0001FA99" not in text(e.channel.sent[0])
	assert e.store.closed == [41]


# ── no community ─────────────────────────────────────────────────────────
def test_no_community_grades_and_closes_without_gold(env):
	# A channel that was never enrolled has no gold economy. Grading and
	# closing are still owed; paying is not, and the card must not claim it.
	e = env(votes=[VOTE_RIGHT, VOTE_WRONG], community_id=None)
	asyncio.run(e.job._reveal(e.post, fresh=True))

	assert e.store.grades == [(41, 1, True), (41, 2, False)]
	assert e.bank.grants == [] and e.bank.seeded == []
	assert "quiz_paid_total" not in e.calls
	assert e.store.closed == [41]
	assert e.calls[-1] == "close_post"
	assert "\U0001FA99" not in text(e.channel.sent[0])
	assert "gold" not in text(e.channel.sent[0]).lower()


# ── what counts as a vote ────────────────────────────────────────────────
def test_choiceless_rows_are_not_paid(env):
	# GHOST is a reveal-era row: it exists in quiz_answers with answered_at
	# NULL and is therefore not returned by answers_for_post at all. It is in
	# the env to document the filter — no grade, no seed, no grant, no name on
	# the winners line.
	e = env(votes=[VOTE_RIGHT, GHOST])
	asyncio.run(e.job._reveal(e.post, fresh=True))

	assert [g[1] for g in e.store.grades] == [1]
	assert [g[1] for g in e.bank.grants] == [1]
	assert [s[1] for s in e.bank.seeded] == [1]
	assert "Ghost" not in text(e.channel.sent[0])
	assert "Ghost" not in text(e.channel.message.edits[0][0])


def test_multi_post_with_no_stored_selection_grades_wrong(env):
	# choice_indices NULL on a multi post grades as wrong, not as a crash —
	# and a wrong answer is still a cast vote, so it is still paid the 10.
	empty = dict(post_id=41, user_id=1, nick="Ann", choice_index=None,
			choice_indices=None, answered_at=1_500)
	e = env(votes=[empty])
	asyncio.run(e.job._reveal(dict(MULTI_POST), fresh=True))

	assert e.store.grades == [(41, 1, False)]
	assert e.bank.grants == [(7, 1, 41, False)]
	assert e.store.closed == [41]


def test_multi_post_grades_the_exact_set(env):
	right = dict(post_id=41, user_id=1, nick="Ann", choice_index=None,
			choice_indices="[0, 2]", answered_at=1_500)
	partial = dict(post_id=41, user_id=2, nick="Bob", choice_index=None,
			choice_indices="[0]", answered_at=1_600)
	e = env(votes=[right, partial])
	asyncio.run(e.job._reveal(dict(MULTI_POST), fresh=True))

	assert e.store.grades == [(41, 1, True), (41, 2, False)]
	assert "Ann" in text(e.channel.sent[0])
	assert "Bob" not in text(e.channel.sent[0])


# ── the two renders ──────────────────────────────────────────────────────
def test_the_card_edit_is_the_locked_card_with_its_view_stripped(env):
	e = env(votes=[VOTE_RIGHT, VOTE_WRONG])
	asyncio.run(e.job._reveal(e.post, fresh=True))

	embed, view = e.channel.message.edits[0]
	assert "locked" in embed.title.lower()
	assert view is None, "the components go with the lock"
	assert "✅ A. Knight" in text(embed)      # the correct option is marked
	assert "Ann" in text(embed) and "Bob" in text(embed)


def test_fresh_announcement_is_yesterdays_answer(env):
	e = env(votes=[VOTE_RIGHT])
	asyncio.run(e.job._reveal(e.post, fresh=True))
	assert e.channel.sent[0].title == "Yesterday's answer"
	assert "Because armour." in text(e.channel.sent[0])
	assert "Ann" in text(e.channel.sent[0])


def test_stale_close_edits_but_does_not_announce(env):
	# _close_due passes fresh=False: these are leftovers (the quiz was
	# disabled, the process died), and a fresh "Yesterday's answer" for a
	# week-old question is noise.
	e = env(votes=[VOTE_RIGHT])
	asyncio.run(e.job._reveal(e.post, fresh=False))
	assert e.channel.sent == []
	assert len(e.channel.message.edits) == 1
	assert e.store.closed == [41]


def test_a_deleted_card_does_not_block_the_close(env):
	import nextcord
	e = env(votes=[VOTE_RIGHT], fetch_raises=nextcord.NotFound())
	asyncio.run(e.job._reveal(e.post, fresh=True))
	assert e.bank.grants == [(7, 1, 41, True)]
	assert e.channel.sent, "the announcement still goes out"
	assert e.store.closed == [41]
	assert e.calls[-1] == "close_post"


def test_a_forbidden_card_edit_does_not_block_the_close_either(env):
	""" THE ONE THAT USED TO WEDGE THE POLL. Only `NotFound` was caught, and a
	404 is self-healing — the card is gone and stays gone. A 403 is not: the
	bot lost Manage Messages (a permission edit, a channel lockdown) and EVERY
	retry fails identically. By this line the grades are written and the gold
	is paid, so the raise did not protect anything; it aborted _reveal before
	`close_post`, leaving the post open past closes_at, which is _close_due's
	retry ticket — so the sweep re-entered it every 30 seconds, forever,
	re-fetching and re-editing a card it can never edit and never closing the
	poll. A cosmetic failure must not outrank a committed payment. """
	import nextcord
	e = env(votes=[VOTE_RIGHT], fetch_raises=nextcord.Forbidden())
	asyncio.run(e.job._reveal(e.post, fresh=True))
	assert e.bank.grants == [(7, 1, 41, True)]
	assert e.channel.sent, "the announcement still goes out"
	assert e.store.closed == [41]
	assert e.calls[-1] == "close_post"


def test_a_failed_card_edit_still_never_covers_a_payment_failure(env):
	""" The widened `except` around the card edit sits BELOW the payment loop
	and must stay there. A version that wrapped the payments in it — or that
	caught broadly around the whole results block and swallowed the
	RuntimeError on its way out — would close a post over unpaid gold, which is
	the one thing this whole ordering exists to prevent. Both failures at once,
	so a catch wide enough to cover the payment gets caught here. """
	import nextcord
	e = env(votes=[VOTE_RIGHT], fail_for=(1,), fetch_raises=nextcord.Forbidden())
	raised = None
	try:
		asyncio.run(e.job._reveal(e.post, fresh=True))
	except Exception as exc:                                    # noqa: BLE001
		raised = exc
	assert "close_post" not in e.calls
	assert e.store.closed == []
	assert isinstance(raised, RuntimeError)


def test_a_missing_channel_still_grades_pays_and_closes(env):
	# dc.get_channel returns None after a redeploy / a deleted channel. The
	# gold is still owed and the post must not stay open forever.
	e = env(votes=[VOTE_RIGHT, VOTE_WRONG], channel=False)
	asyncio.run(e.job._reveal(e.post, fresh=True))
	assert len(e.store.grades) == 2
	assert len(e.bank.grants) == 2
	assert e.calls[-1] == "close_post"


# ── _post_question ───────────────────────────────────────────────────────
GAME_Q = dict(id="combat:knight_v_pike", category="combat", difficulty="medium",
		prompt="Which unit wins?", options=["Knight", "Pikeman"],
		correct_index=0, correct_indices=[0], explanation="Because armour.",
		source="game")

TECHGAP_Q = dict(GAME_Q, id="techgaps:x", category="techgaps",
		options=["a", "b", "c"], correct_index=None, correct_indices=[0, 2])


def _post_with(e, q):
	async def _next_question(_channel_id, seq, _day):
		return dict(q, seq=seq)

	e.job._next_question = _next_question
	return asyncio.run(e.job._post_question(900, 86_400, 1_000))


def test_post_question_sends_the_poll_card(env):
	e = env()
	post_id = _post_with(e, GAME_Q)

	assert post_id == 41
	embed = e.channel.sent[0]
	view = e.channel.sent_kwargs[0]["view"]
	# The poll card, not the reveal-era teaser: it carries the question and a
	# tally, and it never marks the answer.
	assert embed.title == "Daily AoE2 quiz"
	assert "Which unit wins?" in text(embed)
	assert "A. Knight" in text(embed) and "**0** vote(s)" in text(embed)
	assert "✅" not in text(embed)
	# Single-answer category -> one button per option, wired to the post id.
	assert [b.custom_id for b in view.children] == ["quiz:41:ans:0", "quiz:41:ans:1"]
	assert view.timeout is None and view.auto_defer is False
	# The row is read back and rendered from — the card is a function of the
	# stored post, which is what lets every later press re-render it.
	assert "get_post" in e.calls
	assert e.store.message_ids == [(41, 555)]
	# Unchanged by this task: the day is claimed only after a confirmed send.
	assert e.store.configs[0][0] == 900
	assert set(e.store.configs[0][1]) == {"last_post_ymd", "last_post_at"}
	assert e.calls.index("create_post") < e.calls.index("channel_send")
	assert e.calls.index("channel_send") < e.calls.index("upsert_config")


def test_post_question_multi_category_sends_a_select(env):
	import nextcord
	e = env()
	_post_with(e, TECHGAP_Q)
	view = e.channel.sent_kwargs[0]["view"]
	assert len(view.children) == 1
	assert isinstance(view.children[0], nextcord.ui.StringSelect)
	assert view.children[0].custom_id == "quiz:41:msel"
	assert view.children[0].max_values == 3


def test_post_question_reads_the_multi_flag_from_the_category(env):
	# The flag is is_multi_category(category), not a constant: swapping the two
	# questions must swap the control.
	e = env()
	_post_with(e, GAME_Q)
	single = e.channel.sent_kwargs[0]["view"]
	e2 = env()
	_post_with(e2, TECHGAP_Q)
	multi = e2.channel.sent_kwargs[0]["view"]
	assert len(single.children) == 2 and len(multi.children) == 1


# ── the reveal era is gone ───────────────────────────────────────────────
def test_jobs_no_longer_references_the_deleted_builders():
	# Task 9 deletes card_embed / card_view (and the ephemeral question flow
	# behind them). Every reference left in jobs.py would be an AttributeError
	# inside a best-effort guard — logged once, and never seen again.
	# WORD-BOUNDARY matching, not `in`: the surviving builders are named
	# final_card_embed / vote_view / poll_card_lines, and a plain substring
	# test would flag `final_card_embed` as a reference to the deleted
	# `card_embed` and make this assertion impossible to satisfy.
	import inspect
	import re
	source = inspect.getsource(jobs)
	for gone in ("card_embed", "card_view", "question_embed", "answer_view",
			"record_reveal", "record_answer", "get_answer"):
		assert not re.search(r"\b{}\b".format(gone), source), \
			"jobs.py still references {}".format(gone)


def test_the_deleted_builders_are_actually_gone():
	for gone in ("card_embed", "card_view", "question_embed", "answer_view"):
		assert not hasattr(embeds, gone), "bot.quiz.embeds still exports {}".format(gone)
	from bot.quiz import store as quiz_store
	for gone in ("record_reveal", "record_answer", "record_answer_multi", "get_answer"):
		assert not hasattr(quiz_store, gone), "bot.quiz.store still exports {}".format(gone)
	from bot.quiz import view as quiz_view
	# letter_options went with question_lines, its only caller — tally_lines
	# builds the (richer) lettered line the poll card actually renders.
	for gone in ("card_lines", "question_lines", "too_late_notice",
			"already_answered_notice", "letter_options"):
		assert not hasattr(quiz_view, gone), "bot.quiz.view still exports {}".format(gone)
