import base64
import io
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from nammaoe2bot.web import pubobot_migration


def _archive(overrides=None):
	files = {
		"qc_matches.csv": (
			"match_id,queue,at,winner_team,maps\n"
			"7,nomad,2026-01-15 20:30:00,0,Nomad\n"),
		"qc_players.csv": (
			"user_id,nick,is_hidden,rating,deviation,wins,losses,draws,streak\n"
			"42,Alice,0,1510,90,8,3,0,2\n"
			"43,Bob,0,1490,95,3,8,0,-2\n"),
		"qc_player_matches.csv": (
			"match_id,user_id,team\n7,42,0\n7,43,1\n"),
		"qc_rating_history.csv": (
			"user_id,at,rating_before,rating_change,deviation_before,deviation_change,match_id,reason\n"
			"42,2026-01-15 20:30:01,1500,10,100,-10,7,nomad\n"
			"43,2026-01-15 20:30:01,1500,-10,100,-5,7,nomad\n"),
	}
	files.update(overrides or {})
	buffer = io.BytesIO()
	with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
		for name, content in files.items():
			archive.writestr("export/" + name, content)
	return buffer.getvalue()


def _payload(content=None, timezone="UTC"):
	return {
		"file_name": "pubobot-export.zip",
		"content_base64": base64.b64encode(content or _archive()).decode("ascii"),
		"timezone": timezone,
	}


def test_complete_archive_is_validated_and_transformed_in_the_selected_timezone():
	parsed = pubobot_migration.parse_archive(_payload(timezone="America/Los_Angeles"))

	expected = int(datetime(2026, 1, 15, 20, 30, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp())
	assert parsed["matches"] == [{
		"source_match_id": 7, "queue_name": "nomad", "reported_at": expected,
		"winner": 0, "alpha_score": 1, "beta_score": 0, "maps": "Nomad",
	}]
	assert parsed["players"][0]["last_ranked_match_at"] == expected
	assert parsed["match_players"] == [
		{"source_match_id": 7, "user_id": 42, "team": 0},
		{"source_match_id": 7, "user_id": 43, "team": 1},
	]
	assert parsed["rating_history"][0]["source_match_id"] == 7
	assert parsed["timezone"] == "America/Los_Angeles"


def test_archive_rejects_relations_to_missing_matches():
	content = _archive({
		"qc_player_matches.csv": "match_id,user_id,team\n999,42,0\n",
	})
	with pytest.raises(pubobot_migration.MigrationInputError, match="match absent"):
		pubobot_migration.parse_archive(_payload(content))


def test_archive_requires_all_four_exports():
	buffer = io.BytesIO()
	with zipfile.ZipFile(buffer, "w") as archive:
		archive.writestr("qc_players.csv", "user_id,nick\n42,Alice\n")
	with pytest.raises(pubobot_migration.MigrationInputError, match="ZIP is missing"):
		pubobot_migration.parse_archive(_payload(buffer.getvalue()))


def test_migration_digest_is_bound_to_both_storage_channels():
	parsed = pubobot_migration.parse_archive(_payload())
	base = pubobot_migration.migration_digest(100, 100, parsed)
	assert base != pubobot_migration.migration_digest(200, 100, parsed)
	assert base != pubobot_migration.migration_digest(100, 200, parsed)
