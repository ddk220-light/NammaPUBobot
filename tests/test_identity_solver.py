# -*- coding: utf-8 -*-
"""The identity deduction solver — bot/identity_solver.py.

Two halves, tested two ways:

  deduce()            — pure scoring over already-assembled rosters. No DB, no
                        fakes, just data in and bindings out.
  run_for_community() — the async wrapper, against a fake adapter (same pattern
                        as test_identity.py / test_community.py: no MySQL).

No pytest-asyncio anywhere in this repo — an `async def test_` would be
collected and SILENTLY SKIPPED, so every async call here goes through
asyncio.run() from a plain sync test.
"""
import asyncio

import bot.identity_solver as solver


def _m(profiles, users):
	""" One paired match: {profile_id: won} on the replay side, {user_id: won}
	on the Discord side. """
	return {"profiles": dict(profiles), "users": dict(users)}


# Five players with stable identities across the fixtures below: profile 101 is
# really user 10, 102 is 20, and so on.
A, B, C, D, E = 10, 20, 30, 40, 50
PA, PB, PC, PD, PE = 101, 102, 103, 104, 105


def _shuffled_2v2_season():
	""" Three 2v2 games in which every pair of players is split up at least
	once. Each profile's true owner is on its side in all three; every other
	user only lands there once. This is the shape real varied games have, and
	the reason the solver needs no anchor player. """
	return [
		_m({PA: True, PB: True, PC: False, PD: False}, {A: True, B: True, C: False, D: False}),
		_m({PA: True, PC: True, PB: False, PD: False}, {A: True, C: True, B: False, D: False}),
		_m({PA: True, PD: True, PB: False, PC: False}, {A: True, D: True, B: False, C: False}),
	]


# ─── deduce: the pure core ──────────────────────────────────────────────

def test_unanimous_evidence_binds_a_profile():
	bindings = solver.deduce(_shuffled_2v2_season(), {})

	assert {pid: b[0] for pid, b in bindings.items()} == {PA: A, PB: B, PC: C, PD: D}
	user_id, games, ratio, margin = bindings[PA]
	assert (user_id, games, ratio) == (A, 3, 1.0)
	assert round(margin, 2) == 0.67   # 3 hits for A, 1 for the best rival, over 3 games


def test_a_single_game_never_binds():
	one_game = [_m({PA: True, PB: False}, {A: True, B: False})]

	# Perfect, unambiguous evidence — and still refused: one game is one
	# mispairing away from a wrong global binding.
	assert solver.deduce(one_game, {}) == {}
	# Two is still under the floor.
	assert solver.deduce(one_game * 2, {}) == {}
	# ...and it is the floor doing the refusing, nothing else about the fixture.
	assert solver.deduce(one_game, {}, min_games=1)[PA][0] == A


def test_fifty_fifty_evidence_does_not_bind():
	# Two players who never get split up are indistinguishable to co-occurrence
	# scoring, however many games they play: both score 1.00, so margin is 0.
	fixed_teams = [_m({PA: True, PB: True, PC: False, PD: False},
					  {A: True, B: True, C: False, D: False})] * 4

	assert solver.deduce(fixed_teams, {}) == {}


def test_roster_size_mismatch_match_is_skipped():
	# A fifth profile in the game (a lobby guest) against four bot players: the
	# two rosters describe different sets of people, so nothing in that match
	# can be trusted — not even the four players who do line up. Fired on 6 of
	# 1107 real paired matches.
	season = _shuffled_2v2_season()
	with_a_guest = _m({PA: True, PD: True, PB: False, PC: False, 999: False},
					  {A: True, D: True, B: False, C: False})

	assert solver.deduce([*season[:2], with_a_guest], {}) == {}
	# The same three games with the guest gone bind fine, so it is the skip that
	# refused above (two usable games, under the floor), not the fixture.
	assert solver.deduce(season, {})[PA][0] == A


def test_within_match_exclusion_prevents_double_attribution():
	# A and B win together in all three games and are never split up, so on raw
	# co-occurrence they tie for profile PB. But PA is already known to be A,
	# and A cannot also be playing PB in the same game — so B is the only
	# candidate left.
	same_side = [_m({PA: True, PB: True, PC: False, PD: False},
					{A: True, B: True, C: False, D: False})] * 3

	bindings = solver.deduce(same_side, {PA: A})

	assert bindings[PB] == (B, 3, 1.0, 1.0)
	# Without the exclusion A ties B at 3/3 and nothing separates them.
	assert solver.deduce(same_side, {}) == {}


def test_a_multi_account_user_is_not_broken_by_the_exclusion():
	# A owns PA (already known) and a second account, 202. The exclusion is
	# per-match: it may only bar A from the OTHER profiles of a game A is
	# already accounted for in, never from profiles in games where A's known
	# account is absent. Five real users own 2-3 profiles.
	alt = 202
	on_the_known_account = [_m({PA: True, PC: False}, {A: True, C: False})] * 3
	on_the_alt = [
		_m({alt: True, PC: True, PD: False, PB: False}, {A: True, C: True, D: False, B: False}),
		_m({alt: True, PD: True, PC: False, PB: False}, {A: True, D: True, C: False, B: False}),
		_m({alt: True, PB: True, PC: False, PD: False}, {A: True, B: True, C: False, D: False}),
	]

	bindings = solver.deduce([*on_the_known_account, *on_the_alt], {PA: A})

	assert bindings[alt][0] == A      # the second account is A's too
	assert PA not in bindings         # already known, never re-derived


def test_contradicting_games_drop_ratio_below_the_floor():
	# Four games with A on PA's side, one where somebody else played A's slot:
	# 4/5 = 0.80, under the 0.90 floor.
	agreeing = [_m({PA: True, PB: False}, {A: True, B: False})] * 4
	contradicting = [_m({PA: True, PB: False}, {B: True, A: False})]
	matches = [*agreeing, *contradicting]

	assert solver.deduce(matches, {}) == {}
	# It is the ratio floor that refused, not the margin: 4-1 over 5 is 0.60.
	relaxed = solver.deduce(matches, {}, min_ratio=0.75)
	assert relaxed[PA][0] == A
	assert round(relaxed[PA][2], 2) == 0.80
	assert round(relaxed[PA][3], 2) == 0.60


def test_already_known_profiles_are_not_rebound():
	# PA is bound; the run must not re-derive (and so never re-write) it, while
	# still resolving everything else in the same pass.
	bindings = solver.deduce(_shuffled_2v2_season(), {PA: A})

	assert PA not in bindings
	assert {pid: b[0] for pid, b in bindings.items()} == {PB: B, PC: C, PD: D}


def test_output_is_deterministic_under_input_reordering():
	matches = _shuffled_2v2_season()
	reordered = [
		_m(dict(reversed(list(m["profiles"].items()))), dict(reversed(list(m["users"].items()))))
		for m in reversed(matches)
	]

	# Same pairs AND the same order — dict equality would not catch an
	# iteration-order dependence, list-of-items equality does.
	assert list(solver.deduce(matches, {}).items()) == list(solver.deduce(reordered, {}).items())


def test_a_tie_breaks_on_the_lower_user_id():
	# With the margin floor lifted, an exact tie must still resolve the same way
	# on every run — never on whichever user_id the dict happened to yield first.
	tied = [_m({PA: True, PB: False}, {B: True, A: True})] * 3
	tied_other_order = [_m({PA: True, PB: False}, {A: True, B: True})] * 3

	assert solver.deduce(tied, {}, min_margin=0.0)[PA][0] == A          # A(10) < B(20)
	assert solver.deduce(tied_other_order, {}, min_margin=0.0)[PA][0] == A


def test_the_calibrated_thresholds_sit_in_the_real_gap():
	# Production calibration over 1107 paired matches (see the module docstring):
	# accepted bindings ran margin 0.50-0.78 from a cold start and 0.70-1.00 with
	# the curated mapping loaded, while nothing refused on margin scored above
	# 0.33. MIN_MARGIN=0.50 sits in that gap; these two fixtures stand on either
	# side of it, at the module's real constants.
	assert (solver.MIN_GAMES, solver.MIN_RATIO, solver.MIN_MARGIN) == (3, 0.90, 0.50)

	# margin 0.75 — a four-game season where A never repeats a partner, i.e.
	# the real accepted band's lower edge.
	accepted = [
		_m({PA: True, PB: True, PC: False, PD: False}, {A: True, B: True, C: False, D: False}),
		_m({PA: True, PC: True, PD: False, PE: False}, {A: True, C: True, D: False, E: False}),
		_m({PA: True, PD: True, PB: False, PE: False}, {A: True, D: True, B: False, E: False}),
		_m({PA: True, PE: True, PB: False, PC: False}, {A: True, E: True, B: False, C: False}),
	]
	assert solver.deduce(accepted, {})[PA][0] == A
	assert round(solver.deduce(accepted, {})[PA][3], 2) == 0.75

	# margin 0.33 — the strongest rejected case in the real data: A's partner
	# repeats, so two users are nearly indistinguishable.
	rejected = [
		_m({PA: True, PB: True, PC: False, PD: False}, {A: True, B: True, C: False, D: False}),
		_m({PA: True, PB: True, PC: False, PD: False}, {A: True, B: True, C: False, D: False}),
		_m({PA: True, PC: True, PB: False, PD: False}, {A: True, C: True, B: False, D: False}),
	]
	assert PA not in solver.deduce(rejected, {})
	assert round(solver.deduce(rejected, {}, min_margin=0.0)[PA][3], 2) == 0.33


# ─── run_for_community: the async wrapper ───────────────────────────────

class FakeDb:
	""" Answers the solver's three reads by the table each one names. """

	def __init__(self, replay_rows=(), discord_rows=(), known_rows=(), channel_for_match=None):
		self.replay_rows = [dict(r) for r in replay_rows]
		self.discord_rows = [dict(r) for r in discord_rows]
		self.known_rows = [dict(r) for r in known_rows]
		self.channel_for_match = dict(channel_for_match or {})
		self.queries = []

	async def select_one(self, columns, table, where=None):
		assert table == "matches" and columns == ["channel_id"]
		channel_id = self.channel_for_match.get((where or {}).get("match_id"))
		return None if channel_id is None else dict(channel_id=channel_id)

	async def fetchall(self, sql, params=None):
		self.queries.append((sql, list(params or [])))
		if "rs_player_games" in sql:
			return [dict(r) for r in self.replay_rows]
		if "match_players" in sql:
			return [dict(r) for r in self.discord_rows]
		if "identities" in sql:
			return [dict(r) for r in self.known_rows]
		raise AssertionError(f"unexpected solver query: {sql}")


class FakeIdentity:
	def __init__(self, fail_for=()):
		self.fail_for = set(fail_for)
		self.learned = []

	async def learn(self, profile_id, user_id, source, aoe2_name=None):
		if profile_id in self.fail_for:
			raise RuntimeError("db down")
		self.learned.append((profile_id, user_id, source, aoe2_name))


def _fake_db_for(matches, known=None, winner_overrides=None):
	""" Turn `deduce`-shaped matches back into the raw join rows the wrapper
	reads, so the wrapper has to do the team==winner comparison itself. """
	replay, discord = [], []
	for match_id, m in enumerate(matches, start=1):
		replay += [
			dict(match_id=match_id, profile_id=pid, winner=1 if won else 0)
			for pid, won in m["profiles"].items()
		]
		winner = (winner_overrides or {}).get(match_id, 0)
		discord += [
			dict(match_id=match_id, user_id=uid, team=(0 if won else 1), winner=winner)
			for uid, won in m["users"].items()
		]
	known_rows = [dict(profile_id=pid, user_id=uid) for pid, uid in (known or {}).items()]
	return FakeDb(replay, discord, known_rows)


def _setup(monkeypatch, fake_db, fake_identity=None):
	fake_identity = fake_identity or FakeIdentity()
	monkeypatch.setattr(solver, "db", fake_db)
	monkeypatch.setattr(solver, "identity", fake_identity)
	return fake_identity


def test_run_for_community_writes_a_learned_binding_per_deduction(monkeypatch):
	fake_db = _fake_db_for(_shuffled_2v2_season())
	fake_identity = _setup(monkeypatch, fake_db)

	written = asyncio.run(solver.run_for_community(7))

	assert written == 4
	assert sorted(fake_identity.learned) == [
		(PA, A, "learned", None), (PB, B, "learned", None),
		(PC, C, "learned", None), (PD, D, "learned", None),
	]
	# The two per-community reads are scoped to the community asked about; the
	# identities read is global (a binding earned anywhere holds everywhere).
	assert [params for _sql, params in fake_db.queries] == [[7], [7], []]


def test_run_for_community_skips_a_match_whose_winner_is_unknown(monkeypatch):
	# 8 rows in production have matches.winner NULL. Which side won is then
	# unknowable, so such a match must contribute no evidence at all — here that
	# drops the season to two usable games, under the floor.
	fake_db = _fake_db_for(_shuffled_2v2_season(), winner_overrides={3: None})
	fake_identity = _setup(monkeypatch, fake_db)

	assert asyncio.run(solver.run_for_community(7)) == 0
	assert fake_identity.learned == []


def test_run_for_community_keeps_writing_after_one_learn_fails(monkeypatch):
	fake_db = _fake_db_for(_shuffled_2v2_season())
	fake_identity = _setup(monkeypatch, fake_db, FakeIdentity(fail_for=[PB]))

	written = asyncio.run(solver.run_for_community(7))

	assert written == 3
	assert sorted(pid for pid, *_ in fake_identity.learned) == [PA, PC, PD]


def test_run_for_community_never_learns_a_null_user_id(monkeypatch):
	# identity_conflicts' primary key forbids a NULL claimed_user_id, so a
	# roster row with no user_id must never reach learn(). The match it belongs
	# to is unusable for the same reason a NULL winner is: the Discord roster we
	# can read is not the roster that played.
	fake_db = _fake_db_for(_shuffled_2v2_season())
	for row in fake_db.discord_rows:
		if row["user_id"] == D:
			row["user_id"] = None
	fake_identity = _setup(monkeypatch, fake_db)

	assert asyncio.run(solver.run_for_community(7)) == 0
	assert fake_identity.learned == []


def test_run_for_community_with_no_paired_matches_writes_nothing(monkeypatch):
	fake_identity = _setup(monkeypatch, FakeDb())

	assert asyncio.run(solver.run_for_community(7)) == 0
	assert fake_identity.learned == []


# ─── the trigger entry points: never raise, skip quietly ────────────────

class FakeCommunity:
	def __init__(self, mapping=None, boom=False):
		self.mapping = dict(mapping or {})
		self.boom = boom
		self.asked = []

	async def community_for_channel(self, channel_id):
		self.asked.append(channel_id)
		if self.boom:
			raise RuntimeError("db down")
		return self.mapping.get(channel_id)


def test_run_for_channel_skips_an_unenrolled_channel_quietly(monkeypatch):
	fake_db = _fake_db_for(_shuffled_2v2_season())
	fake_identity = _setup(monkeypatch, fake_db)
	monkeypatch.setattr(solver, "community", FakeCommunity())

	assert asyncio.run(solver.run_for_channel(555)) == 0
	assert fake_db.queries == []      # nothing loaded, nothing deduced
	assert fake_identity.learned == []


def test_run_for_channel_never_raises_into_its_caller(monkeypatch):
	# It is called from replay ingest and from user-facing commands: a solver
	# failure must never break either.
	_setup(monkeypatch, FakeDb())
	monkeypatch.setattr(solver, "community", FakeCommunity(boom=True))

	assert asyncio.run(solver.run_for_channel(555)) == 0


def test_run_for_match_resolves_the_community_from_the_match(monkeypatch):
	fake_db = _fake_db_for(_shuffled_2v2_season())
	fake_db.channel_for_match = {4242: 555}
	fake_identity = _setup(monkeypatch, fake_db)
	monkeypatch.setattr(solver, "community", FakeCommunity({555: 7}))

	assert asyncio.run(solver.run_for_match(4242)) == 4
	assert len(fake_identity.learned) == 4


def test_run_for_match_is_a_no_op_for_an_unknown_match(monkeypatch):
	fake_db = _fake_db_for(_shuffled_2v2_season())
	fake_identity = _setup(monkeypatch, fake_db)
	community_fake = FakeCommunity({555: 7})
	monkeypatch.setattr(solver, "community", community_fake)

	assert asyncio.run(solver.run_for_match(9999)) == 0
	assert community_fake.asked == []
	assert fake_identity.learned == []
