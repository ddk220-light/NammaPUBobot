"""AST-level tests for bot/web.py's identity reads.

bot/web.py can't be imported under CI — it pulls in aiohttp.web and
nammaoe2bot.runtime.client's nextcord dependency, neither installed in the pytest-only CI
job (see test_persona_store.py's PERIODS comparison for the established
workaround of parsing the source with ast instead of importing it).

These assert that every Discord-user -> AoE2-profile lookup in the web layer
goes through the identity resolver (nammaoe2bot/features/identity/resolver.py), and that the legacy
three-store union it replaced — two now-retired profile tables plus a
hand-maintained CSV — is gone from the file entirely. The two table names
themselves are guarded globally by tests/test_naming.py's OLD_NAMES, which is
why they are not spelled out here.
"""
import ast
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "bot" / "web.py"
_SRC = _PATH.read_text()
_TREE = ast.parse(_SRC)


def _function(name):
	for node in ast.walk(_TREE):
		if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
			return node
	raise AssertionError(f"{name} not found in bot/web.py")


def _function_names():
	return {node.name for node in ast.walk(_TREE)
			if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


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


def _string_constants(node):
	return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_mapped_player_identity_consults_identity_resolver():
	calls = _attr_calls(_function("_mapped_player_identity"))
	assert ("resolver", "profiles_for_users") in calls
	assert ("resolver", "names_for_profiles") in calls


def test_player_directory_reads_only_the_identity_resolver():
	node = _function("_match_stat_players")
	assert ("resolver", "profiles_and_names_by_user") in _attr_calls(node)
	assert "_mapped_profiles_by_user" not in _bare_calls(node)


def test_public_stats_check_reads_only_the_identity_resolver():
	node = _function("_player_has_public_stats")
	assert ("resolver", "profiles_for_users") in _attr_calls(node)
	assert "_mapped_profiles_by_user" not in _bare_calls(node)


def test_the_legacy_three_store_union_helpers_are_gone():
	"""_mapped_profiles_by_user unioned the two retired tables with the CSV;
	_csv_profile_rows was the CSV third of it. Both are deleted, not merely
	unused — a helper left behind is a second answer waiting to be called."""
	names = _function_names()
	assert "_mapped_profiles_by_user" not in names
	assert "_csv_profile_rows" not in names


def test_the_profile_map_csv_is_not_read_anywhere_in_web():
	"""The whole file, not just the functions above. The retired TABLES are
	covered by tests/test_naming.py (a global guard beats a per-file one); this
	covers the CSV, which is not a table name and so is not in OLD_NAMES."""
	assert "player_profile_map" not in _SRC, "bot/web.py still reads the profile-map CSV"
