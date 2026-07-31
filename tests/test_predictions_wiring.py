"""The wiring between the match flow and bot/predictions — the part that was
silently dead in production.

WHAT HAPPENED. bot/predictions/__init__.py used to end with `from .jobs import jobs`, binding
the PredictionJobs singleton onto the package under the same name as the `jobs`
SUBMODULE. Every call site in bot/match/ then did
`from bot.predictions import jobs as prediction_jobs` and reached for a module
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

import bot.predictions as predictions
import bot.predictions.flow as jobs_module

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HOOKS = ("open_for_match", "restart_for_match", "resolve_for_match", "void_for_match")


# ── what the package exports ─────────────────────────────────────────────

def test_the_package_exports_every_match_lifecycle_hook():
	""" The fix. `from bot.predictions import open_for_match` has exactly one
	possible meaning; `from bot.predictions import jobs` does not. """
	for name in _HOOKS:
		assert hasattr(predictions, name), f"bot.predictions no longer exports {name}"
		assert inspect.iscoroutinefunction(getattr(predictions, name)), name


def test_the_exported_hooks_are_the_modules_functions_not_the_singletons():
	""" The precise shape of the bug: the names must resolve to the functions in
	bot/predictions/flow.py, never to attributes fetched off the
	PredictionJobs instance. """
	for name in _HOOKS:
		assert getattr(predictions, name) is getattr(jobs_module, name), name


def test_the_package_attribute_jobs_is_still_the_singleton():
	""" bot/events.py drives every package as `bot.<pkg>.jobs.think(frame_time)`,
	the convention bot/quiz, bot/lobby and bot/replay_stats all share. The fix
	renamed the MODULE, not this export, so that call site is untouched. """
	assert isinstance(predictions.jobs, jobs_module.PredictionJobs)
	assert inspect.iscoroutinefunction(predictions.jobs.think)


def test_the_singleton_does_not_carry_the_lifecycle_hooks():
	""" REPRODUCES THE ORIGINAL FAILURE. This is what every call site was
	holding, and every attribute below is the AttributeError that was logged
	and swallowed on each of 3312 ranked matches. If a later refactor makes
	these resolve, the shadowing has become harmless and this test should be
	deleted rather than "fixed" — but until then it is the thing that broke. """
	for name in _HOOKS:
		assert not hasattr(predictions.jobs, name), (
			f"PredictionJobs now has {name}; the call sites' old import shape would "
			f"appear to work, which is how this went unnoticed for 3312 matches")


def test_the_submodule_name_no_longer_collides_with_the_singleton():
	""" The root cause, closed. While the module was called jobs.py, BOTH
	`from bot.predictions import jobs` and `import bot.predictions.jobs as m`
	handed back the instance — the second one bit this very test file as it was
	being written. A distinct module name makes the collision impossible rather
	than something every future caller has to remember. """
	assert not (_REPO_ROOT / "bot" / "predictions" / "jobs.py").exists()
	assert isinstance(sys.modules["bot.predictions.flow"], types.ModuleType)
	assert predictions.flow is sys.modules["bot.predictions.flow"]


# ── what the call sites actually type ────────────────────────────────────

def _prediction_imports(relative_path):
	"""Every `from bot.predictions import ...` in a file, as {module: [names]}.

	Parsed rather than executed: importing bot.match pulls in the Discord client.
	"""
	source = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
	out = []
	for node in ast.walk(ast.parse(source)):
		if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("bot.predictions"):
			out.append((node.module, [a.name for a in node.names]))
	return out


def test_no_caller_imports_the_shadowed_name_again():
	""" The regression guard. `from bot.predictions import jobs` in a module that
	then calls a lifecycle function is the exact line that broke this, and it
	fails at runtime inside a guard that hides it — so it has to fail here
	instead. """
	for path in ("bot/match/match.py", "bot/match/draft.py"):
		for module, names in _prediction_imports(path):
			assert "jobs" not in names, (
				f"{path} imports the shadowed name `jobs` from {module}; that resolves to "
				f"the PredictionJobs instance, not the module, and every lifecycle call "
				f"on it raises AttributeError into a best-effort guard")


def test_every_lifecycle_call_site_imports_a_hook_the_package_exports():
	""" Both halves together: the call sites name real exports, and the package
	really exports them. Either alone passes while the feature is dead. """
	seen = set()
	for path in ("bot/match/match.py", "bot/match/draft.py"):
		for _module, names in _prediction_imports(path):
			for name in names:
				assert name in _HOOKS, f"{path} imports unexpected {name} from bot.predictions"
				assert hasattr(predictions, name), f"{path} imports {name}, which is not exported"
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
	assert "await _freeze(post, now)" in code
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

	async def _fake_freeze(post, now):
		calls["n"] += 1
		if post["id"] == 2:
			raise RuntimeError("simulated freeze failure")
		frozen.append(post["id"])

	original_store, original_freeze = jobs_module.store, jobs_module._freeze
	jobs_module.store = types.SimpleNamespace(due_to_freeze=_due_to_freeze)
	jobs_module._freeze = _fake_freeze
	try:
		asyncio.run(jobs_module.PredictionJobs()._run())
	finally:
		jobs_module.store, jobs_module._freeze = original_store, original_freeze

	assert calls["n"] == 3, "every due post is attempted"
	assert frozen == [1, 3], "a failing post costs only itself"


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
	""" bot/events.py is the one caller that legitimately wants the instance. """
	source = (_REPO_ROOT / "bot" / "events.py").read_text(encoding="utf-8")
	assert "bot.predictions.jobs.think(" in source


assert "pytest_asyncio" not in sys.modules, (
	"pytest-asyncio would make an `async def test_` here silently skip; every test "
	"in this file drives its coroutines with asyncio.run() on purpose")
