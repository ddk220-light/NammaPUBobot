# -*- coding: utf-8 -*-
"""Single source of truth for every table's contract: layer (core/raw/link/
derived/ops), tenancy, writers, and retention class. Names say WHAT a
table is; this registry says HOW it is treated. tests/test_data_registry.py
enforces two-way agreement with the ensure_table declarations.

`writers` records the modules that write each table TODAY, as found by
grepping bot/ and core/ for db.insert/insert_many/update/delete and raw
INSERT/UPDATE/DELETE/REPLACE execute() calls. It is a description of the
current, imperfect state — several tables below are written from more than
one module. The project's target is exactly one dedicated writer per table;
later stages consolidate these multi-writer entries down to one each. An
empty tuple means the table is read-only from bot/core (populated
externally, e.g. by an offline utils/ script) — do not invent a writer for
it."""

REGISTRY = {
	# core — irreplaceable
	"qc_matches": dict(
		layer="core", tenancy="channel", writers=("bot/elo_sync.py", "bot/stats/stats.py"), retention="forever"
	),
	"qc_player_matches": dict(
		layer="core", tenancy="channel", writers=("bot/elo_sync.py", "bot/stats/stats.py"), retention="forever"
	),
	"qc_players": dict(
		layer="core",
		tenancy="channel",
		writers=("bot/elo_sync.py", "bot/events.py", "bot/stats/stats.py"),
		retention="forever",
	),
	"qc_rating_history": dict(
		layer="core",
		tenancy="channel",
		writers=("bot/elo_sync.py", "bot/stats/rating.py", "bot/stats/stats.py"),
		retention="forever",
	),
	"qc_match_id_counter": dict(
		layer="core", tenancy="global", writers=("bot/stats/stats.py",), retention="forever"
	),
	"qc_configs": dict(layer="core", tenancy="channel", writers=("core/cfg_factory.py",), retention="forever"),
	"pq_configs": dict(layer="core", tenancy="channel", writers=("core/cfg_factory.py",), retention="forever"),
	"qc_saved_state": dict(layer="core", tenancy="global", writers=("bot/main.py",), retention="forever"),
	"players": dict(layer="core", tenancy="global", writers=("bot/commands/misc.py",), retention="forever"),
	"noadds": dict(layer="core", tenancy="channel", writers=("bot/stats/noadds.py",), retention="forever"),
	"qc_phrases": dict(layer="core", tenancy="channel", writers=("bot/stats/noadds.py",), retention="forever"),
	"qc_douche": dict(layer="core", tenancy="channel", writers=("bot/douche.py",), retention="forever"),
	"disabled_guilds": dict(layer="core", tenancy="global", writers=("bot/stats/stats.py",), retention="forever"),
	# feature state (core contract)
	"qc_quiz_posts": dict(layer="core", tenancy="channel", writers=("bot/quiz/store.py",), retention="forever"),
	"qc_quiz_answers": dict(layer="core", tenancy="channel", writers=("bot/quiz/store.py",), retention="forever"),
	"qc_quiz_config": dict(layer="core", tenancy="channel", writers=("bot/quiz/store.py",), retention="forever"),
	"qc_prediction_posts": dict(
		layer="core", tenancy="channel", writers=("bot/predictions/store.py",), retention="forever"
	),
	"qc_prediction_votes": dict(
		layer="core", tenancy="channel", writers=("bot/predictions/store.py",), retention="forever"
	),
	# raw — append-only observations
	"qc_match_civs": dict(
		layer="raw",
		tenancy="community",
		writers=("bot/civ_matcher.py", "bot/civ_sync.py", "bot/lobby/completed.py"),
		retention="forever",
	),
	"qc_civ_reconcile": dict(
		layer="ops", tenancy="community", writers=("bot/civ_reconcile.py",), retention="forever"
	),
	"qc_lobbies": dict(
		layer="raw",
		tenancy="community",
		writers=("bot/lobby/announce.py", "bot/lobby/completed.py", "bot/lobby/jobs.py", "bot/lobby/watcher.py"),
		retention="forever",
	),
	"qc_profile_map": dict(
		layer="raw", tenancy="global", writers=("bot/lobby/profile_map.py",), retention="forever"
	),
	"rs_config": dict(layer="ops", tenancy="global", writers=("bot/replay_stats/store.py",), retention="forever"),
	"rs_matches": dict(layer="raw", tenancy="global", writers=("bot/replay_stats/store.py",), retention="forever"),
	"rs_player_games": dict(
		layer="raw", tenancy="global", writers=("bot/replay_stats/store.py",), retention="forever"
	),
	"rs_player_units": dict(
		layer="raw", tenancy="global", writers=("bot/replay_stats/store.py",), retention="sweepable"
	),
	"rs_player_techs": dict(
		layer="raw", tenancy="global", writers=("bot/replay_stats/store.py",), retention="sweepable"
	),
	"rs_player_buildings": dict(
		layer="raw", tenancy="global", writers=("bot/replay_stats/store.py",), retention="sweepable"
	),
	"rs_player_events": dict(
		layer="raw", tenancy="global", writers=("bot/replay_stats/store.py",), retention="sweepable"
	),
	"rs_player_apm": dict(
		layer="raw", tenancy="global", writers=("bot/replay_stats/store.py",), retention="sweepable"
	),
	"rs_ingest": dict(layer="ops", tenancy="global", writers=("bot/replay_stats/store.py",), retention="forever"),
	"rs_profiles": dict(layer="raw", tenancy="global", writers=("bot/replay_stats/store.py",), retention="forever"),
	# derived — rebuildable (legacy generation, retired across stages 3-6)
	"rs_player_game_tags": dict(
		layer="derived", tenancy="global", writers=("bot/replay_stats/player_tags.py",), retention="forever"
	),
	"rs_player_personas": dict(
		layer="derived", tenancy="global", writers=("bot/replay_stats/persona_store.py",), retention="forever"
	),
	"cls_classifications": dict(
		layer="derived", tenancy="global", writers=("bot/replay_stats/classifications.py",), retention="forever"
	),
	"cls_data_requirements": dict(
		layer="derived", tenancy="global", writers=("bot/replay_stats/classifications.py",), retention="forever"
	),
	"cls_results": dict(
		layer="derived",
		tenancy="global",
		writers=("bot/replay_stats/classification_sync.py", "bot/replay_stats/classifications.py"),
		retention="forever",
	),
	"cls_result_metrics": dict(
		layer="derived",
		tenancy="global",
		writers=("bot/replay_stats/classification_sync.py", "bot/replay_stats/classifications.py"),
		retention="forever",
	),
	"cls_player_totals": dict(
		layer="derived", tenancy="global", writers=("bot/replay_stats/classifications.py",), retention="forever"
	),
	"cls_match_ingest": dict(
		layer="derived", tenancy="global", writers=("bot/replay_stats/classifications.py",), retention="forever"
	),
	"bot_player_commentary": dict(layer="derived", tenancy="global", writers=(), retention="forever"),
	# ops/web
	"web_sessions": dict(layer="ops", tenancy="global", writers=("bot/web.py",), retention="forever"),
	"web_oauth_states": dict(layer="ops", tenancy="global", writers=("bot/web.py",), retention="forever"),
}

ALL_TABLES = frozenset(REGISTRY)
