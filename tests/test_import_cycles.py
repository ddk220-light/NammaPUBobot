"""No module-level import cycles among the first-party packages.

WHY THIS FILE EXISTS. A circular import is a boot-time crash, and boot is the
one thing the test suite cannot exercise: conftest.py replaces `core.*` with
stubs precisely so tests never open a MySQL connection or a Discord client, so
nothing here ever executes the real import graph. `nextcord` is not even
installed in CI. The failure mode is therefore invisible until the bot starts
in production and dies with

    ImportError: cannot import name 'QueueChannel' from partially
    initialized module 'nammaoe2bot.pickup.channel' (most likely due to a circular
    import)

...which is a redeploy, not a test run.

So this resolves the graph statically, the same way tests/test_import_graph.py
resolves whether a target exists. Between them: that file says every import
points at something real, this one says the pointers do not form a loop.

WHAT COUNTS AS AN EDGE. Only imports at module level -- the ones that run when
the module is first loaded. A function-local import is not an edge, because by
the time it runs both modules are fully initialised; that is exactly why the
old codebase had 65 of them, and why removing that workaround safely needs a
check that the cycle it was hiding is really gone.

Two kinds of module-level import are deliberately NOT edges:

* `if TYPE_CHECKING:` blocks, which never execute at runtime. bot/context/
  uses one to annotate a QueueChannel without importing it, which is the only
  thing keeping channel <-> context acyclic.
* A submodule importing its own ancestor package (`nammaoe2bot.features.quiz.jobs` -> `nammaoe2bot.features.quiz`).
  Python runs a package's `__init__` before any submodule of it can be reached,
  so this edge exists for every package whose `__init__` imports its own
  submodules -- the normal case, not a defect. Cross-package loops are what
  this file is for.

HISTORY. Before the architecture restructure this test would have reported ONE
strongly-connected component containing 34 modules -- essentially the whole
bot -- because `bot/__init__.py` re-exported half the codebase and every module
reached the rest through it.
"""
import ast
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The first-party packages. `bot` and `core` are gone — dissolved into
# nammaoe2bot/. A dotted name whose first segment is not one of these is
# third party (or stdlib) and is not this file's business.
_PACKAGES = ("nammaoe2bot", "utils")
_SKIP_DIRS = {"__pycache__", ".replay_scratch", "data", ".git"}


def _module_name(path):
	rel = os.path.relpath(path, _REPO_ROOT)[: -len(".py")].replace(os.sep, ".")
	return rel[: -len(".__init__")] if rel.endswith(".__init__") else rel


def _modules():
	""" {dotted name: path} for every first-party module. """
	out = {}
	for package in _PACKAGES:
		for dirpath, dirnames, filenames in os.walk(os.path.join(_REPO_ROOT, package)):
			dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
			for filename in sorted(filenames):
				if filename.endswith(".py"):
					path = os.path.join(dirpath, filename)
					out[_module_name(path)] = path
	return out


def _targets(module, path, node):
	""" Dotted names one import statement refers to, absolute. """
	if isinstance(node, ast.Import):
		return [alias.name for alias in node.names]
	if node.level:                                     # relative: from . / .. import
		parts = module.split(".")
		if os.path.basename(path) != "__init__.py":    # a module, not a package
			parts = parts[:-1]
		parts = parts[: len(parts) - (node.level - 1)]
		prefix = ".".join(parts + ([node.module] if node.module else []))
	else:
		prefix = node.module or ""
	# `from x import y` may name a submodule OR an attribute; try both.
	return [prefix] + [f"{prefix}.{alias.name}" for alias in node.names]


def _is_type_checking(node):
	""" `if TYPE_CHECKING:` — never executed, so never an import edge. """
	return any(
		isinstance(n, ast.Name) and n.id == "TYPE_CHECKING"
		for n in ast.walk(node.test)
	)


def _module_level_imports(module, path, tree):
	""" Import nodes that RUN when this module loads. """
	for node in tree.body:
		if isinstance(node, (ast.Import, ast.ImportFrom)):
			yield node
		elif isinstance(node, ast.If):
			if _is_type_checking(node):
				continue
			for sub in ast.walk(node):
				if isinstance(sub, (ast.Import, ast.ImportFrom)):
					yield sub
		elif isinstance(node, ast.Try):                # try/except ImportError
			for sub in ast.walk(node):
				if isinstance(sub, (ast.Import, ast.ImportFrom)):
					yield sub


def _resolve(dotted, modules):
	""" The first-party module `dotted` names, or None.

	`from nammaoe2bot.pickup import stats` names nammaoe2bot.pickup.stats; bot/stats/ is a
	namespace package with no __init__.py, so there is no bot.stats module to
	fall back to and the walk must stop at the directory rather than blaming
	the grandparent. """
	parts = dotted.split(".")
	for i in range(len(parts), 0, -1):
		candidate = ".".join(parts[:i])
		if candidate in modules:
			return candidate
		if os.path.isdir(os.path.join(_REPO_ROOT, *candidate.split("."))):
			return None                                # namespace package
	return None


def _is_ancestor(parent, child):
	return child.startswith(parent + ".")


def _graph():
	modules = _modules()
	graph = {}
	for module, path in sorted(modules.items()):
		with open(path) as f:
			tree = ast.parse(f.read())
		deps = set()
		for node in _module_level_imports(module, path, tree):
			for dotted in _targets(module, path, node):
				target = _resolve(dotted, modules)
				if target and target != module and not _is_ancestor(target, module):
					deps.add(target)
		graph[module] = deps
	return graph


def _cycles(graph):
	""" Strongly-connected components with more than one member, plus any
	self-loop. Iterative Tarjan — the graph is a few hundred nodes deep. """
	index, low, on_stack, stack, found = {}, {}, set(), [], []
	counter = [0]

	def visit(root):
		work = [(root, iter(sorted(graph.get(root, ()))))]
		index[root] = low[root] = counter[0]
		counter[0] += 1
		stack.append(root)
		on_stack.add(root)
		while work:
			node, children = work[-1]
			descended = False
			for child in children:
				if child not in index:
					index[child] = low[child] = counter[0]
					counter[0] += 1
					stack.append(child)
					on_stack.add(child)
					work.append((child, iter(sorted(graph.get(child, ())))))
					descended = True
					break
				if child in on_stack:
					low[node] = min(low[node], index[child])
			if descended:
				continue
			work.pop()
			if work:
				low[work[-1][0]] = min(low[work[-1][0]], low[node])
			if low[node] == index[node]:
				component = []
				while True:
					member = stack.pop()
					on_stack.discard(member)
					component.append(member)
					if member == node:
						break
				if len(component) > 1:
					found.append(sorted(component))

	for node in sorted(graph):
		if node not in index:
			visit(node)
	found.extend([node] for node, deps in sorted(graph.items()) if node in deps)
	return found


def test_no_module_level_import_cycles():
	graph = _graph()
	cycles = _cycles(graph)
	if cycles:
		report = []
		for component in cycles:
			report.append(f"cycle of {len(component)}:")
			for member in component:
				inside = sorted(d for d in graph[member] if d in component)
				report.append(f"    {member} -> {', '.join(inside)}")
		raise AssertionError(
			"module-level import cycle(s) — the bot will not boot:\n" + "\n".join(report)
		)


def test_the_detector_sees_a_cycle_when_there_is_one():
	""" A green suite has to mean "no cycles", not "the walk found nothing".
	Every other assertion here is a negative, so this is the one that proves
	_cycles() can fail at all. """
	assert _cycles({"a": {"b"}, "b": {"a"}}) == [["a", "b"]]
	assert _cycles({"a": {"a"}}) == [["a"]]
	assert _cycles({"a": {"b"}, "b": set()}) == []


def test_the_graph_is_not_empty():
	""" And that it is reading real files: a typo in _PACKAGES or a broken
	walk would make the cycle check vacuously pass. """
	graph = _graph()
	assert len(graph) > 100, f"only found {len(graph)} modules"
	assert graph["nammaoe2bot.pickup.match.match"], "nammaoe2bot.pickup.match.match imports nothing?"
