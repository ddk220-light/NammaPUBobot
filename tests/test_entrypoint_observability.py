"""Entrypoint contracts that cannot be tested by importing the live bot.

Importing ``nammaoe2bot.__main__`` connects to MySQL and Discord, so these tests
execute only the small pure/supervision functions extracted from its AST. This
is the same source-level isolation used by the existing entrypoint tests.
"""
import ast
import asyncio
import traceback
import types
from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent
_MAIN = _ROOT / "nammaoe2bot" / "__main__.py"


def _function(name):
	tree = ast.parse(_MAIN.read_text(encoding="utf-8"), filename=str(_MAIN))
	node = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
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
		"_request_shutdown": lambda: stops.append(True),
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


def test_shutdown_drains_pending_mutations_then_flushes_before_closing_db():
	seen = []

	async def close_discord():
		seen.append("discord closed")

	async def close_db():
		seen.append("db closed")

	async def flush(_app):
		seen.append("durable snapshot")

	namespace = {
		"asyncio": asyncio,
		"dc": types.SimpleNamespace(app=object(), close=close_discord),
		"database": types.SimpleNamespace(db=types.SimpleNamespace(close=close_db)),
		"save_state_if_changed": flush, "web_runner": None,
		"log": types.SimpleNamespace(error=lambda _msg: None, close=lambda: seen.append("log closed")),
		"loop": types.SimpleNamespace(stop=lambda: seen.append("loop stopped")),
	}
	exec(_function("_shutdown"), namespace)

	async def run():
		entered = asyncio.Event()

		async def mutation():
			try:
				entered.set()
				await asyncio.Event().wait()
			finally:
				seen.append("mutation rolled back")

		asyncio.create_task(mutation())
		await entered.wait()
		await namespace["_shutdown"]()

	asyncio.run(run())
	assert seen == ["mutation rolled back", "durable snapshot", "discord closed", "db closed", "log closed", "loop stopped"]


def test_repeated_shutdown_requests_disable_commands_and_start_one_cleanup():
	created = []

	def schedule(coro, **_kwargs):
		coro.close()
		created.append(True)
		return object()

	async def shutdown():
		pass

	app = types.SimpleNamespace(ready=True, shutting_down=False)
	namespace = {"_shutdown_task": None, "dc": types.SimpleNamespace(app=app),
		"console": types.SimpleNamespace(terminate=lambda: None),
		"loop": types.SimpleNamespace(create_task=schedule), "_shutdown": shutdown}
	exec(_function("_request_shutdown"), namespace)
	namespace["_request_shutdown"]()
	namespace["_request_shutdown"]()
	assert not app.ready and app.shutting_down
	assert created == [True]


def test_startup_tick_only_updates_liveness_before_restore_finishes():
	path = _ROOT / "nammaoe2bot/discord/events.py"
	tree = ast.parse(path.read_text())
	function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "on_think")
	function.decorator_list = []
	namespace = {"dc": types.SimpleNamespace(app=types.SimpleNamespace(state_restored=False, shutting_down=False))}
	exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
	# Every dependency after the guard is deliberately absent: no match, job
	# or snapshot may run while the application is still being restored.
	asyncio.run(namespace["on_think"](123))
	assert namespace["last_tick_at"] == 123
