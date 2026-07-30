"""AST-level test for bot/web.py's _mapped_player_identity.

bot/web.py can't be imported under CI — it pulls in aiohttp.web and
core.client's nextcord dependency, neither installed in the pytest-only CI
job (see test_persona_store.py's PERIODS comparison for the established
workaround of parsing the source with ast instead of importing it).

This asserts _mapped_player_identity now resolves a Discord user's AoE2
profile ids/names through the identity resolver (bot/identity.py) instead of
_mapped_profiles_by_user's qc_profile_map + rs_profiles + CSV aggregation.
"""
import ast
from pathlib import Path

_SRC = (Path(__file__).resolve().parent.parent / "bot" / "web.py").read_text()
_TREE = ast.parse(_SRC)


def _function(name):
	for node in ast.walk(_TREE):
		if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
			return node
	raise AssertionError(f"{name} not found in bot/web.py")


def _attr_calls(node):
	"""(obj_name, attr) for every `obj_name.attr(...)` call inside `node`."""
	out = []
	for n in ast.walk(node):
		if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name):
			out.append((n.func.value.id, n.func.attr))
	return out


def _bare_calls(node):
	"""Bare `name(...)` calls (not obj.attr(...)) inside `node`."""
	out = []
	for n in ast.walk(node):
		if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
			out.append(n.func.id)
	return out


def test_mapped_player_identity_consults_identity_resolver():
	calls = _attr_calls(_function("_mapped_player_identity"))
	assert ("identity", "profiles_for_users") in calls
	assert ("identity", "names_for_profiles") in calls


def test_mapped_player_identity_no_longer_reads_the_legacy_aggregation():
	node = _function("_mapped_player_identity")
	assert "_mapped_profiles_by_user" not in _bare_calls(node)
