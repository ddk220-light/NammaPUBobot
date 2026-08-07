"""Old table names must never reappear in live code. The allowlist is
nammaoe2bot/runtime/migrations.py (renames reference old names forever) and this test."""
import os

# Only names that cannot collide with ordinary identifiers. `players` and
# `noadds` are deliberately absent: both are live command/module names
# ("/noadds", nammaoe2bot/pickup/noadds.py, team['players']), so a substring guard on
# them fires on legitimate code. Their renames are enforced by
# tests/test_data_registry.py instead, which compares actual declarations.
OLD_NAMES = [
	"qc_matches", "qc_player_matches", "qc_players", "qc_rating_history",
	"qc_match_id_counter", "qc_configs", "pq_configs", "qc_saved_state",
	# Retired by 011_config_factory_rename. `qc_config`/`pq_config` are the
	# cfg_name VALUES CfgFactory.spawn selects on and `pq_id` was
	# queue_settings' primary key; a stray one anywhere in live code means a
	# factory pointing at rows nothing will ever match. Guarded here because
	# they are string literals, which no import test can resolve.
	"qc_config", "pq_config", "pq_id",
	"qc_phrases", "qc_douche", "qc_match_civs", "qc_civ_reconcile",
	"qc_lobbies", "qc_quiz_posts", "qc_quiz_answers", "qc_quiz_config",
	"qc_prediction_posts", "qc_prediction_votes", "on_dublicate",
	"bot_player_commentary", "disabled_guilds", "leaderboard_alternate", "alt_ratings",
	# Retired identity store (identity v2, task 2.5.7): no longer declared or
	# queried anywhere, since `identities` is the sole answer to "who is this
	# person", and dropped by a later migration. Guarded here so a query cannot
	# come back after the table is gone, which would be a runtime error rather
	# than merely stale data. nammaoe2bot/runtime/migrations.py names it forever (the drop
	# migration) and is allowlisted.
	"qc_profile_map",
	# The raw replay tables, renamed out of their inherited `rs_` prefix by
	# 007_raw_renames (stage 3b). None of these eight collides with an ordinary
	# identifier — in particular `rs_player_games` is NOT a substring of the
	# still-live `rs_player_game_tags`, which needs a `_` where this needs an `s`.
	"rs_matches", "rs_player_games", "rs_player_units", "rs_player_techs",
	"rs_player_buildings", "rs_player_events", "rs_player_apm", "rs_ingest",
	# Dropped outright by the same migration: its single `enabled` boolean is now
	# the REPLAY_INGEST_ENABLED config var. Guarded here because a query against a
	# table that no longer exists is a runtime error, not merely stale data.
	"rs_config",
]
# `rs_player_game_tags` and `rs_player_personas` deliberately keep BOTH their
# names and their writers through 007_raw_renames — they are the legacy derived
# generation, still have live readers, and stage 6 deletes them rather than
# carrying them forward — so they must never be added above.
# `rs_profiles` — the OTHER table retired by task 2.5.7 — is deliberately absent
# for the same reason as `players` and `noadds` above: it is a substring of
# ordinary identifiers this codebase already uses (`_match_players_profiles` in
# nammaoe2bot/features/lobby/completed.py, `..._members_profiles_...` in tests/test_identity.py),
# so a substring guard on it fires on legitimate code. Its retirement is
# enforced by tests/test_data_registry.py instead, which compares actual
# ensure_table declarations against nammaoe2bot/runtime/data_registry.py: re-declaring the
# table without a registry entry fails, and adding the entry back is a
# deliberate act rather than an accident.
# Whole-file exemptions, kept to the two files that must name old tables to do
# their job. Everything else stays fully guarded.
_ALLOW = (
	"nammaoe2bot/runtime/migrations.py", "tests/test_naming.py",
	# Exercises the rename guard itself (nammaoe2bot/runtime/migrations.py's own _STAGE1_RENAMES
	# and its post-condition check), so it necessarily uses old table names as
	# literal rename-source strings — same reason nammaoe2bot/runtime/migrations.py is above.
	"tests/test_migrations.py",
)


def _scrub_csv_filenames(src):
	"""Drop `<old_name>.csv` literals before scanning.

	Several modules read on-disk CSVs whose *filenames* still carry the old
	table names, because the files themselves were never renamed — a
	`qc_players.csv` literal is a filename, not a table reference. Exempting
	the whole file would blind the guard to a genuine stale table name
	appearing in it later (nammaoe2bot/discord/events.py alone is 300+ lines), so only the
	filename occurrences are removed and the rest of the file is still
	checked.
	"""
	for name in OLD_NAMES:
		src = src.replace(f"{name}.csv", "")
	return src


# The extensions the walk below scans. `.html` is here for nammaoe2bot/web/page.html,
# the self-contained dashboard SPA: 200KB of inline JS that calls nammaoe2bot/web/server.py's
# REST API, so it is exactly the kind of file a stale table or field name
# survives in — and, being the only non-Python file that talks about the schema
# at all, exactly the kind CI would never look at. Substring matching is safe on
# it for the same reason it is safe on the Python files: every name in OLD_NAMES
# is a multi-word snake_case identifier that cannot occur in ordinary HTML, CSS
# or JS text, and every name that COULD (`players`, `noadds`, `rs_profiles`) is
# deliberately excluded above and enforced by tests/test_data_registry.py instead.
_EXTENSIONS = (".py", ".html")

# Files outside the four package directories that must still be guarded.
# PUBobot2.py is the entrypoint (it drives migrations.run_all and imports every
# bot package) and start.py generates config.cfg — both can name a table, and
# neither lives under bot/, core/, utils/ or tests/, so the walk alone would
# leave them unguarded.
_ROOT_FILES = ("start.py", "PUBobot2.py")


def _scanned_files(root):
	""" Every file the guard checks, as (absolute path, repo-relative path). """
	for base in ("nammaoe2bot", "utils", "tests"):
		for dirpath, _d, files in os.walk(os.path.join(root, base)):
			if "__pycache__" in dirpath:
				continue
			for f in sorted(files):
				if f.endswith(_EXTENSIONS):
					path = os.path.join(dirpath, f)
					yield path, os.path.relpath(path, root)
	for f in _ROOT_FILES:
		yield os.path.join(root, f), f


def test_no_old_table_names_in_live_code():
	root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	hits = []
	for path, rel in _scanned_files(root):
		if rel in _ALLOW:
			continue
		with open(path, encoding="utf-8") as fh:
			src = _scrub_csv_filenames(fh.read())
		for name in OLD_NAMES:
			if name in src:
				hits.append(f"{rel}: {name}")
	assert hits == [], "old names in live code:\n" + "\n".join(hits)


def test_the_guard_covers_the_files_outside_the_package_directories():
	""" The walk above only descends into bot/, core/, utils/ and tests/, so the
	two root-level Python files and the dashboard SPA are reachable only because
	they are named or matched explicitly. A regression there is invisible — the
	guard would still pass, on fewer files.

	This recomputes the walk AND applies _ALLOW, because the guard skips
	allowlisted files before reading them: checking the walk alone left the
	allowlist as an unguarded back door, so adding nammaoe2bot/web/page.html to _ALLOW
	removed 200KB of coverage and passed green. Both halves of "is this file
	actually checked" are asserted. """
	root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	checked = {rel for _path, rel in _scanned_files(root) if rel not in _ALLOW}
	for rel in ("start.py", "PUBobot2.py", os.path.join("nammaoe2bot", "web", "page.html")):
		assert rel in checked, f"{rel} is not covered by the stale-name guard"


def test_the_allowlist_holds_only_the_files_that_must_name_old_tables():
	""" _ALLOW is a whole-file exemption — the one mechanism here that can
	silently remove coverage from anything. It is pinned to its exact contents
	so growing it is a deliberate edit to this test, with a reason, rather than
	a one-line way to make the guard stop complaining. """
	assert set(_ALLOW) == {
		"nammaoe2bot/runtime/migrations.py",      # names every renamed table forever, by design
		"tests/test_naming.py",    # this file lists them as literals
		"tests/test_migrations.py",  # exercises the rename map itself
	}


def test_the_csv_scrub_does_not_blind_the_guard():
	"""A real table reference in a file that also holds a qc_*.csv filename
	must still be caught — otherwise the scrub is a whole-file exemption in
	disguise."""
	src = 'path = "data/qc_players.csv"\nawait db.select(["x"], "qc_players")\n'
	scrubbed = _scrub_csv_filenames(src)
	assert "qc_players.csv" not in scrubbed
	assert "qc_players" in scrubbed
