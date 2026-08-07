# -*- coding: utf-8 -*-
"""nextcord assembly for the quiz poll (nammaoe2bot.features.quiz.embeds) — Task 7.

Exercised against the conftest nextcord STUB, not real nextcord (not
installed in CI). The stub's recording surface is plain attributes on the
fake Embed/View/Button/StringSelect/SelectOption instances (`.description`,
`.children`, `.custom_id`, ...) rather than a generic `.kwargs`/`.items`
bag — see tests/conftest.py's _FakeView/_FakeButton/_FakeStringSelect for
what is actually recorded.

Task 8 appends interaction-flow tests (the router that drives these
builders end to end) to this same file.
"""
from __future__ import annotations

import asyncio
import json
import types

import nextcord
import pytest

from nammaoe2bot.features.quiz import embeds, interactions


def _post(**over):
	base = dict(id=9, channel_id=5, message_id=111, category="combat",
			difficulty="medium", prompt="Q?", options_json='["Knight", "Pikeman"]',
			correct_index=0, correct_indices="[0]", explanation="because",
			seq=5, week=2, day=3, source="game", opened_at=1000,
			closes_at=1000 + 86400, status="open")
	base.update(over)
	return base


# ── vote_view ────────────────────────────────────────────────────────────
def test_vote_view_single_answer_buttons():
	v = embeds.vote_view(9, ["Knight", "Pikeman"], multi=False)
	assert v.timeout is None and v.auto_defer is False
	ids = [b.custom_id for b in v.children]
	assert ids == ["quiz:9:ans:0", "quiz:9:ans:1"]
	assert [b.label for b in v.children] == ["A", "B"]


def test_vote_view_multi_is_a_select():
	v = embeds.vote_view(9, ["a", "b", "c"], multi=True)
	assert v.timeout is None and v.auto_defer is False
	assert v.children[0].custom_id == "quiz:9:msel"
	assert v.children[0].max_values == 3


def test_vote_view_multi_select_options_are_lettered():
	v = embeds.vote_view(9, ["a", "b", "c"], multi=True)
	opts = v.children[0].options
	assert [o.label for o in opts] == ["A. a", "B. b", "C. c"]
	assert [o.value for o in opts] == ["0", "1", "2"]


# ── poll_embed ───────────────────────────────────────────────────────────
def test_poll_embed_reads_the_post_row():
	e = embeds.poll_embed(_post(), [])
	assert "Q?" in e.description
	assert "A. Knight" in e.description


def test_poll_embed_title_and_colour():
	# BOTH, because the name says both and the colour is the only thing telling
	# the three quiz embeds apart at a glance: blurple = the live poll, green =
	# the result announcement, gold = the weekly leaderboard. A poll card that
	# came back green reads as an already-answered question.
	e = embeds.poll_embed(_post(), [])
	assert e.title == "Daily AoE2 quiz"
	assert e.colour.value == nextcord.Colour.blurple().value
	assert e.colour.value != nextcord.Colour.green().value


def test_poll_embed_never_marks_the_answer():
	# The open card must not leak which option is correct.
	e = embeds.poll_embed(_post(), [])
	assert "✅" not in e.description


def test_poll_embed_renders_with_null_difficulty():
	# Old rows may have difficulty NULL — must render fine, not print "None".
	e = embeds.poll_embed(_post(difficulty=None), [])
	assert "None" not in e.description


def test_poll_embed_closes_in_h_floors_at_zero_when_expired():
	e = embeds.poll_embed(_post(closes_at=1), [])
	assert "~0h" in e.description


# ── final_card_embed ─────────────────────────────────────────────────────
def test_final_card_marks_the_correct_option():
	votes = [dict(user_id=1, nick="Ann", choice_index=0, choice_indices=None)]
	e = embeds.final_card_embed(_post(), votes)
	assert "✅ A. Knight" in e.description


def test_final_card_title_says_locked():
	e = embeds.final_card_embed(_post(), [])
	assert "locked" in e.title.lower()


# ── result_embed ─────────────────────────────────────────────────────────
def test_result_embed_without_gold_note_is_backcompat():
	e = embeds.result_embed("Q?", ["a", "b"], [0], "because", ["Ann"])
	assert "\U0001FA99" not in e.description


def test_result_embed_gold_note_passthrough():
	e = embeds.result_embed("Q?", ["a", "b"], [0], "because", ["Ann"],
			gold_note="\U0001FA99 60 gold paid out")
	assert e.description.endswith("\U0001FA99 60 gold paid out")


# ── on_quiz_interaction (Task 8: the vote press) ────────────────────────
# The global component router for the poll era: pressing an option button
# RECORDS a vote and re-renders the shared card as feedback — no ephemeral
# question, no personal deadline. No pytest-asyncio in this repo, so every
# handler call below is driven with asyncio.run via the _run() helper rather
# than an `async def test_...` (which pytest 9 fails outright with no runner
# installed).
#
# Two racing presses (two users voting near-simultaneously) need no special
# handling here: record_vote/record_vote_multi are REPLACE-upserts keyed on
# (post_id, user_id), so both votes land in the DB independently and
# whichever render runs last just reads back an accurate tally — there is no
# shared mutable state for the two requests to corrupt. No test is added for
# this; it falls out of the upsert semantics already pinned in
# tests/test_quiz_store.py.

class _FakeStore:
	"""Stands in for nammaoe2bot.features.quiz.store: an in-memory posts dict and a votes dict
	keyed (post_id, user_id) — the shape the real (post_id, user_id) PRIMARY
	KEY enforces. record_vote/record_vote_multi overwrite the whole row, same
	as the real REPLACE upsert; answers_for_post returns only cast votes.

	The two writers reproduce the real ones' TRANSACTIONAL CONTRACT, not just
	their storage: they re-evaluate the gate for themselves and return True
	only when the vote landed (nammaoe2bot/features/quiz/store.py takes that re-check under a
	`SELECT ... FOR UPDATE` on the post row).

	THEY ALSO OWN THEIR OWN CLOCK — `self.now`, the reading the real gate takes
	AFTER its `FOR UPDATE` returns — and take no timestamp from the router,
	because the real ones do not either. That separation is the point: the
	router's clock is the instant the button was pressed, the store's is the
	instant its transaction got the row, and the two can be seconds apart with a
	resolve's clamp in between. A fake that shared one clock with the router
	could not express the race at all, which is how it stayed open. `race` fires
	once, between the router's pre-read and the write, standing in for the
	resolve's clamp committing in that gap."""

	def __init__(self):
		self.posts = {}
		self.votes = {}
		self.race = None            # fires once, between the pre-read and the write
		self.now = 2_000            # the store's OWN clock, read under its row lock

	async def get_post(self, post_id):
		return self.posts.get(post_id)

	def _still_open(self, post_id):
		if self.race is not None:
			hook, self.race = self.race, None
			hook(self.posts)
		post = self.posts.get(post_id)
		return post is not None and post["status"] == "open" and self.now < int(post["closes_at"])

	async def record_vote(self, post_id, user_id, nick, choice_index):
		if not self._still_open(post_id):
			return False
		self.votes[(post_id, user_id)] = dict(
				post_id=post_id, user_id=user_id, nick=nick,
				choice_index=int(choice_index), choice_indices=None, answered_at=self.now)
		return True

	async def record_vote_multi(self, post_id, user_id, nick, choice_indices):
		if not self._still_open(post_id):
			return False
		self.votes[(post_id, user_id)] = dict(
				post_id=post_id, user_id=user_id, nick=nick, choice_index=None,
				choice_indices=json.dumps(sorted(int(i) for i in choice_indices)),
				answered_at=self.now)
		return True

	async def answers_for_post(self, post_id):
		return [v for (pid, _uid), v in self.votes.items() if pid == post_id]


@pytest.fixture
def quiz_env(monkeypatch):
	store = _FakeStore()
	monkeypatch.setattr(interactions, "store", store)
	return store


class FakeResponse:
	"""nextcord's InteractionResponse, recording the two things a vote press
	can produce: a rewrite of the shared card (edit_message -> .edited) or a
	private reply (send_message -> .sent). One-shot, like the real thing —
	is_done() flips true on the first of either."""

	def __init__(self):
		self.sent = None
		self.edited = None
		self._done = False

	def is_done(self):
		return self._done

	async def send_message(self, content=None, ephemeral=False, view=nextcord.utils.MISSING, **_kw):
		self._done = True
		self.sent = {"content": content, "ephemeral": ephemeral, "view": view}

	async def edit_message(self, embed=None, view=nextcord.utils.MISSING, **_kw):
		self._done = True
		self.edited = {"embed": embed, "view": view}


class FakeFollowup:
	def __init__(self, response):
		self._response = response

	async def send(self, content=None, ephemeral=False, view=nextcord.utils.MISSING, **_kw):
		self._response.sent = {"content": content, "ephemeral": ephemeral, "view": view}


class FakeInteraction:
	"""A component press. `message_id` is the id of the message the button
	actually lives on — deliberately a separate knob from the post's own
	message_id so the old-era-ephemeral guard test can put them out of sync."""
	COMPONENT = 3

	def __init__(self, cid, user_id=42, nick="Ann", message_id=111, now=2000, values=None):
		self.type = FakeInteraction.COMPONENT
		self.data = {"custom_id": cid}
		if values is not None:
			self.data["values"] = values
		self.user = types.SimpleNamespace(id=user_id, display_name=nick, name=nick)
		self.message = types.SimpleNamespace(id=message_id)
		self.now = now
		self.response = FakeResponse()
		self.followup = FakeFollowup(self.response)


def _run(interaction):
	"""Drives on_quiz_interaction for real, freezing THE ROUTER'S clock to the
	interaction's own `now` for the call's duration — what a live press sees:
	the clock reading at the moment the button was pressed.

	It swaps the module ATTRIBUTE `interactions.time`, not `time.time` on the
	stdlib module. The old version assigned through to the real `time` module
	and so froze the clock globally — which silently gave the router and the
	store the same clock, and a shared clock is precisely what makes the vote
	race invisible (the press cannot arrive earlier than the clamp if there is
	only one instant). `_FakeStore` keeps its own `now`."""
	real_time = interactions.time
	interactions.time = types.SimpleNamespace(time=lambda: interaction.now)
	try:
		asyncio.run(interactions.on_quiz_interaction(interaction))
	finally:
		interactions.time = real_time
	return interaction


def test_vote_press_records_and_rerenders_the_card(quiz_env):
	quiz_env.posts[9] = _post()
	i = FakeInteraction(cid="quiz:9:ans:1", user_id=42, nick="Ann", message_id=111, now=2000)
	_run(i)
	assert quiz_env.votes[(9, 42)]["choice_index"] == 1
	assert i.response.edited is not None                  # the card edit IS the feedback
	assert "Ann" in i.response.edited["embed"].description
	assert i.response.sent is None                        # no ephemeral on the happy path


def test_changing_the_vote_replaces_it(quiz_env):
	quiz_env.posts[9] = _post()
	_run(FakeInteraction(cid="quiz:9:ans:1", user_id=42, nick="Ann", message_id=111))
	_run(FakeInteraction(cid="quiz:9:ans:0", user_id=42, nick="Ann", message_id=111))
	assert quiz_env.votes[(9, 42)]["choice_index"] == 0


def test_multi_select_replaces_the_set(quiz_env):
	quiz_env.posts[9] = _post(category="techgaps", correct_indices="[0, 2]",
			options_json='["a", "b", "c"]')
	i = FakeInteraction(cid="quiz:9:msel", values=["2", "0"], user_id=42, nick="Ann", message_id=111)
	_run(i)
	assert quiz_env.votes[(9, 42)]["choice_indices"] == "[0, 2]"


def test_press_at_or_after_closes_at_is_refused(quiz_env):
	quiz_env.posts[9] = _post(closes_at=1500)
	i = FakeInteraction(cid="quiz:9:ans:0", user_id=42, nick="Ann", message_id=111, now=1500)
	_run(i)
	assert (9, 42) not in quiz_env.votes
	assert i.response.sent is not None and "closed" in i.response.sent["content"].lower()


def test_press_on_closed_status_is_refused(quiz_env):
	quiz_env.posts[9] = _post(status="closed")
	i = FakeInteraction(cid="quiz:9:ans:0", user_id=42, nick="Ann", message_id=111, now=2000)
	_run(i)
	assert (9, 42) not in quiz_env.votes


# ── a write refused under the row lock ──────────────────────────────────
# The pre-read gate above is a fast path, not the authority: the row it reads
# can be flipped a millisecond later by nammaoe2bot/features/quiz/jobs.py::_reveal's clamp, and
# the write is a separate round-trip. store.record_vote redoes the check inside
# its own transaction, under a FOR UPDATE lock on the post row and against a
# clock it reads itself once it has that lock, and returns False when the vote
# did not land. The router has to BELIEVE that — a re-render over a refused
# write tells the user their vote counted when no row exists, and the card it
# paints is the tally without them in it.
#
# THE PRESS ARRIVES BEFORE THE CLAMP in both tests below (2_000 vs 2_001), which
# is the only arrangement that puts the refusal where it belongs. When the press
# time equals the clamp time the ROUTER's own `now >= closes_at` already refuses
# it, and the store is never even asked.
def test_a_vote_refused_under_the_row_lock_gets_the_closed_notice(quiz_env):
	quiz_env.posts[9] = _post(closes_at=9_000)
	# The resolve clamps the deadline after the pre-read, before the write; the
	# store's transaction gets the row at 2_002, after both.
	quiz_env.race = lambda posts: posts[9].update(closes_at=2_001)
	quiz_env.now = 2_002
	i = FakeInteraction(cid="quiz:9:ans:1", user_id=42, nick="Ann", message_id=111, now=2_000)
	_run(i)
	assert (9, 42) not in quiz_env.votes, "the store refused it"
	assert i.response.edited is None, "no card re-render over a write the DB refused"
	assert i.response.sent is not None and "closed" in i.response.sent["content"].lower()


def test_a_multi_vote_refused_under_the_row_lock_gets_the_closed_notice(quiz_env):
	quiz_env.posts[9] = _post(category="techgaps", correct_indices="[0, 2]",
			options_json='["a", "b", "c"]', closes_at=9_000)
	quiz_env.race = lambda posts: posts[9].update(closes_at=2_001)
	quiz_env.now = 2_002
	i = FakeInteraction(cid="quiz:9:msel", values=["0", "2"], user_id=42, nick="Ann",
			message_id=111, now=2_000)
	_run(i)
	assert (9, 42) not in quiz_env.votes
	assert i.response.edited is None
	assert i.response.sent is not None and "closed" in i.response.sent["content"].lower()


def test_the_router_hands_the_store_no_timestamp(quiz_env):
	""" The router's `now` is the instant the button was pressed and it is used
	for the fast path ONLY. Passing it down would put the authority's verdict
	back in the hands of a stale reading: the store would compare a deadline
	clamped at 12:00:05 against a press captured at 12:00:04, find it open, and
	write the vote after the resolve's snapshot — never graded, never paid, and
	invisible to reconcile(). Pinned by calling the router with a store whose
	writers accept NO timestamp at all: a router that still passed one would
	TypeError here rather than fail some assertion later. """
	import inspect

	for name in ("record_vote", "record_vote_multi"):
		params = list(inspect.signature(getattr(quiz_env, name)).parameters)
		assert "now" not in params, f"the router's fake still takes a caller clock on {name}"

	quiz_env.posts[9] = _post(closes_at=9_000)
	quiz_env.now = 5_000                       # the store's clock, far from the press's
	i = FakeInteraction(cid="quiz:9:ans:1", user_id=42, nick="Ann", message_id=111, now=2_000)
	_run(i)
	assert quiz_env.votes[(9, 42)]["answered_at"] == 5_000, \
		"answered_at must be the store's under-lock reading, not the press's"


def test_a_vote_refused_because_the_post_closed_gets_the_closed_notice(quiz_env):
	# The other flavour of the same gap: the resolve's close_post lands between
	# the pre-read and the write.
	quiz_env.posts[9] = _post(closes_at=9_000)
	quiz_env.race = lambda posts: posts[9].update(status="closed")
	i = FakeInteraction(cid="quiz:9:ans:1", user_id=42, nick="Ann", message_id=111, now=2_000)
	_run(i)
	assert (9, 42) not in quiz_env.votes
	assert i.response.edited is None
	assert i.response.sent is not None


def test_a_vote_that_lands_is_still_re_rendered(quiz_env):
	# The other side of the boolean: nothing raced, the write landed, and the
	# card edit is still the feedback. A router that treated every return as a
	# refusal would break every honest press.
	quiz_env.posts[9] = _post(closes_at=9_000)
	i = FakeInteraction(cid="quiz:9:ans:1", user_id=42, nick="Ann", message_id=111, now=2_000)
	_run(i)
	assert quiz_env.votes[(9, 42)]["choice_index"] == 1
	assert i.response.edited is not None
	assert i.response.sent is None


def test_press_from_an_old_ephemeral_confirms_without_editing(quiz_env):
	# Old-era ephemeral answer views carry the same ans: routes but live on a
	# DIFFERENT message — the guard records the vote and answers ephemerally
	# instead of painting the card over a private message.
	quiz_env.posts[9] = _post(message_id=111)
	i = FakeInteraction(cid="quiz:9:ans:0", user_id=42, nick="Ann", message_id=999, now=2000)
	_run(i)
	assert quiz_env.votes[(9, 42)]["choice_index"] == 0
	assert i.response.edited is None
	assert i.response.sent is not None


def test_reveal_press_converts_the_old_card(quiz_env):
	quiz_env.posts[9] = _post(message_id=111)
	i = FakeInteraction(cid="quiz:9:reveal", user_id=42, nick="Ann", message_id=111, now=2000)
	_run(i)
	assert (9, 42) not in quiz_env.votes                  # converting is not voting
	assert i.response.edited is not None                  # card now shows the poll
	assert i.response.edited["view"] is not None


def test_the_reveal_fallback_does_not_claim_a_vote_was_counted(quiz_env):
	""" The card has no message_id yet (the send landed, set_message_id did
	not), so _rerender cannot edit it and answers ephemerally instead. On the
	VOTE paths that fallback says "Vote counted" and it is true. On the reveal
	route it would be a flat lie: converting the transition-era card records
	nothing, and a user told their vote counted has no reason to press again —
	they would sit out the poll believing they were in it. """
	quiz_env.posts[9] = _post(message_id=None)
	i = FakeInteraction(cid="quiz:9:reveal", user_id=42, nick="Ann", message_id=111, now=2000)
	_run(i)
	assert (9, 42) not in quiz_env.votes
	assert i.response.edited is None
	body = i.response.sent["content"].lower()
	assert "vote counted" not in body
	assert "no vote" in body


def test_the_vote_fallback_still_confirms_the_vote(quiz_env):
	# The other side of the same branch: a real vote on a card that cannot be
	# edited is still recorded, and the user is still told so.
	quiz_env.posts[9] = _post(message_id=None)
	i = FakeInteraction(cid="quiz:9:ans:1", user_id=42, nick="Ann", message_id=111, now=2000)
	_run(i)
	assert quiz_env.votes[(9, 42)]["choice_index"] == 1
	assert "vote counted" in i.response.sent["content"].lower()


def test_foreign_custom_ids_fall_through(quiz_env):
	i = FakeInteraction(cid="bet:1:0:10", user_id=42, nick="Ann", message_id=111)
	_run(i)
	assert i.response.sent is None and i.response.edited is None


# ── _rerender's view-kind branch ────────────────────────────────────────
# _rerender picks the re-rendered card's component kind with
# is_multi_category(post["category"]) — a StringSelect for techgaps, A/B/C/D
# buttons for everything else. The stored vote (asserted above, e.g.
# test_multi_select_replaces_the_set) says nothing about which control the
# NEXT press sees: a hardcoded `False` there would leave every vote-recording
# test green while silently turning every techgaps poll into single-answer
# buttons after the first vote. These two pin the re-rendered VIEW itself.
def test_rerender_after_multi_vote_uses_a_select(quiz_env):
	quiz_env.posts[9] = _post(category="techgaps", correct_indices="[0, 2]",
			options_json='["a", "b", "c"]', message_id=111)
	i = FakeInteraction(cid="quiz:9:msel", values=["0", "2"], user_id=42, nick="Ann",
			message_id=111, now=2000)
	_run(i)
	view = i.response.edited["view"]
	assert len(view.children) == 1
	assert isinstance(view.children[0], nextcord.ui.StringSelect)
	assert view.children[0].custom_id == "quiz:9:msel"


def test_rerender_after_single_vote_uses_buttons(quiz_env):
	quiz_env.posts[9] = _post(category="combat", options_json='["Knight", "Pikeman"]',
			message_id=111)
	i = FakeInteraction(cid="quiz:9:ans:1", user_id=42, nick="Ann", message_id=111, now=2000)
	_run(i)
	view = i.response.edited["view"]
	assert all(isinstance(c, nextcord.ui.Button) for c in view.children)
	assert [b.custom_id for b in view.children] == ["quiz:9:ans:0", "quiz:9:ans:1"]


# ── mselect's empty-values guard ────────────────────────────────────────
def test_empty_multi_select_is_refused_and_not_recorded(quiz_env):
	quiz_env.posts[9] = _post(category="techgaps", correct_indices="[0, 2]",
			options_json='["a", "b", "c"]', message_id=111)
	i = FakeInteraction(cid="quiz:9:msel", values=[], user_id=42, nick="Ann",
			message_id=111, now=2000)
	_run(i)
	assert (9, 42) not in quiz_env.votes
	assert i.response.edited is None
	assert i.response.sent is not None and i.response.sent["ephemeral"] is True
