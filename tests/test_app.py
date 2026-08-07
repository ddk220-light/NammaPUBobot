"""The Application object that replaces bot/__init__.py's module-level globals.

Six mutable globals — queue_channels, active_matches, active_queues,
waiting_reactions, bot_ready, bot_was_ready — were reachable and writable from
any module that did `import bot`. That is what forced 65 function-local imports
across the codebase: importing at module scope deadlocked, because everything
depended on the module that held the world.

These tests pin the two properties that make the replacement worth having. The
state is per-instance, and there is no global handle to it.
"""
from __future__ import annotations

import ast
from pathlib import Path

from bot.app import Application

_REPO_ROOT = Path(__file__).resolve().parent.parent


class _FakeClient:
	pass


def test_application_starts_with_empty_state():
	app = Application(client=_FakeClient())
	assert app.channels == {}
	assert app.active_matches == []
	assert app.active_queues == []
	assert len(app.waiting_reactions) == 0
	assert app.ready is False
	assert app.was_ready is False


def test_state_is_per_instance_not_shared():
	"""The whole point. Two Applications must not see each other's matches —
	if they do, the state is still class-level and nothing has been fixed."""
	a, b = Application(client=_FakeClient()), Application(client=_FakeClient())
	a.active_matches.append("match")
	a.channels[1] = "channel"
	assert b.active_matches == []
	assert b.channels == {}


def test_the_module_exposes_no_global_instance_and_no_accessor():
	"""A module-level Application, or a get_app()/current_app() accessor, would
	recreate exactly the coupling this class exists to remove: any module could
	reach the world again without declaring that it needs it. The explicit
	hand-off IS the design, so it is worth a test rather than a comment."""
	import bot.app as m

	assert not any(isinstance(v, Application) for v in vars(m).values()), \
		"bot/app.py must not hold a module-level Application instance"
	for accessor in ("get_app", "current_app", "app", "instance", "the_app"):
		assert not hasattr(m, accessor), f"bot/app.py must not expose {accessor}()"


def test_waiting_reactions_still_sweeps_by_ttl():
	"""Moved from bot/__init__.py, not rewritten: the check-in flow leaks a
	callback whenever an exit path raises before unsubscribing, and this sweep
	is what stops that accumulating over a multi-week uptime."""
	import time as _time

	app = Application(client=_FakeClient())
	app.waiting_reactions[1] = lambda: None
	now = _time.time()
	# Expiry is stamped as an absolute epoch time, so the sweep must be given
	# one too — a small integer would silently expire nothing and the test
	# would pass for the wrong reason.
	assert app.waiting_reactions.sweep_expired(now) == 0
	assert app.waiting_reactions.sweep_expired(
		now + app.waiting_reactions.TTL_SECONDS + 1) == 1
	assert len(app.waiting_reactions) == 0


def test_application_is_constructed_exactly_once_at_boot():
	"""Two Applications in one process would be two worlds, and whichever one
	the event handlers got would silently be the only real one."""
	source = (_REPO_ROOT / "PUBobot2.py").read_text(encoding="utf-8")
	tree = ast.parse(source)
	constructions = [
		n for n in ast.walk(tree)
		if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "Application"
	]
	assert len(constructions) == 1, "the entrypoint must construct exactly one Application"
