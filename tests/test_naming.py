"""Old table names must never reappear in live code. The allowlist is
core/migrations.py (renames reference old names forever) and this test."""
import os

# Only names that cannot collide with ordinary identifiers. `players` and
# `noadds` are deliberately absent: both are live command/module names
# ("/noadds", bot/stats/noadds.py, team['players']), so a substring guard on
# them fires on legitimate code. Their renames are enforced by
# tests/test_data_registry.py instead, which compares actual declarations.
OLD_NAMES = [
	"qc_matches", "qc_player_matches", "qc_players", "qc_rating_history",
	"qc_match_id_counter", "qc_configs", "pq_configs", "qc_saved_state",
	"qc_phrases", "qc_douche", "qc_match_civs", "qc_civ_reconcile",
	"qc_lobbies", "qc_quiz_posts", "qc_quiz_answers", "qc_quiz_config",
	"qc_prediction_posts", "qc_prediction_votes", "on_dublicate",
	"bot_player_commentary", "disabled_guilds", "leaderboard_alternate", "alt_ratings",
	# Retired identity store (identity v2, task 2.5.7): no longer declared or
	# queried anywhere, since `identities` is the sole answer to "who is this
	# person", and dropped by a later migration. Guarded here so a query cannot
	# come back after the table is gone, which would be a runtime error rather
	# than merely stale data. core/migrations.py names it forever (the drop
	# migration) and is allowlisted.
	"qc_profile_map",
]
# `rs_profiles` — the OTHER table retired by task 2.5.7 — is deliberately absent
# for the same reason as `players` and `noadds` above: it is a substring of
# ordinary identifiers this codebase already uses (`_match_players_profiles` in
# bot/lobby/completed.py, `..._members_profiles_...` in tests/test_identity.py),
# so a substring guard on it fires on legitimate code. Its retirement is
# enforced by tests/test_data_registry.py instead, which compares actual
# ensure_table declarations against core/data_registry.py: re-declaring the
# table without a registry entry fails, and adding the entry back is a
# deliberate act rather than an accident.
# Whole-file exemptions, kept to the two files that must name old tables to do
# their job. Everything else stays fully guarded.
_ALLOW = (
	"core/migrations.py", "tests/test_naming.py",
	# Exercises the rename guard itself (core/migrations.py's own _STAGE1_RENAMES
	# and its post-condition check), so it necessarily uses old table names as
	# literal rename-source strings — same reason core/migrations.py is above.
	"tests/test_migrations.py",
)


def _scrub_csv_filenames(src):
	"""Drop `<old_name>.csv` literals before scanning.

	Several modules read on-disk CSVs whose *filenames* still carry the old
	table names, because the files themselves were never renamed — a
	`qc_players.csv` literal is a filename, not a table reference. Exempting
	the whole file would blind the guard to a genuine stale table name
	appearing in it later (bot/events.py alone is 300+ lines), so only the
	filename occurrences are removed and the rest of the file is still
	checked.
	"""
	for name in OLD_NAMES:
		src = src.replace(f"{name}.csv", "")
	return src


def test_no_old_table_names_in_live_code():
	root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	hits = []
	for base in ("bot", "core", "utils", "tests"):
		for dirpath, _d, files in os.walk(os.path.join(root, base)):
			if "__pycache__" in dirpath:
				continue
			for f in files:
				if not f.endswith(".py"):
					continue
				path = os.path.join(dirpath, f)
				rel = os.path.relpath(path, root)
				if rel in _ALLOW:
					continue
				with open(path, encoding="utf-8") as fh:
					src = _scrub_csv_filenames(fh.read())
				for name in OLD_NAMES:
					if name in src:
						hits.append(f"{rel}: {name}")
	assert hits == [], "old names in live code:\n" + "\n".join(hits)


def test_the_csv_scrub_does_not_blind_the_guard():
	"""A real table reference in a file that also holds a qc_*.csv filename
	must still be caught — otherwise the scrub is a whole-file exemption in
	disguise."""
	src = 'path = "data/qc_players.csv"\nawait db.select(["x"], "qc_players")\n'
	scrubbed = _scrub_csv_filenames(src)
	assert "qc_players.csv" not in scrubbed
	assert "qc_players" in scrubbed
