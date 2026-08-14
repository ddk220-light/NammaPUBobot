"""Wiring for the gold economy commands and the boot-time bulk seed.

/gold and /gold_top were deleted; /predictions me and /predictions leaderboard
absorbed them. These tests pin that the absorption is real -- a merge that
dropped the balance, the movements, or the seeding path would otherwise pass
every other test in the suite, because nothing else reads them.

None of the touched modules are cheap to import under the conftest stubs:
nammaoe2bot/features/betting/commands.py is fine on its own, but it is only ever reached
through bot/commands/__init__.py's `from .predictions import *`, which also
imports every OTHER bot/commands/* module and pulls in nextcord names the fakes
don't provide. nammaoe2bot/discord/slash.py and nammaoe2bot/discord/events.py pull in the rest
of the (unstubbed) bot/core import graph. So -- same technique as
tests/test_predictions_wiring.py's `_prediction_imports` -- all of them are
checked by parsing source with `ast`, never by importing them.
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _predictions_source():
	return (_REPO_ROOT / "nammaoe2bot" / "features" / "betting" / "commands.py").read_text(encoding="utf-8")


def _predictions_tree():
	return ast.parse(_predictions_source())


def _slash_source():
	return (_REPO_ROOT / "nammaoe2bot" / "discord" / "slash.py").read_text(encoding="utf-8")


def _slash_command_names():
	names = set()
	for node in ast.walk(ast.parse(_slash_source())):
		if not isinstance(node, ast.AsyncFunctionDef):
			continue
		for deco in node.decorator_list:
			if not isinstance(deco, ast.Call):
				continue
			kwargs = {kw.arg: kw.value for kw in deco.keywords}
			name_kw = kwargs.get("name")
			if isinstance(name_kw, ast.Constant):
				names.add(name_kw.value)
	return names


def test_gold_and_gold_top_are_gone():
	""" Both commands were folded into /predictions; a stray re-registration
	would put two competing gold surfaces back in the menu. """
	names = _slash_command_names()
	assert "gold" not in names
	assert "gold_top" not in names


def test_the_module_exports_only_the_two_prediction_handlers():
	""" `from .predictions import *` (bot/commands/__init__.py) only re-exports
	names in __all__ -- without it, bot.commands.predictions_me wouldn't exist. """
	all_values = None
	for node in ast.walk(_predictions_tree()):
		if isinstance(node, ast.Assign) and any(
				isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
			all_values = [elt.value for elt in node.value.elts if isinstance(elt, ast.Constant)]
	assert all_values is not None, "nammaoe2bot/features/betting/commands.py has no __all__"
	assert set(all_values) == {"predictions_leaderboard", "predictions_me"}


def test_handlers_are_coroutine_functions():
	defined = {
		node.name for node in ast.walk(_predictions_tree())
		if isinstance(node, ast.AsyncFunctionDef)}
	assert "predictions_me" in defined, "must be `async def` (run_slash awaits it)"
	assert "predictions_leaderboard" in defined


def test_predictions_me_still_seeds_a_new_holder():
	""" ensure_seeded was one of two paths granting a player their opening 500,
	and it lived in the deleted /gold. Losing it in the merge would leave anyone
	who arrived after the last boot-time bulk seed holding nothing. """
	assert "ensure_seeded" in _predictions_source()


def test_predictions_me_reads_movements_only_for_the_caller():
	""" /gold was ephemeral; /predictions me is public and takes a `player`
	argument, so an unguarded recent_entries() would turn a record lookup into
	an audit of someone else's betting. """
	src = _predictions_source()
	assert "recent_entries" in src
	guard = src[:src.index("recent_entries")]
	assert "target.id == ctx.author.id" in guard


def test_leaderboard_uses_the_unbounded_balance_read():
	""" top_balances() is LIMIT 200 ordered by balance -- as a per-user lookup it
	silently reports "no balance" for anyone poorer than the 200th holder. """
	src = _predictions_source()
	assert "balances_by_user" in src
	assert "top_balances" not in src


def test_leaderboard_filters_through_the_channels_own_eligibility():
	""" The leaderboard SQL never joined player_ratings, so this board applied
	none of the is_hidden / lb_min_matches / lb_last_match_limit gates that
	/leaderboard does -- while the deleted /gold_top filtered is_hidden itself.
	get_lb() is the single definition; restating the predicate here would be a
	second one to drift. """
	src = _predictions_source()
	assert "get_lb(additional_activity=activity)" in src
	assert "bet_activity_by_user" in src
	assert "if eligible is not None" not in src, (
		"an empty eligible set must not fall back to exposing everyone")


def _events_source():
	return (_REPO_ROOT / "nammaoe2bot" / "discord" / "events.py").read_text(encoding="utf-8")


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
