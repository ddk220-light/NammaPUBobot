import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent.parent
EVENTS_PATH = ROOT / "nammaoe2bot" / "discord" / "events.py"
CLIENT_PATH = ROOT / "nammaoe2bot" / "runtime" / "client.py"


def _load_reaction_router(dc, log):
	"""Compile the real helper without importing the bot's production graph."""
	tree = ast.parse(EVENTS_PATH.read_text(), filename=str(EVENTS_PATH))
	function = next(
		node for node in tree.body
		if isinstance(node, ast.AsyncFunctionDef) and node.name == "_route_raw_reaction"
	)
	namespace = {"dc": dc, "log": log, "traceback": __import__("traceback")}
	exec(compile(ast.Module(body=[function], type_ignores=[]), str(EVENTS_PATH), "exec"), namespace)
	return namespace["_route_raw_reaction"]


def test_raw_add_routes_without_a_cached_message():
	calls = []

	async def callback(emoji, member, *, remove=False):
		calls.append((emoji, member, remove))

	member = SimpleNamespace(id=7)
	dc = SimpleNamespace(
		user=SimpleNamespace(id=99),
		app=SimpleNamespace(ready=True, shutting_down=False, waiting_reactions={123: callback}),
		get_guild=lambda _guild_id: None,
	)
	router = _load_reaction_router(dc, SimpleNamespace(error=lambda *_args: None))
	payload = SimpleNamespace(
		user_id=7,
		message_id=123,
		guild_id=1,
		emoji="✅",
		member=member,
	)

	asyncio.run(router(payload, remove=False))

	assert calls == [("✅", member, False)]


def test_raw_remove_resolves_member_from_guild_cache():
	calls = []

	async def callback(emoji, member, *, remove=False):
		calls.append((emoji, member, remove))

	member = SimpleNamespace(id=7)
	guild = SimpleNamespace(get_member=lambda user_id: member if user_id == 7 else None)
	dc = SimpleNamespace(
		user=SimpleNamespace(id=99),
		app=SimpleNamespace(ready=True, shutting_down=False, waiting_reactions={123: callback}),
		get_guild=lambda guild_id: guild if guild_id == 1 else None,
	)
	router = _load_reaction_router(dc, SimpleNamespace(error=lambda *_args: None))
	payload = SimpleNamespace(user_id=7, message_id=123, guild_id=1, emoji="✅")

	asyncio.run(router(payload, remove=True))

	assert calls == [("✅", member, True)]


def test_events_use_raw_add_and_do_not_log_every_channel_message():
	tree = ast.parse(EVENTS_PATH.read_text(), filename=str(EVENTS_PATH))
	async_names = {
		node.name for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
	}
	calls = {
		node.func.id
		for node in ast.walk(tree)
		if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
	}

	assert "on_raw_reaction_add" in async_names
	assert "on_reaction_add" not in async_names
	assert "log_channel_message" not in calls


def test_discord_message_cache_is_small_and_explicit():
	tree = ast.parse(CLIENT_PATH.read_text(), filename=str(CLIENT_PATH))
	assignment = next(
		node for node in tree.body
		if isinstance(node, ast.Assign)
		and any(isinstance(target, ast.Name) and target.id == "MESSAGE_CACHE_SIZE"
				for target in node.targets)
	)
	value = ast.literal_eval(assignment.value)
	client_call = next(
		node.value for node in tree.body
		if isinstance(node, ast.Assign)
		and any(isinstance(target, ast.Name) and target.id == "dc" for target in node.targets)
	)
	max_messages = next(keyword.value for keyword in client_call.keywords
						if keyword.arg == "max_messages")

	assert 0 < value <= 100
	assert isinstance(max_messages, ast.Name)
	assert max_messages.id == "MESSAGE_CACHE_SIZE"

