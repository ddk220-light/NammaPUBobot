# -*- coding: utf-8 -*-
"""The identity deduction solver — bot/identity_solver.py.

Three halves, tested three ways:

  deduce()            — pure scoring over already-assembled rosters, including
                        the two robustness rules. No DB, no fakes, just data in
                        and (bindings, review) out.
  _build_matches()    — pure assembly of the raw join rows, and the four NULLs
                        that make a paired match unusable. Tested directly:
                        going through the wrapper cannot distinguish "the match
                        was dropped" from "everybody lost", so a wrapper-level
                        test passes with the guards deleted.
  run_for_community() — the async wrapper, against a fake adapter (same pattern
                        as test_identity.py / test_community.py: no MySQL).

No pytest-asyncio anywhere in this repo — an `async def test_` would be
collected and SILENTLY SKIPPED, so every async call here goes through
asyncio.run() from a plain sync test.
"""
import asyncio
import types

import bot.identity_solver as solver


def _m(profiles, users):
	""" One paired match: {profile_id: won} on the replay side, {user_id: won}
	on the Discord side. """
	return {"profiles": dict(profiles), "users": dict(users)}


def _bind(*args, **kwargs):
	""" deduce()'s bindings only, for the cases that are not about the review
	list. Every test that CARES about a refusal unpacks both halves. """
	return solver.deduce(*args, **kwargs)[0]


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
	bindings, review = solver.deduce(_shuffled_2v2_season(), {})

	assert {pid: b[0] for pid, b in bindings.items()} == {PA: A, PB: B, PC: C, PD: D}
	assert review == []
	user_id, games, ratio, margin = bindings[PA]
	assert (user_id, games, ratio) == (A, 3, 1.0)
	assert round(margin, 2) == 0.67   # 3 hits for A, 1 for the best rival, over 3 games


def test_a_single_game_never_binds():
	one_game = [_m({PA: True, PB: False}, {A: True, B: False})]

	# Perfect, unambiguous evidence — and still refused: one game is one
	# mispairing away from a wrong global binding.
	assert _bind(one_game, {}) == {}
	# Two is still under the floor.
	assert _bind(one_game * 2, {}) == {}
	# ...and it is the floor doing the refusing, nothing else about the fixture.
	assert _bind(one_game, {}, min_games=1)[PA][0] == A


def test_fifty_fifty_evidence_does_not_bind():
	# Two players who never get split up are indistinguishable to co-occurrence
	# scoring, however many games they play: both score 1.00, so margin is 0.
	fixed_teams = [_m({PA: True, PB: True, PC: False, PD: False},
					  {A: True, B: True, C: False, D: False})] * 4

	bindings, review = solver.deduce(fixed_teams, {})
	# Not even a review entry: this is ordinary insufficient evidence, and there
	# is nothing to tell an admin about it.
	assert (bindings, review) == ({}, [])


def test_roster_size_mismatch_match_is_skipped():
	# A fifth profile in the game (a lobby guest) against four bot players: the
	# two rosters describe different sets of people, so nothing in that match
	# can be trusted — not even the four players who do line up. Fired on 6 of
	# 1107 real paired matches.
	season = _shuffled_2v2_season()
	with_a_guest = _m({PA: True, PD: True, PB: False, PC: False, 999: False},
					  {A: True, D: True, B: False, C: False})

	assert _bind([*season[:2], with_a_guest], {}) == {}
	# The same three games with the guest gone bind fine, so it is the skip that
	# refused above (two usable games, under the floor), not the fixture.
	assert _bind(season, {})[PA][0] == A


def test_contradicting_games_drop_ratio_below_the_floor():
	# Four games with A on PA's side, one where somebody else played A's slot:
	# 4/5 = 0.80, under the 0.90 floor.
	agreeing = [_m({PA: True, PB: False}, {A: True, B: False})] * 4
	contradicting = [_m({PA: True, PB: False}, {B: True, A: False})]
	matches = [*agreeing, *contradicting]

	assert _bind(matches, {}) == {}
	# It is the ratio floor that refused, not the margin: 4-1 over 5 is 0.60.
	relaxed = _bind(matches, {}, min_ratio=0.75)
	assert relaxed[PA][0] == A
	assert round(relaxed[PA][2], 2) == 0.80
	assert round(relaxed[PA][3], 2) == 0.60


def test_already_known_profiles_are_not_rebound():
	# PA is bound; the run must not re-derive (and so never re-write) it, while
	# still resolving everything else in the same pass.
	bindings = _bind(_shuffled_2v2_season(), {PA: A})

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
	assert list(_bind(matches, {}).items()) == list(_bind(reordered, {}).items())


def test_a_tie_breaks_on_the_lower_user_id():
	# With the margin floor lifted, an exact tie must still resolve the same way
	# on every run — never on whichever user_id the dict happened to yield first.
	tied = [_m({PA: True, PB: False}, {B: True, A: True})] * 3
	tied_other_order = [_m({PA: True, PB: False}, {A: True, B: True})] * 3

	assert _bind(tied, {}, min_margin=0.0)[PA][0] == A          # A(10) < B(20)
	assert _bind(tied_other_order, {}, min_margin=0.0)[PA][0] == A


def test_the_calibrated_thresholds_sit_in_the_real_gap():
	# Production calibration over 1107 paired matches (see the module docstring):
	# accepted bindings ran margin 0.50-0.78 from a cold start and 0.70-1.00 with
	# the curated mapping loaded, while nothing refused on margin scored above
	# 0.33 ONCE ratio >= 0.90 was already cleared. These two fixtures stand on
	# either side of that cut, at the module's real constants.
	assert (solver.MIN_GAMES, solver.MIN_RATIO, solver.MIN_MARGIN) == (3, 0.90, 0.50)

	# margin 0.75 — a four-game season where A never repeats a partner, i.e.
	# the real accepted band's lower edge.
	accepted = [
		_m({PA: True, PB: True, PC: False, PD: False}, {A: True, B: True, C: False, D: False}),
		_m({PA: True, PC: True, PD: False, PE: False}, {A: True, C: True, D: False, E: False}),
		_m({PA: True, PD: True, PB: False, PE: False}, {A: True, D: True, B: False, E: False}),
		_m({PA: True, PE: True, PB: False, PC: False}, {A: True, E: True, B: False, C: False}),
	]
	assert _bind(accepted, {})[PA][0] == A
	assert round(_bind(accepted, {})[PA][3], 2) == 0.75

	# margin 0.33 — the strongest rejected case in the real data, among profiles
	# that had already cleared the ratio floor.
	rejected = [
		_m({PA: True, PB: True, PC: False, PD: False}, {A: True, B: True, C: False, D: False}),
		_m({PA: True, PB: True, PC: False, PD: False}, {A: True, B: True, C: False, D: False}),
		_m({PA: True, PC: True, PB: False, PD: False}, {A: True, C: True, B: False, D: False}),
	]
	assert PA not in _bind(rejected, {})
	assert round(_bind(rejected, {}, min_margin=0.0)[PA][3], 2) == 0.33


# ─── the floors are INCLUSIVE: `>=`, not `>` ────────────────────────────

def _at_the_floor_season():
	""" Ten paired 2v2s giving PA ratio EXACTLY 0.90 and margin EXACTLY 0.50 —
	the two floors, hit dead on, with no rounding: A is on PA's side in 9 of 10
	games (9/10 == 0.90) and the best rival B in 4 (5/10 == 0.50).

	Everybody else appears once or twice, so no other profile reaches MIN_GAMES
	and no user collects a second profile — the boundary is the only thing under
	test here. Three real cold-start bindings sit exactly on MIN_MARGIN, so this
	is a live case, not a contrived one. """
	matches = []
	filler = iter(range(9000, 9200))          # fresh profile ids, one use each
	extras = iter(range(600, 700))            # fresh user ids, one use each

	def game(winner_users, loser_users):
		# PA always wins; every other profile in the game is a fresh id, so the
		# roster sizes agree and nothing else can accumulate MIN_GAMES.
		profiles = {PA: True, next(filler): True}
		profiles.update({next(filler): False, next(filler): False})
		users = {u: True for u in winner_users}
		users.update({u: False for u in loser_users})
		return _m(profiles, users)

	# 4 games where A wins alongside B -> B ends on 4.
	for _ in range(4):
		matches.append(game([A, B], [next(extras), next(extras)]))
	# 5 games where A wins alongside somebody new -> each of those ends on 1.
	for _ in range(5):
		matches.append(game([A, next(extras)], [next(extras), next(extras)]))
	# 1 game PA wins with A on the LOSING side -> A ends on 9 of 10.
	matches.append(game([next(extras), next(extras)], [A, next(extras)]))
	return matches


def test_a_profile_exactly_on_both_floors_binds():
	# Pins `ratio >= min_ratio` and `margin >= min_margin` as INCLUSIVE. Both
	# mutants (`>=` -> `>`) refuse this fixture and turn this test red.
	matches = _at_the_floor_season()
	bindings, review = solver.deduce(matches, {})

	assert bindings[PA][0] == A
	assert bindings[PA][2] == solver.MIN_RATIO     # exactly, not approximately
	assert bindings[PA][3] == solver.MIN_MARGIN
	assert review == []


def test_a_profile_one_epsilon_below_either_floor_is_refused():
	# The same evidence against a floor raised by one epsilon — which is the
	# same comparison as evidence one epsilon below the real floor, and cannot
	# be built with small integer counts. Pins that the floors bite at all.
	matches = _at_the_floor_season()

	assert _bind(matches, {}, min_ratio=solver.MIN_RATIO + 1e-9) == {}
	assert _bind(matches, {}, min_margin=solver.MIN_MARGIN + 1e-9) == {}


# ─── rule 1: a conclusion may not rest on the existing bindings ─────────

def test_a_conclusion_that_needs_the_exclusion_is_refused_for_review():
	# A and B win together in all three games and are never split up, so on raw
	# co-occurrence they tie for PB. The `taken` exclusion would break the tie
	# using the stored PA -> A binding, naming B at a maximal 1.00/1.00.
	#
	# That is exactly the shape a single wrong `known` entry exploits: if PA
	# really belonged to B, the exclusion would remove the TRUE owner and hand
	# PB to A with the same perfect confidence. So it is refused and recorded.
	same_side = [_m({PA: True, PB: True, PC: False, PD: False},
					{A: True, B: True, C: False, D: False})] * 3

	bindings, review = solver.deduce(same_side, {PA: A})

	assert bindings == {}
	assert review == [(PB, B, solver.UNSTABLE)]
	# Without the exclusion there is no conclusion to be unstable about, so
	# nothing is bound AND nothing is recorded.
	assert solver.deduce(same_side, {}) == ({}, [])


def test_one_wrong_known_entry_cannot_manufacture_a_binding():
	# The reviewer's fixture, verbatim in effect: 101 and 102 always play
	# together, three games, truth 101->A and 102->B. With `known` empty the
	# solver correctly refuses both (margin 0). With ONE wrong entry — 101
	# mis-linked to B, one typo in `/identity link`, which triggers a solver run
	# in the same command — the pre-fix solver bound 102 to A at 1.00/1.00,
	# moving a real person's whole history onto somebody else, unrecoverably
	# (a `learned` row cannot be displaced by another `learned` row).
	together = [_m({PA: True, PB: True, PC: False, PD: False},
				   {A: True, B: True, C: False, D: False})] * 3

	assert solver.deduce(together, {}) == ({}, [])

	bindings, review = solver.deduce(together, {PA: B})     # the typo
	assert bindings == {}                                   # NOT {PB: (A, 3, 1.0, 1.0)}
	assert review == [(PB, A, solver.UNSTABLE)]


def test_a_binding_both_computations_agree_on_survives():
	# The exclusion is not being disabled — a conclusion that holds with AND
	# without it is still written. Here PA is known and the season is varied, so
	# every remaining profile separates on its own evidence either way.
	bindings, review = solver.deduce(_shuffled_2v2_season(), {PA: A})

	assert {pid: b[0] for pid, b in bindings.items()} == {PB: B, PC: C, PD: D}
	assert review == []


# ─── rule 2: a second profile is never handed out automatically ─────────

def test_a_second_profile_for_a_user_is_held_for_review():
	# A owns PA already and a second account, 202. The evidence for 202 is clean
	# and the exclusion is not needed for it — but a `learned` write that gives
	# one user a SECOND profile is the signature of a 1-for-1 out-of-band
	# substitution (a guest playing an absent player's slot, which _rosters_agree
	# cannot see). Genuine multi-account players are rare and enumerable, so this
	# goes to an admin instead: `/identity link ... additional: True`.
	alt = 202
	on_the_known_account = [_m({PA: True, PC: False}, {A: True, C: False})] * 3
	on_the_alt = [
		_m({alt: True, PC: True, PD: False, PB: False}, {A: True, C: True, D: False, B: False}),
		_m({alt: True, PD: True, PC: False, PB: False}, {A: True, D: True, C: False, B: False}),
		_m({alt: True, PB: True, PC: False, PD: False}, {A: True, B: True, C: False, D: False}),
	]

	bindings, review = solver.deduce([*on_the_known_account, *on_the_alt], {PA: A})

	assert alt not in bindings                        # not auto-bound...
	assert (alt, A, solver.SECOND_PROFILE) in review  # ...recorded instead
	assert PA not in bindings                         # already known, never re-derived
	# The exclusion itself is still per-match, not global: A remained a
	# candidate for `alt` in the games PA was absent from, which is the only
	# reason there is a conclusion here to hold back at all.
	assert not any(pid == alt and reason == solver.UNSTABLE for pid, _u, reason in review)


def test_two_bindings_pointing_at_one_user_hold_each_other_back():
	# Neither profile is in `known`, so nothing says which of the two is the
	# substitution artefact — the evidence cannot tell, and picking one would be
	# a guess. Both are held.
	twin = 303
	season = [
		_m({PA: True, twin: True, PC: False, PD: False}, {A: True, B: True, C: False, D: False}),
		_m({PA: True, twin: True, PC: False, PE: False}, {A: True, B: True, C: False, E: False}),
		_m({PA: True, twin: True, PD: False, PE: False}, {A: True, B: True, D: False, E: False}),
	]
	# Force the shared-owner situation: with the margin floor lifted, PA and
	# `twin` both rank A first (A and B tie at 3/3, and A is the lower id).
	bindings, review = solver.deduce(season, {}, min_margin=0.0)

	assert PA not in bindings and twin not in bindings
	assert [(pid, uid, reason) for pid, uid, reason in review if reason == solver.SECOND_PROFILE] == [
		(PA, A, solver.SECOND_PROFILE), (twin, A, solver.SECOND_PROFILE),
	]


def test_a_user_who_gets_exactly_one_profile_is_not_held_back():
	# The control for the rule above: four profiles, four distinct users, one
	# each — no review entries at all.
	bindings, review = solver.deduce(_shuffled_2v2_season(), {})

	assert len({b[0] for b in bindings.values()}) == len(bindings) == 4
	assert review == []


# ─── _build_matches: what makes a paired match unusable ─────────────────
#
# Tested directly rather than through the wrapper. Through the wrapper, a
# deleted NULL guard reads every affected player as a loser, which produces the
# same zero bindings as dropping the match — so a wrapper-level test passes for
# the wrong reason. These do not.

def _raw_rows():
	""" One clean 2v2's worth of raw join rows: replay side (profile_id +
	winner) and Discord side (user_id + team + winner), team 0 winning. """
	replay = [
		dict(match_id=1, profile_id=PA, winner=1), dict(match_id=1, profile_id=PB, winner=1),
		dict(match_id=1, profile_id=PC, winner=0), dict(match_id=1, profile_id=PD, winner=0),
	]
	discord = [
		dict(match_id=1, user_id=A, team=0, winner=0), dict(match_id=1, user_id=B, team=0, winner=0),
		dict(match_id=1, user_id=C, team=1, winner=0), dict(match_id=1, user_id=D, team=1, winner=0),
	]
	return replay, discord


def test_build_matches_pairs_the_two_rosters_and_resolves_who_won():
	replay, discord = _raw_rows()

	assert solver._build_matches(replay, discord) == [
		{"profiles": {PA: True, PB: True, PC: False, PD: False},
		 "users": {A: True, B: True, C: False, D: False}},
	]


def test_build_matches_drops_a_match_with_a_null_bot_winner():
	# matches.winner is NULL for 8 production rows. Without the guard, every
	# `team == winner` is False and the whole roster reads as "everybody lost" —
	# invented evidence, not missing evidence.
	replay, discord = _raw_rows()
	for row in discord:
		row["winner"] = None

	assert solver._build_matches(replay, discord) == []


def test_build_matches_drops_a_match_with_a_null_replay_winner():
	# rs_player_games.winner is declared notnull=False, so this is reachable.
	# Without the guard, bool(None) is False and that player reads as a loser.
	replay, discord = _raw_rows()
	replay[0]["winner"] = None

	assert solver._build_matches(replay, discord) == []


def test_build_matches_drops_a_match_with_a_null_team():
	# bot/stats/stats.py writes team=None for a player on neither team. Without
	# the guard, `None == winner` is False and they read as a loser — and the
	# roster still looks complete, so the size guard cannot catch it either.
	replay, discord = _raw_rows()
	discord[2]["team"] = None

	assert solver._build_matches(replay, discord) == []


def test_build_matches_drops_a_match_with_a_null_user_id():
	# identity_conflicts' primary key forbids a NULL claimed_user_id, so a NULL
	# user_id must never reach learn(). Without the guard it becomes a candidate
	# keyed by None.
	replay, discord = _raw_rows()
	discord[1]["user_id"] = None

	assert solver._build_matches(replay, discord) == []


def test_build_matches_drops_a_match_with_a_null_profile_id():
	replay, discord = _raw_rows()
	replay[3]["profile_id"] = None

	assert solver._build_matches(replay, discord) == []


def test_build_matches_drops_a_match_whole_not_row_by_row():
	# The bad row is one of eight; the other seven are perfectly readable. They
	# still go, because a roster that looks complete but is not defeats the one
	# guard (_rosters_agree) that could have caught the difference.
	replay, discord = _raw_rows()
	clean_replay = [dict(r, match_id=2) for r in replay]
	clean_discord = [dict(r, match_id=2) for r in discord]
	discord[0]["team"] = None

	built = solver._build_matches([*replay, *clean_replay], [*discord, *clean_discord])
	assert len(built) == 1
	assert built[0]["users"] == {A: True, B: True, C: False, D: False}


def test_build_matches_needs_both_sides():
	replay, discord = _raw_rows()

	assert solver._build_matches(replay, []) == []
	assert solver._build_matches([], discord) == []


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
	def __init__(self, fail_for=(), refuse=()):
		self.fail_for = set(fail_for)
		self.refuse = set(refuse)
		self.learned = []
		self.refused_claims = []

	async def learn(self, profile_id, user_id, source, aoe2_name=None):
		if profile_id in self.fail_for:
			raise RuntimeError("db down")
		if profile_id in self.refuse:
			return False              # the lattice kept a stronger stored binding
		self.learned.append((profile_id, user_id, source, aoe2_name))
		return True

	async def record_refused_claim(self, profile_id, claimed_user_id, source):
		assert profile_id is not None and claimed_user_id is not None
		self.refused_claims.append((profile_id, claimed_user_id, source))


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


def test_run_for_community_reads_and_uses_the_known_bindings(monkeypatch):
	# The `known` plumbing has two separate effects, and this pins BOTH: with
	# `known = {}` substituted for the identities read, PA would be re-derived
	# and re-written (4 writes, not 3).
	fake_db = _fake_db_for(_shuffled_2v2_season(), known={PA: A})
	fake_identity = _setup(monkeypatch, fake_db)

	assert asyncio.run(solver.run_for_community(7)) == 3
	assert sorted(pid for pid, *_ in fake_identity.learned) == [PB, PC, PD]


def test_run_for_community_records_what_the_known_bindings_made_unstable(monkeypatch):
	# The second effect of `known`: it feeds the `taken` exclusion, whose
	# disagreement with the unexcluded scoring is what produces a review entry.
	# With `known = {}` substituted there would be no exclusion, no disagreement
	# and no recorded claim — this asserts the claim.
	same_side = [_m({PA: True, PB: True, PC: False, PD: False},
					{A: True, B: True, C: False, D: False})] * 3
	fake_db = _fake_db_for(same_side, known={PA: A})
	fake_identity = _setup(monkeypatch, fake_db)

	assert asyncio.run(solver.run_for_community(7)) == 0
	assert fake_identity.learned == []
	assert fake_identity.refused_claims == [(PB, B, "learned")]


def test_run_for_community_counts_only_the_writes_that_landed(monkeypatch):
	# learn() refuses an equal-tier claim against a different stored owner, so a
	# count of ATTEMPTS would report bindings that never happened.
	fake_db = _fake_db_for(_shuffled_2v2_season())
	fake_identity = _setup(monkeypatch, fake_db, FakeIdentity(refuse=[PC]))

	assert asyncio.run(solver.run_for_community(7)) == 3
	assert sorted(pid for pid, *_ in fake_identity.learned) == [PA, PB, PD]


def test_run_for_community_skips_a_match_whose_winner_is_unknown(monkeypatch):
	# 8 rows in production have matches.winner NULL. Which side won is then
	# unknowable, so such a match must contribute no evidence at all — here that
	# drops the season to two usable games, under the floor. (The guard itself is
	# pinned directly on _build_matches above; this is the wiring.)
	fake_db = _fake_db_for(_shuffled_2v2_season(), winner_overrides={3: None})
	fake_identity = _setup(monkeypatch, fake_db)

	assert asyncio.run(solver.run_for_community(7)) == 0
	assert fake_identity.learned == []


def test_run_for_community_logs_the_evidence_behind_every_binding(monkeypatch):
	""" A refusal leaves a conflict row a human can go and read. A BINDING left
	nothing at all: `confidence='learned'` is indistinguishable from a
	migration-003 seed, and it is the one output of this module a player cannot
	undo themselves (moving a `learned` row takes an admin). So the evidence has
	to be recorded where it lands, or it is recorded nowhere. """
	fake_db = _fake_db_for(_shuffled_2v2_season())
	_setup(monkeypatch, fake_db)
	lines = []
	monkeypatch.setattr(solver, "log", types.SimpleNamespace(
		info=lines.append, error=lines.append, warning=lines.append))

	asyncio.run(solver.run_for_community(7))

	bound = [line for line in lines if "BOUND" in line]
	assert len(bound) == 4, f"one line per binding, got {bound}"
	for profile_id, user_id in ((PA, A), (PB, B), (PC, C), (PD, D)):
		line = next(ln for ln in bound if f"profile {profile_id} " in ln)
		assert f"user {user_id}" in line
		# The three numbers that decide a binding, so a wrong one can be argued
		# with after the fact rather than merely noticed.
		assert "game(s)" in line and "ratio" in line and "margin" in line, line


def test_run_for_community_does_not_log_a_binding_that_was_refused(monkeypatch):
	""" learn() can refuse, so logging before checking its answer would put a
	binding in the record that the lattice never made. """
	fake_db = _fake_db_for(_shuffled_2v2_season())
	_setup(monkeypatch, fake_db, FakeIdentity(refuse=[PC]))
	lines = []
	monkeypatch.setattr(solver, "log", types.SimpleNamespace(
		info=lines.append, error=lines.append, warning=lines.append))

	asyncio.run(solver.run_for_community(7))

	bound = [line for line in lines if "BOUND" in line]
	assert len(bound) == 3
	assert not any(f"profile {PC} " in line for line in bound)


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
	assert fake_identity.refused_claims == []


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
