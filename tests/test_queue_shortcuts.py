from nammaoe2bot.discord.shortcuts import queue_shortcut_action


def test_add_shortcut_accepts_ascii_and_full_width_keyboard_forms():
	assert queue_shortcut_action("++") == "add"
	assert queue_shortcut_action("＋＋") == "add"


def test_remove_shortcut_accepts_ascii_and_smart_dash_forms():
	for text in ("--", "—", "–", "−", "－－"):
		assert queue_shortcut_action(text) == "remove"


def test_shortcuts_tolerate_surrounding_whitespace_only():
	assert queue_shortcut_action("  ++\n") == "add"
	assert queue_shortcut_action("  — ") == "remove"


def test_ordinary_text_and_a_single_ascii_hyphen_are_not_commands():
	for text in (None, "", "-", "+", "hello -- there", "+++", "——"):
		assert queue_shortcut_action(text) is None
