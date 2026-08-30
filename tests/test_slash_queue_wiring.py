"""Regression tests for the core queue slash-command bindings.

``slash.py`` has a deliberately broad import graph, so inspect its AST rather
than importing a Discord client and the production configuration.  These tests
pin the two failures seen in production: a SlashOption called ``queues`` must
not shadow the queue command module, and the timeout/defer race must tolerate a
shielded command answering just before ``defer()`` runs.
"""
from __future__ import annotations

import ast
from pathlib import Path


_PATH = Path(__file__).resolve().parent.parent / "nammaoe2bot" / "discord" / "slash.py"
_TREE = ast.parse(_PATH.read_text(encoding="utf-8"))


def _function(name):
	return next(
		node for node in ast.walk(_TREE)
		if isinstance(node, ast.AsyncFunctionDef) and node.name == name)


def _attributes(node):
	return {
		(value.id, child.attr)
		for child in ast.walk(node)
		if isinstance(child, ast.Attribute)
		and isinstance((value := child.value), ast.Name)
	}


def test_queue_module_is_aliased_away_from_slash_option_names():
	aliases = {
		alias.name: alias.asname
		for node in _TREE.body
		if isinstance(node, ast.ImportFrom)
		and node.module == "nammaoe2bot.discord.commands"
		for alias in node.names
	}
	assert aliases["queues"] == "queue_commands"
	assert not any(
		isinstance(node, ast.Attribute)
		and isinstance(node.value, ast.Name)
		and node.value.id == "queues"
		for node in ast.walk(_TREE)
	), "a queues SlashOption would shadow this module access at runtime"


def test_public_add_and_remove_dispatch_to_the_queue_module():
	assert ("queue_commands", "add") in _attributes(_function("_add"))
	assert ("queue_commands", "remove") in _attributes(_function("_remove"))


def test_slash_timeout_defer_is_guarded_against_the_response_race():
	run_slash = _function("run_slash")
	assert any(
		isinstance(node, ast.Call)
		and isinstance(node.func, ast.Attribute)
		and node.func.attr == "is_done"
		for node in ast.walk(run_slash)
	)
	assert any(
		isinstance(node, ast.ExceptHandler)
		and isinstance(node.type, ast.Name)
		and node.type.id == "InteractionResponded"
		for node in ast.walk(run_slash)
	)
