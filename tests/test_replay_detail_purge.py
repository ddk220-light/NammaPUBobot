"""Safety contract for the explicit paused-replay storage tool."""
import gzip
import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "purge_paused_replay_detail.py"
SPEC = importlib.util.spec_from_file_location("purge_paused_replay_detail", SCRIPT)
purge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(purge)


def test_target_list_contains_only_high_cardinality_replay_detail():
	assert purge.TARGET_TABLES == (
		"replay_events", "replay_techs", "replay_buildings",
		"replay_units", "replay_apm")
	for preserved in (
		"matches", "match_players", "match_replays", "replay_matches",
		"replay_players", "game_stats", "game_labels", "civ_picks"):
		assert preserved not in purge.TARGET_TABLES


def test_connection_url_decodes_credentials_without_printing_them(monkeypatch):
	monkeypatch.setenv(
		"DB_URI", "mysql://user%40name:p%3Ass@private.internal:3307/community")
	settings = purge.connection_settings()
	assert settings == {
		"host": "private.internal", "port": 3307, "user": "user@name",
		"password": "p:ss", "database": "community"}


def test_apply_flags_must_be_explicitly_false(monkeypatch):
	monkeypatch.delenv("REPLAY_INGEST_ENABLED", raising=False)
	assert purge._false_env("REPLAY_INGEST_ENABLED") is False
	for value in ("False", "0", "off", "NO"):
		monkeypatch.setenv("REPLAY_INGEST_ENABLED", value)
		assert purge._false_env("REPLAY_INGEST_ENABLED") is True
	monkeypatch.setenv("REPLAY_INGEST_ENABLED", "True")
	assert purge._false_env("REPLAY_INGEST_ENABLED") is False


def test_backup_validation_reads_through_the_gzip_crc(tmp_path):
	backup = tmp_path / "backup.sql.gz"
	with gzip.open(backup, "wb") as stream:
		stream.write(b"CREATE TABLE example (id BIGINT);\n")
	digest = purge.verify_backup(backup)
	assert len(digest) == 64

	broken = tmp_path / "broken.sql.gz"
	broken.write_bytes(backup.read_bytes()[:-4])
	try:
		purge.verify_backup(broken)
		assert False, "truncated backup must be rejected"
	except SystemExit as exc:
		assert "integrity" in str(exc)
