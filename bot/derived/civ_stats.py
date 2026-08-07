# -*- coding: utf-8 -*-
"""Derived-community civ_stats: per-civ win/loss tallies for one community,
aggregated once by a refresh pass over `civ_picks` instead of scanned live
every time stage-5's civ-stats page renders.

NAME COLLISION, DELIBERATE AND NOT A BUG: `nammaoe2bot/features/civs/pools.py` also exists, for
a completely different purpose (civ pools for randomised team balancing). It
is NOT retired — stage 5c repointed it at the table this module writes,
deleting the two sources it used to rank on (a frozen `data/civ_elo_stats.csv`
snapshot and a live cross-community `civ_picks` GROUP BY). So the two are a
writer/reader pair now, not an old and a new: this one aggregates `civ_picks`
into `civ_stats` on the refresh pass, that one reads `civ_stats` at pick time.
Neither imports the other, and neither should.

compute_civ_stats is pure -- no DB, no I/O -- exactly like its sibling
modules in this package. It differs from them in one respect worth stating
up front: rollups.compute_rollup and boards.compute_board both take rows
ALREADY resolved to one community (identity/community resolution is
explicitly somebody else's job -- see rollups.py's module docstring).
civ_stats does the community join itself. That asymmetry is deliberate, not
an inconsistency:

  * player_rollups and metric_boards key on `user_id`, which only exists
    after walking a Discord user's full set of linked AoE2 profiles through
    `identities` -- a genuinely non-trivial resolution with its own failure
    modes, which is exactly why task 4.5's refresh job exists as a dedicated
    owner for it.
  * civ_stats keys on `civ`, never on a person. Its only linking need is
    "which community does this civ_picks row belong to", and that is a
    single stored fact -- one row in `community_channels` per channel, no
    graph to walk, no ambiguity to resolve. `civ_picks` itself carries no
    community_id column (task 4.1's elaboration measured this against the
    live schema), so SOMETHING has to perform this join; pushing a one-hop
    dict lookup out to a future caller would only relocate it, not simplify
    it, and no other task claims ownership of it the way 4.5 claims identity
    resolution.
"""
from nammaoe2bot.runtime.database import db

_WIN_RESULTS = frozenset({"W", "L"})


def compute_civ_stats(pick_rows, channel_rows):
	"""Every community's whole civ_stats set, in one pass. Pure: no DB, no I/O.

	`pick_rows` are civ_picks-shaped dicts (only `channel_id`, `civ`, `result`
	are read); `channel_rows` are community_channels-shaped dicts (only
	`channel_id`, `community_id` are read) -- i.e. exactly what a bare
	`SELECT * FROM community_channels` returns, the same "hand over the whole
	table, let the pure function join it" shape compute_rollup uses for
	label_rows joined against stat_rows.

	A pick row whose channel has no matching community_channels row (never
	enrolled, or a channel_id that no longer resolves) contributes to
	NOTHING -- not a stray `None`-keyed community. It is not this table's
	business to invent a home for an orphaned pick.

	A pick row whose `result` is neither 'W' nor 'L' -- a game whose outcome
	was never resolved (see nammaoe2bot/features/civs/sync.py's `result=None` branch for when
	that happens) -- is dropped from every count on every civ it touches.
	This table's contract is exactly three numeric columns with no "unknown"
	bucket, so the invariant `games == wins + losses` must hold for every row
	this function ever returns; counting an unresolved result into `games`
	alone would silently break it.

	Returns {community_id: {civ: {"games": int, "wins": int, "losses": int}}}.
	Every inner value is only ever incremented, so `games` equals `wins` plus
	`losses` by construction -- there is no code path that touches one
	without the other two.
	"""
	channel_communities = {row["channel_id"]: row["community_id"] for row in channel_rows}

	out = {}
	for pick in pick_rows:
		result = pick.get("result")
		if result not in _WIN_RESULTS:
			continue
		community_id = channel_communities.get(pick.get("channel_id"))
		if community_id is None:
			continue
		civ = pick.get("civ")
		tally = out.setdefault(community_id, {}).setdefault(civ, dict(games=0, wins=0, losses=0))
		tally["games"] += 1
		if result == "W":
			tally["wins"] += 1
		else:
			tally["losses"] += 1
	return out


_COLUMNS = ("community_id", "civ", "games", "wins", "losses", "computed_at")


async def write(community_id, civ_counts, computed_at):
	"""Store one community's whole civ_stats set. Idempotent:
	DELETE this community's rows, then insert what compute_civ_stats
	returned for it (i.e. one value of the outer dict compute_civ_stats
	returns, keyed by this same community_id).

	Mirrors bot/derived/game_stats.py's write() for the same reason: the row
	SET for one key can shrink between recomputes (a civ that had picks last
	pass but none since -- a channel un-enrolled, a correction landing on
	`civ_picks` -- must not leave a stale row behind), so a delete is the
	only way to guarantee the stored set exactly matches the latest compute.
	Unlike player_rollups/metric_boards (whose grain is a single
	non-shrinking key per row, where a REPLACE alone is correct and safer --
	see rollups.write's docstring), this table's grain per community is a
	whole SET of civs, exactly like game_stats' grain per match is a whole
	set of players.

	ACCEPTED TRADEOFF, deliberate, same as every delete-then-insert writer in
	this package: the DELETE and the INSERT are not one transaction (the
	adapter runs autocommit with no transaction surface -- see
	nammaoe2bot/runtime/database/mysql.py). An insert failing after a successful delete
	leaves this community's civ-stats page briefly empty rather than stale;
	the next refresh pass repopulates it.
	"""
	await db.execute("DELETE FROM civ_stats WHERE community_id=%s", [community_id])
	if not civ_counts:
		return
	payload = []
	for civ, counts in civ_counts.items():
		# Merged rather than subscripted field-by-field so a `counts` dict
		# missing a key (e.g. a caller-built row that forgot `losses`) shows
		# up as a key-set mismatch below -- a loud, diagnosable ValueError --
		# instead of a bare KeyError from reading `counts["losses"]` directly.
		row = dict(civ=civ, community_id=community_id, computed_at=computed_at, **counts)
		if set(row) != set(_COLUMNS):
			# Loud, not coerced -- see game_stats.write for why.
			raise ValueError(
				f"civ_stats row for community {community_id} civ {civ} has keys {sorted(row)}, "
				f"expected exactly {sorted(_COLUMNS)}")
		payload.append({c: row[c] for c in _COLUMNS})
	await db.insert_many("civ_stats", payload, on_duplicate="replace")
