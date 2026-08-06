"""Wiring for /gold, /gold_top and the boot-time bulk seed.

None of the three touched modules are cheap to import under the conftest
stubs: bot/commands/predictions.py is fine on its own, but it is only ever
reached through bot/commands/__init__.py's `from .predictions import *`,
which also imports every OTHER bot/commands/* module (stats.py etc) and pulls
in nextcord names the fakes don't provide. bot/context/slash/commands.py and
bot/events.py pull in the rest of the (unstubbed) bot/core import graph. So —
same technique as tests/test_predictions_wiring.py's `_prediction_imports` —
all three are checked by parsing source with `ast`, never by importing them.
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _predictions_source():
	return (_REPO_ROOT / "bot" / "commands" / "predictions.py").read_text(encoding="utf-8")


def _predictions_tree():
	return ast.parse(_predictions_source())


def test_the_module_exports_gold_and_gold_top():
	""" `from .predictions import *` (bot/commands/__init__.py) only re-exports
	names in __all__ — without it, bot.commands.gold wouldn't exist. """
	tree = _predictions_tree()
	all_values = None
	for node in ast.walk(tree):
		if isinstance(node, ast.Assign) and any(
				isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
			all_values = [elt.value for elt in node.value.elts if isinstance(elt, ast.Constant)]
	assert all_values is not None, "bot/commands/predictions.py has no __all__"
	assert 'gold' in all_values
	assert 'gold_top' in all_values


def test_gold_and_gold_top_are_coroutine_functions():
	tree = _predictions_tree()
	defined = {
		node.name for node in ast.walk(tree)
		if isinstance(node, ast.AsyncFunctionDef)}
	assert 'gold' in defined, "gold must be defined with `async def` (run_slash awaits it)"
	assert 'gold_top' in defined, "gold_top must be defined with `async def` (run_slash awaits it)"


def test_gold_top_accepts_an_optional_page_argument():
	tree = _predictions_tree()
	gold_top = next(
		node for node in ast.walk(tree)
		if isinstance(node, ast.AsyncFunctionDef) and node.name == "gold_top")
	arg_names = [a.arg for a in gold_top.args.args]
	assert 'page' in arg_names
	default_offset = arg_names.index('page') - (len(arg_names) - len(gold_top.args.defaults))
	assert gold_top.args.defaults[default_offset].value == 1


def _slash_source():
	return (_REPO_ROOT / "bot" / "context" / "slash" / "commands.py").read_text(encoding="utf-8")


def test_slash_commands_registers_gold_and_gold_top():
	""" Parsed, not imported — bot/context/slash/commands.py pulls in the full
	nextcord/core.client import graph that conftest doesn't stub. """
	tree = ast.parse(_slash_source())
	names = set()
	for node in ast.walk(tree):
		if isinstance(node, ast.AsyncFunctionDef):
			for deco in node.decorator_list:
				call = deco if isinstance(deco, ast.Call) else None
				if call is None:
					continue
				func_name = ""
				if isinstance(call.func, ast.Attribute):
					func_name = call.func.attr
				kwargs = {kw.arg: kw.value for kw in call.keywords}
				name_kw = kwargs.get("name")
				name_val = name_kw.value if isinstance(name_kw, ast.Constant) else None
				if func_name == "slash_command" and name_val in ("gold", "gold_top"):
					names.add(name_val)
	assert names == {"gold", "gold_top"}


def test_gold_slash_command_runs_the_gold_handler():
	source = _slash_source()
	assert "await run_slash(bot.commands.gold, interaction=interaction)" in source


def test_gold_top_slash_command_runs_the_gold_top_handler_with_page():
	source = _slash_source()
	assert "await run_slash(bot.commands.gold_top, interaction=interaction, page=page or 1)" in source


def _events_source():
	return (_REPO_ROOT / "bot" / "events.py").read_text(encoding="utf-8")


def test_events_imports_time():
	tree = ast.parse(_events_source())
	imported = {n.name for node in ast.walk(tree) if isinstance(node, ast.Import) for n in node.names}
	assert "time" in imported


def test_on_ready_runs_the_idempotent_bulk_seed_after_the_csv_seed():
	""" Ordering matters only in that the bulk seed must exist at all and must
	not be able to abort boot -- both checked here without executing on_ready
	(which needs a live Discord client). """
	source = _events_source()
	seed_idx = source.index("await seed_ratings_from_csv()")
	bulk_idx = source.index("gold_bank.bulk_seed(int(time.time()))")
	assert bulk_idx > seed_idx, "bulk seed must run after the CSV seed, not before"
	# Wrapped in try/except so a seeding failure can never abort boot.
	tail = source[seed_idx:bulk_idx]
	assert "try:" in tail
	after = source[bulk_idx:bulk_idx + 400]
	assert "except Exception:" in after
