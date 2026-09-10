"""Entrypoint contracts that cannot be tested by importing the live bot.

Importing ``nammaoe2bot.__main__`` connects to MySQL and Discord, so these tests
execute only the small pure/supervision functions extracted from its AST. This
is the same source-level isolation used by the existing entrypoint tests.
"""
import ast
import traceback
import types
from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent
_MAIN = _ROOT / "nammaoe2bot" / "__main__.py"


def _function(name):
	tree = ast.parse(_MAIN.read_text(encoding="utf-8"), filename=str(_MAIN))
	node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
	return compile(ast.Module(body=[node], type_ignores=[]), str(_MAIN), "exec")


def test_process_exit_code_distinguishes_normal_and_fatal_shutdown():
	namespace = {"_fatal_exit": False}
	exec(_function("_process_exit_code"), namespace)
	assert namespace["_process_exit_code"]() == 0
	namespace["_fatal_exit"] = True
	assert namespace["_process_exit_code"]() == 1


def test_a_crashed_supervised_task_marks_the_process_fatal_and_stops_the_loop():
	class Task:
		def cancelled(self):
			return False

		def exception(self):
			return RuntimeError("boom")

		def get_name(self):
			return "discord_client"

	stops = []
	logs = []
	namespace = {
		"_fatal_exit": False,
		"traceback": traceback,
		"log": types.SimpleNamespace(error=logs.append),
		"sentinel": object(),
		"save_state": lambda _app: None,
		"dc": types.SimpleNamespace(app=object()),
		"sentry_sdk": None,
		"loop": types.SimpleNamespace(stop=lambda: stops.append(True)),
	}
	exec(_function("_task_done_callback"), namespace)

	namespace["_task_done_callback"](Task())

	assert namespace["_fatal_exit"] is True
	assert stops == [True]
	assert any("CRITICAL" in message for message in logs)


def test_entrypoint_exits_with_the_resolved_status_after_the_loop():
	tree = ast.parse(_MAIN.read_text(encoding="utf-8"), filename=str(_MAIN))
	run_index = next(
		index for index, node in enumerate(tree.body)
		if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
		and isinstance(node.value.func, ast.Attribute)
		and node.value.func.attr == "run_forever")
	exit_node = tree.body[run_index + 1]
	assert isinstance(exit_node, ast.Raise)
	assert isinstance(exit_node.exc, ast.Call)
	assert isinstance(exit_node.exc.func, ast.Name) and exit_node.exc.func.id == "SystemExit"
	status = exit_node.exc.args[0]
	assert isinstance(status, ast.Call)
	assert isinstance(status.func, ast.Name) and status.func.id == "_process_exit_code"


def test_ws_enable_selects_full_dashboard_or_probe_only_server():
	tree = ast.parse(_MAIN.read_text(encoding="utf-8"), filename=str(_MAIN))
	choice = next(
		node for node in tree.body
		if isinstance(node, ast.If)
		and isinstance(node.test, ast.Attribute) and node.test.attr == "WS_ENABLE")
	full = choice.body[0]
	minimal = choice.orelse[0]
	assert isinstance(full, ast.ImportFrom)
	assert full.module == "nammaoe2bot.web.server"
	assert full.names[0].name == "start_web_server"
	assert isinstance(minimal, ast.ImportFrom)
	assert minimal.module == "nammaoe2bot.web.probes"
	assert minimal.names[0].name == "start_probe_server"
	assert minimal.names[0].asname == "start_web_server"


def test_railway_uses_db_checking_readiness_not_continuous_liveness():
	config = (_ROOT / "railway.toml").read_text(encoding="utf-8")
	assert 'healthcheckPath = "/ready"' in config
