# -*- coding: utf-8 -*-
"""Live replay-stats subsystem — strictly additive, opt-in (off until rs_config.enabled=1).
Mirrors bot/quiz/ isolation: dedicated rs_* tables declared here via ensure_table at import,
imported by bot/__init__.py for that side effect and the ReplayStatsJobs singleton. Heavy
imports (mgz, requests) stay lazy inside fetch.py/parse.py so importing this package is
test-safe under the conftest stubs."""
from core.database import db

# Bumped whenever the mgz pin or SUPPORTED_SAVE_VERSIONS policy changes (see policy.py),
# or when the extractor's output shape changes. Stored on every parsed match; a bump
# auto-reopens pending_parser_update rows (NOT 'done' rows — those are re-done by an explicit backfill).
PARSER_VERSION = "mgz-a1683d8+3"   # +3: emit per-queue production events -> rs_player_events (growth-curve timeline)

db.ensure_table(dict(
    tname="rs_config",
    columns=[
        dict(cname="id", ctype=db.types.int),          # always 1 (single-row global config)
        dict(cname="enabled", ctype=db.types.bool, notnull=True, default=0),
    ],
    primary_keys=["id"],
))

db.ensure_table(dict(
    tname="rs_matches",
    columns=[
        dict(cname="aoe2_match_id", ctype=db.types.int),
        dict(cname="bot_match_id", ctype=db.types.int, notnull=False),
        dict(cname="map", ctype=db.types.str, notnull=False),
        dict(cname="save_version", ctype=db.types.float, notnull=False),
        dict(cname="duration_s", ctype=db.types.int, notnull=False),
        dict(cname="played_at", ctype=db.types.str, notnull=False),   # date string from extract
        dict(cname="replay_url", ctype=db.types.str, notnull=False),
        dict(cname="parsed_at", ctype=db.types.int, notnull=False),
        dict(cname="parser_version", ctype=db.types.str, notnull=False),
    ],
    primary_keys=["aoe2_match_id"],
))

db.ensure_table(dict(
    tname="rs_player_games",
    columns=[
        dict(cname="aoe2_match_id", ctype=db.types.int),
        dict(cname="profile_id", ctype=db.types.int),
        dict(cname="player_number", ctype=db.types.int),
        dict(cname="user_id", ctype=db.types.int, notnull=False),
        dict(cname="identity", ctype=db.types.str, notnull=False),
        dict(cname="attribution", ctype=db.types.str, notnull=False),
        dict(cname="civ", ctype=db.types.str, notnull=False),
        dict(cname="team", ctype=db.types.str, notnull=False),
        dict(cname="winner", ctype=db.types.bool, notnull=False),
        dict(cname="eapm", ctype=db.types.int, notnull=False),
        dict(cname="age_reliable", ctype=db.types.bool, notnull=False),
        dict(cname="tc_relocations", ctype=db.types.int, notnull=False),
        dict(cname="feudal_s", ctype=db.types.int, notnull=False),
        dict(cname="castle_s", ctype=db.types.int, notnull=False),
        dict(cname="imperial_s", ctype=db.types.int, notnull=False),
        dict(cname="first_tc_s", ctype=db.types.int, notnull=False),
        dict(cname="villagers", ctype=db.types.int, notnull=False),
        dict(cname="vil_pre_feudal", ctype=db.types.int, notnull=False),
        dict(cname="vil_pre_castle", ctype=db.types.int, notnull=False),
        dict(cname="vil_pre_imperial", ctype=db.types.int, notnull=False),
        dict(cname="military", ctype=db.types.int, notnull=False),
        dict(cname="mil_pre_feudal", ctype=db.types.int, notnull=False),
        dict(cname="mil_pre_castle", ctype=db.types.int, notnull=False),
        dict(cname="mil_pre_imperial", ctype=db.types.int, notnull=False),
    ],
    primary_keys=["aoe2_match_id", "profile_id"],
))

db.ensure_table(dict(
    tname="rs_player_units",
    columns=[
        dict(cname="aoe2_match_id", ctype=db.types.int),
        dict(cname="player_number", ctype=db.types.int),
        dict(cname="unit", ctype=db.types.str),
        dict(cname="profile_id", ctype=db.types.int, notnull=False),
        dict(cname="category", ctype=db.types.str, notnull=False),
        dict(cname="is_military", ctype=db.types.bool, notnull=False),
        dict(cname="total", ctype=db.types.int, notnull=False),
        dict(cname="pre_feudal", ctype=db.types.int, notnull=False),
        dict(cname="pre_castle", ctype=db.types.int, notnull=False),
        dict(cname="pre_imperial", ctype=db.types.int, notnull=False),
    ],
    primary_keys=["aoe2_match_id", "player_number", "unit"],
))

db.ensure_table(dict(
    tname="rs_player_techs",
    columns=[
        dict(cname="aoe2_match_id", ctype=db.types.int),
        dict(cname="player_number", ctype=db.types.int),
        dict(cname="tech", ctype=db.types.str),
        dict(cname="profile_id", ctype=db.types.int, notnull=False),
        dict(cname="click_s", ctype=db.types.int, notnull=False),
        dict(cname="phase", ctype=db.types.str, notnull=False),
    ],
    primary_keys=["aoe2_match_id", "player_number", "tech"],
))

db.ensure_table(dict(
    tname="rs_player_buildings",
    columns=[
        dict(cname="aoe2_match_id", ctype=db.types.int),
        dict(cname="player_number", ctype=db.types.int),
        dict(cname="building", ctype=db.types.str),
        dict(cname="profile_id", ctype=db.types.int, notnull=False),
        dict(cname="count", ctype=db.types.int, notnull=False),
    ],
    primary_keys=["aoe2_match_id", "player_number", "building"],
))

db.ensure_table(dict(
    tname="rs_player_events",
    # Per-action production timeline (the genuinely-new data the growth-curve chart needs):
    # one row per DE_QUEUE train-click, carrying its timestamp + batch amount + unit category.
    # `kind` is 'queue' today; 'build'/'research'/'age' are reserved for B3 (no migration needed).
    # PK (aoe2_match_id, player_number, seq) indexes the match-id lookup AND makes re-ingest
    # idempotent (write_match deletes by aoe2_match_id first, then re-inserts seq from 0).
    columns=[
        dict(cname="aoe2_match_id", ctype=db.types.int),
        dict(cname="player_number", ctype=db.types.int),
        dict(cname="seq", ctype=db.types.int),               # per-(match,player) event index
        dict(cname="profile_id", ctype=db.types.int, notnull=False),
        dict(cname="t_s", ctype=db.types.int, notnull=False),   # action timestamp, seconds from start
        dict(cname="kind", ctype=db.types.str, notnull=False),  # 'queue' (B1); build/research/age reserved
        dict(cname="name", ctype=db.types.str, notnull=False),  # unit name
        dict(cname="category", ctype=db.types.str, notnull=False),
        dict(cname="is_military", ctype=db.types.bool, notnull=False),
        dict(cname="amount", ctype=db.types.int, notnull=False),
    ],
    primary_keys=["aoe2_match_id", "player_number", "seq"],
))

db.ensure_table(dict(
    tname="rs_player_personas",
    # Materialized persona per (player, period window) — recomputed for a
    # match's players right after ingest (see store.write_match), read by the
    # web API instead of re-deriving from full history on every request.
    columns=[
        dict(cname="user_id", ctype=db.types.int),
        dict(cname="period", ctype=db.types.str),
        dict(cname="persona_key", ctype=db.types.str, notnull=False),
        dict(cname="style", ctype=db.types.str, notnull=False),
        dict(cname="role", ctype=db.types.str, notnull=False),
        dict(cname="name", ctype=db.types.str, notnull=False),
        dict(cname="epithet", ctype=db.types.str, notnull=False),
        dict(cname="tagline", ctype=db.types.str, notnull=False),
        dict(cname="evidence_json", ctype=db.types.dict, notnull=False),
        dict(cname="carry_rate", ctype=db.types.float, notnull=False),
        dict(cname="impact_sd", ctype=db.types.float, notnull=False),
        dict(cname="matches", ctype=db.types.int, notnull=False),
        dict(cname="computed_at", ctype=db.types.int, notnull=False),
    ],
    primary_keys=["user_id", "period"],
))

db.ensure_table(dict(
    tname="rs_ingest",
    columns=[
        dict(cname="aoe2_match_id", ctype=db.types.int),
        dict(cname="status", ctype=db.types.str, notnull=True),
        dict(cname="save_version", ctype=db.types.float, notnull=False),
        dict(cname="parser_version", ctype=db.types.str, notnull=False),
        dict(cname="attempts", ctype=db.types.int, notnull=True, default=0),
        dict(cname="first_seen_at", ctype=db.types.int, notnull=False),
        dict(cname="last_attempt_at", ctype=db.types.int, notnull=False),
        dict(cname="next_attempt_at", ctype=db.types.int, notnull=False),
        dict(cname="error_reason", ctype=db.types.str, notnull=False),
    ],
    primary_keys=["aoe2_match_id"],
))

db.ensure_table(dict(
    tname="rs_profiles",
    columns=[
        dict(cname="profile_id", ctype=db.types.int),
        dict(cname="user_id", ctype=db.types.int, notnull=False),
        dict(cname="name", ctype=db.types.str, notnull=False),
        dict(cname="last_seen_at", ctype=db.types.int, notnull=False),
    ],
    primary_keys=["profile_id"],
))

db.ensure_table(dict(
    tname="rs_player_game_tags",
    columns=[
        dict(cname="aoe2_match_id", ctype=db.types.int),
        dict(cname="player_number", ctype=db.types.int),
        dict(cname="tag", ctype=db.types.str),
        dict(cname="tag_label", ctype=db.types.str, notnull=False),
        dict(cname="category", ctype=db.types.str, notnull=False),
        dict(cname="score", ctype=db.types.float, notnull=False),
        dict(cname="evidence_json", ctype=db.types.dict, notnull=False),
        dict(cname="played_at", ctype=db.types.int, notnull=False),
        dict(cname="created_at", ctype=db.types.int, notnull=False),
        dict(cname="user_id", ctype=db.types.int, notnull=False),
        dict(cname="profile_id", ctype=db.types.int, notnull=False),
        dict(cname="identity", ctype=db.types.str, notnull=False),
        dict(cname="civ", ctype=db.types.str, notnull=False),
        dict(cname="team", ctype=db.types.str, notnull=False),
        dict(cname="winner", ctype=db.types.bool, notnull=False),
    ],
    primary_keys=["aoe2_match_id", "player_number", "tag"],
))

from .jobs import jobs  # noqa: E402,F401  (ReplayStatsJobs singleton)
