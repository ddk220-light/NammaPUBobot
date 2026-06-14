# -*- coding: utf-8 -*-
"""Lobby tracking subsystem — strictly additive, opt-in.

A self-contained feature that watches AoE2 lobbies and, when one is linked to a
ranked pickup match, can drive a captain-confirmed result. It does NOT change
the existing flow: creating your own lobby and running ``/report`` as usual
keeps working byte-for-byte, and the bot's own civ / rating / reconcile
pipelines are untouched. This only adds a smoother path when a lobby is created
named ``test123`` (auto-detected) or tracked via ``/lobby2`` / ``/lobby``. Every
entry point degrades silently if the (unofficial) lobby socket or API misbehaves.

Durable store is ``qc_lobbies`` (NOT saved_state.json) — lobbies survive a
Railway redeploy the same way matches do, through MySQL. ``qc_profile_map`` is
the DB-backed, self-healing Discord-user <-> AoE2-profile map that replaces the
stale data/player_profile_map.csv.

Tables are declared here (ensure_table auto-creates + ALTERs at import, the
civ_sync.py pattern). bot/__init__.py imports this module for that side effect
and for the LobbyJobs singleton.
"""
from core.database import db

# One row per tracked lobby. status walks: created -> filling -> in_progress ->
# completed | expired. match_id links to the ranked bot match (NULL for a bare
# informational /lobby <id>). profile_ids is a csv of captured slot profileIds.
db.ensure_table(dict(
	tname="qc_lobbies",
	columns=[
		dict(cname="id", ctype=db.types.int, autoincrement=True),
		dict(cname="aoe2_game_id", ctype=db.types.int),
		dict(cname="channel_id", ctype=db.types.int),
		dict(cname="message_id", ctype=db.types.int, notnull=False),
		dict(cname="completed_message_id", ctype=db.types.int, notnull=False),
		dict(cname="match_id", ctype=db.types.int, notnull=False),
		dict(cname="status", ctype=db.types.str),
		dict(cname="lobby_name", ctype=db.types.str),
		dict(cname="map_name", ctype=db.types.str),
		dict(cname="server", ctype=db.types.str),
		dict(cname="profile_ids", ctype=db.types.text),
		dict(cname="created_at", ctype=db.types.int),
		dict(cname="last_edit_at", ctype=db.types.int, notnull=False),
		dict(cname="requested_by", ctype=db.types.int, notnull=False),
	],
	primary_keys=["id"],
))

# Self-healing Discord-user <-> AoE2-profile map. Learned from roster-confirmed
# lobbies: each slot's (profileId, name) is authoritative for that match's
# players. The results/ratings loop NEVER reads this — it only powers the
# optional winner-name hint + per-player civ attribution, both best-effort.
# Composite PK lets a user carry more than one profile (smurf/alt) without dup
# rows; Phase 2 link logic prefers the most recent.
db.ensure_table(dict(
	tname="qc_profile_map",
	columns=[
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="profile_id", ctype=db.types.int),
		dict(cname="name", ctype=db.types.str),
		dict(cname="linked_at", ctype=db.types.int, notnull=False),
		dict(cname="source", ctype=db.types.str),  # 'lobby' | 'register' | 'seed'
	],
	primary_keys=["user_id", "profile_id"],
))

from .jobs import jobs  # noqa: E402,F401  (LobbyJobs singleton — bot.lobby.jobs.think)
