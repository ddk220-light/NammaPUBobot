"""Tests for the pure render helpers in nammaoe2bot.features.quiz.view (no nextcord)."""
from __future__ import annotations

import nammaoe2bot.features.quiz.view as v


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
	rl = v.result_lines(prompt="Q?", options=["a", "b", "c", "d"], correct_indices=[2],
						explanation="because", winners=["x", "y"])
	joined = "\n".join(rl)
	assert "C" in joined and "because" in joined and "x, y" in joined


def test_closed_notice_is_the_one_surviving_notice():
	# already_answered_notice / too_late_notice went with the reveal era —
	# a vote is changeable and there is no private timer to run out of.
	assert "closed" in v.closed_notice().lower()
	assert not hasattr(v, "already_answered_notice")
	assert not hasattr(v, "too_late_notice")


from nammaoe2bot.features.quiz.view import result_lines


def test_result_lines_render_multiple_correct_letters():
	out = "\n".join(result_lines("Q?", ["a", "b", "c", "d"], [0, 2], "because", ["Ann"]))
	assert "A, C" in out
	assert "because" in out and "Ann" in out


def test_poll_card_shows_the_source_tag():
	# The tag moved from the deleted card_lines onto the poll card, which is
	# the only card there is now.
	game = "\n".join(v.poll_card_lines("combat", "hard", 1, 1, 2, 24, "Q?", ["a", "b"], [],
			source="game"))
	player = "\n".join(v.poll_card_lines("Villagers", "medium", 2, 1, 1, 24, "Q?", ["a", "b"], [],
			source="player"))
	assert "Game" in game and "combat" in game
	assert "Player" in player and "Villagers" in player


def test_poll_card_without_source_is_backcompat():
	out = "\n".join(v.poll_card_lines("combat", "hard", 5, 1, 1, 24, "Q?", ["a", "b"], []))
	assert "#5" in out and "Game" not in out and "Player" not in out


def test_poll_card_shows_question_and_options_but_never_the_answer():
	votes = [dict(user_id=1, nick="Ann", choice_index=0, choice_indices=None)]
	lines = v.poll_card_lines("combat", "medium", 5, 2, 3, 23.5,
			"Which unit wins?", ["Knight", "Pikeman"], votes, source="game")
	text = "\n".join(lines)
	assert "Which unit wins?" in text
	assert "A. Knight" in text and "B. Pikeman" in text
	assert "Week 2" in text and "Day 3" in text and "#5" in text
	assert "medium" in text
	assert "50" in text and "10" in text          # the gold rule is on the card
	assert "✅" not in text                        # the open card never marks the answer


def test_poll_card_without_difficulty_renders():
	lines = v.poll_card_lines("combat", None, 5, 2, 3, 23.5, "Q?", ["a", "b"], [])
	assert not any("None" in ln for ln in lines)


def test_tally_counts_and_names():
	votes = [
		dict(user_id=1, nick="Ann", choice_index=0, choice_indices=None),
		dict(user_id=2, nick="Bob", choice_index=0, choice_indices=None),
		dict(user_id=3, nick="Cy", choice_index=1, choice_indices=None),
	]
	lines = v.tally_lines(["Knight", "Pikeman"], votes)
	assert "**2** vote(s)" in lines[0] and "Ann" in lines[0] and "Bob" in lines[0]
	assert "**1** vote(s)" in lines[1] and "Cy" in lines[1]


def test_tally_multi_voter_appears_under_each_pick():
	votes = [dict(user_id=1, nick="Ann", choice_index=None, choice_indices="[0, 2]")]
	lines = v.tally_lines(["a", "b", "c"], votes)
	assert "Ann" in lines[0] and "Ann" not in lines[1] and "Ann" in lines[2]


def test_tally_caps_names_at_12():
	votes = [dict(user_id=i, nick=f"P{i}", choice_index=0, choice_indices=None)
			for i in range(15)]
	line = v.tally_lines(["a", "b"], votes)[0]
	assert "**15** vote(s)" in line
	assert "+3 more" in line
	assert "P11" in line and "P12" not in line


def test_tally_ignores_choiceless_rows():
	votes = [dict(user_id=1, nick="Ghost", choice_index=None, choice_indices=None)]
	lines = v.tally_lines(["a", "b"], votes)
	assert "Ghost" not in "\n".join(lines)
	assert "**0** vote(s)" in lines[0]


def test_tally_marks_correct_options_when_told():
	lines = v.tally_lines(["a", "b"], [], correct_indices={1})
	assert "✅" not in lines[0] and "✅" in lines[1]


def test_result_lines_gold_note_appended_only_when_given():
	base = v.result_lines("Q?", ["a", "b"], [0], "because", ["Ann"])
	with_gold = v.result_lines("Q?", ["a", "b"], [0], "because", ["Ann"],
			gold_note="🪙 60 gold paid out")
	assert "🪙 60 gold paid out" not in "\n".join(base)
	assert with_gold[-1] == "🪙 60 gold paid out"
