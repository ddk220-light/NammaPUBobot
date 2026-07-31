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
  relink()    — admin correction at `manual`: displaces whoever owned the
                profile AND releases the member's other profiles in one call.
                This is the only path that can move a `manual` binding to a
                DIFFERENT owner, since learn() now refuses equal-tier writes.
                (Other paths do rewrite a `manual` row without moving it:
                unlink() clears its owner, and link_self() refreshes one whose
                owner is already the caller.)

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


async def confidence_for_profiles(profile_ids) -> dict:
	""" {profile_id: confidence} for every profile_id in `profile_ids` that has
	a stored row. Unknown profile ids are simply absent.

	The tier is what separates a guess from a decision — `learned` is the
	deduction solver's arithmetic, `manual` is a human's instruction — so an
	admin reading a member's profiles needs it to know whether a wrong-looking
	link is something to correct or something somebody already chose. Kept here
	beside names_for_profiles rather than read from `identities` at the call
	site: this module owns the table, and every reader goes through it. """
	out = {}
	for pid in profile_ids:
		row = await db.select_one(["confidence"], "identities", where={"profile_id": pid})
		if row and row["confidence"]:
			out[pid] = row["confidence"]
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


async def record_refused_claim(profile_id, claimed_user_id, source) -> None:
	""" Record a binding an automated source COULD have written and deliberately
	did not — the public door onto identity_conflicts for a writer that stopped
	itself, as opposed to learn()/link_self() being stopped by the lattice.

	bot/identity_solver.py is the caller: it refuses to auto-apply a conclusion
	that depended on the existing bindings being correct, and one that would
	hand a user a second profile (see that module's docstring, rules 1 and 2).
	Those are exactly the cases a human has to settle, and identity_conflicts is
	where this bot puts claims a human has to settle. The row lands `open`, at
	`source`'s own tier, so `/identity conflicts` shows it beside every claim
	that lost a lattice comparison; INSERT IGNORE on the primary key means
	re-running the solver on every trigger never accumulates duplicates.

	Both ids are required. identity_conflicts' primary key is
	(profile_id, claimed_user_id), so a NULL in either would be a write error
	surfacing far from whatever produced the None — refused here instead. """
	if profile_id is None or claimed_user_id is None:
		raise ValueError(
			f"record_refused_claim needs both ids, got profile_id={profile_id!r} "
			f"claimed_user_id={claimed_user_id!r}")
	_rank(source)  # same fail-fast as learn(); an out-of-lattice tier is unreadable
	await _record_conflict(profile_id, claimed_user_id, source, int(time.time()))


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


async def learn(profile_id, user_id, source, aoe2_name=None) -> bool:
	""" Record that `profile_id` belongs to `user_id`, as observed by the
	automated `source` (one of CONFIDENCE_ORDER). This is the path every
	automated writer takes, and it can never clobber a human's correction.

	Returns whether the binding IS now (profile_id -> user_id) — the same
	question link_self() answers, and for the same reason: this function can
	refuse, so a caller that reports or counts what it did cannot assume a
	call was a write. True covers all three accepting branches below (inserted,
	moved, or already this user); False is the refusal, where the stored owner
	stands and the incoming claim was recorded as a conflict instead. Callers
	that only want the write are free to ignore it.

	The precedence rule, by the rank of `source` against the stored row's
	confidence:

	  STRICTLY HIGHER — the binding moves: user_id/aoe2_name/confidence are
	    all updated. If that displaces a DIFFERENT previous owner, the
	    displaced claim is recorded as `superseded`.
	  SAME OR LOWER, same user — not a disagreement, just a fresh observation
	    of what is already stored: aoe2_name (when provided) and last_seen_at
	    are refreshed, the binding is left alone, nothing is recorded. Note
	    the name refresh happens on the LOWER branch too: a weaker source
	    still observed the in-game name just now, and the name is a
	    display-only observation, not part of the binding it was outranked on
	    (spec §1: "same or lower tier, same user: refresh last_seen_at /
	    aoe2_name only").
	  SAME OR LOWER, different user — the binding does NOT move, and the
	    incoming claim is recorded as an `open` conflict, whatever tier the
	    stored row holds. Two sources disagreeing is a fact for an admin to
	    settle, not a race the last writer wins. (Stage 2 did the opposite —
	    ties overwrote — and identity v2 inverted it; do not "restore" that.)
	    last_seen_at is still bumped: the profile genuinely was observed
	    again just now.

	    The stored tier deliberately does NOT gate that recording. It used to:
	    only a block by a `manual` row was recorded, on the reasoning that
	    being outranked by anything else is just precedence working. That
	    reasoning fails the moment two automated tiers both write — a `seed`
	    claim against the deduction solver's `learned` binding is a real
	    disagreement between two guesses, and dropping it contradicts this
	    module's whole premise that nothing is ever silently discarded.

	A stored row with user_id NULL is unowned, so a refused write against it
	records nothing: there is no rival claim to disagree with, and a conflict
	row whose current_owner is None is unreadable to a human anyway.

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
		return True

	new_rank, stored_rank = _rank(source), _rank(existing["confidence"])

	# A strictly higher tier moves the binding; so does any tier over an
	# unowned row, which has no claim to lose.
	if new_rank > stored_rank or (new_rank == stored_rank and existing["user_id"] is None):
		await _overwrite_binding(profile_id, user_id, source, aoe2_name, existing, now)
		return True

	if existing["user_id"] == user_id:
		# Same owner at the same or a lower tier: refresh the observation, not
		# the binding. The name refresh applies at both tiers — see the
		# docstring's "SAME OR LOWER, same user" branch.
		await db.update("identities", dict(
			aoe2_name=aoe2_name if aoe2_name is not None else existing["aoe2_name"],
			last_seen_at=now,
		), keys=dict(profile_id=profile_id))
		invalidate_cache()
		return True

	# Refused: a same-or-lower-tier claim that disagrees with the stored owner.
	# Recorded whatever tier that stored row holds (see the docstring), unless
	# the row is unowned and there is therefore nobody to disagree with. The
	# mapping stays as-is, but this profile_id was seen again just now.
	if existing["user_id"] is not None:
		await _record_conflict(profile_id, user_id, source, now)
	await db.update("identities", dict(last_seen_at=now), keys=dict(profile_id=profile_id))
	invalidate_cache()
	return False


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

	A confidence already above `self` is never lowered WHEN THE ROW IS ALREADY
	THIS PLAYER'S (an admin's `manual` binding to them): demoting it would make
	an admin's decision overwritable by any later `manual`-tier write. An
	UNOWNED row is clamped down to `self` instead — its stored tier belongs to
	whatever wrote it (e.g. a `manual` CSV row seeded with no user_id, or any
	future high-tier ownerless state), and inheriting it would let a
	self-service `/link` mint a `manual` binding that only an admin is allowed
	to create. """
	now = int(time.time())
	existing = await db.select_one(
		["user_id", "confidence", "aoe2_name"], "identities", where={"profile_id": profile_id})

	if existing is None:
		await _insert_binding(profile_id, user_id, observed_name, "self", now)
		return True

	if existing["user_id"] is not None and existing["user_id"] != user_id:
		await _record_conflict(profile_id, user_id, "self", now)
		return False

	keeps_stored_tier = existing["user_id"] == user_id and _rank(existing["confidence"]) > _rank("self")
	confidence = existing["confidence"] if keeps_stored_tier else "self"
	await _overwrite_binding(profile_id, user_id, confidence, observed_name, existing, now)
	return True


async def unlink(profile_id, status="unlinked") -> None:
	""" Remove `profile_id`'s owner with no replacement: user_id → None and
	confidence → `seed`, the unowned state — so the profile is claimable again
	by any tier, including the player's own `/link`. last_seen_at is bumped
	(it was acted on just now) and aoe2_name is kept: an observed in-game name
	is an observation, not ownership.

	The removed claim is recorded with source `manual` (an admin is the only
	caller) and `status` — `unlinked` by default, which is the plain "an admin
	removed this link" case. relink() passes `superseded` instead, because a
	profile released to move a member elsewhere was not removed on its own
	merits, and spec §3 records both halves of a relink that way. Keeping the
	two distinguishable is the point: a reader of identity_conflicts can tell
	"this link was judged wrong" from "this link lost its owner to a move".

	A profile with no owner is a NO-OP, not a rewrite: without that guard, an
	unlink of an already-unowned row would drop its confidence to `seed`, and
	a `seed` write that the stored tier previously outranked could then bind
	it. Unlinking twice must not make a profile easier to claim than
	unlinking once. An unknown profile_id is a no-op for the same family of
	reason: it must not fabricate a row. """
	now = int(time.time())
	existing = await db.select_one(["user_id"], "identities", where={"profile_id": profile_id})
	if existing is None or existing["user_id"] is None:
		return

	# Record BEFORE the write, same order as _overwrite_binding. The adapter
	# runs with autocommit and exposes no transaction API, so a failure between
	# the two statements is not rolled back — recording first means the worst
	# case is a conflict row for a binding that still stands (visible, and
	# self-correcting on a retry), rather than a binding silently gone with no
	# audit trail of who used to hold it.
	await _record_conflict(profile_id, existing["user_id"], "manual", now, status=status)

	await db.update("identities", dict(
		user_id=None,
		confidence="seed",
		last_seen_at=now,
	), keys=dict(profile_id=profile_id))
	invalidate_cache()


async def relink(profile_id, user_id, additional=False) -> None:
	""" The admin correction: bind `profile_id` to `user_id` at `manual` and,
	unless `additional`, release every OTHER profile that member owns — one
	call, so the member ends up owning exactly the profile just assigned.

	One call for both steps on purpose. "Relink this member to a different
	profile id" that only bound the new one would leave them owning both, and
	every consumer resolves profile → user through this table, so their
	statistics would be double-attributed from two profiles at once. Both
	halves of the move are recorded as `superseded` (the displaced owner of
	this profile, and each profile the member is released from), per spec §3.

	KNOWN LIMITATION — this is NOT atomic. core/DBAdapters/mysql.py connects
	with autocommit=True and exposes no transaction API, so these are several
	independent statements: an exception after the bind but before/while the
	release loop runs leaves the member owning two profiles, exactly the state
	this function exists to prevent. Re-running the same relink repairs it
	(the bind is idempotent and the loop resumes), and each step records its
	own conflict row, so the damage is visible rather than silent. Making it
	genuinely atomic needs a transaction API on the adapter, which is a
	separate change to core/.

	`additional=True` is for genuine multi-account players (the flagship
	community has several): it adds this profile alongside whatever the member
	already owns.

	Unlike learn(), the bind is UNCONDITIONAL — it overwrites even an existing
	`manual` row, recording the previous owner as `superseded`. It has to:
	learn() refuses equal-tier writes, so `manual` over `manual` (an admin
	correcting an earlier admin's mistake, the single most likely relink)
	would otherwise be silently refused, which is exactly the "you must unlink
	first" workflow this function exists to remove. """
	# The release loop below compares this against profile ids read back out of
	# the DB, which are ints. A str `profile_id` (a command argument that was
	# never coerced, say) would compare unequal to its own just-bound row and
	# the loop would unlink it again — leaving the member owning NOTHING, the
	# exact opposite of what was asked.
	profile_id = int(profile_id)
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
			await unlink(other_profile_id, status="superseded")


async def open_conflicts() -> list[dict]:
	""" Every open identity_conflicts row, grouped by profile_id and paired
	with `identities`' current stored owner for that profile_id — so a caller
	(the future management UI, or the `identity conflicts` admin command)
	can present the whole disagreement rather than just the losing claim
	that got recorded. `identities` is re-read live rather than trusted from
	whatever was true at record time, since the stored owner can move on
	(another manual correction, a later learn()) after a conflict is logged
	and before anyone looks at it.

	A claim whose user_id IS the current owner is dropped, and a profile whose
	claims are all dropped that way disappears from the result entirely. Such
	rows are real and permanent: `status` never moves on its own (nothing
	clears `open`), so a claimant refused at one tier and later given the
	profile by an admin keeps their stale `open` row forever, and reporting it
	would render as "Current owner: @X, Competing claim(s): @X" — a
	self-contradiction on the only surface a moderator has. Filtering here on
	the READ side rather than only at the write sites is deliberate: migration
	003_seed_identities has already written rows of this shape to production,
	so a write-side fix alone would leave them showing.

	Returns a list of
	  {profile_id, current_owner, claims: [{user_id, source, noticed_at}, ...]}
	one entry per profile_id with at least one open conflict that is not the
	current owner; `current_owner` is None if the profile has since lost its
	owner entirely (should not normally happen, but open_conflicts must not
	crash if it does — and with no owner, no claim can be the owner, so every
	claim survives the filter). Empty list means no open conflicts. """
	rows = await db.select(
		["profile_id", "claimed_user_id", "source", "noticed_at"],
		"identity_conflicts", where={"status": "open"})

	grouped = {}
	for r in rows or []:
		grouped.setdefault(r["profile_id"], []).append(dict(
			user_id=r["claimed_user_id"], source=r["source"], noticed_at=r["noticed_at"]
		))

	out = []
	for profile_id, claims in grouped.items():
		current_owner = await user_for_profile(profile_id)
		live = [c for c in claims if c["user_id"] != current_owner]
		if live:
			out.append(dict(profile_id=profile_id, current_owner=current_owner, claims=live))
	return out


# The window coverage is measured over. 90 days is "who plays here now": long
# enough that a fortnight off does not drop somebody out of the count, short
# enough that the number stays actionable. A lifetime count would include
# players who left years ago and read as a permanent failure no amount of
# linking could fix. Command copy quotes this constant rather than restating
# the number, so the figure and its caption can never disagree.
COVERAGE_WINDOW_DAYS = 90

# Every distinct Discord player a community has seen in a recent window, from
# the bot's OWN match records (not the replay side): match_players is written
# for every reported match, so this is the full population that identity
# coverage is measured against.
#
# Scoped through community_channels, so the number a moderator is shown
# describes their community and not the whole database. The join to `matches`
# carries both keys (match_id AND channel_id), matching identity_solver's
# _DISCORD_ROSTERS_SQL — match_players stores channel_id denormalised and its
# primary key is (match_id, user_id).
#
# `matches` / `match_players` are bot/stats/stats.py's tables and
# `community_channels` is bot/community.py's; this module only ever READS
# them. Keeping the read here rather than in the command handler is the point
# of the function (see coverage_for_community).
_WINDOW_PLAYERS_SQL = (
	"SELECT DISTINCT mp.user_id AS user_id "
	"FROM match_players mp "
	"JOIN matches m ON m.match_id = mp.match_id AND m.channel_id = mp.channel_id "
	"JOIN community_channels cc ON cc.channel_id = m.channel_id "
	"WHERE cc.community_id = %s AND m.reported_at > %s AND mp.user_id IS NOT NULL"
)


async def coverage_for_community(community_id, days=COVERAGE_WINDOW_DAYS) -> dict:
	""" How much of a community is actually linked:

	  {"players": int, "linked": int, "unlinked": int, "conflicts": int}

	`players` is the distinct Discord users who appeared in a reported match in
	this community in the last `days`; `linked` is how many of those own at
	least one AoE2 profile. That ratio is the one number that says whether the
	analysis features work here at all — every one of them resolves a player
	through `identities`, and an unlinked player is silently missing from all of
	them. Spec §3: silent feature-failure is replaced by a number an admin can
	act on.

	Counted per PERSON, not per row and not per profile: a player who appeared
	in forty matches is one player, and one of the five production users who own
	three profiles each is one linked player, not three.

	The window matters as much as the ratio. Someone who last played two years
	ago is not a coverage gap anybody can chase, so a lifetime count would read
	as a permanent failure that no amount of linking could ever fix.

	`conflicts` is deliberately NOT community-scoped: identity_conflicts has no
	community column, because a profile_id<->user_id claim is global truth (see
	this module's docstring). With one community in production that distinction
	is invisible; a second one would see the first's conflict count here. Fixing
	that means giving identity_conflicts a community — a schema change, not a
	filter — and `/identity conflicts` has the same global scope today.

	Read-only, and used by `/identity status` today and the web UI later — which
	is the whole reason the query lives in this module rather than inline in a
	command handler. """
	cutoff = int(time.time()) - days * 86400
	rows = await db.fetchall(_WINDOW_PLAYERS_SQL, [community_id, cutoff]) or []
	user_ids = {row["user_id"] for row in rows if row["user_id"] is not None}

	# profiles_for_users OMITS users who own nothing (never maps them to []), so
	# its size IS the linked count — no filtering of empty lists needed here.
	linked = len(await profiles_for_users(user_ids))

	return dict(
		players=len(user_ids),
		linked=linked,
		unlinked=len(user_ids) - linked,
		conflicts=len(await open_conflicts()),
	)


def invalidate_cache() -> None:
	""" Clear the cached user_id -> [profile_id, ...] map. Call after every
	write to `identities`. Also bumps _cache_generation so a reload already
	in flight when this fires can detect it went stale — see
	profiles_for_users' docstring. """
	global _profiles_cache, _cache_generation
	_profiles_cache = None
	_cache_generation += 1
