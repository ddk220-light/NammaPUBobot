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

from bot.quiz import embeds


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
