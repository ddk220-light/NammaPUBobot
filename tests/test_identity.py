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
import sys
import types
from enum import IntEnum
from pathlib import Path

import bot.identity as identity
import bot.community as community


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


def test_learn_does_not_record_a_block_by_a_non_manual_row(monkeypatch):
	""" A `seed` write outranked by an existing `learned` row is the
	resolver's ordinary precedence order working as intended, not a human
	correction discarding someone else's claim -- only a block by `manual`
	is recorded. """
	fake = _setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "learned"))

	asyncio.run(identity.learn(111, 999, "seed"))

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
	] == [(111, 222, "unlinked")]


def test_relink_additional_keeps_the_members_other_profiles(monkeypatch):
	""" Multi-account players are real (the flagship community has several), so
	`additional` adds a second profile instead of replacing the first. """
	_setup(monkeypatch)
	asyncio.run(identity.learn(111, 222, "learned"))

	asyncio.run(identity.relink(112, 222, additional=True))

	assert sorted(asyncio.run(identity.profiles_for_users([222]))[222]) == [111, 112]


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

	def __init__(self, access_level=_Perms.ADMIN, channel_id=1):
		self.access_level = access_level
		self.qc = types.SimpleNamespace(gt=lambda s: s)
		self.channel = types.SimpleNamespace(id=channel_id)
		self.replies = []
		self.successes = []

	def check_perms(self, req_perms):
		if self.access_level.value < req_perms.value:
			raise _PermsDenied("insufficient permissions")

	async def reply(self, content=None, embed=None):
		self.replies.append(types.SimpleNamespace(content=content, embed=embed))

	async def success(self, content=None, title=None):
		self.successes.append(content)


def _load_admin_module(monkeypatch):
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

	path = Path(__file__).resolve().parent.parent / "bot" / "commands" / "admin.py"
	spec = importlib.util.spec_from_file_location("admin_standalone_test", path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


# ─── identity link ───────────────────────────────────────────────────────

def test_identity_link_requires_admin_permission(monkeypatch):
	admin = _load_admin_module(monkeypatch)
	calls = []
	monkeypatch.setattr(identity, "learn", lambda *a, **k: calls.append((a, k)))
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

	async def _user_for_profile(profile_id):
		return None

	learn_calls = []

	async def _learn(profile_id, user_id, source, aoe2_name=None):
		learn_calls.append((profile_id, user_id, source))

	monkeypatch.setattr(identity, "user_for_profile", _user_for_profile)
	monkeypatch.setattr(identity, "learn", _learn)
	ctx = _FakeCtx()
	member = _FakeMember(222, name="Fenrir")

	asyncio.run(admin.identity_link(ctx, member, 111))

	assert learn_calls == [(111, 222, "manual")]
	assert len(ctx.successes) == 1
	assert "111" in ctx.successes[0] and "Fenrir" in ctx.successes[0]


def test_identity_link_relinking_the_same_owner_needs_no_force(monkeypatch):
	admin = _load_admin_module(monkeypatch)

	async def _user_for_profile(profile_id):
		return 222  # already owned by the same member we're linking

	learn_calls = []

	async def _learn(profile_id, user_id, source, aoe2_name=None):
		learn_calls.append((profile_id, user_id, source))

	monkeypatch.setattr(identity, "user_for_profile", _user_for_profile)
	monkeypatch.setattr(identity, "learn", _learn)
	ctx = _FakeCtx()
	member = _FakeMember(222)

	asyncio.run(admin.identity_link(ctx, member, 111, force=False))

	assert learn_calls == [(111, 222, "manual")]


def test_identity_link_refuses_to_steal_a_different_owner_without_force(monkeypatch):
	admin = _load_admin_module(monkeypatch)

	async def _user_for_profile(profile_id):
		return 999  # owned by someone else

	learn_calls = []

	async def _learn(*a, **k):
		learn_calls.append((a, k))

	monkeypatch.setattr(identity, "user_for_profile", _user_for_profile)
	monkeypatch.setattr(identity, "learn", _learn)
	ctx = _FakeCtx()
	member = _FakeMember(222)

	try:
		asyncio.run(admin.identity_link(ctx, member, 111, force=False))
	except admin.bot.Exc.ValueError as e:
		assert "999" in str(e)
	else:
		raise AssertionError("identity_link must refuse to reassign a profile without force")

	assert learn_calls == []


def test_identity_link_force_reassigns_from_a_different_owner(monkeypatch):
	admin = _load_admin_module(monkeypatch)

	async def _user_for_profile(profile_id):
		return 999

	learn_calls = []

	async def _learn(profile_id, user_id, source, aoe2_name=None):
		learn_calls.append((profile_id, user_id, source))

	monkeypatch.setattr(identity, "user_for_profile", _user_for_profile)
	monkeypatch.setattr(identity, "learn", _learn)
	ctx = _FakeCtx()
	member = _FakeMember(222)

	asyncio.run(admin.identity_link(ctx, member, 111, force=True))

	assert learn_calls == [(111, 222, "manual")]
	assert len(ctx.successes) == 1


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
