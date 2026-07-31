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

Two tables:

  identities         — profile_id <-> user_id is GLOBAL truth. An AoE2 account
                       belongs to a person regardless of which Discord server
                       cares about them. One row per AoE2 profile_id; a person
                       can own several (profiles_for_users' value is a list).
  identity_conflicts — every claim that was refused, superseded or removed, so
                       a disagreement is never silently discarded.

Four write paths over one lattice (CONFIDENCE_ORDER: seed < learned < self <
manual):

  learn()     — automated sources: replay ingest, the deduction solver, CSV
                seeding. Only a STRICTLY higher tier may move a binding; an
                equal-tier disagreement is refused and recorded rather than
                settled by whoever wrote last.
  link_self() — the player's own one-time `/link`, at `self`. Refuses on
                OWNERSHIP, not on rank: a profile someone else already owns is
                never taken, even though `self` outranks `learned`.
  unlink()    — admin removal, back to the unowned state (user_id NULL).
  relink()    — admin correction at `manual`, atomic: displaces whoever owned
                the profile AND releases the member's other profiles in one
                call. This is the ONLY path that can overwrite a `manual` row,
                since learn() now refuses equal-tier writes.

See identity v2 (docs/superpowers/specs/2026-07-30-identity-v2-design.md §1
and §3) for why the tie rule inverted and why relink must be atomic.

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

# Every profile_id<->user_id claim that would otherwise be silently discarded
# -- by 003_seed_identities' INSERT IGNORE (see that migration's docstring),
# by learn() refusing a lower- or equal-tier write, by link_self() refusing a
# profile someone else owns, or by relink()/unlink() removing a binding.
# Recording these instead of dropping them is the whole point: there is no
# resolution UI yet, so a human needs somewhere to go look, and a later
# management UI needs somewhere to read from.
#
# `status` is one of:
#   open       — a live disagreement: this claim lost, the stored owner stands
#   superseded — this claim WAS the stored owner until a higher tier displaced it
#   unlinked   — this claim was the stored owner until an admin removed it
# Nothing here ever writes 'dismissed'/'applied'; resolution is exclusively
# the future UI's job.
#
# NOTE the (profile_id, claimed_user_id) primary key: one row per pair, and
# _record_conflict's INSERT IGNORE keeps the FIRST status recorded for a pair.
# The same user re-claiming a profile they were once superseded from does not
# add a second row (see _record_conflict's docstring).
#
# Same "why is this raw DDL duplicated in core/migrations.py" answer as
# `identities` above: 003_seed_identities needs to write this table too, and
# it cannot import bot.identity (see this module's docstring, and
# core/migrations.py's _ensure_identity_conflicts_table). Keep the two
# declarations in sync by hand if this table's columns ever change.
db.ensure_table(dict(
	tname="identity_conflicts",
	columns=[
		dict(cname="profile_id", ctype=db.types.int),
		dict(cname="claimed_user_id", ctype=db.types.int),
		dict(cname="source", ctype=db.types.str, notnull=True),
		dict(cname="noticed_at", ctype=db.types.int, notnull=True),
		dict(cname="status", ctype=db.types.str, notnull=True, default="open"),
	],
	primary_keys=["profile_id", "claimed_user_id"],
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


async def _record_conflict(profile_id, claimed_user_id, source, noticed_at, status="open") -> None:
	""" Record that `source` claimed `profile_id` belongs to `claimed_user_id`
	and that claim is not (or is no longer) the stored binding — see the
	identity_conflicts declaration up top for what each `status` means and who
	writes it. INSERT IGNORE on the (profile_id, claimed_user_id) primary key:
	the same disagreement observed again later (e.g. a source relearning the
	same losing claim) is a no-op rather than a duplicate row.

	Consequence of that primary key, worth knowing before reading the table:
	a (profile_id, claimed_user_id) pair can hold only ONE row, so the FIRST
	status recorded for a pair is the one that sticks — a user whose `open`
	claim is later `superseded` (or vice versa) keeps the earlier status.
	Distinguishing those would need a wider primary key, i.e. a real schema
	change, not a write-side workaround. Beyond that, `status` only ever moves
	by human action through the future management UI, never by re-observing
	the same claim. """
	await db.insert("identity_conflicts", dict(
		profile_id=profile_id,
		claimed_user_id=claimed_user_id,
		source=source,
		noticed_at=noticed_at,
		status=status,
	), on_duplicate="ignore")


async def _insert_binding(profile_id, user_id, aoe2_name, confidence, now) -> None:
	""" The first-ever row for `profile_id`. Nothing can be displaced, so
	nothing is recorded in identity_conflicts. """
	await db.insert("identities", dict(
		profile_id=profile_id,
		user_id=user_id,
		aoe2_name=aoe2_name,
		confidence=confidence,
		first_seen_at=now,
		last_seen_at=now,
	))
	invalidate_cache()


async def _overwrite_binding(profile_id, user_id, confidence, aoe2_name, existing, now) -> None:
	""" Move `profile_id` to `user_id` at `confidence`, recording whoever this
	displaces as a `superseded` claim. `existing` is the row being overwritten,
	as the caller already read it (user_id/confidence/aoe2_name).

	Nothing is recorded when nothing is actually displaced: a row with
	user_id NULL is unowned, and a rewrite that agrees with the stored owner
	displaces no one. The displaced claim is recorded under the confidence
	tier that had made it — that tier is what a human reading the conflict
	needs to judge it.

	aoe2_name=None means "not observed by this call"; the stored name is kept
	rather than clobbered with None. """
	if existing["user_id"] is not None and existing["user_id"] != user_id:
		await _record_conflict(
			profile_id, existing["user_id"], existing["confidence"], now, status="superseded")

	await db.update("identities", dict(
		user_id=user_id,
		aoe2_name=aoe2_name if aoe2_name is not None else existing["aoe2_name"],
		confidence=confidence,
		last_seen_at=now,
	), keys=dict(profile_id=profile_id))
	invalidate_cache()


async def learn(profile_id, user_id, source, aoe2_name=None) -> None:
	""" Record that `profile_id` belongs to `user_id`, as observed by the
	automated `source` (one of CONFIDENCE_ORDER). This is the path every
	automated writer takes, and it can never clobber a human's correction.

	The precedence rule, by the rank of `source` against the stored row's
	confidence:

	  STRICTLY HIGHER — the binding moves: user_id/aoe2_name/confidence are
	    all updated. If that displaces a DIFFERENT previous owner, the
	    displaced claim is recorded as `superseded`.
	  EQUAL, same user — not a disagreement, just a fresh observation of what
	    is already stored: aoe2_name (when provided) and last_seen_at are
	    refreshed, the binding is left alone, nothing is recorded.
	  EQUAL, different user — the binding does NOT move, and the incoming
	    claim is recorded as an `open` conflict. Two sources of equal
	    confidence disagreeing is a fact for an admin to settle, not a race
	    the last writer wins. (Stage 2 did the opposite — ties overwrote —
	    and identity v2 inverted it; do not "restore" that.) last_seen_at is
	    still bumped: the profile genuinely was observed again just now.
	  LOWER — the binding does not move, last_seen_at is bumped, and the
	    losing claim is recorded as an `open` conflict only when it was
	    blocked specifically by a `manual` row (a human correction) that
	    disagrees on user_id. A block by a non-manual row is the resolver's
	    ordinary, working precedence order — not the "a human said so, and
	    someone else's claim was discarded" case identity_conflicts exists to
	    surface — so it is not recorded.

	An existing row with user_id NULL ("profile known, owner unknown" — the
	state 003_seed_identities and unlink() leave behind) is UNOWNED, not a
	rival claim: it makes no equal-rank disagreement, so an equal-tier write
	binds it and displaces nobody. Refusing there would strand the row
	unclaimable by its own tier and log a conflict against no one.

	Note what learn() therefore cannot do: it can never overwrite a `manual`
	row, not even from `manual`. An admin correcting an earlier admin mistake
	goes through relink(), which is authoritative by construction.

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
		["user_id", "confidence", "aoe2_name"], "identities", where={"profile_id": profile_id})

	if existing is None:
		await _insert_binding(profile_id, user_id, aoe2_name, source, now)
		return

	new_rank, stored_rank = _rank(source), _rank(existing["confidence"])

	# A strictly higher tier moves the binding; so does any tier over an
	# unowned row, which has no claim to lose.
	if new_rank > stored_rank or (new_rank == stored_rank and existing["user_id"] is None):
		await _overwrite_binding(profile_id, user_id, source, aoe2_name, existing, now)
		return

	if new_rank == stored_rank and existing["user_id"] == user_id:
		# Same owner, same tier: refresh the observation, not the binding.
		await db.update("identities", dict(
			aoe2_name=aoe2_name if aoe2_name is not None else existing["aoe2_name"],
			last_seen_at=now,
		), keys=dict(profile_id=profile_id))
		invalidate_cache()
		return

	# Refused: an equal-tier disagreement, or an outranked write. The mapping
	# stays as-is, but this profile_id was seen again just now.
	if new_rank == stored_rank or (existing["confidence"] == "manual" and existing["user_id"] != user_id):
		await _record_conflict(profile_id, user_id, source, now)
	await db.update("identities", dict(last_seen_at=now), keys=dict(profile_id=profile_id))
	invalidate_cache()


async def link_self(profile_id, user_id, observed_name) -> bool:
	""" The player's own one-time `/link` claim of `profile_id`, at the `self`
	tier. Returns True when the binding is (now) theirs, False when refused.

	Unlike learn(), this refuses on OWNERSHIP rather than on rank: `self`
	outranks `learned`, so the lattice alone would let a player take a profile
	the deduction solver had already bound to somebody else. A player may only
	ever claim a profile nobody owns:

	  unowned (no row, or user_id NULL) → bound at `self`, True
	  already theirs                    → name/last_seen_at refreshed, True
	                                      (idempotent: `/link` is re-runnable)
	  owned by someone else             → `identities` untouched, an `open`
	                                      conflict recorded, False

	`observed_name` is the in-game name the caller validated the profile id
	against; it is stored as the display-only aoe2_name (None = not observed,
	keeping any stored name). Callers validate the id against the AoE2 API
	*before* calling — this module never checks that a profile id is real.

	A confidence already above `self` (an admin's `manual` binding to this
	same player) is never lowered: demoting it would make an admin's decision
	overwritable by any later `manual`-tier write. """
	now = int(time.time())
	existing = await db.select_one(
		["user_id", "confidence", "aoe2_name"], "identities", where={"profile_id": profile_id})

	if existing is None:
		await _insert_binding(profile_id, user_id, observed_name, "self", now)
		return True

	if existing["user_id"] is not None and existing["user_id"] != user_id:
		await _record_conflict(profile_id, user_id, "self", now)
		return False

	confidence = existing["confidence"] if _rank(existing["confidence"]) > _rank("self") else "self"
	await _overwrite_binding(profile_id, user_id, confidence, observed_name, existing, now)
	return True


async def unlink(profile_id) -> None:
	""" Remove `profile_id`'s owner with no replacement: user_id → None and
	confidence → `seed`, the unowned state — so the profile is claimable again
	by any tier, including the player's own `/link`. last_seen_at is bumped
	(it was acted on just now) and aoe2_name is kept: an observed in-game name
	is an observation, not ownership.

	The removed claim is recorded as `unlinked`, with source `manual` because
	an admin is the only caller — relink() below is the one internal user, and
	it is itself an admin action.

	An unknown profile_id is a no-op: unlinking something that was never
	linked must not fabricate a row. A profile that had no owner records no
	conflict either — there was no claim to remove. """
	now = int(time.time())
	existing = await db.select_one(["user_id"], "identities", where={"profile_id": profile_id})
	if existing is None:
		return

	await db.update("identities", dict(
		user_id=None,
		confidence="seed",
		last_seen_at=now,
	), keys=dict(profile_id=profile_id))
	invalidate_cache()

	if existing["user_id"] is not None:
		await _record_conflict(profile_id, existing["user_id"], "manual", now, status="unlinked")


async def relink(profile_id, user_id, additional=False) -> None:
	""" The admin correction: bind `profile_id` to `user_id` at `manual` and,
	unless `additional`, release every OTHER profile that member owns — one
	call, so the member ends up owning exactly the profile just assigned.

	Atomic on purpose. "Relink this member to a different profile id" that
	only bound the new one would leave them owning both, and every consumer
	resolves profile → user through this table, so their statistics would be
	double-attributed from two profiles at once. Releasing goes through
	unlink(), so a released profile leaves the same audit trail as any other
	admin removal.

	`additional=True` is for genuine multi-account players (the flagship
	community has several): it adds this profile alongside whatever the member
	already owns.

	Unlike learn(), the bind is UNCONDITIONAL — it overwrites even an existing
	`manual` row, recording the previous owner as `superseded`. It has to:
	learn() refuses equal-tier writes, so `manual` over `manual` (an admin
	correcting an earlier admin's mistake, the single most likely relink)
	would otherwise be silently refused, which is exactly the "you must unlink
	first" workflow this function exists to remove. """
	now = int(time.time())
	existing = await db.select_one(
		["user_id", "confidence", "aoe2_name"], "identities", where={"profile_id": profile_id})

	if existing is None:
		await _insert_binding(profile_id, user_id, None, "manual", now)
	else:
		await _overwrite_binding(profile_id, user_id, "manual", None, existing, now)

	if additional:
		return

	# _overwrite_binding/_insert_binding just invalidated the cache, so this
	# reload sees the binding written above and skips it by profile_id.
	owned = await profiles_for_users([user_id])
	for other_profile_id in owned.get(user_id, []):
		if other_profile_id != profile_id:
			await unlink(other_profile_id)


async def open_conflicts() -> list[dict]:
	""" Every open identity_conflicts row, grouped by profile_id and paired
	with `identities`' current stored owner for that profile_id — so a caller
	(the future management UI, or the `identity conflicts` admin command)
	can present the whole disagreement rather than just the losing claim
	that got recorded. `identities` is re-read live rather than trusted from
	whatever was true at record time, since the stored owner can move on
	(another manual correction, a later learn()) after a conflict is logged
	and before anyone looks at it.

	Returns a list of
	  {profile_id, current_owner, claims: [{user_id, source, noticed_at}, ...]}
	one entry per profile_id with at least one open conflict; `current_owner`
	is None if the profile has since lost its owner entirely (should not
	normally happen, but open_conflicts must not crash if it does). Empty
	list means no open conflicts. """
	rows = await db.select(
		["profile_id", "claimed_user_id", "source", "noticed_at"],
		"identity_conflicts", where={"status": "open"})

	grouped = {}
	for r in rows or []:
		grouped.setdefault(r["profile_id"], []).append(dict(
			user_id=r["claimed_user_id"], source=r["source"], noticed_at=r["noticed_at"]
		))

	return [
		dict(profile_id=profile_id, current_owner=await user_for_profile(profile_id), claims=claims)
		for profile_id, claims in grouped.items()
	]


def invalidate_cache() -> None:
	""" Clear the cached user_id -> [profile_id, ...] map. Call after every
	write to `identities`. Also bumps _cache_generation so a reload already
	in flight when this fires can detect it went stale — see
	profiles_for_users' docstring. """
	global _profiles_cache, _cache_generation
	_profiles_cache = None
	_cache_generation += 1
