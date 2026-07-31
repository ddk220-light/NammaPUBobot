"""The identity resolver — the single answer to "who is this person" that
later tasks re-point the four fragmented readers (player_profile_map.csv,
profile_resolved.csv, rs_profiles, qc_profile_map) at.

Pure-logic tests against a fake adapter, same pattern as test_community.py:
no MySQL involved. bot.identity.db is monkeypatched per test so nothing here
touches the real core.database fake conftest.py installs for every other
test file.
"""
import asyncio
import importlib.util
import sqlite3
import sys
import time
import types
from enum import IntEnum
from pathlib import Path

import bot.identity as identity
import bot.community as community
# /link validates an id through this module before it writes anything. Imported
# for real (it is pure + lazy-imports aiohttp, see tests/test_lobby_api.py) so
# the tests below can monkeypatch fetch_profile on the actual dependency the
# handler resolves at call time -- no network, ever.
from bot.lobby import api as lobby_api


class FakeDb:
	def __init__(self):
		self.identities = []  # [{profile_id, user_id, aoe2_name, confidence, first_seen_at, last_seen_at}]
		self.identity_conflicts = []  # [{profile_id, claimed_user_id, source, noticed_at, status}]
		self.select_calls = 0

	def _table(self, table):
		return {
			"identities": self.identities,
			"identity_conflicts": self.identity_conflicts,
		}[table]

	async def select_one(self, columns, table, where=None):
		self.select_calls += 1
		where = where or {}
		for row in self._table(table):
			if all(row.get(k) == v for k, v in where.items()):
				return {c: row.get(c) for c in columns}
		return None

	async def select(self, columns, table, where=None, **_kwargs):
		self.select_calls += 1
		where = where or {}
		return [
			{c: row.get(c) for c in columns}
			for row in self._table(table)
			if all(row.get(k) == v for k, v in where.items())
		]

	_PRIMARY_KEYS = {
		"identities": ("profile_id",),
		"identity_conflicts": ("profile_id", "claimed_user_id"),
	}

	async def insert(self, table, d, on_duplicate=None):
		rows = self._table(table)
		pk = self._PRIMARY_KEYS[table]
		if on_duplicate in ("replace", "ignore"):
			for i, row in enumerate(rows):
				if all(row.get(k) == d.get(k) for k in pk):
					if on_duplicate == "replace":
						rows[i] = dict(d)
					# on_duplicate == "ignore": row with this primary key already
					# exists — mirror MySQL's INSERT IGNORE and leave it untouched.
					return None
		rows.append(dict(d))
		return None

	async def update(self, table, d, keys=None):
		keys = keys or {}
		for row in self._table(table):
			if all(row.get(k) == v for k, v in keys.items()):
				row.update(d)


def _setup(monkeypatch):
	fake = FakeDb()
	monkeypatch.setattr(identity, "db", fake)
	identity.invalidate_cache()
	return fake


# ─── parse_seed_csv ─────────────────────────────────────────────────────

def test_parse_seed_csv_profile_map_shape():
	text = (
		"user_id,nick,aoe2_name,profile_id,country\n"
		"238042803093897216,fenrir05,Fenrir,209754,us\n"
	)
	rows = identity.parse_seed_csv(text, "profile_map")
	assert rows == [dict(profile_id=209754, user_id=238042803093897216, aoe2_name="Fenrir", source=None)]


def test_parse_seed_csv_resolved_shape():
	text = (
		"profile_id,user_id,nick,aoe2_name,source,appearances\n"
		"12297184,786488329864478751,guruGreatest,GuruGreatest,seed,31\n"
	)
	rows = identity.parse_seed_csv(text, "resolved")
	assert rows == [dict(profile_id=12297184, user_id=786488329864478751, aoe2_name="GuruGreatest", source="seed")]


def test_parse_seed_csv_keeps_row_with_empty_user_id():
	text = (
		"profile_id,user_id,nick,aoe2_name,source,appearances\n"
		"24413606,,,SomeName,unmapped,1\n"
	)
	rows = identity.parse_seed_csv(text, "resolved")
	assert rows == [dict(profile_id=24413606, user_id=None, aoe2_name="SomeName", source="unmapped")]


def test_parse_seed_csv_reports_manual_source_trimmed():
	text = (
		"profile_id,user_id,nick,aoe2_name,source,appearances\n"
		"5771336,527532506153615360,aquasama7056,KIT WALKER, manual ,\n"
	)
	rows = identity.parse_seed_csv(text, "resolved")
	assert rows[0]["source"] == "manual"


def test_parse_seed_csv_profile_map_rows_have_no_source():
	"""player_profile_map.csv has no `source` column at all — DictReader
	simply never populates the key, so parse_seed_csv must fall back to
	None rather than raising or fabricating a value."""
	text = (
		"user_id,nick,aoe2_name,profile_id,country\n"
		"238042803093897216,fenrir05,Fenrir,209754,us\n"
	)
	rows = identity.parse_seed_csv(text, "profile_map")
	assert rows[0]["source"] is None


def test_parse_seed_csv_skips_row_with_non_numeric_profile_id():
	text = (
		"user_id,nick,aoe2_name,profile_id,country\n"
		"695309066771365970,thelivi,Mr.Livi / Mr. X,17841676 / 2885693,in\n"
	)
	rows = identity.parse_seed_csv(text, "profile_map")
	assert rows == []


def test_parse_seed_csv_skips_row_with_non_numeric_user_id():
	text = (
		"user_id,nick,aoe2_name,profile_id,country\n"
		"not-a-number,fenrir05,Fenrir,209754,us\n"
	)
	rows = identity.parse_seed_csv(text, "profile_map")
	assert rows == []


def test_parse_seed_csv_skips_row_with_missing_profile_id():
	text = (
		"user_id,nick,aoe2_name,profile_id,country\n"
		"284337490712592386,srinimama,,,\n"
	)
	rows = identity.parse_seed_csv(text, "profile_map")
	assert rows == []


def test_parse_seed_csv_returns_empty_list_for_header_only_text():
	text = "user_id,nick,aoe2_name,profile_id,country\n"
	assert identity.parse_seed_csv(text, "profile_map") == []


# ─── learn: precedence ──────────────────────────────────────────────────

def test_rank_rejects_an_unknown_confidence():
	try:
		identity._rank("trusted")
	except ValueError as e:
		assert "trusted" in str(e)
		for tier in identity.CONFIDENCE_ORDER:
			assert tier in str(e)
	else:
		raise AssertionError("_rank() with an unknown confidence must raise ValueError, not tuple.index's opaque one")


def test_learn_rejects_an_unknown_confidence_source_on_the_insert_path(monkeypatch):
	""" A bad `source` must raise a clear error naming the value and the
	allowed set — the same shape of validation parse_seed_csv already does
	for its `kind` argument — not an opaque tuple.index ValueError. And it
	must do so even for a brand-new profile_id with no existing row: learn()
	calls _rank(source) as its very first statement, before any DB read/
	write, so an invalid source can never reach storage via the insert path.

	This used to be a documented gap instead: the insert path skipped
	straight to storing `confidence=source` with no rank comparison, so an
	out-of-lattice value landed in the row and _rank() only ever raised on
	the *next* learn() for that profile_id (the "existing row" comparison
	path) — by which point the bad value was already permanently stuck,
	since every subsequent learn() call for that profile_id would raise
	before it could ever correct it. """
	fake = _setup(monkeypatch)

	try:
		asyncio.run(identity.learn(111, 222, "trusted"))
	except ValueError as e:
		assert "trusted" in str(e)
		for tier in identity.CONFIDENCE_ORDER:
			assert tier in str(e)
	else:
		raise AssertionError("learn() with an unknown confidence source must raise ValueError")

	assert fake.identities == [], "an invalid source must never reach storage"


def test_learn_rejects_an_unknown_confidence_source_on_the_update_path(monkeypatch):
	""" Same validation, reached via the pre-existing "existing row" _rank()
	comparison rather than the insert path above — kept as a regression test
	for that path now that the insert path fails fast too. """
	_setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "seed"))

	try:
		asyncio.run(identity.learn(111, 333, "trusted"))
	except ValueError as e:
		assert "trusted" in str(e)
		for tier in identity.CONFIDENCE_ORDER:
			assert tier in str(e)
	else:
		raise AssertionError("learn() with an unknown confidence source must raise ValueError")


def test_learn_inserts_new_row(monkeypatch):
	fake = _setup(monkeypatch)

	asyncio.run(identity.learn(111, 222, "learned", aoe2_name="Foo"))

	assert len(fake.identities) == 1
	row = fake.identities[0]
	assert row["profile_id"] == 111
	assert row["user_id"] == 222
	assert row["aoe2_name"] == "Foo"
	assert row["confidence"] == "learned"
	assert row["first_seen_at"] == row["last_seen_at"]


def test_learn_does_not_lower_confidence(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "manual"))

	asyncio.run(identity.learn(111, 333, "seed"))

	assert fake.identities[0]["confidence"] == "manual"


def test_learn_does_not_overwrite_manual_mapping_with_learned(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "manual", aoe2_name="RealName"))

	asyncio.run(identity.learn(111, 999, "learned", aoe2_name="WrongGuess"))

	row = fake.identities[0]
	assert row["user_id"] == 222
	assert row["aoe2_name"] == "RealName"
	assert row["confidence"] == "manual"


def test_learn_does_overwrite_seed_mapping_with_manual(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "seed", aoe2_name="Guess"))

	asyncio.run(identity.learn(111, 999, "manual", aoe2_name="Confirmed"))

	row = fake.identities[0]
	assert row["user_id"] == 999
	assert row["aoe2_name"] == "Confirmed"
	assert row["confidence"] == "manual"


def test_learn_always_bumps_last_seen_at_even_when_blocked(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "manual"))
	fake.identities[0]["last_seen_at"] = 1

	asyncio.run(identity.learn(111, 333, "seed"))

	assert fake.identities[0]["last_seen_at"] != 1


def test_learn_preserves_aoe2_name_when_not_provided(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "seed", aoe2_name="Original"))

	asyncio.run(identity.learn(111, 222, "learned"))

	assert fake.identities[0]["aoe2_name"] == "Original"


# ─── learn: the v2 tie rule ──────────────────────────────────────────────
# Stage 2 let an EQUAL-confidence write overwrite the stored owner (ties
# won). Identity v2 forbids it: an equal-confidence disagreement is a fact
# for an admin to settle, not a coin flip the last writer wins. Only a
# strictly higher tier may move a binding, and doing so records the owner it
# displaced.

def test_confidence_lattice_places_self_between_learned_and_manual():
	assert identity.CONFIDENCE_ORDER == ("seed", "learned", "self", "manual")
	assert identity._rank("learned") < identity._rank("self") < identity._rank("manual")


def test_learn_equal_rank_different_user_no_longer_overwrites(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "learned", aoe2_name="First"))
	fake.identities[0]["last_seen_at"] = 1

	asyncio.run(identity.learn(111, 333, "learned", aoe2_name="Second"))

	row = fake.identities[0]
	assert row["user_id"] == 222, "an equal-confidence claim must not take the binding"
	assert row["aoe2_name"] == "First"
	assert row["confidence"] == "learned"
	assert row["last_seen_at"] != 1, "the profile genuinely was observed again just now"


def test_learn_equal_rank_different_user_records_an_open_conflict(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "learned"))

	asyncio.run(identity.learn(111, 333, "learned"))

	assert len(fake.identity_conflicts) == 1
	row = fake.identity_conflicts[0]
	assert row["profile_id"] == 111
	assert row["claimed_user_id"] == 333, "the losing claim is the one that was just refused"
	assert row["source"] == "learned"
	assert row["status"] == "open"


def test_learn_equal_rank_same_user_still_refreshes(monkeypatch):
	""" Not a disagreement at all — the same owner observed again, so the
	newly observed in-game name and last_seen_at must still land. """
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "learned", aoe2_name="Old"))
	fake.identities[0]["last_seen_at"] = 1

	asyncio.run(identity.learn(111, 222, "learned", aoe2_name="New"))

	row = fake.identities[0]
	assert row["aoe2_name"] == "New"
	assert row["last_seen_at"] != 1
	assert fake.identity_conflicts == []


def test_learn_equal_rank_binds_an_unowned_row(monkeypatch):
	""" user_id NULL is the seeded "profile known, owner unknown" state — it
	makes no competing claim, so an equal-tier write binds it rather than
	being refused. Without this carve-out an unowned row could never be
	claimed by a same-tier writer, and the refusal would log a conflict
	against nobody. """
	fake = _setup(monkeypatch)
	fake.identities.append(dict(
		profile_id=111, user_id=None, aoe2_name=None,
		confidence="learned", first_seen_at=1, last_seen_at=1,
	))

	asyncio.run(identity.learn(111, 222, "learned"))

	assert fake.identities[0]["user_id"] == 222
	assert fake.identity_conflicts == []


def test_learn_higher_rank_records_the_displaced_owner_as_superseded(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "learned"))

	asyncio.run(identity.learn(111, 999, "manual"))

	assert fake.identities[0]["user_id"] == 999
	assert fake.identities[0]["confidence"] == "manual"
	assert len(fake.identity_conflicts) == 1
	row = fake.identity_conflicts[0]
	assert row["profile_id"] == 111
	assert row["claimed_user_id"] == 222, "the DISPLACED owner is what gets recorded, not the winner"
	assert row["source"] == "learned", "the tier that had made the now-displaced claim"
	assert row["status"] == "superseded"


def test_learn_higher_rank_over_an_unowned_row_records_no_conflict(monkeypatch):
	""" Filling in the owner of a seeded, ownerless profile displaces nobody. """
	fake = _setup(monkeypatch)
	fake.identities.append(dict(
		profile_id=111, user_id=None, aoe2_name="Fenrir",
		confidence="seed", first_seen_at=1, last_seen_at=1,
	))

	asyncio.run(identity.learn(111, 222, "learned"))

	assert fake.identities[0]["user_id"] == 222
	assert fake.identity_conflicts == []


# ─── learn: conflict recording ───────────────────────────────────────────
# A `manual` row is the highest authority and never yields to an automated
# write, but the write it refuses used to just vanish -- these tests cover
# task 2.6: that refused claim must now land in identity_conflicts instead.

def test_learn_records_a_claim_blocked_by_a_manual_row(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "manual", aoe2_name="RealName"))

	asyncio.run(identity.learn(111, 999, "learned", aoe2_name="WrongGuess"))

	assert len(fake.identity_conflicts) == 1
	row = fake.identity_conflicts[0]
	assert row["profile_id"] == 111
	assert row["claimed_user_id"] == 999
	assert row["source"] == "learned"
	assert row["status"] == "open"
	# The manual row itself must still be untouched -- recording the conflict
	# must not change the winner.
	assert fake.identities[0]["user_id"] == 222
	assert fake.identities[0]["confidence"] == "manual"


def test_learn_does_not_record_a_claim_that_agrees_with_the_manual_row(monkeypatch):
	""" Same user_id, just observed again from a lower-precedence source --
	not a disagreement, so nothing should land in identity_conflicts. """
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "manual"))

	asyncio.run(identity.learn(111, 222, "learned"))

	assert fake.identity_conflicts == []


def test_learn_lower_rank_different_user_records_a_conflict_against_a_learned_row(monkeypatch):
	""" A lower-tier claim that disagrees with the stored owner is recorded
	whatever tier that owner holds -- not only when the row is `manual`.

	This test previously asserted the opposite (a block by a non-manual row
	went unrecorded, on the reasoning that ordinary precedence is not a
	conflict). Spec section 1 says "same or LOWER tier, different user: no
	change + an `open` conflict row", and the deduction solver writes at
	`learned`, so a `seed` disagreement against it is two automated sources
	contradicting each other -- exactly what must not be silently discarded. """
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "learned"))

	asyncio.run(identity.learn(111, 999, "seed"))

	assert fake.identities[0]["user_id"] == 222, "the lower tier must not take the binding"
	assert len(fake.identity_conflicts) == 1
	row = fake.identity_conflicts[0]
	assert row["profile_id"] == 111
	assert row["claimed_user_id"] == 999
	assert row["source"] == "seed"
	assert row["status"] == "open"


def test_learn_lower_rank_against_an_unowned_row_records_nothing(monkeypatch):
	""" ...but an unowned row (user_id NULL) is nobody to disagree with. The
	write is still outranked and refused, and recording it would produce a
	conflict whose current_owner is None -- unreadable to the human it exists
	for. """
	fake = _setup(monkeypatch)
	fake.identities.append(dict(
		profile_id=111, user_id=None, aoe2_name=None,
		confidence="learned", first_seen_at=1, last_seen_at=1,
	))

	asyncio.run(identity.learn(111, 999, "seed"))

	assert fake.identities[0]["user_id"] is None
	assert fake.identity_conflicts == []


def test_learn_lower_rank_same_user_refreshes_the_observed_name(monkeypatch):
	""" Spec section 1: "same or lower tier, same user: refresh last_seen_at /
	aoe2_name only". The lower branch used to bump last_seen_at alone, so a
	name observed by a weaker source was thrown away -- even though aoe2_name
	is a display-only observation, not part of the binding that outranked it. """
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "manual", aoe2_name="Old"))
	fake.identities[0]["last_seen_at"] = 1

	asyncio.run(identity.learn(111, 222, "learned", aoe2_name="RenamedInGame"))

	row = fake.identities[0]
	assert row["aoe2_name"] == "RenamedInGame"
	assert row["confidence"] == "manual", "the name refresh must not touch the tier"
	assert row["user_id"] == 222
	assert row["last_seen_at"] != 1
	assert fake.identity_conflicts == []


def test_learn_does_not_record_on_a_normal_successful_write(monkeypatch):
	""" learn() calls that displace nobody -- a brand-new profile_id, or a
	higher-tier write that agrees with the stored owner -- must never touch
	identity_conflicts. (A higher-tier write that displaces a DIFFERENT owner
	does record one, as `superseded`: see
	test_learn_higher_rank_records_the_displaced_owner_as_superseded.) """
	fake = _setup(monkeypatch)

	asyncio.run(identity.learn(111, 222, "seed"))          # brand-new row
	asyncio.run(identity.learn(111, 222, "learned"))        # outranks, same owner
	asyncio.run(identity.learn(111, 222, "manual"))         # outranks, same owner

	assert fake.identity_conflicts == []
	assert fake.identities[0]["user_id"] == 222
	assert fake.identities[0]["confidence"] == "manual"


def test_learn_blocked_conflict_is_not_duplicated_on_a_repeat_observation(monkeypatch):
	""" The exact same disagreement observed twice (e.g. a replay ingest that
	re-learns the same, still-wrong, profile more than once) must not
	accumulate duplicate identity_conflicts rows -- INSERT IGNORE on the
	(profile_id, claimed_user_id) primary key keeps it to one. """
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "manual"))

	asyncio.run(identity.learn(111, 999, "learned"))
	asyncio.run(identity.learn(111, 999, "learned"))

	assert len(fake.identity_conflicts) == 1


# ─── open_conflicts ──────────────────────────────────────────────────────

def test_open_conflicts_returns_empty_list_when_none_open(monkeypatch):
	_setup(monkeypatch)

	assert asyncio.run(identity.open_conflicts()) == []


def test_open_conflicts_pairs_claims_with_the_stored_owner(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "manual"))
	asyncio.run(identity.learn(111, 999, "learned"))  # blocked -> recorded

	result = asyncio.run(identity.open_conflicts())

	assert len(result) == 1
	entry = result[0]
	assert entry["profile_id"] == 111
	assert entry["current_owner"] == 222
	assert entry["claims"] == [dict(user_id=999, source="learned", noticed_at=fake.identity_conflicts[0]["noticed_at"])]


def test_open_conflicts_groups_multiple_claimants_under_one_profile(monkeypatch):
	_setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "manual"))
	asyncio.run(identity.learn(111, 999, "learned"))
	asyncio.run(identity.learn(111, 555, "seed"))

	result = asyncio.run(identity.open_conflicts())

	assert len(result) == 1
	claimants = sorted(c["user_id"] for c in result[0]["claims"])
	assert claimants == [555, 999]


def test_open_conflicts_omits_a_claim_that_now_owns_the_profile(monkeypatch):
	""" A claimant who was refused and later WON the profile must not be listed
	as their own rival. Nothing ever clears `status`, so that stale `open` row
	is permanent, and reporting it renders as "Current owner: @999, Competing
	claim(s): @999" -- a self-contradiction on the only surface a moderator
	has. With no other live claim the profile drops out of the result entirely.

	Filtered on the READ side on purpose: migration 003_seed_identities has
	already written rows of this shape to production, so fixing only the
	writers would leave them showing forever. """
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "learned"))
	asyncio.run(identity.learn(111, 999, "learned"))  # refused -> open conflict for 999
	asyncio.run(identity.relink(111, 999))            # admin: it really is 999's

	assert asyncio.run(identity.user_for_profile(111)) == 999
	assert any(
		c["claimed_user_id"] == 999 and c["status"] == "open" for c in fake.identity_conflicts
	), "the stale open row is still in the table -- this is a read-side filter, not a delete"
	assert asyncio.run(identity.open_conflicts()) == []


def test_open_conflicts_keeps_the_rival_claims_on_a_profile_the_owner_once_lost(monkeypatch):
	""" Dropping the owner's own stale claim must not drop the profile when
	somebody else is still contesting it. """
	_setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "learned"))
	asyncio.run(identity.learn(111, 999, "learned"))  # refused -> open
	asyncio.run(identity.learn(111, 555, "learned"))  # refused -> open
	asyncio.run(identity.relink(111, 999))

	result = asyncio.run(identity.open_conflicts())

	assert len(result) == 1
	assert result[0]["current_owner"] == 999
	assert [c["user_id"] for c in result[0]["claims"]] == [555]


# ─── profiles_for_users / user_for_profile ──────────────────────────────

def test_profiles_for_users_groups_multiple_profiles_under_one_user(monkeypatch):
	_setup(monkeypatch)
	asyncio.run(identity.learn(1, 555, "seed"))
	asyncio.run(identity.learn(2, 555, "seed"))
	asyncio.run(identity.learn(3, 666, "seed"))

	result = asyncio.run(identity.profiles_for_users([555, 666]))

	assert sorted(result[555]) == [1, 2]
	assert result[666] == [3]


def test_profiles_for_users_omits_users_with_no_known_profile(monkeypatch):
	_setup(monkeypatch)
	asyncio.run(identity.learn(1, 555, "seed"))

	result = asyncio.run(identity.profiles_for_users([555, 12345]))

	assert 12345 not in result


def test_user_for_profile_returns_none_for_unknown_profile(monkeypatch):
	_setup(monkeypatch)

	assert asyncio.run(identity.user_for_profile(999999)) is None


def test_user_for_profile_returns_known_user(monkeypatch):
	_setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "seed"))

	assert asyncio.run(identity.user_for_profile(111)) == 222


# ─── names_for_profiles ──────────────────────────────────────────────────
# bot/web.py's profile pages need a Discord user's known AoE2 in-game names
# (to match civ_picks rows recorded via the un-linked lobby scrape, which
# carry no user_id — see persist_lobby_civs). identities.aoe2_name is the
# resolver's own record of that, one per profile_id, so this is a thin
# batch read alongside profiles_for_users/user_for_profile rather than a
# reach into the table from outside the module.

def test_names_for_profiles_returns_known_names(monkeypatch):
	_setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "seed", aoe2_name="Fenrir"))
	asyncio.run(identity.learn(112, 222, "seed", aoe2_name="Fenrir_Alt"))

	result = asyncio.run(identity.names_for_profiles([111, 112]))

	assert result == {111: "Fenrir", 112: "Fenrir_Alt"}


def test_names_for_profiles_omits_profiles_with_no_known_name(monkeypatch):
	_setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "seed"))   # no aoe2_name

	result = asyncio.run(identity.names_for_profiles([111, 999999]))

	assert result == {}


def test_names_for_profiles_empty_input_returns_empty_dict(monkeypatch):
	_setup(monkeypatch)

	assert asyncio.run(identity.names_for_profiles([])) == {}


# ─── cache invalidation ─────────────────────────────────────────────────

def test_cache_is_invalidated_by_a_write(monkeypatch):
	_setup(monkeypatch)
	asyncio.run(identity.learn(1, 555, "seed"))
	first = asyncio.run(identity.profiles_for_users([555, 666]))
	assert 666 not in first

	asyncio.run(identity.learn(2, 666, "seed"))
	second = asyncio.run(identity.profiles_for_users([555, 666]))

	assert second[666] == [2]


def test_invalidate_cache_forces_a_requery(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(1, 555, "seed"))
	asyncio.run(identity.profiles_for_users([555]))
	calls_after_first = fake.select_calls

	identity.invalidate_cache()
	asyncio.run(identity.profiles_for_users([555]))

	assert fake.select_calls > calls_after_first


def test_invalidate_cache_bumps_the_generation_counter(monkeypatch):
	_setup(monkeypatch)
	before = identity._cache_generation

	identity.invalidate_cache()

	assert identity._cache_generation == before + 1


class _GatedFakeDb(FakeDb):
	""" Like FakeDb, but an `identities` SELECT snapshots its rows immediately
	(as a real query would, at the moment it executes) and then suspends on
	`gate` before returning to its caller — letting a test interleave a
	concurrent write + invalidate_cache() between the snapshot and the
	return. That is exactly the race profiles_for_users' generation counter
	must survive: see the test below. """

	def __init__(self):
		super().__init__()
		self.gate = asyncio.Event()

	async def select(self, columns, table, where=None, **kwargs):
		rows = await super().select(columns, table, where, **kwargs)
		await self.gate.wait()
		return rows


def test_profiles_for_users_survives_invalidate_during_in_flight_load(monkeypatch):
	""" Reproduces the race described in profiles_for_users' docstring: a
	reload already in flight when a concurrent learn() calls
	invalidate_cache() must not get cached back in once it resolves —
	otherwise the mapping that concurrent learn() just wrote would stay
	invisible to every future caller until some unrelated write happened to
	invalidate the cache again, possibly forever (no TTL). """
	fake = _GatedFakeDb()
	monkeypatch.setattr(identity, "db", fake)
	identity.invalidate_cache()
	asyncio.run(identity.learn(1, 555, "seed"))

	async def scenario():
		load_task = asyncio.create_task(identity.profiles_for_users([555, 666]))
		await asyncio.sleep(0)  # let load_task run up to its gate.wait() suspension
		await identity.learn(2, 666, "seed")  # concurrent write; invalidates mid-load
		fake.gate.set()  # release the in-flight load with its now-stale snapshot
		return await load_task

	first_result = asyncio.run(scenario())
	assert 666 not in first_result, "the in-flight load's snapshot predates the concurrent learn()"

	second_result = asyncio.run(identity.profiles_for_users([555, 666]))
	assert second_result[666] == [2], (
		"a stale in-flight load must not poison the cache — the next call must "
		"reload and see the mapping the concurrent learn() wrote"
	)


# ─── link_self ──────────────────────────────────────────────────────────
# The player's own one-time /link, at the `self` tier. It is deliberately not
# just learn(source="self"): a player must never be able to take a profile
# somebody else is already linked to, even though `self` outranks `learned`.

def test_link_self_binds_an_unowned_profile(monkeypatch):
	fake = _setup(monkeypatch)

	assert asyncio.run(identity.link_self(111, 222, "Fenrir")) is True

	row = fake.identities[0]
	assert row["user_id"] == 222
	assert row["confidence"] == "self"
	assert row["aoe2_name"] == "Fenrir"
	assert fake.identity_conflicts == []


def test_link_self_binds_a_profile_whose_owner_is_unknown(monkeypatch):
	""" The seeded "profile known, owner unknown" row (user_id NULL) — and the
	state unlink() leaves behind — is unowned, so a player may claim it. """
	fake = _setup(monkeypatch)
	fake.identities.append(dict(
		profile_id=111, user_id=None, aoe2_name=None,
		confidence="seed", first_seen_at=1, last_seen_at=1,
	))

	assert asyncio.run(identity.link_self(111, 222, "Fenrir")) is True

	assert len(fake.identities) == 1
	assert fake.identities[0]["user_id"] == 222
	assert fake.identities[0]["confidence"] == "self"


def test_link_self_refuses_and_records_when_owned_by_another(monkeypatch):
	""" `self` outranks `learned`, so the lattice alone would have let this
	overwrite — link_self must refuse on ownership, not on rank. """
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 999, "learned", aoe2_name="SomeoneElse"))

	assert asyncio.run(identity.link_self(111, 222, "Fenrir")) is False

	row = fake.identities[0]
	assert row["user_id"] == 999
	assert row["confidence"] == "learned"
	assert row["aoe2_name"] == "SomeoneElse"
	assert len(fake.identity_conflicts) == 1
	conflict = fake.identity_conflicts[0]
	assert conflict["profile_id"] == 111
	assert conflict["claimed_user_id"] == 222
	assert conflict["source"] == "self"
	assert conflict["status"] == "open"


def test_link_self_is_idempotent_for_the_same_owner(monkeypatch):
	fake = _setup(monkeypatch)
	assert asyncio.run(identity.link_self(111, 222, "Fenrir")) is True

	assert asyncio.run(identity.link_self(111, 222, "FenrirRenamed")) is True

	assert len(fake.identities) == 1
	assert fake.identities[0]["user_id"] == 222
	assert fake.identities[0]["aoe2_name"] == "FenrirRenamed", "the newly observed name refreshes"
	assert fake.identity_conflicts == []


def test_link_self_does_not_lower_an_admin_set_confidence(monkeypatch):
	""" Re-running /link on a profile an admin already bound to the same
	player must not demote that `manual` row to `self` — it would become
	overwritable by a later admin-tier write from anywhere. """
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "manual", aoe2_name="Old"))

	assert asyncio.run(identity.link_self(111, 222, "New")) is True

	assert fake.identities[0]["confidence"] == "manual"
	assert fake.identities[0]["aoe2_name"] == "New"


def test_link_self_does_not_inherit_a_higher_tier_from_an_unowned_row(monkeypatch):
	""" Keeping a stored tier above `self` is only ever right when the row is
	ALREADY this player's (an admin bound it to them). An UNOWNED row's tier
	belongs to whoever wrote it -- data/profile_resolved.csv seeds rows at
	`manual` from its own `source` column, and such a row can carry no user_id
	at all -- so inheriting it would let a self-service /link mint a `manual`
	binding, a tier only an admin is allowed to create, and one no later
	`self` write could correct. """
	fake = _setup(monkeypatch)
	fake.identities.append(dict(
		profile_id=111, user_id=None, aoe2_name="Fenrir",
		confidence="manual", first_seen_at=1, last_seen_at=1,
	))

	assert asyncio.run(identity.link_self(111, 222, "Fenrir")) is True

	assert fake.identities[0]["user_id"] == 222
	assert fake.identities[0]["confidence"] == "self", "a player's own claim is `self`, never `manual`"


# ─── unlink ─────────────────────────────────────────────────────────────

def test_unlink_clears_owner_and_records_the_removed_claim(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "manual", aoe2_name="Fenrir"))
	fake.identities[0]["last_seen_at"] = 1

	asyncio.run(identity.unlink(111))

	row = fake.identities[0]
	assert row["user_id"] is None
	assert row["confidence"] == "seed", "back to the unowned state, not left at manual"
	assert row["aoe2_name"] == "Fenrir", "the observed in-game name is not ownership and survives"
	assert row["last_seen_at"] != 1
	assert asyncio.run(identity.profiles_for_users([222])) == {}
	assert len(fake.identity_conflicts) == 1
	conflict = fake.identity_conflicts[0]
	assert conflict["profile_id"] == 111
	assert conflict["claimed_user_id"] == 222
	assert conflict["source"] == "manual", "an admin is the only caller"
	assert conflict["status"] == "unlinked"


def test_unlink_is_a_noop_for_an_unknown_profile(monkeypatch):
	fake = _setup(monkeypatch)

	asyncio.run(identity.unlink(999999))

	assert fake.identities == []
	assert fake.identity_conflicts == []


def test_unlink_records_nothing_for_an_already_unowned_profile(monkeypatch):
	fake = _setup(monkeypatch)
	fake.identities.append(dict(
		profile_id=111, user_id=None, aoe2_name="Fenrir",
		confidence="seed", first_seen_at=1, last_seen_at=1,
	))

	asyncio.run(identity.unlink(111))

	assert fake.identities[0]["user_id"] is None
	assert fake.identity_conflicts == [], "there was no claim to remove"


def test_unlink_does_not_demote_an_already_unowned_row(monkeypatch):
	""" A no-op unlink used to still rewrite the row to confidence='seed'. That
	silently WEAKENS the profile: a `learned` row that a `seed` write could not
	touch becomes one that an equal-tier `seed` write binds outright. Unlinking
	twice must not make a profile easier to claim than unlinking once. """
	fake = _setup(monkeypatch)
	fake.identities.append(dict(
		profile_id=111, user_id=None, aoe2_name="Fenrir",
		confidence="learned", first_seen_at=1, last_seen_at=1,
	))

	asyncio.run(identity.unlink(111))

	assert fake.identities[0]["confidence"] == "learned"

	# ...and the tier it kept still outranks a `seed` claim, which is the whole
	# point of not demoting it.
	asyncio.run(identity.learn(111, 222, "seed"))
	assert fake.identities[0]["user_id"] is None


def test_unlink_records_the_removed_claim_before_dropping_the_binding(monkeypatch):
	""" The adapter runs with autocommit and has no transaction API, so the
	record and the write are two independent statements. Recording first means
	a failure between them leaves a conflict row for a binding that still
	stands (visible, and self-correcting on a retry) rather than a binding
	silently gone with no trace of who held it. """
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "manual"))

	async def _boom(*a, **k):
		raise RuntimeError("conflict insert failed")

	monkeypatch.setattr(identity, "_record_conflict", _boom)

	try:
		asyncio.run(identity.unlink(111))
	except RuntimeError:
		pass
	else:
		raise AssertionError("unlink must not swallow a failed conflict record")

	assert fake.identities[0]["user_id"] == 222, "the binding must still stand if its audit row never landed"


# ─── relink ─────────────────────────────────────────────────────────────
# The admin correction, at `manual` tier, atomic by construction: the profile
# moves AND the member stops owning anything else in one call, so a relink can
# never leave a member owning both the wrong profile and the right one (which
# would double-count their statistics).

def test_relink_supersedes_the_previous_owner_atomically(monkeypatch):
	""" Note the previous owner is itself a `manual` row: plain learn() would
	refuse this as an equal-rank disagreement, but an admin correction of an
	earlier admin mistake is exactly what relink exists for. """
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "manual"))

	asyncio.run(identity.relink(111, 999))

	row = fake.identities[0]
	assert row["user_id"] == 999
	assert row["confidence"] == "manual"
	assert len(fake.identity_conflicts) == 1
	conflict = fake.identity_conflicts[0]
	assert conflict["profile_id"] == 111
	assert conflict["claimed_user_id"] == 222
	assert conflict["status"] == "superseded"


def test_relink_without_additional_releases_the_members_other_profiles(monkeypatch):
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "learned"))  # the wrong profile

	asyncio.run(identity.relink(112, 222))  # admin: it is really this one

	by_pid = {r["profile_id"]: r for r in fake.identities}
	assert by_pid[112]["user_id"] == 222
	assert by_pid[112]["confidence"] == "manual"
	assert by_pid[111]["user_id"] is None, "the member must not end up owning both"
	assert by_pid[111]["confidence"] == "seed"
	assert asyncio.run(identity.profiles_for_users([222])) == {222: [112]}
	assert [
		(c["profile_id"], c["claimed_user_id"], c["status"]) for c in fake.identity_conflicts
	] == [(111, 222, "superseded")]


def test_relink_records_released_profiles_as_superseded(monkeypatch):
	""" Spec section 3: BOTH halves of a relink -- the displaced owner of the
	new profile and every profile the member is released from -- are recorded
	as `superseded`. Releasing goes through unlink(), whose default status is
	`unlinked`, so relink must override it: "an admin removed this link" and
	"an admin moved this member elsewhere" are different events, and a reader
	of identity_conflicts has only `status` to tell them apart. """
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "learned"))  # the member's wrong profile
	asyncio.run(identity.learn(112, 999, "learned"))  # the right profile, wrongly owned

	asyncio.run(identity.relink(112, 222))

	recorded = {(c["profile_id"], c["claimed_user_id"]): c for c in fake.identity_conflicts}
	assert recorded[(112, 999)]["status"] == "superseded", "the displaced owner of the new profile"
	assert recorded[(111, 222)]["status"] == "superseded", "the profile the member was released from"
	assert recorded[(111, 222)]["source"] == "manual"


def test_unlink_on_its_own_still_records_unlinked(monkeypatch):
	""" The counterpart to the test above: the default must stay `unlinked`, so
	relink's `superseded` remains a distinction and not just the only value
	anything ever writes. """
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "manual"))

	asyncio.run(identity.unlink(111))

	assert fake.identity_conflicts[0]["status"] == "unlinked"


def test_relink_additional_keeps_the_members_other_profiles(monkeypatch):
	""" Multi-account players are real (the flagship community has several), so
	`additional` adds a second profile instead of replacing the first. """
	_setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "learned"))

	asyncio.run(identity.relink(112, 222, additional=True))

	assert sorted(asyncio.run(identity.profiles_for_users([222]))[222]) == [111, 112]


def test_relink_coerces_the_profile_id_to_an_int(monkeypatch):
	""" The release loop compares `profile_id` against ids read back out of the
	DB, which are ints. A str one (an uncoerced command argument) would compare
	unequal to its own just-bound row, so the loop would unlink it again and
	the member would end up owning NOTHING.

	This fake stores whatever it is handed and compares with ==, so it cannot
	reproduce that mismatch -- what it CAN pin is the coercion itself: without
	int(), the row lands with a str profile_id. """
	fake = _setup(monkeypatch)

	asyncio.run(identity.relink("112", 222))

	assert fake.identities[0]["profile_id"] == 112
	assert isinstance(fake.identities[0]["profile_id"], int)


def test_relink_leaves_another_members_profiles_alone(monkeypatch):
	_setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "learned"))
	asyncio.run(identity.learn(113, 333, "learned"))

	asyncio.run(identity.relink(112, 222))

	assert asyncio.run(identity.profiles_for_users([333])) == {333: [113]}


def test_link_self_unlink_and_relink_each_invalidate_the_cache(monkeypatch):
	""" profiles_for_users() has no TTL — a write that forgets to invalidate
	stays invisible to every reader until some unrelated write happens to
	invalidate the cache, possibly forever. """
	_setup(monkeypatch)

	before = identity._cache_generation
	asyncio.run(identity.link_self(111, 222, "Fenrir"))
	after_link_self = identity._cache_generation
	assert after_link_self > before

	asyncio.run(identity.relink(112, 222, additional=True))
	after_relink = identity._cache_generation
	assert after_relink > after_link_self

	asyncio.run(identity.unlink(112))
	assert identity._cache_generation > after_relink


# ─── coverage_for_community ─────────────────────────────────────────────
# The number `/identity status` exists to show. It lives here, not inline in
# the command handler, so the future web UI reports the same figure from the
# same query rather than a second one that drifts.


class _CoverageFakeDb(FakeDb):
	""" FakeDb (identities/identity_conflicts as in-memory lists, so the tests
	below can set links up through the real learn()/link_self()) plus a genuine
	sqlite3 database holding the three tables the coverage query joins over.

	fetchall() runs the module's ACTUAL SQL string, swapping only MySQL's %s
	placeholder for sqlite's ? — so a wrong join key or a mistyped column fails
	here instead of in production. A Python re-implementation of the join would
	have agreed with whatever the query happened to say, which is exactly the
	bug class this query can have. """

	def __init__(self):
		super().__init__()
		self.sqlite = sqlite3.connect(":memory:")
		self.sqlite.row_factory = sqlite3.Row
		self.sqlite.executescript(
			"CREATE TABLE matches (match_id INT, channel_id INT, reported_at INT);"
			"CREATE TABLE match_players (match_id INT, channel_id INT, user_id INT);"
			"CREATE TABLE community_channels (channel_id INT, community_id INT);"
		)

	def enroll(self, channel_id, community_id):
		self.sqlite.execute("INSERT INTO community_channels VALUES (?, ?)", (channel_id, community_id))

	def played(self, match_id, channel_id, reported_at, user_ids):
		self.sqlite.execute("INSERT INTO matches VALUES (?, ?, ?)", (match_id, channel_id, reported_at))
		for user_id in user_ids:
			self.sqlite.execute("INSERT INTO match_players VALUES (?, ?, ?)", (match_id, channel_id, user_id))

	async def fetchall(self, sql, params=None):
		self.select_calls += 1
		return [dict(row) for row in self.sqlite.execute(sql.replace("%s", "?"), list(params or []))]


def _coverage_setup(monkeypatch):
	fake = _CoverageFakeDb()
	monkeypatch.setattr(identity, "db", fake)
	identity.invalidate_cache()
	return fake


def test_coverage_for_community_counts_distinct_players_in_the_window(monkeypatch):
	""" The shape of the live figure: 49 distinct users played in the last 90
	days, 35 of them linked. A player who appears in several matches is one
	player, not one per match. """
	fake = _coverage_setup(monkeypatch)
	now = int(time.time())
	fake.enroll(channel_id=10, community_id=7)
	fake.played(1, 10, now - 86400, [111, 222, 333])
	fake.played(2, 10, now - 2 * 86400, [111, 222])  # the same people again
	asyncio.run(identity.learn(9001, 111, "self"))

	assert asyncio.run(identity.coverage_for_community(7)) == dict(
		players=3, linked=1, unlinked=2, conflicts=0)


def test_coverage_for_community_excludes_players_outside_the_window(monkeypatch):
	""" "Players seen in the last 90 days" is the number an admin can act on —
	somebody who last played two years ago is not a coverage gap to chase. """
	fake = _coverage_setup(monkeypatch)
	now = int(time.time())
	fake.enroll(channel_id=10, community_id=7)
	fake.played(1, 10, now - 10 * 86400, [111])
	fake.played(2, 10, now - 200 * 86400, [222])

	result = asyncio.run(identity.coverage_for_community(7))

	assert result["players"] == 1
	assert result["unlinked"] == 1


def test_coverage_for_community_ignores_another_communitys_channels(monkeypatch):
	""" Multi-tenancy: the count a moderator is shown must describe THEIR
	community, so the window is scoped through community_channels. """
	fake = _coverage_setup(monkeypatch)
	now = int(time.time())
	fake.enroll(channel_id=10, community_id=7)
	fake.enroll(channel_id=20, community_id=8)
	fake.played(1, 10, now - 86400, [111])
	fake.played(2, 20, now - 86400, [222, 333])

	assert asyncio.run(identity.coverage_for_community(7))["players"] == 1
	assert asyncio.run(identity.coverage_for_community(8))["players"] == 2


def test_coverage_for_community_counts_a_multi_profile_owner_once(monkeypatch):
	""" 5 production users own several profiles (up to 3). "linked" counts
	PEOPLE, so owning three profiles must not report three linked players out
	of a community of one. """
	fake = _coverage_setup(monkeypatch)
	now = int(time.time())
	fake.enroll(channel_id=10, community_id=7)
	fake.played(1, 10, now - 86400, [111])
	asyncio.run(identity.learn(9001, 111, "self"))
	asyncio.run(identity.learn(9002, 111, "manual"))

	assert asyncio.run(identity.coverage_for_community(7)) == dict(
		players=1, linked=1, unlinked=0, conflicts=0)


def test_coverage_for_community_reports_open_conflicts(monkeypatch):
	fake = _coverage_setup(monkeypatch)
	now = int(time.time())
	fake.enroll(channel_id=10, community_id=7)
	fake.played(1, 10, now - 86400, [111])
	asyncio.run(identity.learn(9001, 111, "learned"))
	asyncio.run(identity.learn(9001, 222, "learned"))  # refused -> one open conflict

	assert asyncio.run(identity.coverage_for_community(7))["conflicts"] == 1


def test_coverage_for_community_reports_zeros_for_a_silent_community(monkeypatch):
	""" A community with no matches in the window must answer 0/0, not crash on
	an empty result set. """
	fake = _coverage_setup(monkeypatch)
	fake.enroll(channel_id=10, community_id=7)

	assert asyncio.run(identity.coverage_for_community(7)) == dict(
		players=0, linked=0, unlinked=0, conflicts=0)


# ─── admin identity commands (bot/commands/admin.py) ────────────────────
# bot/commands/admin.py does `from nextcord import Member, Embed, Colour`,
# `from core.utils import seconds_to_str, get_nick` and `import bot`. Under
# CI (pytest only, no nextcord/aiomysql/aiohttp) none of that resolves, and
# importing it the normal way -- `import bot.commands.admin` -- would first
# run bot/commands/__init__.py, which star-imports every other command
# module (quiz, replay_stats, player_details, insights, ...) and pulls in
# far more than this file needs to fake.
#
# Instead: register minimal nextcord/nextcord.utils fakes in sys.modules
# (tests/test_match_final_message.py's established trick) and load
# admin.py directly by file path (tests/test_replay_scoring.py's
# `player_tags_standalone_test` trick), bypassing bot/commands/__init__.py
# entirely. bot.identity and bot.community are imported for real above
# (both are pure DB-only modules, already proven importable under CI) and
# their functions are monkeypatched directly per test -- admin.py reaches
# them as `bot.identity.X` / `bot.community.X` at call time, so patching
# the real modules *is* patching admin.py's actual dependency.

class _Perms(IntEnum):
	USER = 0
	MEMBER = 1
	MODERATOR = 2
	ADMIN = 3


class _PermsDenied(Exception):
	pass


class _FakeMember:
	def __init__(self, user_id, name="Player"):
		self.id = user_id
		self.name = name
		self.nick = None


class _FakeCtx:
	Perms = _Perms

	def __init__(self, access_level=_Perms.ADMIN, channel_id=1, author=None):
		self.access_level = access_level
		# `qc` is a QueueChannel on the real context, so it carries the channel
		# id as well as the translator — both commands under test use the id to
		# resolve the community for the deduction solver.
		self.qc = types.SimpleNamespace(gt=lambda s: s, id=channel_id)
		self.channel = types.SimpleNamespace(id=channel_id)
		self.author = author if author is not None else _FakeMember(1)
		self.replies = []
		self.successes = []

	def check_perms(self, req_perms):
		if self.access_level.value < req_perms.value:
			raise _PermsDenied("insufficient permissions")

	async def reply(self, content=None, embed=None, ephemeral=False):
		# `ephemeral` mirrors SlashContext.reply, which forwards its kwargs to
		# interaction.response.send_message / interaction.followup.send — so a
		# test can assert a message really is private to the caller.
		self.replies.append(types.SimpleNamespace(content=content, embed=embed, ephemeral=ephemeral))

	async def success(self, content=None, title=None):
		self.successes.append(content)


def _load_command_module(monkeypatch, filename, modname):
	fake_nextcord = types.ModuleType("nextcord")
	fake_nextcord.Member = object
	fake_nextcord.Colour = lambda value=0: value

	class _FakeEmbed:
		def __init__(self, title=None, description=None, colour=None, color=None):
			self.title = title
			self.description = description
			self.colour = colour if colour is not None else color
			self.fields = []

		def add_field(self, name=None, value=None, inline=True):
			self.fields.append(dict(name=name, value=value, inline=inline))

	fake_nextcord.Embed = _FakeEmbed
	monkeypatch.setitem(sys.modules, "nextcord", fake_nextcord)

	fake_nextcord_utils = types.ModuleType("nextcord.utils")
	fake_nextcord_utils.get = lambda *a, **k: None
	fake_nextcord_utils.find = lambda *a, **k: None
	fake_nextcord_utils.escape_markdown = lambda s: s
	monkeypatch.setitem(sys.modules, "nextcord.utils", fake_nextcord_utils)

	import bot.exceptions as exceptions
	monkeypatch.setattr(sys.modules["bot"], "Exc", exceptions.Exceptions, raising=False)

	path = Path(__file__).resolve().parent.parent / "bot" / "commands" / filename
	spec = importlib.util.spec_from_file_location(modname, path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def _load_admin_module(monkeypatch):
	return _load_command_module(monkeypatch, "admin.py", "admin_standalone_test")


def _load_link_module(monkeypatch):
	return _load_command_module(monkeypatch, "identity.py", "commands_identity_standalone_test")


# ─── identity link ───────────────────────────────────────────────────────

def test_identity_link_requires_admin_permission(monkeypatch):
	admin = _load_admin_module(monkeypatch)
	calls = []
	monkeypatch.setattr(identity, "relink", lambda *a, **k: calls.append((a, k)))
	ctx = _FakeCtx(access_level=_Perms.MODERATOR)

	try:
		asyncio.run(admin.identity_link(ctx, _FakeMember(1), 111))
	except _PermsDenied:
		pass
	else:
		raise AssertionError("identity_link must require ADMIN permission")

	assert calls == []


def test_identity_link_records_a_brand_new_mapping(monkeypatch):
	admin = _load_admin_module(monkeypatch)
	fake = _setup(monkeypatch)
	ctx = _FakeCtx()
	member = _FakeMember(222, name="Fenrir")

	asyncio.run(admin.identity_link(ctx, member, 111))

	row = fake.identities[0]
	assert (row["profile_id"], row["user_id"], row["confidence"]) == (111, 222, "manual")
	assert len(ctx.successes) == 1
	assert "111" in ctx.successes[0] and "Fenrir" in ctx.successes[0]


def test_identity_link_triggers_the_deduction_solver(monkeypatch):
	""" Spec section 4: an admin's binding is a new constraint that can resolve
	other profiles in the same games immediately, so the solver runs on the
	spot. A solver failure must never turn a completed link into an error. """
	import bot.identity_solver as identity_solver

	admin = _load_admin_module(monkeypatch)
	_setup(monkeypatch)
	ran = []

	async def _run_for_channel(channel_id):
		ran.append(channel_id)
		return 0

	monkeypatch.setattr(identity_solver, "run_for_channel", _run_for_channel)
	ctx = _FakeCtx(channel_id=4242)

	asyncio.run(admin.identity_link(ctx, _FakeMember(222), 111))

	assert ran == [4242]

	async def _boom(channel_id):
		raise RuntimeError("solver exploded")

	monkeypatch.setattr(identity_solver, "run_for_channel", _boom)
	ctx = _FakeCtx(channel_id=4242)

	try:
		asyncio.run(admin.identity_link(ctx, _FakeMember(222), 111))
	except RuntimeError:
		raise AssertionError("a solver failure must never surface as a command error") from None

	assert len(ctx.successes) == 1, "the link still reports success"


def test_identity_link_relinking_the_members_own_profile_releases_nothing(monkeypatch):
	""" Re-running the same link is not a correction of anything: the member
	already owns exactly this profile, so nothing is released and the reply must
	not invent a release. """
	admin = _load_admin_module(monkeypatch)
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "manual"))
	ctx = _FakeCtx()

	asyncio.run(admin.identity_link(ctx, _FakeMember(222), 111))

	assert asyncio.run(identity.profiles_for_users([222])) == {222: [111]}
	assert fake.identity_conflicts == []
	assert len(ctx.successes) == 1


def test_identity_link_additional_keeps_the_members_other_profiles(monkeypatch):
	""" The multi-account case: 5 production users own several profiles (up to
	3), and before `additional` there was NO command that could give a member a
	second one -- the only reassignment path released their others. """
	admin = _load_admin_module(monkeypatch)
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "self", aoe2_name="Main"))
	ctx = _FakeCtx()

	asyncio.run(admin.identity_link(ctx, _FakeMember(222, name="Fenrir"), 112, additional=True))

	assert sorted(asyncio.run(identity.profiles_for_users([222]))[222]) == [111, 112]
	assert fake.identity_conflicts == [], "nothing was released, so nothing is superseded"
	assert len(ctx.successes) == 1
	assert "112" in ctx.successes[0]


def test_identity_link_default_releases_the_members_other_profiles(monkeypatch):
	""" The "fix a wrong link" case, and the default: the member ends up owning
	exactly the profile just assigned, because every consumer resolves profile
	-> user through this table and owning two would double-attribute their
	statistics. """
	admin = _load_admin_module(monkeypatch)
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "learned"))  # the wrong profile
	ctx = _FakeCtx()

	asyncio.run(admin.identity_link(ctx, _FakeMember(222), 112))

	assert asyncio.run(identity.profiles_for_users([222])) == {222: [112]}
	assert [(c["profile_id"], c["status"]) for c in fake.identity_conflicts] == [(111, "superseded")]


def test_identity_link_reply_names_the_released_profiles(monkeypatch):
	""" A release is destructive and invisible -- an admin who meant
	`additional: true` has no way back unless the reply says which ids it just
	took away. """
	admin = _load_admin_module(monkeypatch)
	_setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "learned"))
	asyncio.run(identity.learn(113, 222, "learned"))
	ctx = _FakeCtx()

	asyncio.run(admin.identity_link(ctx, _FakeMember(222), 112))

	msg = ctx.successes[0]
	assert "112" in msg
	assert "111" in msg and "113" in msg, "name every id that was released"
	assert "additional" in msg.lower(), "and how to put one back"


def test_identity_link_reassigns_a_profile_owned_by_another_member(monkeypatch):
	""" An admin correction of an earlier admin's mistake IS a profile that
	belongs to somebody else -- so it must simply work, with no confirmation
	flag. learn() would refuse this (equal-tier, different user), record an
	`open` conflict against the admin's own instruction, and still reply
	"Linked"; relink() is the one writer allowed to move a `manual` binding. """
	admin = _load_admin_module(monkeypatch)
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 999, "manual", aoe2_name="WrongPerson"))
	ctx = _FakeCtx()

	asyncio.run(admin.identity_link(ctx, _FakeMember(222, name="Fenrir"), 111))

	row = fake.identities[0]
	assert row["user_id"] == 222
	assert row["confidence"] == "manual"
	assert [(c["claimed_user_id"], c["status"]) for c in fake.identity_conflicts] == [(999, "superseded")]
	assert len(ctx.successes) == 1
	assert "999" in ctx.successes[0], "say whose link was superseded"


# ─── identity unlink ─────────────────────────────────────────────────────

def test_identity_unlink_requires_admin_permission(monkeypatch):
	admin = _load_admin_module(monkeypatch)
	calls = []
	monkeypatch.setattr(identity, "unlink", lambda *a, **k: calls.append((a, k)))
	ctx = _FakeCtx(access_level=_Perms.MODERATOR)

	try:
		asyncio.run(admin.identity_unlink(ctx, _FakeMember(222), 111))
	except _PermsDenied:
		pass
	else:
		raise AssertionError("identity_unlink must require ADMIN permission")

	assert calls == []


def test_identity_unlink_clears_the_binding(monkeypatch):
	admin = _load_admin_module(monkeypatch)
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "manual", aoe2_name="Fenrir"))
	ctx = _FakeCtx()

	asyncio.run(admin.identity_unlink(ctx, _FakeMember(222, name="Fenrir"), 111))

	assert fake.identities[0]["user_id"] is None
	assert asyncio.run(identity.profiles_for_users([222])) == {}
	assert [(c["claimed_user_id"], c["status"]) for c in fake.identity_conflicts] == [(222, "unlinked")]
	assert len(ctx.successes) == 1
	msg = ctx.successes[0]
	assert "111" in msg
	assert "/link" in msg, "say it can be claimed again"


def test_identity_unlink_refuses_when_the_profile_belongs_to_someone_else(monkeypatch):
	""" A mistyped id must never silently unlink a third party. """
	admin = _load_admin_module(monkeypatch)
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 999, "manual"))
	ctx = _FakeCtx()

	try:
		asyncio.run(admin.identity_unlink(ctx, _FakeMember(222), 111))
	except admin.bot.Exc.ValueError as e:
		assert "999" in str(e), "name who actually owns it"
		assert "111" in str(e)
	else:
		raise AssertionError("identity_unlink must refuse to remove another member's link")

	assert fake.identities[0]["user_id"] == 999
	assert fake.identity_conflicts == []
	assert ctx.successes == []


def test_identity_unlink_refuses_an_unowned_profile(monkeypatch):
	""" Nothing to remove is not success -- replying "unlinked" would tell an
	admin their typo had taken effect. """
	admin = _load_admin_module(monkeypatch)
	fake = _setup(monkeypatch)
	ctx = _FakeCtx()

	try:
		asyncio.run(admin.identity_unlink(ctx, _FakeMember(222), 111))
	except admin.bot.Exc.ValueError as e:
		assert "111" in str(e)
	else:
		raise AssertionError("identity_unlink must refuse a profile nobody owns")

	assert fake.identities == []
	assert ctx.successes == []


# ─── identity show ───────────────────────────────────────────────────────

def test_identity_show_requires_moderator_permission(monkeypatch):
	admin = _load_admin_module(monkeypatch)
	calls = []
	monkeypatch.setattr(identity, "profiles_for_users", lambda *a, **k: calls.append(a))
	ctx = _FakeCtx(access_level=_Perms.MEMBER)

	try:
		asyncio.run(admin.identity_show(ctx, _FakeMember(1)))
	except _PermsDenied:
		pass
	else:
		raise AssertionError("identity_show must require MODERATOR permission")

	assert calls == []


def test_identity_show_lists_known_profiles(monkeypatch):
	admin = _load_admin_module(monkeypatch)
	_setup(monkeypatch)  # the name/confidence lookups are real reads

	async def _profiles_for_users(user_ids):
		return {222: [111, 222]}

	monkeypatch.setattr(identity, "profiles_for_users", _profiles_for_users)
	ctx = _FakeCtx(access_level=_Perms.MODERATOR)
	member = _FakeMember(222, name="Fenrir")

	asyncio.run(admin.identity_show(ctx, member))

	assert len(ctx.replies) == 1
	embed = ctx.replies[0].embed
	values = [f["value"] for f in embed.fields]
	assert any("111" in v and "222" in v for v in values)
	assert not any("Nick" in (f["name"] or "") for f in embed.fields), (
		"identity_aliases is gone — Discord supplies display names live"
	)


def test_identity_show_reports_no_known_profiles(monkeypatch):
	admin = _load_admin_module(monkeypatch)

	async def _profiles_for_users(user_ids):
		return {}

	monkeypatch.setattr(identity, "profiles_for_users", _profiles_for_users)
	ctx = _FakeCtx()
	member = _FakeMember(222)

	asyncio.run(admin.identity_show(ctx, member))

	embed = ctx.replies[0].embed
	assert len(embed.fields) == 1
	assert embed.fields[0]["value"]  # some "none known" placeholder, not blank


def test_identity_show_no_longer_resolves_a_community(monkeypatch):
	""" identity_show used to look up this channel's community purely to print
	a per-community nick. With identity_aliases deleted that lookup is dead
	weight — and it made a moderator-visible read fail differently in a
	channel that was never enrolled. """
	admin = _load_admin_module(monkeypatch)
	_setup(monkeypatch)

	async def _profiles_for_users(user_ids):
		return {222: [111]}

	community_calls = []

	async def _community_for_channel(*a, **k):
		community_calls.append((a, k))
		return None

	monkeypatch.setattr(identity, "profiles_for_users", _profiles_for_users)
	monkeypatch.setattr(community, "community_for_channel", _community_for_channel)
	ctx = _FakeCtx()
	member = _FakeMember(222)

	asyncio.run(admin.identity_show(ctx, member))  # must not raise

	assert community_calls == []
	assert len(ctx.replies) == 1


def test_identity_show_lists_names_and_confidence_per_profile(monkeypatch):
	""" The ids alone cannot be checked by a human. The observed in-game name is
	what an admin recognises the account by, and the confidence tier is what
	tells them whether they are looking at an automated deduction or somebody's
	deliberate decision -- the difference between "fix this" and "leave it". """
	admin = _load_admin_module(monkeypatch)
	_setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "manual", aoe2_name="Fenrir"))
	asyncio.run(identity.learn(112, 222, "learned"))  # deduced, name never observed
	ctx = _FakeCtx(access_level=_Perms.MODERATOR)

	asyncio.run(admin.identity_show(ctx, _FakeMember(222, name="Fenrir")))

	text = "\n".join(f["value"] for f in ctx.replies[0].embed.fields)
	assert "111" in text and "Fenrir" in text and "manual" in text
	assert "112" in text and "learned" in text
	assert not any("Nick" in (f["name"] or "") for f in ctx.replies[0].embed.fields)


# ─── identity status ──────────────────────────────────────────────────────
# Spec section 3: the visibility surface. Silent feature-failure is replaced by
# a number an admin can act on.

def test_identity_status_requires_moderator_permission(monkeypatch):
	admin = _load_admin_module(monkeypatch)
	calls = []
	monkeypatch.setattr(identity, "coverage_for_community", lambda *a, **k: calls.append((a, k)))
	ctx = _FakeCtx(access_level=_Perms.MEMBER)

	try:
		asyncio.run(admin.identity_status(ctx))
	except _PermsDenied:
		pass
	else:
		raise AssertionError("identity_status must require MODERATOR permission")

	assert calls == []


def test_identity_status_reports_linked_and_unlinked_counts(monkeypatch):
	""" The live figures at the time of writing: 49 players in the window, 35
	linked, 1 open conflict. """
	admin = _load_admin_module(monkeypatch)
	asked = []

	async def _community_for_channel(channel_id):
		asked.append(channel_id)
		return 7

	async def _coverage_for_community(community_id, days=90):
		assert (community_id, days) == (7, 90)
		return dict(players=49, linked=35, unlinked=14, conflicts=1)

	monkeypatch.setattr(community, "community_for_channel", _community_for_channel)
	monkeypatch.setattr(identity, "coverage_for_community", _coverage_for_community)
	ctx = _FakeCtx(access_level=_Perms.MODERATOR, channel_id=4242)

	asyncio.run(admin.identity_status(ctx))

	assert asked == [4242]
	assert len(ctx.replies) == 1
	text = _embed_text(ctx.replies[0].embed)
	assert "35" in text and "49" in text, "N of M, the headline number"
	assert "14" in text, "and how many are still missing"
	assert "90" in text, "state the window, or the number means nothing"
	assert "/link" in text, "tell the admin what the unlinked players can do"
	conflict_fields = [f for f in ctx.replies[0].embed.fields if "conflict" in (f["name"] or "").lower()]
	assert len(conflict_fields) == 1 and "1" in conflict_fields[0]["value"]


def test_identity_status_omits_conflicts_when_there_are_none(monkeypatch):
	""" "0 open conflicts" is noise on the one surface that exists to make a
	real number stand out. """
	admin = _load_admin_module(monkeypatch)

	async def _community_for_channel(channel_id):
		return 7

	async def _coverage_for_community(community_id, days=90):
		return dict(players=49, linked=49, unlinked=0, conflicts=0)

	monkeypatch.setattr(community, "community_for_channel", _community_for_channel)
	monkeypatch.setattr(identity, "coverage_for_community", _coverage_for_community)
	ctx = _FakeCtx(access_level=_Perms.MODERATOR)

	asyncio.run(admin.identity_status(ctx))

	assert not any("conflict" in (f["name"] or "").lower() for f in ctx.replies[0].embed.fields)


def test_identity_status_reports_a_community_with_no_recent_matches(monkeypatch):
	""" 0 of 0 read as a coverage failure would send an admin hunting for
	players who do not exist. """
	admin = _load_admin_module(monkeypatch)

	async def _community_for_channel(channel_id):
		return 7

	async def _coverage_for_community(community_id, days=90):
		return dict(players=0, linked=0, unlinked=0, conflicts=0)

	monkeypatch.setattr(community, "community_for_channel", _community_for_channel)
	monkeypatch.setattr(identity, "coverage_for_community", _coverage_for_community)
	ctx = _FakeCtx(access_level=_Perms.MODERATOR)

	asyncio.run(admin.identity_status(ctx))  # must not raise

	assert _embed_text(ctx.replies[0].embed).strip(), "some readable answer, not a blank embed"


def test_identity_status_reports_an_unenrolled_channel_without_erroring(monkeypatch):
	""" A channel that was never enrolled has no community to measure. That is
	an ordinary state (most channels), so it is an answer, not an error -- and
	the coverage query must not run at all. """
	admin = _load_admin_module(monkeypatch)
	coverage_calls = []

	async def _community_for_channel(channel_id):
		return None

	async def _coverage_for_community(*a, **k):
		coverage_calls.append((a, k))
		return {}

	monkeypatch.setattr(community, "community_for_channel", _community_for_channel)
	monkeypatch.setattr(identity, "coverage_for_community", _coverage_for_community)
	ctx = _FakeCtx(access_level=_Perms.MODERATOR)

	asyncio.run(admin.identity_status(ctx))  # must not raise

	assert coverage_calls == []
	assert len(ctx.replies) == 1
	assert "enrolled" in (ctx.replies[0].content or "").lower()


# ─── identity conflicts ───────────────────────────────────────────────────

def test_identity_conflicts_requires_moderator_permission(monkeypatch):
	admin = _load_admin_module(monkeypatch)
	calls = []
	monkeypatch.setattr(identity, "open_conflicts", lambda *a, **k: calls.append(a))
	ctx = _FakeCtx(access_level=_Perms.MEMBER)

	try:
		asyncio.run(admin.identity_conflicts(ctx))
	except _PermsDenied:
		pass
	else:
		raise AssertionError("identity_conflicts must require MODERATOR permission")

	assert calls == []


def test_identity_conflicts_reports_none_open(monkeypatch):
	admin = _load_admin_module(monkeypatch)

	async def _open_conflicts():
		return []

	monkeypatch.setattr(identity, "open_conflicts", _open_conflicts)
	ctx = _FakeCtx(access_level=_Perms.MODERATOR)

	asyncio.run(admin.identity_conflicts(ctx))

	assert len(ctx.replies) == 1
	assert ctx.replies[0].content == "no open identity conflicts"
	assert ctx.replies[0].embed is None


def test_identity_conflicts_lists_profile_owner_and_claimants(monkeypatch):
	admin = _load_admin_module(monkeypatch)

	async def _open_conflicts():
		return [dict(
			profile_id=5771336,
			current_owner=527532506153615360,
			claims=[dict(user_id=850996190282776577, source="seed", noticed_at=1)],
		)]

	monkeypatch.setattr(identity, "open_conflicts", _open_conflicts)
	ctx = _FakeCtx(access_level=_Perms.MODERATOR)

	asyncio.run(admin.identity_conflicts(ctx))

	assert len(ctx.replies) == 1
	embed = ctx.replies[0].embed
	assert len(embed.fields) == 1
	field = embed.fields[0]
	assert "5771336" in field["name"]
	assert "527532506153615360" in field["value"]
	assert "850996190282776577" in field["value"]
	assert "seed" in field["value"]


def test_identity_conflicts_reports_unknown_current_owner_without_erroring(monkeypatch):
	admin = _load_admin_module(monkeypatch)

	async def _open_conflicts():
		return [dict(profile_id=42, current_owner=None, claims=[dict(user_id=1, source="seed", noticed_at=1)])]

	monkeypatch.setattr(identity, "open_conflicts", _open_conflicts)
	ctx = _FakeCtx(access_level=_Perms.MODERATOR)

	asyncio.run(admin.identity_conflicts(ctx))  # must not raise

	embed = ctx.replies[0].embed
	assert embed.fields[0]["value"]  # some "none known" placeholder, not blank


# ─── the player /link command (bot/commands/identity.py) ─────────────────
# The ONLY way a player in a brand-new community gets linked without an admin,
# so its refusals are tested as hard as its happy path: a wrong id must never
# write, and "your id is wrong" must never be conflated with "the AoE2 service
# is down". Loaded by file path with the same fake-nextcord harness as admin.py
# above; fetch_profile is faked per test so nothing here touches the network.

def _fake_fetch_profile(monkeypatch, result):
	""" Stand in for bot.lobby.api.fetch_profile. Returns the list of profile
	ids it was called with, so a test can assert it was NOT called at all. """
	calls = []

	async def _fetch(profile_id):
		calls.append(profile_id)
		return result

	monkeypatch.setattr(lobby_api, "fetch_profile", _fetch)
	return calls


def _embed_text(embed):
	""" Everything the player can actually read, flattened -- copy assertions
	shouldn't care whether a line lives in the description or a field. """
	parts = [embed.title or "", embed.description or ""]
	for f in embed.fields:
		parts += [f["name"] or "", f["value"] or ""]
	return "\n".join(parts)


def _only_reply(ctx):
	""" The single reply the command produced, asserted private.

	Every personal answer -- instructions, the current-link view, both refusals
	-- is ephemeral: none of them is anyone else's business, and the failures in
	particular must not land in the channel. ctx.reply(ephemeral=True) is what
	survives a defer; ctx.error() drops the flag on its post-defer branch, which
	/link reaches whenever the profile API is slow. """
	assert len(ctx.replies) == 1, f"expected exactly one reply, got {len(ctx.replies)}"
	assert ctx.replies[0].ephemeral is True, "personal /link answers must be ephemeral"
	return ctx.replies[0]


def test_link_shows_current_link_when_already_linked(monkeypatch):
	link_mod = _load_link_module(monkeypatch)
	_setup(monkeypatch)
	asyncio.run(identity.learn(612690, 222, "self", aoe2_name="ddk220"))
	ctx = _FakeCtx(author=_FakeMember(222))

	asyncio.run(link_mod.link(ctx))

	text = _embed_text(_only_reply(ctx).embed)
	assert "612690" in text
	assert "ddk220" in text, "the observed in-game name lets them recognise the link"
	assert "https://www.aoe2insights.com/user/relic/612690/" in text
	assert "admin" in text.lower(), "must say only an admin can change it"
	assert ctx.successes == []


def test_link_lists_every_profile_a_multi_account_player_owns(monkeypatch):
	""" Multi-account players exist in the flagship data (see relink's
	`additional` flag) -- showing only one of their profiles would look like the
	others had been lost. """
	link_mod = _load_link_module(monkeypatch)
	_setup(monkeypatch)
	asyncio.run(identity.learn(612690, 222, "self", aoe2_name="ddk220"))
	asyncio.run(identity.learn(209754, 222, "manual", aoe2_name="Fenrir"))
	ctx = _FakeCtx(author=_FakeMember(222))

	asyncio.run(link_mod.link(ctx))

	text = _embed_text(ctx.replies[0].embed)
	assert "612690" in text and "209754" in text
	assert "ddk220" in text and "Fenrir" in text


def test_link_shows_a_linked_profile_with_no_observed_name(monkeypatch):
	""" aoe2_name is an observation, not a guarantee -- a seeded row can have
	none, and the view must still render (id + verify URL) rather than blank. """
	link_mod = _load_link_module(monkeypatch)
	_setup(monkeypatch)
	asyncio.run(identity.learn(612690, 222, "seed"))  # no aoe2_name
	ctx = _FakeCtx(author=_FakeMember(222))

	asyncio.run(link_mod.link(ctx))

	text = _embed_text(ctx.replies[0].embed)
	assert "612690" in text
	assert "https://www.aoe2insights.com/user/relic/612690/" in text


def test_link_ignores_a_supplied_id_when_already_linked(monkeypatch):
	""" Spec section 2: an already-linked player's /link is VIEW ONLY whether or
	not they passed an id -- players can never change their own link. Passing one
	is not an error either; they just get shown what they are linked to. And the
	API is never even consulted, since nothing can be written from this branch. """
	link_mod = _load_link_module(monkeypatch)
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(612690, 222, "self", aoe2_name="ddk220"))
	before = [dict(r) for r in fake.identities]
	calls = _fake_fetch_profile(monkeypatch, ("ok", {"profile_id": 111, "name": "SomeoneElse"}))
	ctx = _FakeCtx(author=_FakeMember(222))

	asyncio.run(link_mod.link(ctx, profile_id=111))

	assert fake.identities == before, "an already-linked player's /link must never write"
	assert fake.identity_conflicts == []
	assert calls == [], "nothing can be written here, so don't even call the API"
	text = _embed_text(_only_reply(ctx).embed)
	assert "612690" in text, "they are shown what they ARE linked to"
	assert ctx.successes == []


def test_link_gives_instructions_when_unlinked_and_no_id(monkeypatch):
	link_mod = _load_link_module(monkeypatch)
	fake = _setup(monkeypatch)
	calls = _fake_fetch_profile(monkeypatch, ("ok", {"profile_id": 1, "name": "x"}))
	ctx = _FakeCtx(author=_FakeMember(222))

	asyncio.run(link_mod.link(ctx))

	assert fake.identities == [], "instructions are a read -- nothing is written"
	assert fake.identity_conflicts == []
	assert calls == []
	text = _embed_text(_only_reply(ctx).embed)
	assert "aoe2insights.com" in text, "must say concretely where to find the number"
	assert "/link" in text, "must say what to run once they have it"
	assert str(link_mod.EXAMPLE_PROFILE_ID) in text, "a worked example, not just a description"
	assert "https://www.aoe2insights.com/user/relic/612690/" in text, \
		"show the URL SHAPE -- '/user/<id>/' and '/user/relic/<id>/' both circulate and only " \
		"the second is real, so 'the number in the address' alone is not enough to act on"

	lower = text.lower()
	assert lower.index("search your in-game name") < lower.index("lobby"), \
		"lead with the method that works in ANY community: a name search on a public site"
	assert "if your server posts lobby cards" in lower, \
		"lobby cards come from LobbyBOT, a third-party bot the target community does not run " \
		"-- state the method conditionally or it describes something they will never find"


def test_link_rejects_an_unknown_profile_id_without_writing(monkeypatch):
	link_mod = _load_link_module(monkeypatch)
	fake = _setup(monkeypatch)
	calls = _fake_fetch_profile(monkeypatch, ("not_found", None))
	ctx = _FakeCtx(author=_FakeMember(222))

	asyncio.run(link_mod.link(ctx, profile_id=999999999))

	embed = _only_reply(ctx).embed
	assert embed.title == "Couldn't find that profile", "a human heading, not an exception class name"
	text = _embed_text(embed)
	assert "`999999999`" in text, "name the id they actually typed"
	assert "aoe2insights.com" in text or "/link" in text, "point back at the instructions"

	assert calls == [999999999], "the id is validated BEFORE anything is written"
	assert fake.identities == []
	assert fake.identity_conflicts == []
	assert ctx.successes == []


def test_link_reports_a_transient_api_failure_without_writing(monkeypatch):
	""" A service outage is not the player's fault and must not read like their
	id is wrong -- they would go hunting for a new number that was fine. """
	link_mod = _load_link_module(monkeypatch)
	fake = _setup(monkeypatch)
	_fake_fetch_profile(monkeypatch, ("unavailable", None))
	ctx = _FakeCtx(author=_FakeMember(222))

	asyncio.run(link_mod.link(ctx, profile_id=612690))

	embed = _only_reply(ctx).embed
	assert embed.title == "Couldn't check that just now"
	text = _embed_text(embed)
	assert "612690" not in text, "an outage says nothing about their id -- don't blame it"
	assert "again" in text.lower(), "tell them to retry"

	assert fake.identities == []
	assert fake.identity_conflicts == []
	assert ctx.successes == []


def test_link_failures_are_answers_not_raised_exceptions(monkeypatch):
	""" run_slash_coro renders any bot.Exc.PubobotException with
	title=e.__class__.__name__, so raising from here would greet a first-time
	player with a red "ValueError" -- and its post-defer branch sends without
	ephemeral=True, so on the slow path (fetch_profile waits up to 15s,
	run_slash defers at 2.5s) that ValueError would land in the channel for
	everyone. None of these three is an internal error; each is an ordinary,
	private answer. """
	link_mod = _load_link_module(monkeypatch)
	cases = [
		("an id that does not exist", ("not_found", None), 999999999, None),
		("an unreachable profile service", ("unavailable", None), 612690, None),
		("a profile another player owns", ("ok", {"profile_id": 612690, "name": "SomeoneElse"}), 612690, 999),
		("an id that cannot exist", None, -5, None),
	]
	for label, result, profile_id, other_owner in cases:
		fake = _setup(monkeypatch)
		if other_owner is not None:
			asyncio.run(identity.learn(profile_id, other_owner, "learned", aoe2_name="SomeoneElse"))
		if result is not None:
			_fake_fetch_profile(monkeypatch, result)
		ctx = _FakeCtx(author=_FakeMember(222))

		try:
			asyncio.run(link_mod.link(ctx, profile_id=profile_id))
		except link_mod.bot.Exc.PubobotException as e:
			raise AssertionError(
				f"{label}: /link raised {e.__class__.__name__}, which run_slash_coro would "
				f"render as the embed title"
			)

		embed = _only_reply(ctx).embed
		assert embed.title and embed.title not in ("ValueError", "RuntimeError", "Error"), \
			f"{label}: the heading a player reads must be human, got {embed.title!r}"
		assert ctx.successes == [], f"{label}: nothing succeeded"
		if other_owner is None:
			assert fake.identities == [], f"{label}: nothing was written"
		else:
			assert fake.identities[0]["user_id"] == other_owner, f"{label}: the existing binding stands"


def test_link_binds_a_valid_profile_and_echoes_the_ingame_name(monkeypatch):
	link_mod = _load_link_module(monkeypatch)
	fake = _setup(monkeypatch)
	calls = _fake_fetch_profile(monkeypatch, ("ok", {"profile_id": 612690, "name": "ddk220"}))
	ctx = _FakeCtx(author=_FakeMember(222))

	asyncio.run(link_mod.link(ctx, profile_id=612690))

	assert calls == [612690]
	row = fake.identities[0]
	assert row["profile_id"] == 612690
	assert row["user_id"] == 222
	assert row["confidence"] == "self", "a player's own claim is the `self` tier"
	assert row["aoe2_name"] == "ddk220", "validation refreshes the observed in-game name"
	assert fake.identity_conflicts == []

	assert len(ctx.successes) == 1
	msg = ctx.successes[0]
	assert "612690" in msg
	assert "ddk220" in msg, "echo the in-game name so a wrong-but-real id is caught by eye"
	assert "https://www.aoe2insights.com/user/relic/612690/" in msg


def test_link_triggers_the_deduction_solver_for_the_channels_community(monkeypatch):
	""" Spec section 4: a fresh link is a new constraint that can immediately
	resolve this player's teammates, so the solver runs on the spot rather than
	waiting for the next ingest. """
	import bot.identity_solver as identity_solver

	link_mod = _load_link_module(monkeypatch)
	_setup(monkeypatch)
	_fake_fetch_profile(monkeypatch, ("ok", {"profile_id": 612690, "name": "ddk220"}))
	ran = []

	async def _run_for_channel(channel_id):
		ran.append(channel_id)
		return 0

	monkeypatch.setattr(identity_solver, "run_for_channel", _run_for_channel)
	ctx = _FakeCtx(channel_id=4242, author=_FakeMember(222))

	asyncio.run(link_mod.link(ctx, profile_id=612690))

	assert ran == [4242]
	assert len(ctx.successes) == 1, "the solver runs AFTER the reply, and never instead of it"


def test_link_still_replies_when_the_deduction_solver_blows_up(monkeypatch):
	""" run_for_channel swallows its own failures, but the command must not
	depend on that: the link has already landed and the player must be told. """
	import bot.identity_solver as identity_solver

	link_mod = _load_link_module(monkeypatch)
	fake = _setup(monkeypatch)
	_fake_fetch_profile(monkeypatch, ("ok", {"profile_id": 612690, "name": "ddk220"}))

	async def _boom(channel_id):
		raise RuntimeError("solver exploded")

	monkeypatch.setattr(identity_solver, "run_for_channel", _boom)
	ctx = _FakeCtx(author=_FakeMember(222))

	try:
		asyncio.run(link_mod.link(ctx, profile_id=612690))
	except RuntimeError:
		raise AssertionError("a solver failure must never surface as a /link error") from None

	assert fake.identities[0]["user_id"] == 222
	assert len(ctx.successes) == 1


def test_link_succeeds_when_the_validated_profile_has_no_name(monkeypatch):
	""" fetch_profile returns name="" when the API omits it. That must still
	link -- and must not overwrite a previously observed name with an empty
	string, which would blank the display value everywhere it is read. """
	link_mod = _load_link_module(monkeypatch)
	fake = _setup(monkeypatch)
	fake.identities.append(dict(
		profile_id=612690, user_id=None, aoe2_name="ddk220",
		confidence="seed", first_seen_at=1, last_seen_at=1,
	))
	_fake_fetch_profile(monkeypatch, ("ok", {"profile_id": 612690, "name": ""}))
	ctx = _FakeCtx(author=_FakeMember(222))

	asyncio.run(link_mod.link(ctx, profile_id=612690))

	assert fake.identities[0]["user_id"] == 222
	assert fake.identities[0]["aoe2_name"] == "ddk220", "an unobserved name must not blank the stored one"
	assert len(ctx.successes) == 1
	assert "612690" in ctx.successes[0]


def test_link_refuses_a_profile_owned_by_another_player(monkeypatch):
	""" link_self already records the losing claim in identity_conflicts -- the
	command must not record a second one. """
	link_mod = _load_link_module(monkeypatch)
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(612690, 999, "learned", aoe2_name="SomeoneElse"))
	_fake_fetch_profile(monkeypatch, ("ok", {"profile_id": 612690, "name": "SomeoneElse"}))
	ctx = _FakeCtx(author=_FakeMember(222))

	asyncio.run(link_mod.link(ctx, profile_id=612690))

	embed = _only_reply(ctx).embed
	assert embed.title == "That profile is already linked"
	text = _embed_text(embed)
	assert "612690" in text
	assert "admin" in text.lower(), "the only route forward is an admin"

	assert fake.identities[0]["user_id"] == 999, "the existing binding stands"
	assert fake.identities[0]["confidence"] == "learned"
	assert len(fake.identity_conflicts) == 1, "link_self recorded it; the command must not duplicate it"
	assert fake.identity_conflicts[0]["claimed_user_id"] == 222
	assert fake.identity_conflicts[0]["source"] == "self"
	assert ctx.successes == []


def test_link_refuses_an_impossible_profile_id_without_calling_the_api(monkeypatch):
	""" A zero or negative id cannot exist. Verified live 2026-07-30: the API
	answers `-5` with a 400 and `0` with a 404, and fetch_profile maps a 400 to
	"unavailable" -- so without this guard a negative would tell the player the
	SERVICE is broken when it is their number that cannot exist. """
	link_mod = _load_link_module(monkeypatch)
	for bad in (0, -5):
		fake = _setup(monkeypatch)
		calls = _fake_fetch_profile(monkeypatch, ("unavailable", None))
		ctx = _FakeCtx(author=_FakeMember(222))

		asyncio.run(link_mod.link(ctx, profile_id=bad))

		embed = _only_reply(ctx).embed
		assert embed.title == "Couldn't find that profile", \
			f"{bad} is a wrong number, not an outage -- say so"
		text = _embed_text(embed)
		assert f"`{bad}`" in text, "quote back the number they typed, not just any digit"
		assert "couldn't find" in text.lower()
		assert "aoe2insights.com" in text or "/link" in text, "point back at the instructions"

		assert calls == [], "an id that cannot exist is refused without asking the API"
		assert fake.identities == []
		assert ctx.successes == []


def test_link_binds_the_profile_id_the_api_confirmed(monkeypatch):
	""" fetch_profile parses the id back out of the profile body, and THAT is
	what gets written, echoed and linked to -- it is what the service says the
	profile is. They agree in every normal case; if they ever disagreed (an
	alias, a redirect), writing the typed number would bind an id nothing had
	validated, while the player was shown a page proving the opposite. """
	link_mod = _load_link_module(monkeypatch)
	fake = _setup(monkeypatch)
	_fake_fetch_profile(monkeypatch, ("ok", {"profile_id": 209754, "name": "Fenrir"}))
	ctx = _FakeCtx(author=_FakeMember(222))

	asyncio.run(link_mod.link(ctx, profile_id=612690))

	assert fake.identities[0]["profile_id"] == 209754, "the validated id is the one stored"
	msg = ctx.successes[0]
	assert "209754" in msg and "https://www.aoe2insights.com/user/relic/209754/" in msg
	assert "612690" not in msg, "don't echo an id the API never confirmed"
