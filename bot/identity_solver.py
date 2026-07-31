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

WHY SCORING AND NOT SET INTERSECTION. The design (spec §4) called for
intersecting candidate sets across matches and binding when exactly one
candidate survived. That was implemented and run against the real data first:
it produced 127 contradictions and 11 empty candidate sets over the same 1101
matches, because substitutes and lobby guests mean the owner genuinely is NOT
on the matching side in literally every game, and one such game empties the
set forever. Scoring tolerates that; intersection cannot. Do not "simplify"
this back to an intersection.

CALIBRATION, measured twice on the real flagship data (1107 paired matches, of
which the roster-size guard rejects 6):

  With the curated mapping already loaded (the flagship's state today), it binds
  12 of the 41 profiles still unlinked, all at ratio 0.93-1.00 and margin
  0.70-1.00. Nothing it refuses on margin scores above 0.33.

  From ZERO known bindings (the partner-community bootstrap this exists for) it
  binds 52 of the 88 profiles it sees, at ratio 0.91-1.00 and margin 0.50-0.78.
  Refusals on margin: 0.33, 0.33, 0.27.

  Accuracy, measured against the hand-curated CSVs on that cold-start run: of
  the 52 bindings, 40 are profiles the humans had also mapped and ALL 40 agree;
  0 disagree; the other 12 are profiles the curated mapping never had.

So MIN_MARGIN=0.50 sits in a real gap in both distributions (weakest accepted
0.50/0.70 against strongest rejected 0.33) — though in the cold-start case three
bindings land exactly ON it, so it is the floor of the accepted cluster rather
than the middle of a wide gap. These numbers are why the thresholds are what
they are; changing them is a data question, not a taste question.

Structure: `deduce()` is pure (stdlib only, no DB, no I/O) so the scoring rules
are testable as data-in/data-out, and `run_for_community()` is the thin async
wrapper that loads rows and writes bindings. CI installs only pytest, so this
module must keep importing with nothing but the stdlib, core.database and its
bot.identity/bot.community siblings — no nextcord, aiomysql or aiohttp.
"""
from bot import community, identity
from core.console import log
from core.database import db

# Calibrated on the flagship community's 1101 usable paired matches (see the
# module docstring for the full distribution) — these are measurements, not
# guesses, and none of them should be changed without re-running that sweep.
MIN_GAMES = 3     # a binding must survive several different rosters
MIN_RATIO = 0.90  # the winner must be on the profile's side in almost every game
MIN_MARGIN = 0.50 # ...and must beat the runner-up by half the games played

# The two per-community reads. Both start from match_replays, which is the
# authority on "this bot match is that replay" going forward — rs_matches's
# bot_match_id column hardcodes one community owning a replay and is dropped in
# a later stage. match_replays is empty in production until the backfill task
# populates it (1107 historical pairings), at which point this solver starts
# seeing evidence with no change here.
#
# `rs_player_games` / `aoe2_match_id` are renamed to `replay_players` /
# `replay_match_id` in a later stage; today's names are used here.
_REPLAY_ROSTERS_SQL = (
	"SELECT mr.match_id AS match_id, pg.profile_id AS profile_id, pg.winner AS winner "
	"FROM match_replays mr "
	"JOIN rs_player_games pg ON pg.aoe2_match_id = mr.replay_match_id "
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
	""" Whether a paired match's two rosters describe the same number of people.

	They disagree when somebody in the game was not in the bot match (a lobby
	guest) or somebody in the bot match did not play it (a substitute). Either
	way the pairing describes two different sets of people, so NOTHING in that
	match is usable evidence — not even the players who do line up, since the
	one who does not is exactly the person whose absence shifts everybody
	else's candidate list. """
	return len(match["profiles"]) == len(match["users"])


def deduce(matches, known, min_games=MIN_GAMES, min_ratio=MIN_RATIO, min_margin=MIN_MARGIN) -> dict:
	""" Score every unbound profile against every candidate user and return
	`{profile_id: (user_id, games, ratio, margin)}` for the ones that separate
	decisively. Pure — no I/O, no module state, safe to call on any data.

	`matches` is `[{"profiles": {profile_id: won}, "users": {user_id: won}}, ...]`
	(one entry per paired match, `won` a bool on both sides) and `known` is the
	`{profile_id: user_id}` already bound.

	Per match:
	  - skip it entirely unless the two rosters agree in size (_rosters_agree);
	  - work out `taken`, the users bound to a profile present in THIS match,
	    and drop them from every other profile's candidates here — one person
	    plays one profile per game. This is a per-match exclusion, NOT a global
	    one: five real users own 2-3 profiles each, and a global exclusion would
	    make a second account permanently underivable;
	  - every unbound profile scores +1 game, and +1 for each remaining
	    candidate on the same outcome side.

	Then per profile with at least `min_games`: `ratio` is the top user's score
	over games (how consistently they were there) and `margin` is the top user's
	lead over the runner-up, also over games (how much better than the next best
	explanation). Both floors must be cleared — ratio alone would happily bind
	either half of a pair who always play together, since both of them are on
	the profile's side every single game.

	Ties break on the lower user_id, and profiles are emitted in sorted order,
	so the same evidence always produces the same bindings in the same order
	regardless of dict or row ordering. """
	scores = {}   # profile_id -> {user_id: times seen on the same outcome side}
	games = {}    # profile_id -> paired matches this profile appeared in

	for match in matches:
		if not _rosters_agree(match):
			continue
		profiles, users = match["profiles"], match["users"]
		taken = {known[pid] for pid in profiles if pid in known}
		for profile_id, profile_won in profiles.items():
			if profile_id in known:
				continue
			games[profile_id] = games.get(profile_id, 0) + 1
			candidates = scores.setdefault(profile_id, {})
			for user_id, user_won in users.items():
				if user_id not in taken and bool(user_won) == bool(profile_won):
					candidates[user_id] = candidates.get(user_id, 0) + 1

	bindings = {}
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
			bindings[profile_id] = (top_user, played, ratio, margin)
	return bindings


def _build_matches(replay_rows, discord_rows) -> list:
	""" Assemble the two raw row sets into `deduce`'s per-match shape, dropping
	any match we cannot read an honest outcome for.

	A match is dropped whole when either side has a row with a NULL winner,
	team or user_id. Reading a NULL winner as a loss would invent evidence, and
	silently dropping just the unreadable row would leave a roster that looks
	complete but is not — worse than having no evidence, since the resulting
	size mismatch is the one thing _rosters_agree could otherwise have caught.
	A NULL user_id is doubly disqualifying: identity_conflicts' primary key
	forbids one, so it must never reach learn().

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
	Returns the number of bindings actually written.

	Writes go through identity.learn(..., "learned"), so the lattice decides
	what actually lands: a profile a player claimed themselves or an admin
	assigned outranks this, and — since identity v2 — an equal-tier `learned`
	binding to somebody else is NOT overwritten either; the disagreement is
	recorded as an open conflict for an admin instead. That is intended. This
	module never writes `identities` directly.

	Each write is best-effort and independent: one failure is logged and the
	rest still land. The alternative — aborting the run — would let a single
	transient DB error discard a whole community's deductions, and re-deriving
	them is free (the solver is stateless) but only on the next trigger. """
	replay_rows = await db.fetchall(_REPLAY_ROSTERS_SQL, [community_id]) or []
	discord_rows = await db.fetchall(_DISCORD_ROSTERS_SQL, [community_id]) or []
	known_rows = await db.fetchall(_KNOWN_OWNERS_SQL) or []

	known = {row["profile_id"]: row["user_id"] for row in known_rows}
	matches = _build_matches(replay_rows, discord_rows)
	bindings = deduce(matches, known)

	written = 0
	for profile_id, (user_id, _games, _ratio, _margin) in bindings.items():
		if user_id is None:
			# _build_matches already drops NULL user_ids, so this is unreachable
			# today; it stays because learn() writing a NULL claimed_user_id
			# would violate identity_conflicts' primary key, and that failure
			# would surface far from here.
			continue
		try:
			await identity.learn(profile_id, user_id, "learned", aoe2_name=None)
			written += 1
		except Exception as e:
			log.error(f"identity solver: learn failed for profile {profile_id} -> user {user_id}: {e}")

	paired = len({row["match_id"] for row in replay_rows})
	usable = sum(1 for m in matches if _rosters_agree(m))
	log.info(
		f"identity solver: community {community_id} — {paired} paired match(es), "
		f"{paired - usable} skipped (unknown outcome or roster mismatch), "
		f"{written} of {len(bindings)} deduced profile(s) bound"
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
