# -*- coding: utf-8 -*-
"""The deduction solver — how a community gets linked with nobody lifting a finger.

`/link` and the admin commands are the correction layer; this module is the
bootstrap. A partner community has no seed CSVs and an admin who will never
curate a mapping, so unless identity can be *derived* from what the bot already
stores, every analysis feature there is permanently dark (spec §4 of
docs/superpowers/specs/2026-07-30-identity-v2-design.md).

The evidence: a paired match gives two rosters for the same eight people —
the Discord side (user_ids, and which side won) and the replay side
(profile_ids, and which side won). No names are involved at any point; names
are mutable on both sides and are a display-only observation everywhere in
identity v2.

The signal: a profile's true owner sits on that profile's outcome side in
essentially every game. Anybody else lands there about half the time, and the
half-time coincidences get split further every time teams are shuffled. So
score co-occurrence over all of a community's paired matches and bind only
where one user separates decisively.

WHAT THIS MODULE REPLACED, and why that mechanism was unsafe. THIS PARAGRAPH IS
THE CANONICAL STATEMENT — nammaoe2bot/features/lobby/watcher.py and nammaoe2bot/features/lobby/profile_map.py
point here rather than restating it. The lobby watcher used to "heal" identity
by elimination: once a full lobby matched a bot match on player count, it paired
the single leftover profile with the single leftover user. That pinned a global
binding on counts alone, with no roster guard whatsoever — one outsider in the
lobby plus one absent match player leaves two leftovers who are NOT each other,
and the wrong pair was written as global truth. This module deduces the same
fact from the paired match's two rosters, scored across every paired game, with
a roster-size guard, a margin threshold, and the two robustness rules below.

WHY SCORING AND NOT SET INTERSECTION. The design (spec §4) called for
intersecting candidate sets across matches and binding when exactly one
candidate survived. That was implemented and run against the real data first:
it produced 127 contradictions and 11 empty candidate sets over the same 1101
matches, because substitutes and lobby guests mean the owner genuinely is NOT
on the matching side in literally every game, and one such game empties the
set forever. Scoring tolerates that; intersection cannot. Do not "simplify"
this back to an intersection.

WHAT MAKES THIS DANGEROUS, and the two rules that answer it. `identities` is a
GLOBAL source of truth and there is no human in the loop, so a wrong binding
silently moves one player's whole history onto somebody else, and a `learned`
row cannot be displaced by another `learned` row — it takes an admin. Two
failure modes were found by review, both reproduced on hand-built fixtures:

  1. ONE WRONG `known` ENTRY MANUFACTURES CONFIDENT WRONG BINDINGS. The
     per-match `taken` exclusion removes a bound user from candidacy for the
     other profiles of that match. Feed it a single typo — say `/identity link`
     binding profile 101 to the wrong user, which then triggers a solver run in
     the same command — and the TRUE owner of 101 is excluded, collapsing 102's
     evidence onto the wrong survivor at ratio 1.00 / margin 1.00. Two players
     who always queue together is all it takes.
     RULE: every profile is scored TWICE, once with the exclusion and once
     without it, and a binding is written only when both computations name the
     same user and both clear every floor. A disagreement means the conclusion
     rested on the prior bindings being right, which is precisely the case where
     it must not be trusted. It is refused and recorded for an admin instead.

  2. A 1-FOR-1 SUBSTITUTION BINDS A STRANGER TO AN ABSENT PLAYER. _rosters_agree
     compares sizes only (see its docstring): a friend playing an absent
     rostered player's slot leaves both sides at eight, so the guard never
     fires and the guest's profile scores against the absent player in every
     game they do it. This gets MORE likely as the identity map fills up.
     RULE: a `learned` write that would hand one user a SECOND profile is the
     signature of that failure. Genuine multi-account players are rare and
     enumerable (five in the flagship), so such a conclusion is never
     auto-applied — it is recorded for admin review, and the correct handling
     of a real second account is one `/identity link ... additional: True`. NOT
     the player's own `/link`: that command is view-only for anybody who already
     owns a profile, which every member reaching this rule does by definition.

CALIBRATION, measured on the real flagship data (1107 paired matches, of which
the roster-size guard rejects 6). Every figure below is from the offline
production dump, re-measured against THIS module with both rules in place:

  From ZERO known bindings (the partner-community bootstrap this exists for),
  the floors alone clear 52 of the 88 profiles seen. The rules hold 7 of those
  back — 0 as UNSTABLE (with nothing in `known` there is nothing for rule 1 to
  disagree about) and 7 as SECOND_PROFILE — leaving 45 auto-bound at ratio
  0.91-1.00 and margin 0.50-0.78, three of them EXACTLY on MIN_MARGIN.

  With the curated mapping already loaded (the flagship's state today), the
  floors clear 12 of the 41 profiles still unlinked; 1 is held as
  SECOND_PROFILE, 0 as UNSTABLE, leaving 11 at ratio 0.93-1.00, margin
  0.70-1.00.

  WHAT RULE 2 COSTS, exactly: the 7 cold-start holds are 3 users holding 2, 2
  and 3 profiles, and every one of them is a genuine multi-account player the
  curated CSVs already list as such — the rule cost 6 CSV-confirmed-correct
  bindings and 1 novel one, and caught no wrong binding, because this dataset
  contains no wrong binding to catch. Its value is prospective (rule 2 in the
  section above), and nothing is lost: all 7 are recorded for an admin, whose
  `/identity link ... additional: True` is the one right way to grant a second
  profile. Rule 1 costs nothing measurable on this data — which says the `taken`
  exclusion is not load-bearing here, not that it is safe to trust.

  ACCURACY, cold start, against the hand-curated CSVs. Of the 52 pre-rule
  bindings, 40 are profiles the humans had also mapped and ALL 40 agree, 0
  disagree. But 40 is NOT 40 independent human confirmations: 26 come from
  the hand-maintained profile-map CSV, 6 from rows tagged `manual`,
  and 8 from rows tagged `elim` — i.e. 8 merely agree with the output of the
  very eliminate() mechanism this module replaces, which confirms nothing.
  INDEPENDENT HUMAN CONFIRMATION IS 32/32, not 40/40. Of the 45 that survive
  both rules, 34 agree (24 CSV + 4 `manual` + 6 `elim`, so 28/28 independent),
  0 disagree, and 11 are profiles the curated mapping never had — those 11 are
  the feature's actual output, and no human has ever checked them.

  The margin gap is real but CONDITIONAL: among profiles already clearing ratio
  >= 0.90, nothing refused on margin scores above 0.33. Unconditioned — over
  every profile with at least MIN_GAMES games, whatever its ratio — the
  strongest rejected margin is 0.65, i.e. ABOVE the floor. MIN_MARGIN alone is
  not a moat; it is the second of two floors, and both are inclusive with three
  real bindings sitting exactly on one of them (hence the boundary tests in
  tests/test_identity_solver.py pinning `>=` rather than `>`).

These numbers are why the thresholds are what they are; changing them is a data
question, not a taste question.

Structure: `deduce()` is pure (stdlib only, no DB, no I/O) so the scoring rules
are testable as data-in/data-out, and `run_for_community()` is the thin async
wrapper that loads rows and writes bindings. CI installs only pytest, so this
module must keep importing with nothing but the stdlib, nammaoe2bot.runtime.database and its
nammaoe2bot.features.identity.resolver/nammaoe2bot.community siblings — no nextcord, aiomysql or aiohttp.
"""
from nammaoe2bot import community
from nammaoe2bot.features.identity import resolver
from nammaoe2bot.runtime.console import log
from nammaoe2bot.runtime.database import db

# Calibrated on the flagship community's 1101 usable paired matches (see the
# module docstring for the full distribution) — these are measurements, not
# guesses, and none of them should be changed without re-running that sweep.
MIN_GAMES = 3     # a binding must survive several different rosters
MIN_RATIO = 0.90  # the winner must be on the profile's side in almost every game
MIN_MARGIN = 0.50 # ...and must beat the runner-up by half the games played

# Why a conclusion was withheld. Both are refusals to auto-bind, recorded
# against the profile so an admin has somewhere to look; see deduce().
UNSTABLE = "depends-on-existing-bindings"      # rule 1 in the module docstring
SECOND_PROFILE = "would-be-a-second-profile"   # rule 2

# The two per-community reads. Both start from match_replays, which is the
# authority on "this bot match is that replay" going forward — replay_matches's
# bot_match_id column hardcodes one community owning a replay and is dropped in
# a later stage. match_replays is empty in production until the backfill task
# populates it (1107 historical pairings), at which point this solver starts
# seeing evidence with no change here.
_REPLAY_ROSTERS_SQL = (
	"SELECT mr.match_id AS match_id, pg.profile_id AS profile_id, pg.winner AS winner "
	"FROM match_replays mr "
	"JOIN replay_players pg ON pg.replay_match_id = mr.replay_match_id "
	"WHERE mr.community_id = %s"
)

# matches.winner and match_players.team are both 0/1 side indexes, so a user won
# iff team == winner. matches.winner is NULL for 8 production rows (a match
# whose result was never recorded); those matches are dropped whole, not read as
# a loss — see _build_matches.
_DISCORD_ROSTERS_SQL = (
	"SELECT mr.match_id AS match_id, mp.user_id AS user_id, mp.team AS team, m.winner AS winner "
	"FROM match_replays mr "
	"JOIN matches m ON m.match_id = mr.match_id "
	"JOIN match_players mp ON mp.match_id = m.match_id AND mp.channel_id = m.channel_id "
	"WHERE mr.community_id = %s"
)

# Deliberately NOT scoped to the community: a binding earned anywhere is true
# everywhere (identities is global), and a profile already owned must be
# excluded as a candidate here no matter which community proved it.
_KNOWN_OWNERS_SQL = "SELECT profile_id, user_id FROM identities WHERE user_id IS NOT NULL"


def _rosters_agree(match) -> bool:
	""" Whether a paired match's two rosters describe the same NUMBER of people.

	It is a size comparison and nothing more. What it covers is the UNBALANCED
	pairing: an extra player on one side (a lobby guest who was never in the bot
	match) or a missing one (a rostered player absent from the game), in any
	combination that leaves the counts unequal. It fired on 6 of 1107 real
	paired matches. When it fires, NOTHING in that match is usable evidence —
	not even the players who do line up, since the one who does not is exactly
	the person whose absence shifts everybody else's candidate list.

	WHAT IT EXPLICITLY DOES NOT COVER: a 1-for-1 out-of-band substitution. A
	friend playing in an absent rostered player's slot leaves both sides at
	eight, so the sizes AGREE while the rosters describe different people — and
	the guest's profile then scores against the absent player in every game they
	do this, reaching ratio 1.00 / margin 1.00 with enough of them. No size
	comparison can detect that, and it gets MORE likely as the identity map
	approaches completeness, which is this feature's goal. The mitigation lives
	downstream in deduce(): a conclusion that would give one user a second
	profile is refused and recorded for an admin (module docstring, rule 2). """
	return len(match["profiles"]) == len(match["users"])


def _score(matches, known, apply_taken):
	""" Co-occurrence counts over the usable matches: returns
	`({profile_id: {user_id: hits}}, {profile_id: games})`.

	`apply_taken` selects between the two computations deduce() compares. With
	it, the users bound to a profile present in THIS match are dropped from
	every other profile's candidates here — one person plays one profile per
	game. That exclusion is what makes an otherwise inseparable pair separable,
	and it is also the whole attack surface of rule 1, which is why the caller
	runs the scoring both ways.

	The exclusion is per-match, NEVER global: five real users own 2-3 profiles
	each, and a global exclusion would make a second account permanently
	underivable.

	Profiles already in `known` are never scored — a binding is never re-derived
	(and so never re-written) by either computation. """
	scores, games = {}, {}

	for match in matches:
		if not _rosters_agree(match):
			continue
		profiles, users = match["profiles"], match["users"]
		taken = {known[pid] for pid in profiles if pid in known} if apply_taken else set()
		for profile_id, profile_won in profiles.items():
			if profile_id in known:
				continue
			games[profile_id] = games.get(profile_id, 0) + 1
			candidates = scores.setdefault(profile_id, {})
			for user_id, user_won in users.items():
				if user_id not in taken and bool(user_won) == bool(profile_won):
					candidates[user_id] = candidates.get(user_id, 0) + 1

	return scores, games


def _clear_the_floors(scores, games, min_games, min_ratio, min_margin) -> dict:
	""" `{profile_id: (user_id, games, ratio, margin)}` for every profile whose
	top candidate clears all three floors.

	`ratio` is the top user's score over games played (how consistently they
	were there); `margin` is the top user's lead over the runner-up, also over
	games (how much better than the next best explanation). Both are needed:
	ratio alone would happily bind either half of a pair who always play
	together, since both are on the profile's side every single game.

	Every comparison here is INCLUSIVE (`>=`). Three of the 45 cold-start
	bindings sit exactly on MIN_MARGIN, so `>` instead of `>=` silently drops
	three real bindings; tests/test_identity_solver.py pins both operators.

	Ties break on the lower user_id and profiles are emitted in sorted order, so
	the same evidence always produces the same result in the same order,
	whatever the dict or row ordering was. """
	out = {}
	for profile_id in sorted(games):
		played = games[profile_id]
		if played < min_games:
			continue
		ranked = sorted(scores[profile_id].items(), key=lambda kv: (-kv[1], kv[0]))
		if not ranked:
			continue
		top_user, top_score = ranked[0]
		runner_up = ranked[1][1] if len(ranked) > 1 else 0
		ratio = top_score / played
		margin = (top_score - runner_up) / played
		if ratio >= min_ratio and margin >= min_margin:
			out[profile_id] = (top_user, played, ratio, margin)
	return out


def deduce(matches, known, min_games=MIN_GAMES, min_ratio=MIN_RATIO, min_margin=MIN_MARGIN):
	""" Score every unbound profile and split the answers into what may be
	written and what a human has to look at. Pure — no I/O, no module state,
	safe to call on any data. Returns

	  `({profile_id: (user_id, games, ratio, margin)}, [(profile_id, user_id, reason)])`

	the bindings first, then the refusals-for-review in profile_id order, each
	carrying the user the solver would have named and one of UNSTABLE /
	SECOND_PROFILE.

	`matches` is `[{"profiles": {profile_id: won}, "users": {user_id: won}}, ...]`
	(one entry per paired match, `won` a bool on both sides) and `known` is the
	`{profile_id: user_id}` already bound.

	Three gates, in order:

	  FLOORS — games/ratio/margin, per _clear_the_floors, over the usable
	    matches (_rosters_agree). Failing these is ordinary insufficient
	    evidence: nothing is recorded, because there is nothing to tell anyone.

	  STABILITY (rule 1) — the whole scoring runs twice, with and without the
	    per-match `taken` exclusion. A profile is bindable only if both runs
	    name the SAME user and both clear the floors. Otherwise the conclusion
	    depended on the existing bindings being correct, and one bad `known`
	    entry is enough to manufacture a maximally confident wrong answer. The
	    with-exclusion candidate is recorded as UNSTABLE — that is the claim
	    being refused, and the conflict table wants the claim, not the doubt.

	  SECOND PROFILE (rule 2) — a surviving binding whose user already owns a
	    different profile, either in `known` or from another binding in this
	    same run, is held back as SECOND_PROFILE. When two bindings in one run
	    point at one user, BOTH are held: the evidence cannot say which of them
	    is the substitution artefact, and picking one would be a guess.

	Both rules are refusals, never overrides: this function never binds anything
	the plain floors would not have bound. See the module docstring for the two
	real failure modes they exist for and what they cost on production data.

	One structural consequence of rule 1 worth knowing before touching this: the
	`taken` exclusion can no longer DECIDE anything. Every binding must also be
	reached without it, so `bindings` is always a subset of what the unexcluded
	scoring found, and the exclusion's only remaining power is to veto (and to
	name what got vetoed). That is the point — it is the contaminated computation
	— but it does mean the exclusion is now a second opinion, not a
	discriminator, and it changed no outcome at all on the production dump. """
	with_exclusion = _clear_the_floors(
		*_score(matches, known, True), min_games, min_ratio, min_margin)
	without_exclusion = _clear_the_floors(
		*_score(matches, known, False), min_games, min_ratio, min_margin)

	review = {}
	stable = {}
	for profile_id, result in with_exclusion.items():
		unconditioned = without_exclusion.get(profile_id)
		if unconditioned is None or unconditioned[0] != result[0]:
			review[profile_id] = (result[0], UNSTABLE)
			continue
		stable[profile_id] = result

	# How many profiles each candidate owner would hold once this run lands:
	# what they already own (globally, from `known`) plus what this run adds.
	owned = {}
	for profile_id, user_id in known.items():
		owned.setdefault(user_id, set()).add(profile_id)
	for profile_id, result in stable.items():
		owned.setdefault(result[0], set()).add(profile_id)

	bindings = {}
	for profile_id in sorted(stable):
		user_id = stable[profile_id][0]
		if len(owned[user_id]) > 1:
			review[profile_id] = (user_id, SECOND_PROFILE)
			continue
		bindings[profile_id] = stable[profile_id]

	return bindings, [(pid, review[pid][0], review[pid][1]) for pid in sorted(review)]


def _build_matches(replay_rows, discord_rows) -> list:
	""" Assemble the two raw row sets into `deduce`'s per-match shape, dropping
	any match we cannot read an honest outcome for.

	A match is dropped whole when either side has a row with a NULL winner,
	team or user_id. Reading a NULL winner as a loss would invent evidence, and
	silently dropping just the unreadable row would leave a roster that looks
	complete but is not — worse than having no evidence, since the resulting
	size mismatch is the one thing _rosters_agree could otherwise have caught.
	A NULL user_id is doubly disqualifying: identity_conflicts dedups on
	(profile_id, claimed_user_id, status) and MySQL treats NULLs in a unique
	index as never equal, so a NULL claim would accumulate a fresh row on every
	run. record_refused_claim rejects one outright; it must never reach learn()
	either.

	All four NULLs are genuinely reachable: `matches.winner` is NULL for 8
	production rows, `replay_players.winner` is declared notnull=False,
	`match_players.team` is written as None by nammaoe2bot/pickup/stats.py for a player
	on neither team, and `match_players.user_id` is nullable.

	Pure, like deduce(), so the "what counts as usable" rule is testable
	without a database. """
	profiles_by_match, users_by_match, unusable = {}, {}, set()

	for row in replay_rows:
		match_id = row["match_id"]
		if row["winner"] is None or row["profile_id"] is None:
			unusable.add(match_id)
			continue
		profiles_by_match.setdefault(match_id, {})[row["profile_id"]] = bool(row["winner"])

	for row in discord_rows:
		match_id = row["match_id"]
		if row["user_id"] is None or row["team"] is None or row["winner"] is None:
			unusable.add(match_id)
			continue
		users_by_match.setdefault(match_id, {})[row["user_id"]] = row["team"] == row["winner"]

	return [
		dict(profiles=profiles_by_match[match_id], users=users_by_match[match_id])
		for match_id in sorted(profiles_by_match)
		if match_id not in unusable and match_id in users_by_match
	]


async def run_for_community(community_id) -> int:
	""" Deduce and write every binding this community's paired matches support.
	Returns the number of bindings that actually LANDED — learn() can refuse one
	(see below), and a count of attempts would overstate what happened.

	Writes go through resolver.learn(..., "learned"), so the lattice decides
	what actually lands: a profile a player claimed themselves or an admin
	assigned outranks this, and — since identity v2 — an equal-tier `learned`
	binding to somebody else is NOT overwritten either; the disagreement is
	recorded as an open conflict for an admin instead. That is intended. This
	module never writes `identities` directly.

	Everything deduce() refused for review is recorded through
	resolver.record_refused_claim() at the same `learned` tier, so `/identity
	conflicts` shows it beside every other unsettled claim. Recording is
	idempotent (identity_conflicts' unique claim index + INSERT IGNORE), so
	re-running the solver on every trigger does not accumulate rows.

	Each write is best-effort and independent: one failure is logged and the
	rest still land. The alternative — aborting the run — would let a single
	transient DB error discard a whole community's deductions, and re-deriving
	them is free (the solver is stateless) but only on the next trigger. """
	replay_rows = await db.fetchall(_REPLAY_ROSTERS_SQL, [community_id]) or []
	discord_rows = await db.fetchall(_DISCORD_ROSTERS_SQL, [community_id]) or []
	known_rows = await db.fetchall(_KNOWN_OWNERS_SQL) or []

	known = {row["profile_id"]: row["user_id"] for row in known_rows}
	matches = _build_matches(replay_rows, discord_rows)
	bindings, review = deduce(matches, known)

	written = 0
	for profile_id, (user_id, games, ratio, margin) in bindings.items():
		if user_id is None:
			# _build_matches already drops NULL user_ids, so this is unreachable
			# today; it stays because a NULL claimed_user_id defeats
			# identity_conflicts' unique claim index (NULLs never compare equal),
			# so the refusal would silently duplicate on every run.
			continue
		try:
			if await resolver.learn(profile_id, user_id, "learned", aoe2_name=None):
				written += 1
				# One line per BINDING, not just per refusal. A refusal leaves a
				# conflict row a human can go and read; a binding leaves nothing
				# at all -- `confidence='learned'` is indistinguishable from a
				# migration-003 seed, and this is the one output of this module
				# that a player cannot undo themselves (a `learned` row takes an
				# admin to move). So the evidence that produced it is recorded
				# here, at the moment it lands, or it is recorded nowhere.
				log.info(
					f"identity solver: BOUND profile {profile_id} -> user {user_id} "
					f"at learned ({games} game(s), ratio {ratio:.2f}, margin {margin:.2f})")
		except Exception as e:
			log.error(f"identity solver: learn failed for profile {profile_id} -> user {user_id}: {e}")

	for profile_id, user_id, reason in review:
		if user_id is None:
			continue
		try:
			await resolver.record_refused_claim(profile_id, user_id, "learned")
			log.info(
				f"identity solver: NOT binding profile {profile_id} -> user {user_id} "
				f"({reason}); recorded for admin review")
		except Exception as e:
			log.error(f"identity solver: could not record refused claim for profile {profile_id}: {e}")

	paired = len({row["match_id"] for row in replay_rows})
	usable = sum(1 for m in matches if _rosters_agree(m))
	log.info(
		f"identity solver: community {community_id} — {paired} paired match(es), "
		f"{paired - usable} skipped (unknown outcome or roster mismatch), "
		f"{written} of {len(bindings)} deduced profile(s) bound, "
		f"{len(review)} held for review"
	)
	return written


async def run_for_channel(channel_id) -> int:
	""" Run the solver for whichever community owns `channel_id`. A trigger
	entry point: NEVER raises, and quietly does nothing for a channel that is
	not enrolled in a community (the expected state for most channels).

	Best-effort by construction because of where it is called from — after a
	replay ingest, after `/link`, after an admin relink. A deduction failing is
	never a reason to fail the thing that triggered it: the ingest's raw parse
	is irreplaceable, the player's link already succeeded, and the solver is
	stateless, so the next trigger simply re-derives everything. """
	try:
		community_id = await community.community_for_channel(channel_id)
		if community_id is None:
			return 0
		return await run_for_community(community_id)
	except Exception as e:
		log.error(f"identity solver run failed for channel {channel_id}: {e}")
		return 0


async def run_for_match(match_id) -> int:
	""" Run the solver for whichever community owns `match_id`'s channel — the
	trigger a freshly ingested paired replay uses, since ingest knows the bot
	match id and not the community. Same never-raises contract as
	run_for_channel; an unknown match_id is a no-op. """
	try:
		row = await db.select_one(["channel_id"], "matches", where={"match_id": match_id})
	except Exception as e:
		log.error(f"identity solver could not resolve match {match_id}: {e}")
		return 0
	if row is None:
		return 0
	return await run_for_channel(row["channel_id"])
