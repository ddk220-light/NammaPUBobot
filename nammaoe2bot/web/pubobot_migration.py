"""Pure, bounded parser for a complete legacy Pubobot export archive."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import time
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MAX_ARCHIVE_BYTES = 5_000_000
MAX_EXPANDED_BYTES = 25_000_000
MAX_ARCHIVE_MEMBERS = 40
MAX_MATCHES = 10_000
MAX_PLAYERS = 2_000
MAX_PLAYER_MATCHES = 100_000
MAX_HISTORY_ROWS = 100_000
MAX_TEXT = 191

_REQUIRED_FILES = (
	"qc_matches.csv",
	"qc_players.csv",
	"qc_player_matches.csv",
	"qc_rating_history.csv",
)


class MigrationInputError(ValueError):
	"""A user-correctable archive, timezone, or row-shape error."""


def _decode_archive(payload):
	if not isinstance(payload, dict):
		raise MigrationInputError("Migration request must be a JSON object.")
	name = str(payload.get("file_name") or "").strip()
	encoded = payload.get("content_base64")
	if not name or not isinstance(encoded, str) or not encoded:
		raise MigrationInputError("Choose a Pubobot export ZIP.")
	if len(encoded) > (MAX_ARCHIVE_BYTES * 4 // 3) + 8:
		raise MigrationInputError("Migration archive is larger than 5 MB.")
	try:
		content = base64.b64decode(encoded, validate=True)
	except (TypeError, ValueError) as exc:
		raise MigrationInputError("Migration archive is not valid base64 data.") from exc
	if len(content) > MAX_ARCHIVE_BYTES:
		raise MigrationInputError("Migration archive is larger than 5 MB.")
	return name, content


def _timezone(name):
	name = str(name or "UTC").strip() or "UTC"
	try:
		return name, ZoneInfo(name)
	except (ZoneInfoNotFoundError, ValueError) as exc:
		raise MigrationInputError(f"Unknown timezone {name!r}.") from exc


def _archive_csvs(content):
	try:
		archive = zipfile.ZipFile(io.BytesIO(content))
	except (zipfile.BadZipFile, OSError) as exc:
		raise MigrationInputError("The migration ZIP cannot be read.") from exc
	with archive:
		members = [info for info in archive.infolist() if not info.is_dir()]
		if len(members) > MAX_ARCHIVE_MEMBERS:
			raise MigrationInputError(f"ZIP contains more than {MAX_ARCHIVE_MEMBERS} files.")
		if any(info.flag_bits & 0x1 for info in members):
			raise MigrationInputError("Password-protected ZIP files are not supported.")
		if sum(info.file_size for info in members) > MAX_EXPANDED_BYTES:
			raise MigrationInputError("Expanded migration ZIP is larger than 25 MB.")

		by_name = {}
		for info in members:
			basename = info.filename.replace("\\", "/").rsplit("/", 1)[-1].lower()
			if basename in _REQUIRED_FILES:
				if basename in by_name:
					raise MigrationInputError(f"ZIP contains more than one {basename} file.")
				by_name[basename] = info
		missing = [name for name in _REQUIRED_FILES if name not in by_name]
		if missing:
			raise MigrationInputError("ZIP is missing: " + ", ".join(missing) + ".")

		out = {}
		for basename, info in by_name.items():
			try:
				data = archive.read(info)
			except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
				raise MigrationInputError(f"Could not read {basename} from the ZIP.") from exc
			try:
				text = data.decode("utf-8-sig")
			except UnicodeDecodeError as exc:
				raise MigrationInputError(f"{basename} must use UTF-8 encoding.") from exc
			try:
				reader = csv.DictReader(io.StringIO(text))
				if not reader.fieldnames:
					raise MigrationInputError(f"{basename} has no header row.")
				out[basename] = list(reader)
			except csv.Error as exc:
				raise MigrationInputError(f"Could not parse {basename}: {exc}.") from exc
		return out


def _value(row, key, label, *, required=True):
	value = row.get(key)
	if value is None or str(value).strip() in ("", "NULL"):
		if required:
			raise ValueError(f"{label} is required")
		return None
	return str(value).strip()


def _integer(row, key, label, *, minimum=None, maximum=None, required=True):
	value = _value(row, key, label, required=required)
	if value is None:
		return None
	try:
		parsed = int(value)
	except ValueError as exc:
		raise ValueError(f"{label} must be a whole number") from exc
	if minimum is not None and parsed < minimum:
		raise ValueError(f"{label} must be at least {minimum}")
	if maximum is not None and parsed > maximum:
		raise ValueError(f"{label} must be at most {maximum}")
	return parsed


def _text(row, key, label, *, required=False):
	value = _value(row, key, label, required=required)
	if value is None:
		return None
	if len(value) > MAX_TEXT:
		raise ValueError(f"{label} is longer than {MAX_TEXT} characters")
	return value


def _timestamp(row, key, label, tz):
	value = _value(row, key, label)
	try:
		parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
	except ValueError as exc:
		raise ValueError(f"{label} must use YYYY-MM-DD HH:MM:SS") from exc
	epoch = int(parsed.timestamp())
	if epoch < 946_684_800 or epoch > int(time.time()) + 86_400:
		raise ValueError(f"{label} is outside the supported 2000-to-present range")
	return epoch


def _row_error(errors, filename, line, exc):
	if len(errors) < 25:
		errors.append(f"{filename} line {line}: {exc}.")


def parse_archive(payload):
	"""Validate and transform all four legacy CSVs without touching a database."""
	source_name, content = _decode_archive(payload)
	timezone_name, tz = _timezone(payload.get("timezone"))
	files = _archive_csvs(content)
	limits = {
		"qc_matches.csv": MAX_MATCHES,
		"qc_players.csv": MAX_PLAYERS,
		"qc_player_matches.csv": MAX_PLAYER_MATCHES,
		"qc_rating_history.csv": MAX_HISTORY_ROWS,
	}
	for filename, limit in limits.items():
		if len(files[filename]) > limit:
			raise MigrationInputError(f"{filename} contains more than {limit:,} rows.")

	errors = []
	matches = []
	for index, row in enumerate(files["qc_matches.csv"], start=2):
		try:
			winner = _integer(row, "winner_team", "winner_team", minimum=0, maximum=1, required=False)
			match_id = _integer(row, "match_id", "match_id", minimum=0)
			matches.append({
				"source_match_id": match_id,
				"queue_name": _text(row, "queue", "queue", required=True),
				"reported_at": _timestamp(row, "at", "match time", tz),
				"winner": winner,
				"alpha_score": 1 if winner == 0 else (0 if winner == 1 else None),
				"beta_score": 0 if winner == 0 else (1 if winner == 1 else None),
				"maps": _text(row, "maps", "maps") or "",
			})
		except ValueError as exc:
			_row_error(errors, "qc_matches.csv", index, exc)

	players = []
	for index, row in enumerate(files["qc_players.csv"], start=2):
		try:
			players.append({
				"user_id": _integer(row, "user_id", "user_id", minimum=1),
				"nick": _text(row, "nick", "nick", required=True),
				"is_hidden": _integer(row, "is_hidden", "is_hidden", minimum=0, maximum=1, required=False) or 0,
				"rating": _integer(row, "rating", "rating", minimum=0, maximum=9_999, required=False),
				"deviation": _integer(row, "deviation", "deviation", minimum=0, maximum=3_000, required=False),
				"wins": _integer(row, "wins", "wins", minimum=0, required=False) or 0,
				"losses": _integer(row, "losses", "losses", minimum=0, required=False) or 0,
				"draws": _integer(row, "draws", "draws", minimum=0, required=False) or 0,
				"streak": _integer(row, "streak", "streak", minimum=-100_000, maximum=100_000, required=False) or 0,
			})
		except ValueError as exc:
			_row_error(errors, "qc_players.csv", index, exc)

	player_matches = []
	for index, row in enumerate(files["qc_player_matches.csv"], start=2):
		try:
			player_matches.append({
				"source_match_id": _integer(row, "match_id", "match_id", minimum=0),
				"user_id": _integer(row, "user_id", "user_id", minimum=1),
				"team": _integer(row, "team", "team", minimum=0, maximum=1, required=False),
			})
		except ValueError as exc:
			_row_error(errors, "qc_player_matches.csv", index, exc)

	history = []
	for index, row in enumerate(files["qc_rating_history.csv"], start=2):
		try:
			history.append({
				"user_id": _integer(row, "user_id", "user_id", minimum=1),
				"at": _timestamp(row, "at", "rating history time", tz),
				"rating_before": _integer(row, "rating_before", "rating_before", minimum=0, maximum=9_999),
				"rating_change": _integer(row, "rating_change", "rating_change", minimum=-9_999, maximum=9_999),
				"deviation_before": _integer(row, "deviation_before", "deviation_before", minimum=0, maximum=3_000),
				"deviation_change": _integer(row, "deviation_change", "deviation_change", minimum=-3_000, maximum=3_000),
				"source_match_id": _integer(row, "match_id", "match_id", minimum=0, required=False),
				"reason": _text(row, "reason", "reason"),
			})
		except ValueError as exc:
			_row_error(errors, "qc_rating_history.csv", index, exc)
	if errors:
		raise MigrationInputError("Archive rows are invalid:\n" + "\n".join(errors))

	match_ids = [row["source_match_id"] for row in matches]
	player_ids = [row["user_id"] for row in players]
	if len(set(match_ids)) != len(match_ids):
		raise MigrationInputError("Match export contains duplicate match_id values.")
	if len(set(player_ids)) != len(player_ids):
		raise MigrationInputError("Player export contains duplicate user_id values.")
	known_matches, known_players = set(match_ids), set(player_ids)
	pm_keys = [(row["source_match_id"], row["user_id"]) for row in player_matches]
	if len(set(pm_keys)) != len(pm_keys):
		raise MigrationInputError("Player-match export contains duplicate match/player pairs.")
	if any(row["source_match_id"] not in known_matches for row in player_matches):
		raise MigrationInputError("Player-match export references a match absent from the match export.")
	if any(row["user_id"] not in known_players for row in player_matches):
		raise MigrationInputError("Player-match export references a user absent from the player export.")
	if any(row["user_id"] not in known_players for row in history):
		raise MigrationInputError("Rating history references a user absent from the player export.")
	if any(row["source_match_id"] is not None and row["source_match_id"] not in known_matches for row in history):
		raise MigrationInputError("Rating history references a match absent from the match export.")

	match_times = {row["source_match_id"]: row["reported_at"] for row in matches}
	last_ranked = {}
	for row in player_matches:
		last_ranked[row["user_id"]] = max(
			last_ranked.get(row["user_id"], 0), match_times[row["source_match_id"]])
	for row in players:
		row["last_ranked_match_at"] = last_ranked.get(row["user_id"])
	return {
		"source_name": source_name,
		"timezone": timezone_name,
		"archive_sha256": hashlib.sha256(content).hexdigest(),
		"matches": matches,
		"players": players,
		"match_players": player_matches,
		"rating_history": history,
	}


def migration_digest(channel_id, rating_channel_id, parsed):
	canonical = {
		"channel_id": int(channel_id),
		"rating_channel_id": int(rating_channel_id),
		"timezone": parsed["timezone"],
		"archive_sha256": parsed["archive_sha256"],
	}
	encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
	return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
