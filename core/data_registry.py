# -*- coding: utf-8 -*-
"""Single source of truth for every table's contract: layer (core/raw/link/
derived/ops), tenancy, sole writer, and retention class. Names say WHAT a
table is; this registry says HOW it is treated. tests/test_data_registry.py
enforces two-way agreement with the ensure_table declarations."""

REGISTRY = {
	# core — irreplaceable
	"qc_matches": dict(layer="core", tenancy="channel", writer="bot/stats/stats.py", retention="forever"),
	"qc_player_matches": dict(layer="core", tenancy="channel", writer="bot/stats/stats.py", retention="forever"),
	"qc_players": dict(layer="core", tenancy="channel", writer="bot/stats/stats.py", retention="forever"),
	"qc_rating_history": dict(layer="core", tenancy="channel", writer="bot/stats/rating.py", retention="forever"),
	"qc_match_id_counter": dict(layer="core", tenancy="global", writer="bot/stats/stats.py", retention="forever"),
	"qc_configs": dict(layer="core", tenancy="channel", writer="core/cfg_factory.py", retention="forever"),
	"pq_configs": dict(layer="core", tenancy="channel", writer="core/cfg_factory.py", retention="forever"),
	"qc_saved_state": dict(layer="core", tenancy="global", writer="bot/main.py", retention="forever"),
	"players": dict(layer="core", tenancy="global", writer="bot/commands/misc.py", retention="forever"),
	"noadds": dict(layer="core", tenancy="channel", writer="bot/stats/noadds.py", retention="forever"),
	"qc_phrases": dict(layer="core", tenancy="channel", writer="bot/stats/noadds.py", retention="forever"),
	"qc_douche": dict(layer="core", tenancy="channel", writer="bot/douche.py", retention="forever"),
	"disabled_guilds": dict(layer="core", tenancy="global", writer="bot/stats/stats.py", retention="forever"),
	# feature state (core contract)
	"qc_quiz_posts": dict(layer="core", tenancy="channel", writer="bot/quiz/store.py", retention="forever"),
	"qc_quiz_answers": dict(layer="core", tenancy="channel", writer="bot/quiz/store.py", retention="forever"),
	"qc_quiz_config": dict(layer="core", tenancy="channel", writer="bot/quiz/store.py", retention="forever"),
	"qc_prediction_posts": dict(layer="core", tenancy="channel", writer="bot/predictions/store.py", retention="forever"),
	"qc_prediction_votes": dict(layer="core", tenancy="channel", writer="bot/predictions/store.py", retention="forever"),
	# raw — append-only observations
	"qc_match_civs": dict(layer="raw", tenancy="community", writer="bot/civ_matcher.py", retention="forever"),
	"qc_civ_reconcile": dict(layer="ops", tenancy="community", writer="bot/civ_reconcile.py", retention="forever"),
	"qc_lobbies": dict(layer="raw", tenancy="community", writer="bot/lobby/jobs.py", retention="forever"),
	"qc_profile_map": dict(layer="raw", tenancy="global", writer="bot/lobby/profile_map.py", retention="forever"),
	"rs_config": dict(layer="ops", tenancy="global", writer="bot/replay_stats/store.py", retention="forever"),
	"rs_matches": dict(layer="raw", tenancy="global", writer="bot/replay_stats/store.py", retention="forever"),
	"rs_player_games": dict(layer="raw", tenancy="global", writer="bot/replay_stats/store.py", retention="forever"),
	"rs_player_units": dict(layer="raw", tenancy="global", writer="bot/replay_stats/store.py", retention="sweepable"),
	"rs_player_techs": dict(layer="raw", tenancy="global", writer="bot/replay_stats/store.py", retention="sweepable"),
	"rs_player_buildings": dict(layer="raw", tenancy="global", writer="bot/replay_stats/store.py", retention="sweepable"),
	"rs_player_events": dict(layer="raw", tenancy="global", writer="bot/replay_stats/store.py", retention="sweepable"),
	"rs_player_apm": dict(layer="raw", tenancy="global", writer="bot/replay_stats/store.py", retention="sweepable"),
	"rs_ingest": dict(layer="ops", tenancy="global", writer="bot/replay_stats/jobs.py", retention="forever"),
	"rs_profiles": dict(layer="raw", tenancy="global", writer="bot/replay_stats/store.py", retention="forever"),
	# derived — rebuildable (legacy generation, retired across stages 3-6)
	"rs_player_game_tags": dict(layer="derived", tenancy="global", writer="bot/replay_stats/player_tags.py", retention="forever"),
	"rs_player_personas": dict(layer="derived", tenancy="global", writer="bot/replay_stats/persona_store.py", retention="forever"),
	"cls_classifications": dict(layer="derived", tenancy="global", writer="bot/replay_stats/classifications.py", retention="forever"),
	"cls_data_requirements": dict(layer="derived", tenancy="global", writer="bot/replay_stats/classifications.py", retention="forever"),
	"cls_results": dict(layer="derived", tenancy="global", writer="bot/replay_stats/classification_sync.py", retention="forever"),
	"cls_result_metrics": dict(layer="derived", tenancy="global", writer="bot/replay_stats/classification_sync.py", retention="forever"),
	"cls_player_totals": dict(layer="derived", tenancy="global", writer="bot/replay_stats/classifications.py", retention="forever"),
	"cls_match_ingest": dict(layer="derived", tenancy="global", writer="bot/replay_stats/classifications.py", retention="forever"),
	"bot_player_commentary": dict(layer="derived", tenancy="global", writer="offline", retention="forever"),
	# ops/web
	"web_sessions": dict(layer="ops", tenancy="global", writer="bot/web.py", retention="forever"),
	"web_oauth_states": dict(layer="ops", tenancy="global", writer="bot/web.py", retention="forever"),
}

ALL_TABLES = frozenset(REGISTRY)
