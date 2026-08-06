# -*- coding: utf-8 -*-
"""nextcord assembly for the quiz poll (bot.quiz.embeds) — Task 7.

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

from bot.quiz import embeds, interactions


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
	e = embeds.poll_embed(_post(), [])
	assert e.title == "Daily AoE2 quiz"


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
	"""Stands in for bot.quiz.store: an in-memory posts dict and a votes dict
	keyed (post_id, user_id) — the shape the real (post_id, user_id) PRIMARY
	KEY enforces. record_vote/record_vote_multi overwrite the whole row, same
	as the real REPLACE upsert; answers_for_post returns only cast votes."""

	def __init__(self):
		self.posts = {}
		self.votes = {}

	async def get_post(self, post_id):
		return self.posts.get(post_id)

	async def record_vote(self, post_id, user_id, nick, choice_index, now):
		self.votes[(post_id, user_id)] = dict(
				post_id=post_id, user_id=user_id, nick=nick,
				choice_index=int(choice_index), choice_indices=None, answered_at=now)

	async def record_vote_multi(self, post_id, user_id, nick, choice_indices, now):
		self.votes[(post_id, user_id)] = dict(
				post_id=post_id, user_id=user_id, nick=nick, choice_index=None,
				choice_indices=json.dumps(sorted(int(i) for i in choice_indices)),
				answered_at=now)

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
	"""Drives on_quiz_interaction for real, freezing time.time() to the
	interaction's own `now` for the call's duration — what a live press sees:
	the clock reading at the moment the button was pressed."""
	real_time = interactions.time.time
	interactions.time.time = lambda: interaction.now
	try:
		asyncio.run(interactions.on_quiz_interaction(interaction))
	finally:
		interactions.time.time = real_time
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
