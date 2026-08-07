# -*- coding: utf-8 -*-
"""Pure, dependency-free identity-seeding logic shared by nammaoe2bot/features/identity/resolver.py
and nammaoe2bot/runtime/migrations.py.

Why this module exists: nammaoe2bot/runtime/migrations.py's 003_seed_identities migration
cannot `import nammaoe2bot.features.identity.resolver` — migrations run before `import bot`, and
importing anything under bot.* would execute bot/__init__.py, which fires
~10 modules' db.ensure_table() calls; that sync wrapper does
`loop.run_until_complete(...)` on the loop the migration is already running
under, and asyncio forbids re-entering run_until_complete, so the boot would
crash with "This event loop is already running". A core/ module never
triggers bot/__init__.py, so pure logic lives here instead, and both sides
import the same code rather than keeping two copies in sync by hand.

Stdlib-only by design: CI's pytest job installs only pytest (no nextcord/
aiomysql/aiohttp), and this module must import cleanly there. Do not add an
import of nammaoe2bot.runtime.database, nammaoe2bot.runtime.config, or anything under bot.* here — that
would reintroduce the exact reentrancy hazard this module exists to avoid.
"""
import csv
import io

# The confidence lattice, weakest first — position IS the precedence, so
# order matters and index arithmetic on it must never assume a fixed length
# (identity v2 inserted `self` between `learned` and `manual`; more tiers may
# follow). nammaoe2bot/features/identity/resolver.py's _rank() compares by position; nammaoe2bot/runtime/migrations.py
# names its tiers as literals and checks them against this tuple at import, so
# RENAMING or REMOVING a value here is a breaking change that fails the boot
# loudly — inserting one is not.
#   seed    — legacy/CSV rows, and the "profile known, owner unknown" state
#   learned — automated inference (replay ingest, the deduction solver)
#   self    — the player's own one-time /link
#   manual  — an admin correction; the highest authority
CONFIDENCE_ORDER = ("seed", "learned", "self", "manual")

_SEED_CSV_KINDS = ("profile_map", "resolved")
# Both real header shapes carry the same three columns this module cares
# about (user_id, aoe2_name, profile_id) by NAME, just in different column
# order and with different extra columns (country vs source/appearances) —
# csv.DictReader keys off the header row, so both parse the same way.
#   profile_map -> user_id,nick,aoe2_name,profile_id,country
#   resolved    -> profile_id,user_id,nick,aoe2_name,source,appearances


def parse_seed_csv(text: str, kind: str) -> list:
	""" Parse one of the two legacy seed CSVs into a list of
	{profile_id, user_id, aoe2_name, source} dicts. Pure: no file I/O, no DB,
	so it is unit-testable on inline CSV text.

	Rows with no profile_id (missing, or not an int) are unusable and
	skipped. A missing/empty user_id is a legitimate identity — a known AoE2
	profile whose Discord owner isn't known — kept with user_id=None; a
	user_id that IS present but not an int is malformed data and the whole
	row is skipped rather than guessed at.

	`source` is the row's own `source` column, trimmed, or None if the row's
	value was missing/empty — including every `profile_map` row, since that
	CSV shape has no `source` column at all. This module deliberately does
	not interpret the value (e.g. map it to a confidence tier) — it is raw
	pass-through data; nammaoe2bot/runtime/migrations.py's 003_seed_identities is what
	decides what a given `source` value means for seeding precedence. """
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
		source = (r.get("source") or "").strip() or None
		rows.append(dict(profile_id=profile_id, user_id=user_id, aoe2_name=aoe2_name, source=source))
	return rows


def parse_name_repairs(text: str) -> list:
	""" Parse data/profile_resolved.csv into a list of
	{profile_id, nick, aoe2_name} dicts — the pair of names
	nammaoe2bot/runtime/migrations.py's 004_identity_v2 needs to tell a Discord nick that was
	wrongly stored as a game name apart from a genuine game name. Pure: no
	file I/O, no DB.

	Deliberately separate from parse_seed_csv rather than an extra key on its
	output, because the two readers disagree about what a usable row IS.
	parse_seed_csv exists to bind profile_id -> user_id, so it drops a row
	whose user_id is present but malformed; a name repair never looks at
	user_id, and dropping such a row would forfeit a repair for no reason. It
	instead requires all three of profile_id, nick and aoe2_name, since a row
	missing either name has nothing to match against or nothing to repair to.
	Widening parse_seed_csv to serve both would mean one function with two
	notions of validity, and any later change to its row filter would silently
	change which names get repaired.

	Values are trimmed (the file is hand-edited) but never case-folded:
	`guruGreatest` (a Discord nick) and `GuruGreatest` (the game name) are a
	real row in that CSV, and the difference between them is the entire
	signal. """
	rows = []
	for r in csv.DictReader(io.StringIO(text)):
		profile_id = _to_int(r.get("profile_id"))
		if profile_id is None:
			continue
		nick = (r.get("nick") or "").strip()
		aoe2_name = (r.get("aoe2_name") or "").strip()
		if not nick or not aoe2_name:
			continue
		rows.append(dict(profile_id=profile_id, nick=nick, aoe2_name=aoe2_name))
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
