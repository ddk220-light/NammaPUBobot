"""Every repo-internal import must resolve to something that exists.

WHY THIS FILE EXISTS. Stage 5b moved `utils/replay_quiz/` to `utils/replay/`
and rewrote nine `from utils.replay_quiz import ...` lines across two files --
and missed a third, `utils/classifications/pipeline/downloader.py`, which kept
importing the deleted package. `ingester.py` imports FROM that downloader, so
two modules died with ModuleNotFoundError the moment anything touched them.
Nothing in CI could see it: ruff does not resolve imports (F401 is about unused
names, not missing targets), and no test imported either module. The offline
classification backfill was simply broken, on main, silently.

WHY IT RESOLVES STATICALLY RATHER THAN BY IMPORTING. The obvious test -- walk
the tree and `importlib.import_module` everything -- needs an exemption list
for modules whose third-party dependencies are not installed in CI (mgz,
aiomysql, requests, ...). That exemption is the trap: it is written as
`except ModuleNotFoundError: skip`, and a ModuleNotFoundError naming
`utils.replay_quiz` is indistinguishable from one naming `mgz` unless somebody
takes care to distinguish them. The exemption would have swallowed exactly the
bug this file is here to catch.

So the sweep never imports anything. It parses each file with `ast`, resolves
every import statement in it (including function-local and relative ones), and
checks the repo-internal ones against the set of modules that actually exist on
disk plus the names those modules actually bind at module level. Third-party
imports are ignored outright rather than exempted, so there is no exemption
list to grow and nothing about a missing optional dependency can hide a missing
repo module.

`_module_level_names` deliberately answers "every name this module binds",
which includes names it imported itself: `from bot import identity` is
satisfied by `bot/identity.py`, and `from utils.db_helpers import create_pool`
by the def in that file. A module containing a star-import is treated as
binding anything, because resolving `from x import *` statically means
importing x, which is the thing this file will not do.
"""
import ast
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The first-party packages. `bot` is mid-migration into `nammaoe2bot` and both
# are live until Phase 2 finishes; a dotted name whose first segment is not one
# of these is third party (or stdlib) and is not this file's business.
_PACKAGES = ("bot", "nammaoe2bot", "utils")

# Directories the walk never descends into: caches, and the vendored mgz fork
# that only exists on a machine that has run the replay pipeline.
_SKIP_DIRS = {"__pycache__", ".replay_scratch", "data", ".git"}


def _py_files():
	""" (dotted module name, absolute path) for every .py file in the three
	first-party packages, plus the two root-level entry points. """
	for package in _PACKAGES:
		for dirpath, dirnames, filenames in os.walk(os.path.join(_REPO_ROOT, package)):
			dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
			for filename in sorted(filenames):
				if not filename.endswith(".py"):
					continue
				path = os.path.join(dirpath, filename)
				rel = os.path.relpath(path, _REPO_ROOT)
				parts = rel[:-3].split(os.sep)
				if parts[-1] == "__init__":
					parts = parts[:-1]
				yield ".".join(parts), path
	for filename in ("PUBobot2.py", "start.py"):
		path = os.path.join(_REPO_ROOT, filename)
		if os.path.exists(path):
			yield filename[:-3], path


def _existing_modules():
	""" Every importable first-party dotted name: modules AND packages. """
	names = set()
	for dotted, _path in _py_files():
		names.add(dotted)
		# A package directory is importable in its own right and so is every
		# prefix of it, whether or not the walk reached its __init__.py.
		parts = dotted.split(".")
		for i in range(1, len(parts)):
			names.add(".".join(parts[:i]))
	return names


def _is_internal(dotted):
	return dotted.split(".")[0] in _PACKAGES


def _resolve(node, module_dotted, is_package):
	""" The absolute module an ImportFrom names, or None for an unresolvable
	relative level.

	`from . import x` inside bot/derived/game_labels.py means bot.derived, so
	the anchor for a non-package module is its PARENT; for a package's
	__init__.py it is the package itself. """
	if not node.level:
		return node.module
	parts = module_dotted.split(".")
	if not is_package:
		parts = parts[:-1]
	if node.level > 1:
		parts = parts[:-(node.level - 1)]
	if not parts:
		return None
	base = ".".join(parts)
	return f"{base}.{node.module}" if node.module else base


def _module_level_names(path):
	""" Every name a module binds at module level, or None when it star-imports
	(in which case it may bind anything and the caller must not judge). """
	with open(path, encoding="utf-8") as fh:
		tree = ast.parse(fh.read(), filename=path)
	names = set()
	for node in tree.body:
		if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
			return None
		if isinstance(node, ast.Import | ast.ImportFrom):
			for alias in node.names:
				names.add(alias.asname or alias.name.split(".")[0])
		elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
			names.add(node.name)
		elif isinstance(node, ast.Assign):
			for target in node.targets:
				names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
		elif isinstance(node, ast.AnnAssign | ast.AugAssign):
			if isinstance(node.target, ast.Name):
				names.add(node.target.id)
		elif isinstance(node, ast.Try):
			# try/except ImportError around an optional import still binds the
			# name in the branch that succeeds.
			for sub in ast.walk(node):
				if isinstance(sub, ast.Import | ast.ImportFrom):
					if isinstance(sub, ast.ImportFrom) and any(a.name == "*" for a in sub.names):
						return None
					for alias in sub.names:
						names.add(alias.asname or alias.name.split(".")[0])
	return names


def _path_of(dotted, files):
	""" The file backing a dotted name, preferring `x.py` over `x/__init__.py`
	only because the walk yields one entry per file and packages are keyed on
	the package name already. """
	return files.get(dotted)


def _unresolved_imports():
	files = dict(_py_files())
	existing = _existing_modules()
	broken = []

	for dotted, path in sorted(files.items()):
		with open(path, encoding="utf-8") as fh:
			tree = ast.parse(fh.read(), filename=path)
		is_package = path.endswith(os.sep + "__init__.py")

		for node in ast.walk(tree):
			if isinstance(node, ast.Import):
				for alias in node.names:
					if _is_internal(alias.name) and alias.name not in existing:
						broken.append(f"{os.path.relpath(path, _REPO_ROOT)}: import {alias.name}")
				continue
			if not isinstance(node, ast.ImportFrom):
				continue

			target = _resolve(node, dotted, is_package)
			if target is None or not _is_internal(target):
				continue
			if target not in existing:
				broken.append(f"{os.path.relpath(path, _REPO_ROOT)}: from {target} import ...")
				continue

			# The module exists; now the names taken out of it. Each is either
			# a submodule of it or a name it binds at module level.
			container = _path_of(target, files)
			if container is None:
				continue          # namespace package with no __init__.py
			bound = _module_level_names(container)
			if bound is None:
				continue          # star-import: anything may be in there
			for alias in node.names:
				if alias.name == "*" or alias.name in bound:
					continue
				if f"{target}.{alias.name}" in existing:
					continue
				broken.append(
					f"{os.path.relpath(path, _REPO_ROOT)}: from {target} import {alias.name}")
	return broken


def test_every_repo_internal_import_resolves():
	""" The guard proper. A failure here means some module in bot/, core/ or
	utils/ imports a first-party module or name that does not exist -- i.e. it
	raises ModuleNotFoundError/ImportError the moment anything touches it. """
	broken = _unresolved_imports()
	assert broken == [], (
		"repo-internal imports that cannot resolve (each of these is a "
		"ModuleNotFoundError/ImportError waiting for its first caller):\n  "
		+ "\n  ".join(broken))


def test_the_sweep_actually_reaches_the_offline_classification_pipeline():
	""" The file stage 5b missed, named explicitly.

	The guard above is only as good as its walk, and a walk that quietly
	stopped covering a directory would still pass -- on fewer files. These four
	are the ones the stage-5b move touched or should have touched. """
	swept = dict(_py_files())
	for dotted in ("utils.classifications.pipeline.downloader",
	               "utils.classifications.pipeline.ingester",
	               "utils.classifications.runner",
	               "utils.replay.download"):
		assert dotted in swept, f"{dotted} is outside the import guard's walk"


def test_the_guard_catches_a_move_that_forgets_a_sibling(tmp_path, monkeypatch):
	""" The meta-test: without this, the sweep could be silently defanged (an
	over-broad exemption, a walk that stops descending) and still report green.

	It rebuilds the exact stage-5b shape in a scratch tree -- a package that
	was moved, and a module still importing its old name -- and asserts the
	resolver reports it. """
	pkg = tmp_path / "utils"
	pkg.mkdir()
	(pkg / "__init__.py").write_text("")
	(pkg / "replay").mkdir()
	(pkg / "replay" / "__init__.py").write_text("")
	(pkg / "replay" / "download.py").write_text("def resolve_profile_ids(gid):\n\treturn []\n")
	(pkg / "stale.py").write_text("from utils.replay_quiz import download as dl\n")
	(pkg / "fine.py").write_text("from utils.replay import download as dl\n")
	(pkg / "missing_name.py").write_text("from utils.replay.download import no_such_function\n")

	monkeypatch.setattr("tests.test_import_graph._REPO_ROOT", str(tmp_path))
	broken = _unresolved_imports()

	assert any("stale.py" in b and "utils.replay_quiz" in b for b in broken)
	assert any("missing_name.py" in b and "no_such_function" in b for b in broken)
	assert not any("fine.py" in b for b in broken)
