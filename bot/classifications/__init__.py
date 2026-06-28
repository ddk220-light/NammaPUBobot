# -*- coding: utf-8 -*-
"""Read-side of the classification framework: the cls_* tables (written offline by
utils/classifications/runner.py) are declared here via ensure_table so the bot can read them
for /insights. Columns mirror utils/classifications/schema.py exactly."""
from core.database import db

db.ensure_table(dict(
	tname="cls_classifications",
	columns=[
		dict(cname="key", ctype=db.types.str),
		dict(cname="title", ctype=db.types.str, notnull=False),
		dict(cname="description", ctype=db.types.text, notnull=False),
		dict(cname="trigger_spec", ctype=db.types.text, notnull=False),
		dict(cname="version", ctype=db.types.int, notnull=False),
		dict(cname="status", ctype=db.types.str, notnull=False),
		dict(cname="updated_at", ctype=db.types.int, notnull=False),
	],
	primary_keys=["key"],
))

db.ensure_table(dict(
	tname="cls_data_requirements",
	columns=[
		dict(cname="key", ctype=db.types.str),
		dict(cname="field", ctype=db.types.str),
		dict(cname="source", ctype=db.types.str, notnull=False),
		dict(cname="status", ctype=db.types.str, notnull=False),
		dict(cname="note", ctype=db.types.text, notnull=False),
	],
	primary_keys=["key", "field"],
))

db.ensure_table(dict(
	tname="cls_results",
	columns=[
		dict(cname="key", ctype=db.types.str),
		dict(cname="aoe2_match_id", ctype=db.types.int),
		dict(cname="player_number", ctype=db.types.int),
		dict(cname="profile_id", ctype=db.types.int, notnull=False),
		dict(cname="identity", ctype=db.types.str, notnull=False),
		dict(cname="civ", ctype=db.types.str, notnull=False),
		dict(cname="team", ctype=db.types.str, notnull=False),
		dict(cname="winner", ctype=db.types.bool, notnull=False),
		dict(cname="played_at", ctype=db.types.int, notnull=False),
	],
	primary_keys=["key", "aoe2_match_id", "player_number"],
))

db.ensure_table(dict(
	tname="cls_result_metrics",
	columns=[
		dict(cname="key", ctype=db.types.str),
		dict(cname="aoe2_match_id", ctype=db.types.int),
		dict(cname="player_number", ctype=db.types.int),
		dict(cname="metric", ctype=db.types.str),
		dict(cname="value", ctype=db.types.float, notnull=False),
	],
	primary_keys=["key", "aoe2_match_id", "player_number", "metric"],
))

db.ensure_table(dict(
	tname="cls_player_totals",
	columns=[
		dict(cname="identity", ctype=db.types.str),
		dict(cname="games", ctype=db.types.int, notnull=False),
		dict(cname="wins", ctype=db.types.int, notnull=False),
		dict(cname="losses", ctype=db.types.int, notnull=False),
	],
	primary_keys=["identity"],
))

db.ensure_table(dict(
	tname="cls_match_ingest",
	columns=[
		dict(cname="aoe2_match_id", ctype=db.types.int),
		dict(cname="classified_at", ctype=db.types.int, notnull=False),
		dict(cname="result_rows", ctype=db.types.int, notnull=False),
		dict(cname="status", ctype=db.types.str, notnull=False),
	],
	primary_keys=["aoe2_match_id"],
))
