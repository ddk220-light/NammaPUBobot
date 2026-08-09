"""The wiring between the match flow and nammaoe2bot/features/betting — the part that was
silently dead in production.

WHAT HAPPENED. nammaoe2bot/features/betting/__init__.py used to end with `from .jobs import jobs`, binding
the PredictionJobs singleton onto the package under the same name as the `jobs`
SUBMODULE. Every call site in bot/match/ then did
`from nammaoe2bot.features.betting import jobs as prediction_jobs` and reached for a module
function on it:

    AttributeError: 'PredictionJobs' object has no attribute 'open_for_match'

Each call site wraps itself in a best-effort guard so a prediction failure can
never break a match — correct design, and exactly what turned a total feature
outage into log noise. prediction_posts held ZERO rows across 3312 ranked
matches. A second, independent bug sat behind it: PredictionJobs._run called
`self._freeze`, which is also a module function, so the freeze sweep would have
died the moment the first bug was fixed. The two hid each other.

Neither is reachable by a test that mocks the import boundary, which is why the
tests here assert on the REAL module objects and the REAL call sites: what the
package exports, what those exports are, and what bot/match/ actually types.
No pytest-asyncio in this repo, so every coroutine is driven with asyncio.run().
"""
import ast
import asyncio
import inspect
import sys
import types
from pathlib import Path

import nammaoe2bot.features.betting as betting
import nammaoe2bot.features.betting.flow as jobs_module

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HOOKS = ("open_for_match", "restart_for_match", "resolve_for_match", "void_for_match")


# ── what the package exports ─────────────────────────────────────────────

def test_the_package_exports_every_match_lifecycle_hook():
	""" The fix. `from nammaoe2bot.features.betting import open_for_match` has exactly one
	possible meaning; `from nammaoe2bot.features.betting import jobs` does not. """
	for name in _HOOKS:
		assert hasattr(betting, name), f"nammaoe2bot.features.betting no longer exports {name}"
		assert inspect.iscoroutinefunction(getattr(betting, name)), name


def test_the_exported_hooks_are_the_modules_functions_not_the_singletons():
	""" The precise shape of the bug: the names must resolve to the functions in
	nammaoe2bot/features/betting/flow.py, never to attributes fetched off the
	PredictionJobs instance. """
	for name in _HOOKS:
		assert getattr(betting, name) is getattr(jobs_module, name), name


def test_the_package_attribute_jobs_is_still_the_singleton():
	""" nammaoe2bot/discord/events.py drives every package as `bot.<pkg>.jobs.think(frame_time)`,
	the convention nammaoe2bot/features/quiz, nammaoe2bot/features/lobby and nammaoe2bot/ingest all share. The fix
	renamed the MODULE, not this export, so that call site is untouched. """
	assert isinstance(betting.jobs, jobs_module.PredictionJobs)
	assert inspect.iscoroutinefunction(betting.jobs.think)


def test_the_singleton_does_not_carry_the_lifecycle_hooks():
	""" REPRODUCES THE ORIGINAL FAILURE. This is what every call site was
	holding, and every attribute below is the AttributeError that was logged
	and swallowed on each of 3312 ranked matches. If a later refactor makes
	these resolve, the shadowing has become harmless and this test should be
	deleted rather than "fixed" — but until then it is the thing that broke. """
	for name in _HOOKS:
		assert not hasattr(betting.jobs, name), (
			f"PredictionJobs now has {name}; the call sites' old import shape would "
			f"appear to work, which is how this went unnoticed for 3312 matches")


def test_the_submodule_name_no_longer_collides_with_the_singleton():
	""" The root cause, closed. While the module was called jobs.py, BOTH
	`from nammaoe2bot.features.betting import jobs` and `import nammaoe2bot.features.betting.jobs as m`
	handed back the instance — the second one bit this very test file as it was
	being written. A distinct module name makes the collision impossible rather
	than something every future caller has to remember. """
	assert not (_REPO_ROOT / "nammaoe2bot" / "features" / "betting" / "jobs.py").exists()
	assert isinstance(sys.modules["nammaoe2bot.features.betting.flow"], types.ModuleType)
	assert betting.flow is sys.modules["nammaoe2bot.features.betting.flow"]


# ── what the call sites actually type ────────────────────────────────────

# THE CALL SITES MOVED. bot/match/ used to import these four hooks directly;
# the domain now announces a lifecycle event and nammaoe2bot/wiring.py answers it, so
# wiring.py IS the call site and is the only file scanned below. The bug this
# section guards against is unchanged: a name that does not resolve to what the
# caller thinks it does, failing inside a best-effort guard where nobody sees it.
_CALL_SITE = "nammaoe2bot/wiring.py"


def _prediction_imports(relative_path):
	"""Every `from nammaoe2bot.features.betting import ...` in a file, as {module: [names]}.

	Parsed rather than executed: importing bot.match pulls in the Discord client.
	"""
	source = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
	out = []
	for node in ast.walk(ast.parse(source)):
		if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("nammaoe2bot.features.betting"):
			out.append((node.module, [a.name for a in node.names]))
	return out


def _prediction_attributes(relative_path):
	""" Every `betting.<name>` reached in a file.

	wiring.py imports the PACKAGE and resolves each hook at call time — which is
	what lets a test swap one — so the names it depends on are attribute
	accesses, not import targets. """
	source = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
	return {node.attr for node in ast.walk(ast.parse(source))
			if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
			and node.value.id == "betting"}


def test_no_caller_imports_the_shadowed_name_again():
	""" The regression guard. `from nammaoe2bot.features.betting import jobs` in a module that
	then calls a lifecycle function is the exact line that broke this, and it
	fails at runtime inside a guard that hides it — so it has to fail here
	instead. """
	for module, names in _prediction_imports(_CALL_SITE):
		assert "jobs" not in names, (
			f"{_CALL_SITE} imports the shadowed name `jobs` from {module}; that resolves "
			f"to the PredictionJobs instance, not the module, and every lifecycle call "
			f"on it raises AttributeError into a best-effort guard")


def test_the_domain_no_longer_calls_betting_at_all():
	""" The direction. A match announces that it finished; it does not know that
	a betting book exists. If this ever fails, the composition root has been
	bypassed and the eleven imports are growing back. """
	for path in ("nammaoe2bot/pickup/match/match.py", "nammaoe2bot/pickup/match/substitution.py", "nammaoe2bot/pickup/match/checkin.py"):
		assert not _prediction_imports(path), f"{path} imports nammaoe2bot.features.betting again"


def test_every_lifecycle_call_site_names_a_hook_the_package_exports():
	""" Both halves together: the call site names real exports, and the package
	really exports them. Either alone passes while the feature is dead. """
	seen = set()
	for name in _prediction_attributes(_CALL_SITE):
		if name not in _HOOKS:
			continue                      # `betting.flow` etc., not a hook
		assert hasattr(betting, name), f"{_CALL_SITE} calls {name}, which is not exported"
		seen.add(name)
	assert seen == set(_HOOKS), f"call sites cover {sorted(seen)}, expected all of {sorted(_HOOKS)}"


# ── the freeze sweep, which the first bug was hiding ─────────────────────

def test_the_freeze_sweep_calls_the_module_function_not_a_method():
	""" THE SECOND BUG. PredictionJobs._run called `self._freeze`, and _freeze is
	a module function — so the sweep raised AttributeError into its per-post
	guard on every pass. It was invisible only because the first bug meant no
	post ever became due to freeze. Fixing the import alone would have shipped a
	feature that opens votes and never resolves them. """
	# The CALL, not the prose: _run carries a comment explaining the bug, which
	# necessarily contains the broken spelling.
	code = "\n".join(
		line.split("#", 1)[0] for line in inspect.getsource(jobs_module.PredictionJobs._run).split("\n"))

	assert "await self._freeze(" not in code
	assert "await _freeze(post, now, headline)" in code
	assert not hasattr(jobs_module.PredictionJobs, "_freeze"), (
		"_freeze is a module function; a method of the same name would make both "
		"spellings work and hide which one the sweep uses")


def test_the_sweep_freezes_every_due_post_and_survives_one_that_fails():
	""" Drives _run against a fake store: the loop reaches the real module-level
	_freeze for each due post, and one post blowing up costs only that post.
	Nothing here mocks the call being tested — the AttributeError the old code
	raised would surface as a failure to freeze post 2. """
	frozen, calls = [], {"n": 0}

	async def _due_to_freeze(_now):
		return [{"id": 1}, {"id": 2}, {"id": 3}]

	async def _empty(_before):
		return []

	async def _fake_freeze(post, now, headline=None):
		calls["n"] += 1
		if post["id"] == 2:
			raise RuntimeError("simulated freeze failure")
		frozen.append(post["id"])

	original_store, original_freeze = jobs_module.store, jobs_module._freeze
	# No open_posts on this fake, deliberately: a PredictionJobs nobody wired a
	# launch check into must not go looking for one. Adding it here would hide
	# a sweep that reached for the lobby feature unconditionally.
	jobs_module.store = types.SimpleNamespace(
		due_to_freeze=_due_to_freeze, unsettled_books=_empty, abandoned_books=_empty)
	jobs_module._freeze = _fake_freeze
	try:
		asyncio.run(jobs_module.PredictionJobs()._run())
	finally:
		jobs_module.store, jobs_module._freeze = original_store, original_freeze

	assert calls["n"] == 3, "every due post is attempted"
	assert frozen == [1, 3], "a failing post costs only itself"


# ── the second reason a book closes: the game started ────────────────────

def _sweep(due=(), open_posts=(), launched=None):
	"""Run one _run() against fakes and return [(post_id, headline)] frozen.

	`launched` is the provider nammaoe2bot/wiring.py injects — None means
	nobody wired one, which is the pre-launch-cutoff bot.
	"""
	seen = []

	async def _due_to_freeze(_now):
		return [dict(p) for p in due]

	async def _open_posts():
		return [dict(p) for p in open_posts]

	async def _empty(_before):
		return []

	async def _fake_freeze(post, _now, headline=None):
		seen.append((post["id"], headline))

	original_store, original_freeze = jobs_module.store, jobs_module._freeze
	jobs_module.store = types.SimpleNamespace(
		due_to_freeze=_due_to_freeze, open_posts=_open_posts,
		unsettled_books=_empty, abandoned_books=_empty)
	jobs_module._freeze = _fake_freeze
	try:
		jobs = jobs_module.PredictionJobs()
		if launched is not None:
			jobs.launched_among = launched
		asyncio.run(jobs._run())
	finally:
		jobs_module.store, jobs_module._freeze = original_store, original_freeze
	return seen


def test_a_book_closes_the_moment_its_game_starts_even_with_time_left():
	""" THE POINT OF THE WHOLE THING. A ten-minute window over a game that
	starts at minute four leaves six minutes in which the civs, the starting
	positions and the first fight are all on screen — on the bot's own lobby
	card — while the buttons are still live. Nothing about `freezes_at` is due
	here; the launch alone has to be enough. """
	async def _launched(_ids):
		return {77}

	assert _sweep(due=[], open_posts=[{"id": 9, "match_id": 77}], launched=_launched) == [
		(9, jobs_module.LAUNCH_HEADLINE)]


def test_a_book_whose_game_has_not_started_keeps_taking_bets():
	async def _launched(_ids):
		return set()

	assert _sweep(due=[], open_posts=[{"id": 9, "match_id": 77}], launched=_launched) == []


def test_a_book_that_is_both_overdue_and_launched_is_frozen_exactly_once():
	""" Both reasons can be true at once, and _freeze takes a snapshot of the
	book: running it twice over one post would put two passes on one snapshot.
	The timer wins the tie — a book past its own deadline closed on time
	whatever else happened to be true of it — so the headline stays the plain
	one. """
	async def _launched(_ids):
		return {77}

	post = {"id": 9, "match_id": 77}
	assert _sweep(due=[post], open_posts=[post], launched=_launched) == [(9, None)]


def test_a_lobby_outage_still_lets_the_timer_close_books():
	""" The lobby feature is best-effort by design — an unofficial websocket and
	an unofficial API — and betting must not inherit its outages. A book that
	cannot find out whether its game started still has a timer, which is
	exactly the behaviour that existed before this cutoff. """
	async def _boom(_ids):
		raise RuntimeError("lobby socket down")

	assert _sweep(due=[{"id": 1, "match_id": 5}],
				  open_posts=[{"id": 2, "match_id": 6}], launched=_boom) == [(1, None)]


def test_an_unwired_sweep_never_reaches_for_the_lobby_feature():
	""" `launched_among` defaults to None, and betting has to work with the
	lobby feature switched off entirely. The fake store here has no open_posts
	at all, so a sweep that asked for one would raise rather than quietly
	degrade. """
	async def _due_to_freeze(_now):
		return [{"id": 1, "match_id": 5}]

	async def _empty(_before):
		return []

	seen = []

	async def _fake_freeze(post, _now, headline=None):
		seen.append((post["id"], headline))

	original_store, original_freeze = jobs_module.store, jobs_module._freeze
	jobs_module.store = types.SimpleNamespace(
		due_to_freeze=_due_to_freeze, unsettled_books=_empty, abandoned_books=_empty)
	jobs_module._freeze = _fake_freeze
	try:
		asyncio.run(jobs_module.PredictionJobs()._run())
	finally:
		jobs_module.store, jobs_module._freeze = original_store, original_freeze

	assert seen == [(1, None)]


def test_the_launch_check_is_only_asked_about_books_the_timer_did_not_claim():
	""" A post the timer already owns has no second answer to be had about it,
	and asking anyway means a query per sweep that can only produce a duplicate.
	"""
	asked = []

	async def _launched(ids):
		asked.append(sorted(ids))
		return set()

	_sweep(due=[{"id": 1, "match_id": 5}],
		   open_posts=[{"id": 1, "match_id": 5}, {"id": 2, "match_id": 6}],
		   launched=_launched)

	assert asked == [[6]], "match 5's post was already claimed by its timer"


def test_observation_mode_explicitly_leaves_betting_timer_only():
	"""The manual /lobby path currently calls a LINK `in_progress`, so handing
	that status query to betting closes a ten-minute book as soon as the id is
	pasted. Until live launch/cancel traces establish a truthful signal, boot
	must actively clear the provider rather than merely forgetting to wire it."""
	from nammaoe2bot import wiring

	original = betting.jobs.launched_among
	try:
		betting.jobs.launched_among = object()  # prove configure clears stale state
		wiring.wire_lobby_to_betting()
		assert wiring.BETTING_LAUNCH_CUTOFF_ENABLED is False
		assert betting.jobs.launched_among is None
	finally:
		betting.jobs.launched_among = original


def test_the_launch_provider_is_still_ready_for_the_evidence_backed_cutover(monkeypatch):
	"""Observation mode is a switch around the existing durable provider, not
	a deletion of the path tomorrow's captured evidence is meant to validate."""
	from nammaoe2bot import wiring
	from nammaoe2bot.features.lobby import started as lobby_started

	original = betting.jobs.launched_among
	try:
		monkeypatch.setattr(wiring, "BETTING_LAUNCH_CUTOFF_ENABLED", True)
		wiring.wire_lobby_to_betting()
		assert betting.jobs.launched_among is lobby_started.launched_among
	finally:
		betting.jobs.launched_among = original


def test_boot_calls_the_lobby_wiring_and_not_only_the_lifecycle_one():
	""" wire_match_lifecycle has been called from bootstrap since it existed;
	the new one is a second call beside it, and a second call is exactly the
	kind of line that gets written in the composition root and never added to
	the boot sequence. """
	source = (_REPO_ROOT / "nammaoe2bot" / "bootstrap.py").read_text()
	assert "wire_lobby_to_betting()" in source
	assert "wire_match_lifecycle(app)" in source


# ── the gate the match flow applies ──────────────────────────────────────

def test_open_is_a_no_op_for_an_unranked_match_without_touching_the_store():
	""" Unranked matches never report a winner to score against, so no card is
	posted. Asserted by driving the real function with a store that raises: the
	guard has to return BEFORE any write, not swallow an exception after one. """
	class _Exploding:
		def __getattr__(self, name):
			raise AssertionError(f"unranked match reached store.{name}")

	match = types.SimpleNamespace(id=7, ranked=False, cfg={"predictions_enabled": True})
	original = jobs_module.store
	jobs_module.store = _Exploding()
	try:
		asyncio.run(jobs_module.open_for_match(match))
	finally:
		jobs_module.store = original


def test_open_is_a_no_op_when_the_queue_has_predictions_disabled():
	class _Exploding:
		def __getattr__(self, name):
			raise AssertionError(f"disabled queue reached store.{name}")

	match = types.SimpleNamespace(id=7, ranked=True, cfg={"predictions_enabled": False})
	original = jobs_module.store
	jobs_module.store = _Exploding()
	try:
		asyncio.run(jobs_module.open_for_match(match))
	finally:
		jobs_module.store = original


def test_a_store_failure_never_escapes_into_the_match_flow():
	""" The guard that hid all of this is still the right guard: a prediction
	failure must not break a match. It stays -- what changes is that the thing
	it was hiding is now pinned by the tests above. """
	class _Failing:
		async def create_post(self, *_a, **_k):
			raise RuntimeError("database down")

	match = types.SimpleNamespace(id=7, ranked=True, cfg={"predictions_enabled": True})
	original = jobs_module.store
	jobs_module.store = _Failing()
	try:
		asyncio.run(jobs_module.open_for_match(match))   # must not raise
	finally:
		jobs_module.store = original


def test_events_still_drives_the_singleton_through_the_package_attribute():
	""" nammaoe2bot/discord/events.py is the one caller that legitimately wants the instance.

	It used to spell this `nammaoe2bot.features.betting.jobs.think(...)`, reaching the
	package through the re-export shelf in bot/__init__.py. That shelf is gone,
	so the module imports the package by name and the attribute lookup is the
	same one — `betting.jobs` is still the PredictionJobs instance the
	package binds over its own submodule, which is the whole reason flow.py is
	not called jobs.py. Both halves are asserted: the import that makes the
	name resolve, and the call that drives the tick. """
	source = (_REPO_ROOT / "nammaoe2bot" / "discord" / "events.py").read_text(encoding="utf-8")
	assert "from nammaoe2bot.features import betting" in source
	assert "betting.jobs.think(" in source


assert "pytest_asyncio" not in sys.modules, (
	"pytest-asyncio would make an `async def test_` here silently skip; every test "
	"in this file drives its coroutines with asyncio.run() on purpose")
