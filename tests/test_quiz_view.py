"""Tests for the pure render helpers in bot.quiz.view (no nextcord)."""
from __future__ import annotations

import bot.quiz.view as v


def test_letter_options():
	assert v.letter_options(["Ram", "Scorpion"]) == ["A. Ram", "B. Scorpion"]


def test_card_lines_hides_answer():
	lines = v.card_lines(category="armor", difficulty="medium", number=42, closes_in_h=24)
	text = "\n".join(lines)
	assert "armor" in text and "#42" in text and "Scorpion" not in text


def test_question_lines_letters_every_option():
	lines = v.question_lines("Q?", ["a", "b", "c", "d"])
	assert lines[0] == "**Q?**"
	assert any(line.startswith("D. ") for line in lines)


def test_leaderboard_lines_ranks_and_accuracy():
	tallied = [
		{"user_id": 1, "nick": "Gaj", "correct": 6, "answered": 6},
		{"user_id": 2, "nick": "nin", "correct": 5, "answered": 6},
	]
	lines = v.leaderboard_lines(tallied)
	assert "1." in lines[0] and "Gaj" in lines[0] and "6/6" in lines[0] and "100%" in lines[0]
	assert "83%" in lines[1]


def test_leaderboard_lines_empty():
	assert v.leaderboard_lines([]) == ["No answers this week."]


def test_result_lines():
	rl = v.result_lines(prompt="Q?", options=["a", "b", "c", "d"], correct_index=2,
						explanation="because", winners=["x", "y"])
	joined = "\n".join(rl)
	assert "C. c" in joined and "because" in joined and "x, y" in joined


def test_notices_are_strings():
	assert "closed" in v.closed_notice().lower()
	assert isinstance(v.already_answered_notice(), str)
	assert isinstance(v.too_late_notice(), str)
