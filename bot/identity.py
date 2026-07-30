# -*- coding: utf-8 -*-
"""The identity resolver — the single answer to "who is this person".

Today that question is answered by FIVE different stores: a hand-maintained
CSV (data/player_profile_map.csv), a generated CSV (data/profile_resolved.csv),
the rs_profiles table (learned during replay ingest), the qc_profile_map table
(designed to replace the CSV but never populated), and a copy inside an
offline SQLite quiz database. Identity is the join key for nearly every table
in this bot, so that fragmentation blocks everything downstream. This module
is the single resolver; a later stage re-points the four existing readers at
it (this module does not touch them, and does not delete the CSVs or rs_profiles).

Two tables, reflecting a split that matters:

  identities        — profile_id <-> user_id is GLOBAL truth. An AoE2 account
                       belongs to a person regardless of which Discord server
                       cares about them. One row per AoE2 profile_id; a person
                       can own several (profiles_for_users' value is a list).
  identity_aliases   — a nickname is PER-COMMUNITY: the same person can go by
                       a different name in each Discord server.

`learn()` is how automated sources (replay ingest, CSV seeding) record a
profile_id<->user_id pairing without being able to clobber a human's
correction: confidence only ever moves up CONFIDENCE_ORDER, and a `manual`
row can only be overwritten by another `manual` write. See its docstring for
the exact precedence rule.

CI installs only pytest (no nextcord/aiomysql/aiohttp), so this module must
import cleanly with nothing but the stdlib and core.database — same
constraint as bot/community.py.
"""
import time

from core.database import db
from core.identity_seed import CONFIDENCE_ORDER, parse_seed_csv  # noqa: F401 (re-exported public API)

# core/migrations.py's 003_seed_identities duplicates this exact schema in a
# self-contained raw CREATE TABLE (_ensure_identities_table) rather than
# importing this module — that migration runs before `import bot` even
# happens, and importing bot.identity from inside it would execute the
# ensure_table() call below against an event loop that is already running
# (migrations.run_all() is itself driven by loop.run_until_complete), which
# asyncio forbids. If you change this table's columns, update that raw DDL
# too — see its docstring for the full explanation.
db.ensure_table(dict(
	tname="identities",
	columns=[
		dict(cname="profile_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int, notnull=False),
		dict(cname="aoe2_name", ctype=db.types.str, notnull=False),
		dict(cname="confidence", ctype=db.types.str, notnull=True),
		dict(cname="first_seen_at", ctype=db.types.int, notnull=True),
		dict(cname="last_seen_at", ctype=db.types.int, notnull=True),
	],
	primary_keys=["profile_id"],
))

db.ensure_table(dict(
	tname="identity_aliases",
	columns=[
		dict(cname="community_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="nick", ctype=db.types.str, notnull=False),
		dict(cname="updated_at", ctype=db.types.int, notnull=True),
	],
	primary_keys=["community_id", "user_id"],
))

# user_id -> [profile_id, ...] for every identity with a known Discord owner.
# None means "not loaded yet". profiles_for_users() is on the hot path (the
# civ matcher calls it on every match), hence the cache; invalidate_cache()
# must be called after every write to `identities`.
_profiles_cache = None

# Bumped by invalidate_cache() on every call. profiles_for_users() captures
# this before awaiting a reload and compares again after — if it moved, a
# write landed (and invalidated) while the reload was in flight, so the just
# -loaded snapshot predates that write and must not be cached back in (see
# profiles_for_users' docstring for the failure this prevents).
_cache_generation = 0


async def _load_profiles_cache():
	rows = await db.select(["user_id", "profile_id"], "identities")
	out = {}
	for r in rows or []:
		uid = r["user_id"]
		if uid is None:
			continue
		out.setdefault(uid, []).append(r["profile_id"])
	return out


async def profiles_for_users(user_ids) -> dict:
	""" {user_id: [profile_id, ...]} for every user_id in `user_ids` that owns
	at least one known AoE2 profile. Users with no known profile are simply
	absent from the returned dict — never mapped to [].

	Race handled: without the generation check below, a learn() that calls
	invalidate_cache() while a reload here is mid-await would be clobbered —
	the stale snapshot this call started loading (before that write) would
	get assigned to _profiles_cache *after* the invalidation, and with no TTL
	the just-learned mapping would stay invisible to every caller until some
	unrelated future write happened to invalidate the cache again, possibly
	forever. """
	global _profiles_cache
	cache = _profiles_cache
	if cache is None:
		generation = _cache_generation
		cache = await _load_profiles_cache()
		if generation == _cache_generation:
			_profiles_cache = cache
		# else: invalidate_cache() fired mid-load — `cache` is stale. Answer
		# this call with it (still the best data available for it), but leave
		# _profiles_cache as None so the next call reloads fresh instead of
		# being stuck behind this stale snapshot.

	wanted = set(user_ids)
	return {uid: list(profiles) for uid, profiles in cache.items() if uid in wanted}


async def user_for_profile(profile_id: int):
	""" The Discord user_id that owns `profile_id`, or None if the profile is
	unknown or has no known owner yet. """
	row = await db.select_one(["user_id"], "identities", where={"profile_id": profile_id})
	return row["user_id"] if row else None


async def names_for_profiles(profile_ids) -> dict:
	""" {profile_id: aoe2_name} for every profile_id in `profile_ids` with a
	known name. Profiles with no known name are simply absent — never mapped
	to None. Used by bot/web.py's profile pages to match civ_picks rows
	recorded without a user_id (the un-linked lobby scrape, see
	bot/civ_sync.persist_lobby_civs) back to a Discord user by AoE2 name. """
	out = {}
	for pid in profile_ids:
		row = await db.select_one(["aoe2_name"], "identities", where={"profile_id": pid})
		if row and row["aoe2_name"]:
			out[pid] = row["aoe2_name"]
	return out


def _rank(confidence):
	if confidence not in CONFIDENCE_ORDER:
		raise ValueError(f"_rank: unknown confidence {confidence!r}, expected one of {CONFIDENCE_ORDER}")
	return CONFIDENCE_ORDER.index(confidence)


async def learn(profile_id, user_id, source, aoe2_name=None) -> None:
	""" Record that `profile_id` belongs to `user_id`, as observed by `source`
	(one of CONFIDENCE_ORDER). Never lowers an existing row's confidence, and
	never overwrites a `manual` mapping with a non-manual one — a human
	correction is the highest authority and must not get clobbered by
	automated learning that runs afterwards.

	Concretely: if `source` outranks (or ties) the row's current confidence,
	user_id/aoe2_name/confidence are all updated to the new values. If it is
	outranked, none of those three change — but last_seen_at is bumped
	regardless, since the profile genuinely was observed again just now.

	aoe2_name=None means "not known by this call"; an existing name is kept
	rather than clobbered with None. Always calls invalidate_cache().

	Raises ValueError immediately if `source` is not in CONFIDENCE_ORDER —
	checked before any DB read/write so a bad value can never reach storage.
	Without this, an out-of-lattice source stored on a brand-new profile_id
	(the insert path below never used to rank-check it) would make every
	later learn() for that profile_id raise from _rank() on the "existing
	row" comparison path forever, bricking the profile against every
	supported correction path. """
	_rank(source)  # fail fast; see the docstring paragraph above
	now = int(time.time())
	existing = await db.select_one(
		["confidence", "aoe2_name"], "identities", where={"profile_id": profile_id})

	if existing is None:
		await db.insert("identities", dict(
			profile_id=profile_id,
			user_id=user_id,
			aoe2_name=aoe2_name,
			confidence=source,
			first_seen_at=now,
			last_seen_at=now,
		))
		invalidate_cache()
		return

	if _rank(source) < _rank(existing["confidence"]):
		# Lower-precedence write: the mapping stays as-is, but this profile_id
		# was seen again just now.
		await db.update("identities", dict(last_seen_at=now), keys=dict(profile_id=profile_id))
		invalidate_cache()
		return

	await db.update("identities", dict(
		user_id=user_id,
		aoe2_name=aoe2_name if aoe2_name is not None else existing["aoe2_name"],
		confidence=source,
		last_seen_at=now,
	), keys=dict(profile_id=profile_id))
	invalidate_cache()


async def set_nick(community_id, user_id, nick) -> None:
	""" Set/replace `user_id`'s nickname within `community_id`. Idempotent on
	the (community_id, user_id) primary key. """
	await db.insert("identity_aliases", dict(
		community_id=community_id,
		user_id=user_id,
		nick=nick,
		updated_at=int(time.time()),
	), on_duplicate="replace")


async def nick_for(community_id, user_id):
	""" `user_id`'s nickname within `community_id`, or None if unset. """
	row = await db.select_one(
		["nick"], "identity_aliases", where={"community_id": community_id, "user_id": user_id})
	return row["nick"] if row else None


def invalidate_cache() -> None:
	""" Clear the cached user_id -> [profile_id, ...] map. Call after every
	write to `identities`. Also bumps _cache_generation so a reload already
	in flight when this fires can detect it went stale — see
	profiles_for_users' docstring. """
	global _profiles_cache, _cache_generation
	_profiles_cache = None
	_cache_generation += 1
