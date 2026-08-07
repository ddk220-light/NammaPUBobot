"""The match lifecycle: the dispatcher, and the wiring that fills it.

The pickup domain used to call into betting, the lobby watcher and the
storyline builders directly — eleven function-local imports across five
methods of the domain. It now announces six moments and nammaoe2bot/wiring.py
subscribes the features. Three things have to hold for that to be an
improvement rather than a layer of indirection:

* a handler that fails cannot take the ones after it with it, because none of
  these features may decide whether a match reports;
* registration order IS dispatch order, because two of the sequences in
  wiring.py encode a real constraint;
* the domain really did stop importing features, which is the only thing that
  makes the direction claim true rather than aspirational.

No pytest-asyncio in this repo, so every coroutine is driven with asyncio.run().
"""
import ast
import asyncio
import os
import types

from nammaoe2bot.pickup.match.events import MatchLifecycle

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _match(**kw):
	return types.SimpleNamespace(id=7, **kw)


# ── the dispatcher ───────────────────────────────────────────────────────
class TestDispatch:
	def test_handlers_run_in_registration_order(self):
		""" wiring.py registers the storyline payoff before settlement, and the
		lobby-watcher teardown before the refund. Both are deliberate. """
		seen = []
		events = MatchLifecycle()
		for name in ("first", "second", "third"):
			events.on("finished", lambda _m, _c, n=name: seen.append(n) or _done())
		asyncio.run(events.emit("finished", _match()))
		assert seen == ["first", "second", "third"]

	def test_a_failing_handler_does_not_stop_the_ones_after_it(self):
		""" THE POINT OF THE ISOLATION. A betting outage must not stop a lobby
		watcher being torn down, and neither may stop the match reporting. """
		seen = []

		async def explodes(_match, _ctx):
			raise RuntimeError("database down")

		async def records(_match, _ctx):
			seen.append("ran")

		events = MatchLifecycle()
		events.on("finished", explodes)
		events.on("finished", records)
		asyncio.run(events.emit("finished", _match()))          # must not raise
		assert seen == ["ran"]

	def test_emit_passes_both_the_match_and_the_ctx(self):
		got = []
		events = MatchLifecycle()
		events.on("live", lambda m, c: _async(lambda: got.append((m, c))))
		match, ctx = _match(), object()
		asyncio.run(events.emit("live", match, ctx))
		assert got == [(match, ctx)]

	def test_ctx_defaults_to_none_rather_than_being_required(self):
		""" Half the handlers never post a message. """
		got = []
		events = MatchLifecycle()
		events.on("ending", lambda m, c: _async(lambda: got.append(c)))
		asyncio.run(events.emit("ending", _match()))
		assert got == [None]

	def test_an_event_with_no_subscribers_is_silent(self):
		asyncio.run(MatchLifecycle().emit("cancelled", _match()))

	def test_an_unknown_event_name_raises_on_subscribe(self):
		""" A typo'd on() would otherwise register a handler nothing ever calls
		and report nothing at all — the failure mode is silence. """
		events = MatchLifecycle()
		try:
			events.on("finshed", lambda m, c: None)
		except ValueError as e:
			assert "finshed" in str(e)
		else:
			raise AssertionError("a misspelled event name was accepted")

	def test_an_unknown_event_name_raises_on_emit(self):
		try:
			asyncio.run(MatchLifecycle().emit("exploded", _match()))
		except ValueError as e:
			assert "exploded" in str(e)
		else:
			raise AssertionError("a misspelled event name was emitted")


def _async(fn):
	async def run():
		fn()
	return run()


def _done():
	async def noop():
		pass
	return noop()


# ── the wiring ───────────────────────────────────────────────────────────
class TestWiring:
	def test_every_event_the_domain_emits_has_a_subscriber(self):
		""" Both halves are written by hand in different files. An emit nobody
		listens to is a feature that silently stopped working — which is
		exactly how audience predictions died the first time. """
		import nammaoe2bot.app as app_module
		import nammaoe2bot.wiring as wiring

		app = app_module.Application(client=None)
		wiring.wire_match_lifecycle(app)

		emitted = _emitted_events()
		for event in sorted(emitted):
			assert app.match_events.handlers(event), f"nothing subscribes to '{event}'"

	def test_every_event_wiring_subscribes_to_is_actually_emitted(self):
		""" And the converse: a handler on an event the domain never announces
		is dead code that reads as live. """
		import nammaoe2bot.app as app_module
		import nammaoe2bot.wiring as wiring

		app = app_module.Application(client=None)
		wiring.wire_match_lifecycle(app)

		emitted = _emitted_events()
		for event in MatchLifecycle.EVENTS:
			if app.match_events.handlers(event):
				assert event in emitted, f"'{event}' is wired but never emitted"

	def test_settlement_runs_after_the_result_is_stored(self):
		""" finish_match writes the `matches` row (register_match_*) and THEN
		emits 'finished'. The betting resume sweep finds a stranded book by
		JOINing prediction_posts to that row, so a payout attempted before it
		exists could not be recovered if it died half-way — and 'ending' has to
		stay on the near side of that write, because the lobby-watcher teardown
		it carries ran there before this indirection existed. """
		body = _function_source("nammaoe2bot/pickup/match/match.py", "finish_match")
		register = body.index("register_match_ranked")
		assert body.index('emit("ending"') < register, "'ending' moved past the result write"
		assert register < body.index('emit("finished"'), (
			"'finished' fires before the result is in `matches` — a payout that "
			"crashes half-way is then invisible to store.unsettled_books"
		)

	def test_the_domain_imports_no_feature(self):
		""" The direction claim, checked rather than asserted in a docstring.
		nammaoe2bot/pickup/ is the domain; betting, the quiz, the lobby watcher,
		the storylines and the replay pipeline are built on top of it.

		Stated as an ALLOWLIST rather than a list of banned features, so a
		feature added later is caught by default instead of being invisible
		until someone remembers to add it here. """
		allowed_roots = ("nammaoe2bot.pickup", "nammaoe2bot.runtime",
						 "nammaoe2bot.exceptions", "nammaoe2bot.discord",
						 # Still in bot/ until Phase 2 finishes; the context
						 # classes are the Discord adapter, not a feature.
						 "bot.context")
		offenders = []
		for relative in _domain_modules():
			for node in ast.walk(ast.parse(_read(relative))):
				if isinstance(node, ast.ImportFrom):
					if node.level:
						continue                       # relative: inside the domain
					target = node.module or ""
				elif isinstance(node, ast.Import):
					target = node.names[0].name
				else:
					continue
				if target.split(".")[0] not in ("bot", "nammaoe2bot"):
					continue                           # stdlib or third party
				if not any(target == root or target.startswith(root + ".")
						   for root in allowed_roots):
					offenders.append(f"{relative}: {target}")
		assert not offenders, "the pickup domain imports a feature:\n  " + "\n  ".join(offenders)


def _read(rel):
	with open(os.path.join(_REPO_ROOT, rel), encoding="utf-8") as f:
		return f.read()


def _function_source(rel, name):
	""" Just the named method's source. Searching the whole file for
	`register_match_ranked` finds the class's own fake-match helper first. """
	source = _read(rel)
	for node in ast.walk(ast.parse(source)):
		if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
			return ast.get_source_segment(source, node)
	raise AssertionError(f"{name}() not found in {rel}")


_DOMAIN = os.path.join("nammaoe2bot", "pickup")


def _domain_modules():
	""" Every module under nammaoe2bot/pickup/, repo-relative. """
	found = []
	for dirpath, dirnames, filenames in os.walk(os.path.join(_REPO_ROOT, _DOMAIN)):
		dirnames[:] = [d for d in dirnames if d != "__pycache__"]
		for filename in sorted(filenames):
			if filename.endswith(".py"):
				found.append(os.path.relpath(os.path.join(dirpath, filename), _REPO_ROOT))
	assert found, "no modules found under " + _DOMAIN
	return found


def _emitted_events():
	""" Event names passed to match_events.emit(...) anywhere in the domain.

	Read out of the source rather than by running a match: constructing one
	needs a Discord channel, a queue and a rating system, and the question here
	is only which names the two files agree on. """
	found = set()
	for relative in _domain_modules():
		tree = ast.parse(_read(relative))
		for node in ast.walk(tree):
			if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
					and node.func.attr == "emit" and node.args
					and isinstance(node.args[0], ast.Constant)):
				found.add(node.args[0].value)
	assert found, "no emit() calls found in the domain — has the walk broken?"
	return found
