"""Pure parsing and validation for the web rating-onboarding flow.

The web handler owns authorization and database writes.  This module owns the
untrusted file boundary so the same rules are used for preview and apply:

* accept a small JSON row list, a CSV, or a Pubobot export ZIP;
* never extract a ZIP to disk;
* cap compressed bytes, expanded bytes, member count, and player rows;
* normalize the few common column-name variants without guessing values; and
* produce a stable digest that binds an apply request to its preview.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import zipfile


MAX_UPLOAD_BYTES = 700_000
MAX_EXPANDED_BYTES = 2_000_000
MAX_ZIP_MEMBERS = 25
MAX_SEED_ROWS = 500
MAX_NICK_LENGTH = 191


class SeedInputError(ValueError):
	"""A user-correctable problem with an onboarding payload."""


_ALIASES = {
	"user_id": ("user_id", "userid", "discord_id", "discord_user_id"),
	"nick": ("nick", "nickname", "name", "username", "display_name"),
	"rating": ("rating", "elo"),
	"deviation": ("deviation", "rd", "rating_deviation"),
}

_IDENTITY_ALIASES = {
	"user_id": ("user_id", "userid", "discord_id", "discord_user_id"),
	"profile_id": ("profile_id", "profileid", "aoe2_profile_id", "aoe_profile_id"),
	"aoe2_name": ("aoe2_name", "game_name", "profile_name", "ingame_name"),
}


def _header(value):
	return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _aliased(row, name):
	clean = {_header(key): value for key, value in row.items() if key is not None}
	for alias in _ALIASES[name]:
		if alias in clean:
			return clean[alias]
	return None


def _int_value(value, label, *, minimum, maximum, required=True):
	if value is None or str(value).strip() == "":
		if required:
			raise ValueError(f"{label} is required.")
		return None
	try:
		parsed = int(str(value).strip())
	except (TypeError, ValueError) as exc:
		raise ValueError(f"{label} must be a whole number.") from exc
	if not minimum <= parsed <= maximum:
		raise ValueError(f"{label} must be between {minimum} and {maximum}.")
	return parsed


def _decode_upload(payload):
	name = str(payload.get("file_name") or "").strip()
	encoded = payload.get("content_base64")
	if not name or not isinstance(encoded, str) or not encoded:
		raise SeedInputError("Choose a CSV or ZIP file to preview.")
	# Reject before decoding as well as after it: an arbitrarily large base64
	# string should not be copied into another arbitrarily large bytes object.
	if len(encoded) > (MAX_UPLOAD_BYTES * 4 // 3) + 8:
		raise SeedInputError(f"Upload is larger than {MAX_UPLOAD_BYTES // 1000} KB.")
	try:
		content = base64.b64decode(encoded, validate=True)
	except (ValueError, TypeError) as exc:
		raise SeedInputError("The uploaded file is not valid base64 data.") from exc
	if len(content) > MAX_UPLOAD_BYTES:
		raise SeedInputError(f"Upload is larger than {MAX_UPLOAD_BYTES // 1000} KB.")
	return name, content


def _csv_from_zip(content):
	try:
		archive = zipfile.ZipFile(io.BytesIO(content))
	except (zipfile.BadZipFile, OSError) as exc:
		raise SeedInputError("The uploaded ZIP cannot be read.") from exc
	with archive:
		members = [info for info in archive.infolist() if not info.is_dir()]
		if len(members) > MAX_ZIP_MEMBERS:
			raise SeedInputError(f"ZIP contains more than {MAX_ZIP_MEMBERS} files.")
		if any(info.flag_bits & 0x1 for info in members):
			raise SeedInputError("Password-protected ZIP files are not supported.")
		if sum(info.file_size for info in members) > MAX_EXPANDED_BYTES:
			raise SeedInputError("Expanded ZIP contents are too large.")

		csv_members = [info for info in members if info.filename.lower().endswith(".csv")]
		pubobot = [
			info for info in csv_members
			if info.filename.replace("\\", "/").rsplit("/", 1)[-1].lower() == "qc_players.csv"
		]
		if len(pubobot) == 1:
			selected = pubobot[0]
		elif len(pubobot) > 1:
			raise SeedInputError("ZIP contains more than one qc_players.csv file.")
		elif len(csv_members) == 1:
			selected = csv_members[0]
		elif not csv_members:
			raise SeedInputError("ZIP does not contain a CSV file.")
		else:
			raise SeedInputError("ZIP contains multiple CSV files but no qc_players.csv.")

		try:
			data = archive.read(selected)
		except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
			raise SeedInputError("The ratings CSV inside the ZIP cannot be read.") from exc
		if len(data) > MAX_EXPANDED_BYTES:
			raise SeedInputError("Ratings CSV expands beyond the supported size.")
		return selected.filename, data


def _csv_rows(data):
	try:
		text = data.decode("utf-8-sig")
	except UnicodeDecodeError as exc:
		raise SeedInputError("CSV must use UTF-8 encoding.") from exc
	try:
		reader = csv.DictReader(io.StringIO(text))
		if not reader.fieldnames:
			raise SeedInputError("CSV has no header row.")
		headers = {_header(field) for field in reader.fieldnames}
		for required in ("user_id", "rating"):
			if not any(alias in headers for alias in _ALIASES[required]):
				raise SeedInputError(f"CSV is missing a {required} column.")
		return [(index, row) for index, row in enumerate(reader, start=2)]
	except csv.Error as exc:
		raise SeedInputError(f"CSV cannot be parsed: {exc}.") from exc


def _source_rows(payload):
	if not isinstance(payload, dict):
		raise SeedInputError("Import request must be a JSON object.")
	if "rows" in payload:
		rows = payload.get("rows")
		if not isinstance(rows, list):
			raise SeedInputError("rows must be a list.")
		return "Manual entry", [(index, row) for index, row in enumerate(rows, start=1)]

	name, content = _decode_upload(payload)
	inner_name = name
	if zipfile.is_zipfile(io.BytesIO(content)) or name.lower().endswith(".zip"):
		inner_name, content = _csv_from_zip(content)
	return inner_name, _csv_rows(content)


def parse_seed_payload(payload, default_deviation):
	"""Return normalized rows with row-level errors; never raise for one bad row."""
	source_name, source_rows = _source_rows(payload)
	useful = []
	for line, raw in source_rows:
		if not isinstance(raw, dict):
			useful.append((line, {}, ["Row must be an object."]))
			continue
		if not any(str(value or "").strip() for value in raw.values()):
			continue
		useful.append((line, raw, []))
	if len(useful) > MAX_SEED_ROWS:
		raise SeedInputError(f"Import contains more than {MAX_SEED_ROWS} player rows.")
	if not useful:
		raise SeedInputError("Import contains no player rows.")

	rows = []
	for line, raw, errors in useful:
		user_id = rating = deviation = None
		try:
			user_id = _int_value(
				_aliased(raw, "user_id"), "Discord user ID",
				minimum=1, maximum=9_223_372_036_854_775_807)
		except ValueError as exc:
			errors.append(str(exc))
		try:
			rating = _int_value(_aliased(raw, "rating"), "Rating", minimum=1, maximum=9_999)
		except ValueError as exc:
			errors.append(str(exc))
		try:
			deviation = _int_value(
				_aliased(raw, "deviation"), "Deviation",
				minimum=1, maximum=2_999, required=False)
		except ValueError as exc:
			errors.append(str(exc))
		if deviation is None:
			deviation = int(default_deviation)

		nick = str(_aliased(raw, "nick") or "").strip()
		if len(nick) > MAX_NICK_LENGTH:
			errors.append(f"Nickname is longer than {MAX_NICK_LENGTH} characters.")
		rows.append({
			"line": line,
			"user_id": user_id,
			"nick": nick,
			"rating": rating,
			"deviation": deviation,
			"errors": errors,
		})

	by_user = {}
	for row in rows:
		if row["user_id"] is not None:
			by_user.setdefault(row["user_id"], []).append(row)
	for duplicates in by_user.values():
		if len(duplicates) > 1:
			for row in duplicates:
				row["errors"].append("Discord user ID appears more than once in this import.")
	return {"name": source_name, "rows": rows}


def seed_digest(target_channel_id, parsed):
	"""Bind a preview to the normalized content and its resolved rating target."""
	canonical = {
		"target_channel_id": int(target_channel_id),
		"source": parsed["name"],
		"rows": parsed["rows"],
	}
	encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
	return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def team_rating_seed_digest(target_channel_id, rows):
	"""Bind a remote ladder preview to its target and observed ratings."""
	canonical_rows = [{
		"user_id": int(row["user_id"]),
		"profile_id": int(row["profile_id"]) if row.get("profile_id") is not None else None,
		"rating": int(row["rating"]) if row.get("rating") is not None else None,
		"status": row["status"],
	} for row in rows]
	canonical = {
		"target_channel_id": int(target_channel_id),
		"leaderboard": "rm_team",
		"rows": canonical_rows,
	}
	encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
	return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _identity_aliased(row, name):
	clean = {_header(key): value for key, value in row.items() if key is not None}
	for alias in _IDENTITY_ALIASES[name]:
		if alias in clean:
			return clean[alias]
	return None


def _identity_csv_from_zip(content):
	try:
		archive = zipfile.ZipFile(io.BytesIO(content))
	except (zipfile.BadZipFile, OSError) as exc:
		raise SeedInputError("The uploaded ZIP cannot be read.") from exc
	with archive:
		members = [info for info in archive.infolist() if not info.is_dir()]
		if len(members) > MAX_ZIP_MEMBERS:
			raise SeedInputError(f"ZIP contains more than {MAX_ZIP_MEMBERS} files.")
		if any(info.flag_bits & 0x1 for info in members):
			raise SeedInputError("Password-protected ZIP files are not supported.")
		if sum(info.file_size for info in members) > MAX_EXPANDED_BYTES:
			raise SeedInputError("Expanded ZIP contents are too large.")

		csv_members = [info for info in members if info.filename.lower().endswith(".csv")]
		by_basename = {
			info.filename.replace("\\", "/").rsplit("/", 1)[-1].lower(): info
			for info in csv_members
		}
		selected = by_basename.get("profile_resolved.csv") or by_basename.get("player_profile_map.csv")
		if selected is None and len(csv_members) == 1:
			selected = csv_members[0]
		elif selected is None and not csv_members:
			raise SeedInputError("ZIP does not contain an identity CSV file.")
		elif selected is None:
			raise SeedInputError(
				"ZIP contains multiple CSV files but no profile_resolved.csv or player_profile_map.csv.")
		try:
			data = archive.read(selected)
		except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
			raise SeedInputError("The identity CSV inside the ZIP cannot be read.") from exc
		if len(data) > MAX_EXPANDED_BYTES:
			raise SeedInputError("Identity CSV expands beyond the supported size.")
		return selected.filename, data


def _identity_csv_rows(data):
	try:
		text = data.decode("utf-8-sig")
	except UnicodeDecodeError as exc:
		raise SeedInputError("CSV must use UTF-8 encoding.") from exc
	try:
		reader = csv.DictReader(io.StringIO(text))
		if not reader.fieldnames:
			raise SeedInputError("CSV has no header row.")
		headers = {_header(field) for field in reader.fieldnames}
		for required in ("user_id", "profile_id"):
			if not any(alias in headers for alias in _IDENTITY_ALIASES[required]):
				raise SeedInputError(f"CSV is missing a {required} column.")
		return [(index, row) for index, row in enumerate(reader, start=2)]
	except csv.Error as exc:
		raise SeedInputError(f"CSV cannot be parsed: {exc}.") from exc


def _identity_source_rows(payload):
	if not isinstance(payload, dict):
		raise SeedInputError("Import request must be a JSON object.")
	if "rows" in payload:
		rows = payload.get("rows")
		if not isinstance(rows, list):
			raise SeedInputError("rows must be a list.")
		return "Manual entry", [(index, row) for index, row in enumerate(rows, start=1)]
	name, content = _decode_upload(payload)
	inner_name = name
	if zipfile.is_zipfile(io.BytesIO(content)) or name.lower().endswith(".zip"):
		inner_name, content = _identity_csv_from_zip(content)
	return inner_name, _identity_csv_rows(content)


def parse_identity_payload(payload):
	"""Normalize Discord-user to AoE2-profile claims for tenant-safe preview."""
	source_name, source_rows = _identity_source_rows(payload)
	useful = []
	for line, raw in source_rows:
		if not isinstance(raw, dict):
			useful.append((line, {}, ["Row must be an object."]))
			continue
		if not any(str(value or "").strip() for value in raw.values()):
			continue
		useful.append((line, raw, []))
	if len(useful) > MAX_SEED_ROWS:
		raise SeedInputError(f"Import contains more than {MAX_SEED_ROWS} identity rows.")
	if not useful:
		raise SeedInputError("Import contains no identity rows.")

	rows = []
	for line, raw, errors in useful:
		user_id = profile_id = None
		try:
			user_id = _int_value(
				_identity_aliased(raw, "user_id"), "Discord user ID",
				minimum=1, maximum=9_223_372_036_854_775_807)
		except ValueError as exc:
			errors.append(str(exc))
		try:
			profile_id = _int_value(
				_identity_aliased(raw, "profile_id"), "AoE2 profile ID",
				minimum=1, maximum=9_223_372_036_854_775_807)
		except ValueError as exc:
			errors.append(str(exc))
		aoe2_name = str(_identity_aliased(raw, "aoe2_name") or "").strip()
		if len(aoe2_name) > MAX_NICK_LENGTH:
			errors.append(f"AoE2 name is longer than {MAX_NICK_LENGTH} characters.")
		rows.append({
			"line": line,
			"user_id": user_id,
			"profile_id": profile_id,
			"aoe2_name": aoe2_name,
			"errors": errors,
		})

	by_profile = {}
	for row in rows:
		if row["profile_id"] is not None:
			by_profile.setdefault(row["profile_id"], []).append(row)
	for duplicates in by_profile.values():
		if len(duplicates) > 1:
			for row in duplicates:
				row["errors"].append("AoE2 profile ID appears more than once in this import.")
	return {"name": source_name, "rows": rows}


def identity_digest(community_id, parsed):
	canonical = {
		"community_id": int(community_id),
		"source": parsed["name"],
		"rows": parsed["rows"],
	}
	encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
	return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
