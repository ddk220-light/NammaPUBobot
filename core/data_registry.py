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
later stages consolidate these multi-writer entries down to one each.

An empty tuple carries one of two distinct meanings, and the entry's comment
must say which:
1. The table is read-only from bot/core (populated externally, e.g. by an
   offline utils/ script) — do not invent a writer for it.
2. The table is declared ahead of its writer: its `ensure_table`/
   `FactoryTable` landed in this deploy so its schema ships early, but the
   module that writes to it is a later task. The entry's comment must name
   that task (e.g. "task 3.3 adds bot/derived/game_labels.py as its
   writer"), so the empty tuple reads as "not yet" rather than "never"."""

REGISTRY = {
	# core — irreplaceable
	"matches": dict(
		layer="core", tenancy="channel", writers=("bot/elo_sync.py", "bot/stats/stats.py"), retention="forever"
	),
	"match_players": dict(
		layer="core", tenancy="channel", writers=("bot/elo_sync.py", "bot/stats/stats.py"), retention="forever"
	),
	# bot/stats/rating.py writes this through `self.table` (set to the literal
	# name once, at the top of the class), which is why a grep for the table name
	# beside a db.insert/update call does not find it.
	"player_ratings": dict(
		layer="core",
		tenancy="channel",
		writers=("bot/elo_sync.py", "bot/events.py", "bot/stats/rating.py", "bot/stats/stats.py"),
		retention="forever",
	),
	"rating_history": dict(
		layer="core",
		tenancy="channel",
		writers=("bot/elo_sync.py", "bot/stats/rating.py", "bot/stats/stats.py"),
		retention="forever",
	),
	"match_counter": dict(
		layer="core", tenancy="global", writers=("bot/stats/stats.py",), retention="forever"
	),
	"channel_settings": dict(layer="core", tenancy="channel", writers=("core/cfg_factory.py",), retention="forever"),
	"queue_settings": dict(layer="core", tenancy="channel", writers=("core/cfg_factory.py",), retention="forever"),
	"bot_state": dict(layer="core", tenancy="global", writers=("bot/main.py",), retention="forever"),
	"player_prefs": dict(layer="core", tenancy="global", writers=("bot/commands/misc.py",), retention="forever"),
	"queue_bans": dict(layer="core", tenancy="channel", writers=("bot/stats/noadds.py",), retention="forever"),
	"player_phrases": dict(layer="core", tenancy="channel", writers=("bot/stats/noadds.py",), retention="forever"),
	"douche_log": dict(layer="core", tenancy="channel", writers=("bot/douche.py",), retention="forever"),
	# feature state (core contract)
	"quiz_posts": dict(layer="core", tenancy="channel", writers=("bot/quiz/store.py",), retention="forever"),
	"quiz_answers": dict(layer="core", tenancy="channel", writers=("bot/quiz/store.py",), retention="forever"),
	"quiz_settings": dict(layer="core", tenancy="channel", writers=("bot/quiz/store.py",), retention="forever"),
	"prediction_posts": dict(
		layer="core", tenancy="channel", writers=("bot/predictions/store.py",), retention="forever"
	),
	"prediction_votes": dict(
		layer="core", tenancy="channel", writers=("bot/predictions/store.py",), retention="forever"
	),
	# raw — append-only observations
	"civ_picks": dict(
		layer="raw",
		tenancy="community",
		writers=("bot/civ_matcher.py", "bot/civ_sync.py", "bot/lobby/completed.py"),
		retention="forever",
	),
	"civ_reconcile": dict(
		layer="ops", tenancy="community", writers=("bot/civ_reconcile.py",), retention="forever"
	),
	"lobbies": dict(
		layer="raw",
		tenancy="community",
		writers=("bot/lobby/announce.py", "bot/lobby/completed.py", "bot/lobby/jobs.py", "bot/lobby/watcher.py"),
		retention="forever",
	),
	# core/migrations.py is a second writer of both: 003_seed_identities creates
	# them with raw DDL and seeds them at boot, before `import bot` happens, so
	# it cannot go through bot/identity.py (see that module's docstring).
	# bot/identity_solver.py is deliberately NOT listed — it deduces bindings but
	# writes them only through identity.learn()/record_refused_claim().
	"identities": dict(
		layer="raw", tenancy="global", writers=("bot/identity.py", "core/migrations.py"), retention="forever"
	),
	"identity_conflicts": dict(
		layer="derived",
		tenancy="global",
		writers=("bot/identity.py", "core/migrations.py"),
		retention="forever",
	),
	# raw replay observations. Renamed out of their inherited `rs_` prefix by
	# 007_raw_renames; the single-row ops table that used to sit alongside them
	# was dropped by the same migration, its one boolean replaced by the
	# REPLAY_INGEST_ENABLED config var.
	"replay_matches": dict(
		layer="raw", tenancy="global", writers=("bot/replay_stats/store.py",), retention="forever"
	),
	"replay_players": dict(
		layer="raw", tenancy="global", writers=("bot/replay_stats/store.py",), retention="forever"
	),
	"replay_units": dict(
		layer="raw", tenancy="global", writers=("bot/replay_stats/store.py",), retention="sweepable"
	),
	"replay_techs": dict(
		layer="raw", tenancy="global", writers=("bot/replay_stats/store.py",), retention="sweepable"
	),
	"replay_buildings": dict(
		layer="raw", tenancy="global", writers=("bot/replay_stats/store.py",), retention="sweepable"
	),
	"replay_events": dict(
		layer="raw", tenancy="global", writers=("bot/replay_stats/store.py",), retention="sweepable"
	),
	"replay_apm": dict(
		layer="raw", tenancy="global", writers=("bot/replay_stats/store.py",), retention="sweepable"
	),
	"replay_ingest": dict(
		layer="ops", tenancy="global", writers=("bot/replay_stats/store.py",), retention="forever"
	),
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
	# core — multi-tenancy root (stage 1.5)
	"communities": dict(
		layer="core", tenancy="community", writers=("bot/community.py",), retention="forever"
	),
	"community_channels": dict(
		layer="core", tenancy="channel", writers=("bot/community.py",), retention="forever"
	),
	# link — cross-tenant joins (stage 1.6)
	# core/migrations.py is a writer too: 004_identity_v2 backfills every
	# historical pairing out of replay_matches.bot_match_id (INSERT IGNORE, so
	# bot/community.py's link_match_replay stays the authoritative one). That
	# migration predates 007_raw_renames and still names the table by its old
	# name, which is correct — it runs before the rename.
	"match_replays": dict(
		layer="link", tenancy="community", writers=("bot/community.py", "core/migrations.py"),
		retention="forever"
	),
	# derived-global (stage 3) — computed once at ingest from the replay_* raw
	# tables, community-independent (unlike derived-community's per-community
	# rollups, a later stage). Neither table carries a user_id; both key on
	# profile_id only (identity v2 §5).
	"game_stats": dict(
		layer="derived", tenancy="global", writers=("bot/derived/game_stats.py",), retention="forever"
	),
	"game_labels": dict(
		layer="derived", tenancy="global", writers=("bot/derived/game_labels.py",), retention="forever"
	),
	# derived-community (stage 4) — aggregates OF the derived-global rows above,
	# keyed on (community_id, user_id) rather than on a slot in a match — the
	# first derived grain that is a person in a community. Identity resolution
	# happens in the task-4.5 refresh job that calls the writer, never in the
	# writer and never in the table.
	"player_rollups": dict(
		layer="derived", tenancy="community", writers=("bot/derived/rollups.py",), retention="forever"
	),
	"metric_boards": dict(
		layer="derived", tenancy="community", writers=("bot/derived/boards.py",), retention="forever"
	),
	"civ_stats": dict(
		layer="derived", tenancy="community", writers=("bot/derived/civ_stats.py",), retention="forever"
	),
	# ops/web
	"web_sessions": dict(layer="ops", tenancy="global", writers=("bot/web.py",), retention="forever"),
	"web_oauth_states": dict(layer="ops", tenancy="global", writers=("bot/web.py",), retention="forever"),
}

ALL_TABLES = frozenset(REGISTRY)
