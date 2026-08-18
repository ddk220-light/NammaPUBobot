import base64
import io
import zipfile

import pytest

from nammaoe2bot.web import onboarding


def _payload(name, content):
	return {
		"file_name": name,
		"content_base64": base64.b64encode(content).decode("ascii"),
	}


def _zip(files):
	buffer = io.BytesIO()
	with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
		for name, content in files.items():
			archive.writestr(name, content)
	return buffer.getvalue()


def test_manual_rating_seed_normalizes_aliases_and_uses_channel_deviation():
	parsed = onboarding.parse_seed_payload({"rows": [
		{"discord_id": "123", "elo": "1450", "display_name": "Alice"},
		{"user_id": 456, "rating": 1325, "rd": 110, "nick": "Bob"},
	]}, default_deviation=200)

	assert parsed == {
		"name": "Manual entry",
		"rows": [
			{"line": 1, "user_id": 123, "nick": "Alice", "rating": 1450,
			 "deviation": 200, "errors": []},
			{"line": 2, "user_id": 456, "nick": "Bob", "rating": 1325,
			 "deviation": 110, "errors": []},
		],
	}


def test_pubobot_zip_selects_the_player_csv_without_extracting_other_csvs():
	content = _zip({
		"export/qc_matches.csv": "match_id,queue\n1,nomad\n",
		"export/qc_players.csv": "user_id,nick,rating,deviation,wins\n123,Alice,1510,95,9\n",
	})
	parsed = onboarding.parse_seed_payload(_payload("pubobot-export.zip", content), 200)

	assert parsed["name"] == "export/qc_players.csv"
	assert parsed["rows"] == [{
		"line": 2, "user_id": 123, "nick": "Alice", "rating": 1510,
		"deviation": 95, "errors": [],
	}]


def test_zip_with_multiple_unknown_csvs_is_rejected_instead_of_guessing():
	content = _zip({
		"one.csv": "user_id,rating\n1,1000\n",
		"two.csv": "user_id,rating\n2,1100\n",
	})
	with pytest.raises(onboarding.SeedInputError, match="multiple CSV files"):
		onboarding.parse_seed_payload(_payload("ratings.zip", content), 200)


def test_duplicate_and_out_of_range_rows_are_returned_as_preview_errors():
	parsed = onboarding.parse_seed_payload({"rows": [
		{"user_id": "99", "rating": "1000"},
		{"user_id": "99", "rating": "10000", "deviation": "0"},
	]}, 200)

	first, second = parsed["rows"]
	assert first["errors"] == ["Discord user ID appears more than once in this import."]
	assert "Rating must be between 1 and 9999." in second["errors"]
	assert "Deviation must be between 1 and 2999." in second["errors"]
	assert "Discord user ID appears more than once in this import." in second["errors"]


def test_preview_digest_is_stable_but_bound_to_the_rating_channel():
	parsed = onboarding.parse_seed_payload({"rows": [{"user_id": 1, "rating": 1000}]}, 200)
	assert onboarding.seed_digest(100, parsed) == onboarding.seed_digest(100, parsed)
	assert onboarding.seed_digest(100, parsed) != onboarding.seed_digest(200, parsed)


def test_identity_rows_allow_multiple_profiles_per_user_but_reject_duplicate_profiles():
	parsed = onboarding.parse_identity_payload({"rows": [
		{"discord_id": "123", "aoe2_profile_id": "9001", "game_name": "Alice"},
		{"user_id": "123", "profile_id": "9002"},
		{"user_id": "456", "profile_id": "9002"},
	]})

	assert parsed["rows"][0] == {
		"line": 1, "user_id": 123, "profile_id": 9001, "aoe2_name": "Alice", "errors": []}
	assert parsed["rows"][1]["errors"] == [
		"AoE2 profile ID appears more than once in this import."]
	assert parsed["rows"][2]["errors"] == [
		"AoE2 profile ID appears more than once in this import."]


def test_identity_zip_prefers_the_resolved_profile_mapping():
	content = _zip({
		"export/players.csv": "user_id,rating\n123,1400\n",
		"export/profile_resolved.csv": "profile_id,user_id,aoe2_name\n9001,123,Alice\n",
	})
	parsed = onboarding.parse_identity_payload(_payload("community.zip", content))

	assert parsed["name"] == "export/profile_resolved.csv"
	assert parsed["rows"] == [{
		"line": 2, "user_id": 123, "profile_id": 9001,
		"aoe2_name": "Alice", "errors": [],
	}]


def test_identity_digest_is_bound_to_the_authorized_community():
	parsed = onboarding.parse_identity_payload({"rows": [{"user_id": 1, "profile_id": 2}]})
	assert onboarding.identity_digest(9, parsed) != onboarding.identity_digest(10, parsed)
