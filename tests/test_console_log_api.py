"""Every `log.<method>(...)` in the repo must name a method `nammaoe2bot.runtime.console.Log`
actually defines.

WHY THIS FILE EXISTS. `Log` shipped chat / debug / command / info / error and no
`warning`, while fifteen call sites across nammaoe2bot/features/lobby/, nammaoe2bot/features/betting/ and
nammaoe2bot/runtime/migrations.py called `log.warning(...)`. Every one of them raised
AttributeError — and every one of them sits INSIDE an `except` block, so the
exception it was trying to report was replaced by a different, uglier one
escaping the handler. `nammaoe2bot/features/betting/interactions.py::_refresh_card` was the
worst case: a best-effort card refresh that could take the whole bet handler
down with it.

Nothing could see this. ruff does not resolve attributes. The unit suite cannot
see it either, and that is the important part: `tests/conftest.py` fakes
`nammaoe2bot.runtime.console` with a null logger whose `__getattr__` answers to ANY name, so
under pytest `log.warning` has always worked. A test that drove a call site and
asserted "it logged" would have passed on the broken code.

So this checks the two halves against each other STATICALLY, the
tests/test_import_graph.py pattern: the method names `Log` binds, parsed out of
the real nammaoe2bot/runtime/console.py, versus the attribute names the call sites reach for.
Neither side is mocked and neither is imported — importing nammaoe2bot.runtime.console for real
creates a logs/ directory and opens a file, and importing it under the conftest
stub would hand back the fake and prove nothing.
"""
import ast
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONSOLE = os.path.join(_REPO_ROOT, "nammaoe2bot", "runtime", "console.py")

# Where `log` means the console singleton. utils/ is standalone tooling with its
# own logging habits and is not in the bot's import path.
_ROOTS = ("bot", "nammaoe2bot")
_SKIP_DIRS = {"__pycache__", ".replay_scratch", "data", ".git"}


def _log_method_names():
	""" Every method `Log` defines, from the source rather than from an import. """
	with open(_CONSOLE, encoding="utf-8") as fh:
		tree = ast.parse(fh.read(), filename=_CONSOLE)
	cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "Log")
	return {n.name for n in cls.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}


def _py_files():
	for root in _ROOTS:
		for dirpath, dirnames, filenames in os.walk(os.path.join(_REPO_ROOT, root)):
			dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
			for filename in sorted(filenames):
				if filename.endswith(".py"):
					yield os.path.join(dirpath, filename)


def _log_calls():
	""" (relative path, line, attribute) for every `log.<attr>(...)` call.

	Only a bare `log` name counts: `self.log(...)` inside the Log class itself is
	a different thing, and nothing in bot/ or core/ shadows the module-level
	singleton (there is exactly one `log = ` assignment in the two trees). """
	for path in _py_files():
		with open(path, encoding="utf-8") as fh:
			tree = ast.parse(fh.read(), filename=path)
		for node in ast.walk(tree):
			if not isinstance(node, ast.Call):
				continue
			func = node.func
			if (isinstance(func, ast.Attribute)
					and isinstance(func.value, ast.Name) and func.value.id == "log"):
				yield os.path.relpath(path, _REPO_ROOT), node.lineno, func.attr


def test_log_defines_warning():
	""" The fix itself, named. Severity between info and error, so the whole
	family reads chat < debug < command < info < warning < error. """
	assert "warning" in _log_method_names()


def test_warning_is_gated_between_info_and_error():
	""" A method that exists but never prints would close the AttributeError and
	still lose the message.

	`LogLevelToInt` has no WARNING key on purpose — it is the set of thresholds
	config.cfg may hold, not the set of severities the bot emits — so `warning`
	has to borrow a neighbour's threshold, and which one it borrows is the whole
	behavioural question. INFO's (`<= 3`) means a warning prints wherever info
	prints and goes quiet when the operator asked for errors only. ERRORS' would
	mean the opposite: warnings surviving a setting that says "errors only",
	i.e. warnings treated as errors. """
	with open(_CONSOLE, encoding="utf-8") as fh:
		tree = ast.parse(fh.read(), filename=_CONSOLE)
	cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "Log")
	methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}

	def _threshold(name):
		test = methods[name].body[-1].test          # `if self.loglevel <= N:`
		return test.comparators[0].value

	def _tag(name):
		call = methods[name].body[-1].body[-1].value  # `self.log(data, 'TAG')`
		return call.args[-1].value

	assert _tag("warning") == "WARNING", "a warning must be labelled as one in the log file"
	assert _threshold("warning") == _threshold("info"), (
		"warning is more severe than info, so anything verbose enough to print info "
		"must print warnings too")
	assert _threshold("warning") < _threshold("error"), (
		"LOG_LEVEL=ERRORS means errors only; a warning that survives it is being "
		"treated as an error")


def test_every_log_call_site_names_a_method_that_exists():
	""" The guard proper, and the thing that would have caught this on day one.
	Each entry below is an AttributeError raised from inside an except block. """
	known = _log_method_names()
	broken = [f"{path}:{line}: log.{attr}(...)"
			  for path, line, attr in _log_calls() if attr not in known]
	assert broken == [], (
		"log calls naming a method nammaoe2bot.runtime.console.Log does not define — each raises "
		"AttributeError, and every one of them lives in an error path:\n  "
		+ "\n  ".join(broken))


def test_the_sweep_actually_reaches_the_files_that_were_broken():
	""" The guard is only as good as its walk. These five modules held the
	pre-existing log.warning calls; a walk that quietly stopped covering a
	directory would still report green, on fewer files. """
	seen = {path for path, _line, _attr in _log_calls()}
	for path in ("nammaoe2bot/features/lobby/api.py", "nammaoe2bot/features/lobby/completed.py", "nammaoe2bot/features/lobby/watcher.py",
				 "nammaoe2bot/features/lobby/announce.py", "nammaoe2bot/features/betting/flow.py",
				 "nammaoe2bot/features/betting/interactions.py"):
		assert path in seen, f"{path} is outside the log-call sweep"
