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
which includes names it imported itself: `from nammaoe2bot.features.identity import resolver` is
satisfied by `nammaoe2bot/features/identity/resolver.py`, and `from utils.db_helpers import create_pool`
by the def in that file. A module containing a star-import is treated as
binding anything, because resolving `from x import *` statically means
importing x, which is the thing this file will not do.
"""
import ast
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The first-party packages. `bot` and `core` are gone — dissolved into
# nammaoe2bot/ — and are guarded by name instead; see _RETIRED_PACKAGES.
_PACKAGES = ("nammaoe2bot", "utils")

# Directories the walk never descends into: caches, and the vendored mgz fork
# that only exists on a machine that has run the replay pipeline.
_SKIP_DIRS = {"__pycache__", ".replay_scratch", "data", ".git"}


def _py_files():
	""" (dotted module name, absolute path) for every .py file in the
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
	for filename in ("nammaoe2bot/__main__.py", "start.py"):
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

	`from . import x` inside nammaoe2bot/derived/game_labels.py means nammaoe2bot.derived, so
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


_RETIRED_PACKAGES = ("bot", "core")


def test_nothing_imports_a_package_that_no_longer_exists():
	""" `bot` and `core` were this codebase's two top-level packages and both
	are gone — dissolved into nammaoe2bot/ by the architecture restructure.

	This needs its own test because the sweep below CANNOT catch it. That one
	resolves imports whose first segment is a first-party package; once a
	package stops existing, an import of it stops looking first-party and gets
	skipped as third party. Five `from core import ...` lines survived the
	move exactly that way — including nammaoe2bot/__main__.py's, the entrypoint's first
	real import, which would have failed on the first line of the first boot.

	`import bot` / `from bot import` is the same shape and the same risk, so
	both names are guarded together. """
	offenders = []
	for _module, path in _py_files():
		with open(path, encoding="utf-8") as f:
			tree = ast.parse(f.read())
		for node in ast.walk(tree):
			if isinstance(node, ast.ImportFrom) and not node.level:
				target = node.module or ""
			elif isinstance(node, ast.Import):
				target = node.names[0].name
			else:
				continue
			root = target.split(".")[0]
			if root in _RETIRED_PACKAGES:
				offenders.append(f"{os.path.relpath(path, _REPO_ROOT)}: imports {target}")
	assert not offenders, (
		"imports of a package that no longer exists (ModuleNotFoundError at "
		"the first line that runs):\n  " + "\n  ".join(offenders))


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


def test_the_module_entrypoint_is_where_start_py_says_it_is():
	""" start.py execs `python -m nammaoe2bot`, which needs
	nammaoe2bot/__main__.py to exist. Nothing else in the suite touches either
	file — the entrypoint cannot be imported under the conftest stubs (it
	connects to MySQL and Discord on the way down) — so this is the only check
	that the two agree.

	`-m` and not the file path, deliberately: running nammaoe2bot/__main__.py
	directly puts that DIRECTORY on sys.path instead of the repo root, and
	every `import nammaoe2bot.x` inside it then fails. Both halves are pinned
	because a fix to either one alone is silent until a deploy. """
	assert os.path.isfile(os.path.join(_REPO_ROOT, "nammaoe2bot", "__main__.py"))
	with open(os.path.join(_REPO_ROOT, "start.py"), encoding="utf-8") as f:
		start = f.read()
	assert '"-m", "nammaoe2bot"' in start, (
		"start.py no longer execs `python -m nammaoe2bot`")
	assert '"nammaoe2bot/__main__.py"' not in start, (
		"start.py execs the file path — that puts nammaoe2bot/ on sys.path "
		"instead of the repo root and every intra-package import fails")


# Third-party packages this repo actually depends on. Anything imported that is
# neither stdlib, nor one of these, nor first-party, cannot resolve at runtime.
_THIRD_PARTY = frozenset({
	"nextcord", "aiomysql", "pymysql", "aiohttp", "requests", "mgz", "glicko2",
	"trueskill", "sentry_sdk", "matplotlib", "emoji", "rlcompleter", "readline",
	"pyreadline",
})

# utils/ scripts are run FROM their own directory (`python3 utils/civ_analysis.py`
# puts utils/ on sys.path), so a bare `from db_helpers import ...` between
# siblings resolves for them and only for them. The bot never imports these.
_SIBLING_IMPORT_DIRS = ("utils/", "scripts/")


def test_no_import_names_a_package_that_does_not_exist_anywhere():
	""" An import whose top-level name is neither stdlib, nor a declared
	dependency, nor first-party. It cannot resolve, anywhere, ever.

	WHY THIS IS SEPARATE FROM THE SWEEP ABOVE, AND WHY IT HAD TO EXIST. That one
	resolves imports whose first segment is a first-party package. An import
	that has LOST its package prefix is not one of those — `from quiz import
	interactions` looks exactly like a third-party import and gets skipped.

	That is not hypothetical. Dissolving the re-export module rewrote every
	`bot.quiz` attribute access to `quiz`, and the regex also hit the deferred
	statement `from bot.quiz import interactions`, leaving `from quiz import
	interactions` inside on_interaction. Three lines in nammaoe2bot/discord/events.py:
	the quiz router, the betting router, and the boot-time gold seed.

	Every one of them shipped. Ruff does not resolve imports; this file skipped
	them as third-party; nothing in the suite pressed a button through the real
	router; and the bot booted perfectly, because a function-local import does
	not run until the function does. The first symptom was a player pressing a
	quiz answer and getting "this bot didn't respond in time" — the interaction
	raised ModuleNotFoundError inside nextcord's handler, which logs and
	continues, so nothing was ever answered and nothing crashed. """
	offenders = []
	for _module, path in _py_files():
		relative = os.path.relpath(path, _REPO_ROOT)
		if relative.startswith(_SIBLING_IMPORT_DIRS):
			continue
		with open(path, encoding="utf-8") as f:
			tree = ast.parse(f.read())
		for node in ast.walk(tree):
			if isinstance(node, ast.ImportFrom):
				if node.level:
					continue                      # relative — covered by the sweep above
				root = (node.module or "").split(".")[0]
			elif isinstance(node, ast.Import):
				root = node.names[0].name.split(".")[0]
			else:
				continue
			if not root or root in _PACKAGES or root in _THIRD_PARTY:
				continue
			if root in sys.stdlib_module_names:
				continue
			offenders.append(f"{relative}:{node.lineno}: imports '{root}', which is not "
							 f"stdlib, not a dependency, and not a package in this repo")
	assert not offenders, (
		"imports that cannot resolve anywhere — usually a package prefix lost in a "
		"refactor:\n  " + "\n  ".join(offenders))


def test_the_third_party_allowlist_has_not_gone_stale():
	""" _THIRD_PARTY is hand-maintained, and the test above is only as good as
	it is: a name wrongly added there hides a real break forever. This pins the
	direction that matters — every entry must still be imported by something,
	so a dependency that goes away takes its allowlist entry with it rather
	than leaving a hole for a future typo to fall through. """
	imported = set()
	for _module, path in _py_files():
		with open(path, encoding="utf-8") as f:
			tree = ast.parse(f.read())
		for node in ast.walk(tree):
			if isinstance(node, ast.ImportFrom) and not node.level and node.module:
				imported.add(node.module.split(".")[0])
			elif isinstance(node, ast.Import):
				for alias in node.names:
					imported.add(alias.name.split(".")[0])
	unused = sorted(_THIRD_PARTY - imported)
	assert not unused, (
		f"_THIRD_PARTY lists packages nothing imports any more: {unused}. Remove them — "
		f"each one is a name a future typo could hide behind.")
