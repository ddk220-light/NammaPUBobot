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
]
_ALLOW = (
	"core/migrations.py", "tests/test_naming.py",
	# Exercises the rename guard itself (core/migrations.py's own _STAGE1_RENAMES
	# and its post-condition check), so it necessarily uses old table names as
	# literal rename-source strings — same reason core/migrations.py is above.
	"tests/test_migrations.py",
	# These read/write on-disk CSV files whose *filenames* were never renamed
	# alongside the DB tables (see core/migrations.py's _STAGE1_RENAMES) — a
	# qc_*.csv literal here is a filename, not a table reference. Do not
	# "fix" these back to the new table names; the files on disk are still
	# named after the old tables.
	"bot/events.py",
	"utils/civ_analysis.py",
	"utils/civ_elo_stats.py",
	"utils/import_pubobot_export.py",
	"utils/replay_quiz/attribution.py",
	"utils/replay_quiz/build_db.py",
)


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
					src = fh.read()
				for name in OLD_NAMES:
					if name in src:
						hits.append(f"{rel}: {name}")
	assert hits == [], "old names in live code:\n" + "\n".join(hits)
