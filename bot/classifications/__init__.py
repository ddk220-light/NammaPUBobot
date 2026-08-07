# -*- coding: utf-8 -*-
"""Read-side of the classification framework: the cls_* tables (written offline by
utils/classifications/runner.py) are declared here via ensure_table so the bot can read them
for /insights. Columns mirror utils/classifications/schema.py exactly.

The `indexes=` entries below only take effect when this declaration CREATES the table
(a fresh install) -- ensure_table never alters the keys of a table that already exists,
by design (see nammaoe2bot/runtime/database/mysql.py's table_blank). An existing database gets the
same indexes from migration 006_derived_indexes instead. Both halves are required:
neither one covers the other's case, so an index added here must be added there too.

The indexes themselves are the per-match access path bot/derived/backfill.py's
reconciliation loop needs: it reads one match's rows at a time (WHERE aoe2_match_id=%s)
and both primary keys lead with `key`, so without them every read is a full table scan.
Index names are written out literally rather than via a shared constant so
tests/test_migrations.py::test_the_index_names_match_the_ensure_table_declaration can
compare all three declarations as text without importing this module (importing it
executes ensure_table)."""
from nammaoe2bot.runtime.database import db

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
	indexes=[("cls_results_match", ["aoe2_match_id"])],
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
	indexes=[("cls_result_metrics_match", ["aoe2_match_id"])],
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
