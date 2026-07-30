# -*- coding: utf-8 -*-
"""Pure, dependency-free identity-seeding logic shared by bot/identity.py
and core/migrations.py.

Why this module exists: core/migrations.py's 003_seed_identities migration
cannot `import bot.identity` — migrations run before `import bot`, and
importing anything under bot.* would execute bot/__init__.py, which fires
~10 modules' db.ensure_table() calls; that sync wrapper does
`loop.run_until_complete(...)` on the loop the migration is already running
under, and asyncio forbids re-entering run_until_complete, so the boot would
crash with "This event loop is already running". A core/ module never
triggers bot/__init__.py, so pure logic lives here instead, and both sides
import the same code rather than keeping two copies in sync by hand.

Stdlib-only by design: CI's pytest job installs only pytest (no nextcord/
aiomysql/aiohttp), and this module must import cleanly there. Do not add an
import of core.database, core.config, or anything under bot.* here — that
would reintroduce the exact reentrancy hazard this module exists to avoid.
"""
import csv
import io

CONFIDENCE_ORDER = ("seed", "learned", "manual")

_SEED_CSV_KINDS = ("profile_map", "resolved")
# Both real header shapes carry the same three columns this module cares
# about (user_id, aoe2_name, profile_id) by NAME, just in different column
# order and with different extra columns (country vs source/appearances) —
# csv.DictReader keys off the header row, so both parse the same way.
#   profile_map -> user_id,nick,aoe2_name,profile_id,country
#   resolved    -> profile_id,user_id,nick,aoe2_name,source,appearances


def parse_seed_csv(text: str, kind: str) -> list:
	""" Parse one of the two legacy seed CSVs into a list of
	{profile_id, user_id, aoe2_name} dicts. Pure: no file I/O, no DB, so it
	is unit-testable on inline CSV text.

	Rows with no profile_id (missing, or not an int) are unusable and
	skipped. A missing/empty user_id is a legitimate identity — a known AoE2
	profile whose Discord owner isn't known — kept with user_id=None; a
	user_id that IS present but not an int is malformed data and the whole
	row is skipped rather than guessed at. """
	if kind not in _SEED_CSV_KINDS:
		raise ValueError(f"parse_seed_csv: unknown kind {kind!r}, expected one of {_SEED_CSV_KINDS}")

	rows = []
	for r in csv.DictReader(io.StringIO(text)):
		profile_id = _to_int(r.get("profile_id"))
		if profile_id is None:
			continue

		user_id_raw = (r.get("user_id") or "").strip()
		if user_id_raw == "":
			user_id = None
		else:
			user_id = _to_int(user_id_raw)
			if user_id is None:
				continue  # present but malformed -> the whole row is unusable

		aoe2_name = (r.get("aoe2_name") or "").strip() or None
		rows.append(dict(profile_id=profile_id, user_id=user_id, aoe2_name=aoe2_name))
	return rows


def _to_int(value):
	if value is None:
		return None
	value = str(value).strip()
	if not value:
		return None
	try:
		return int(value)
	except ValueError:
		return None
