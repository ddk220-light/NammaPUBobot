"""Unit tests for /subauto's candidate-selection helper.

``pick_available(candidates, busy_ids)`` decides which queued player
``/subauto`` pulls in: the first member of the queue who isn't already
committed to another active match. The team rebalance itself reuses the
proven ``Match.init_teams("matchmaking")`` path, so the only genuinely new
pure logic introduced by /subauto is this pick — and that's what we lock
down here.
"""
from __future__ import annotations

from types import SimpleNamespace

from bot.match.subbing import pick_available, should_warn


def _member(id_):
	# Stand-in for a nextcord Member: pick_available only reads ``.id``.
	return SimpleNamespace(id=id_)


class TestPickAvailable:
	def test_returns_first_when_nobody_busy(self):
		a, b, c = _member(1), _member(2), _member(3)
		assert pick_available([a, b, c], set()) is a

	def test_skips_busy_and_returns_first_free_preserving_order(self):
		a, b, c = _member(1), _member(2), _member(3)
		# a and b are already in other active matches -> c is first free.
		assert pick_available([a, b, c], {1, 2}) is c

	def test_returns_none_when_queue_empty(self):
		assert pick_available([], {1, 2}) is None

	def test_returns_none_when_all_busy(self):
		a, b = _member(1), _member(2)
		assert pick_available([a, b], {1, 2}) is None


class TestShouldWarn:
	# end_time = 1000; the 1-minute window is [940, 1000].
	def test_fires_inside_final_minute_when_players_not_ready(self):
		assert should_warn(frame_time=950, end_time=1000, already_warned=False, num_not_ready=2) is True

	def test_fires_at_exact_window_start(self):
		assert should_warn(940, 1000, False, 1) is True

	def test_silent_before_final_minute(self):
		assert should_warn(900, 1000, False, 2) is False

	def test_silent_when_already_warned(self):
		assert should_warn(950, 1000, True, 2) is False

	def test_silent_when_everyone_ready(self):
		assert should_warn(950, 1000, False, 0) is False

	def test_silent_after_deadline_timeout_takes_over(self):
		assert should_warn(1001, 1000, False, 2) is False
