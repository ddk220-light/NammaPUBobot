# -*- coding: utf-8 -*-
"""The scouting report `/rank` prints, rendered from one player_rollups blob.

Pure -- no DB, no Discord, no I/O -- for the same reason
bot/derived/rollups.py's compute is: every copy rule below is a claim about
what is honest to SHOW, and a claim you cannot test without a database is a
claim nobody tests. The caller (bot/commands/stats.py) resolves the community,
reads the row and owns the embed; this module turns a blob into text and
nothing else.

WHAT THIS REPLACED, and why the rules below are absolute. Until stage 5a
`/rank` led with a generated persona -- a name, an epithet and a tagline
("The Siege Enjoyer", and a sentence of prose about their temperament) --
followed by a paragraph of scout-read narration assembled from the same
thin evidence. None of it carried a sample size, none of it distinguished a
tendency measured over forty games from one measured over three, and the
prose had to be regex-scrubbed at render time to strip a tag enumeration it
should never have contained. It asserted personality where it had
arithmetic. The replacement must not assert arithmetic where it has a sample
of three, so:

  * EVERY NUMBER CARRIES THE SAMPLE IT RESTS ON. A percentage with no
    denominator beside it is exactly the failure mode this module exists to
    end, and there is no line here that renders one.
  * A MISSING PIECE IS OMITTED, NEVER HEDGED AND NEVER ZEROED. peak_eapm is
    NULL on every production row today (the bucket-capturing parser shipped
    after the last match was played), so `median_peak` is null and
    `games_peak` is 0: the eAPM line renders the median alone rather than a
    blank, an em-dash, or a 0 standing in for a peak nobody measured. When
    buckets start arriving the peak appears on its own, with its own count.
  * THE THREE SPLIT LINES ARE INDEPENDENT. Each is dropped on its own when
    the rollup carries nothing for it, and the others still render.
    compute_rollup has already applied SPLIT_MIN_GAMES -- a split below the
    floor is absent from the blob entirely rather than flagged -- so there is
    no low-sample warning to render here and there must never be one.

`render(None)` is the design's stage-5 acceptance criterion (identity v2 §5).
An unlinked player has NO player_rollups row rather than a row of zeros, so
the absence is the signal, and the field reads exactly PENDING -- not a
report of zeros, which would be a measurement nobody took.

`gaps()` answers the other half of the same question for `/identity status`
(identity v2 §3): given what a community's rollups actually hold, which of
these lines can it not fill? Counted, never asserted -- a hardcoded list of
"features that might be gated" would be fiction again, which is precisely
why that half of the spec waited until this stage to be implementable.
"""

# The exact copy identity v2 §5 pins for every analysis surface gated behind
# linking. Spelled once, here, so `/rank`, `/identity status`'s explanation of
# it, and every surface that cuts over in 5b-5d cannot drift into three
# slightly different sentences.
PENDING = "Statistics pending linking"

# (blob block, the key field inside its rows, prefix to strip off the label,
#  feature id used by gaps()). The key field differs by block because
# compute_rollup names it after what it holds -- `key` for a classifier label,
# `unit` for a unit name -- and flattening that here would only hide which
# block a bug came from.
#
# The `spawn_` strip is display-only: the stored labels are spawn_near_enemy,
# spawn_gold_poor and so on (bot/derived/game_labels.SPAWN_KEYS), and the line
# already says "spawn", so the prefix would render twice.
_SPLIT_BLOCKS = (
	("strategies", "key", "", "strategy"),
	("spawns", "key", "spawn_", "spawn"),
	("units", "unit", "", "unit"),
)


def _identity(text):
	return text


def _pct(rate):
	""" A 0..1 rate as whole percent. Rates arrive rounded to 4 places
	(rollups._RATE_PLACES), which is 100x finer than this. """
	return round(rate * 100)


def _num(value):
	""" A median as text. compute_rollup deliberately does NOT round an
	even-length sample to an integer -- 62.5 and 62 are different samples and
	must not print as the same number -- so the fractional half survives here
	while a whole number still renders without a pointless `.0`. """
	rounded = round(float(value), 1)
	return str(int(rounded)) if rounded == int(rounded) else str(rounded)


def _label(key, strip_prefix=""):
	""" A stored label key as a human name: `archer_rush` -> `Archer Rush`.

	The same fallback bot/web.py and bot/replay_stats/card_query.py already
	apply to these keys. Deliberately NOT a hand-written display map: there
	are 17 strategy keys and 11 spawn keys, a map would have to be kept in
	step with a classifier registry that lives in another package, and a key
	missing from it would render as nothing at all. """
	name = str(key or "")
	if strip_prefix and name.startswith(strip_prefix):
		name = name[len(strip_prefix):]
	return name.replace("_", " ").title()


def render(rollup, gt=None):
	""" The scouting-report field's text for one player, or None when there is
	no field to render.

	`rollup` is the five-block blob bot/derived/rollups.py wrote (see its
	compute_rollup), or None when the player has no row at all. `gt` is the
	caller's translator (`ctx.qc.gt`); it defaults to the identity function so
	this module stays testable and importable with no Discord context.

	Three distinct answers, and the difference between them is load-bearing:

	  None rollup   -> PENDING. The player is not linked (or owns no reachable
	                   game), so there is nothing measured to report and the
	                   copy says exactly that.
	  empty render  -> None, i.e. the caller omits the field entirely. The
	                   player IS linked and has a row, but every line in it is
	                   below its floor or absent. Saying PENDING here would be
	                   a lie about a linked player; saying "0%" would be a lie
	                   about an unmeasured one; so the report simply has
	                   nothing to say yet and says nothing.
	  otherwise     -> one line per block that has something to stand on. """
	gt = gt or _identity
	if rollup is None:
		return gt(PENDING)

	lines = []

	# Medals. The denominator is games_ranked -- games the parser actually
	# scored this player in -- NOT games played, and the two are different
	# numbers (bot/derived/rollups.was_medal_eligible). It is printed beside
	# the rates for exactly that reason: 34% + 18% summing to 52% is correct
	# and expected, and only the denominator makes that legible.
	medals = rollup.get("medal_rates") or {}
	military, villager, ranked = medals.get("military"), medals.get("villager"), medals.get("games_ranked") or 0
	if ranked and military is not None and villager is not None:
		lines.append(gt(
			"Medals: **{military}%** military · **{villager}%** villager, over {games} ranked games"
		).format(military=_pct(military), villager=_pct(villager), games=ranked))

	# eAPM. Two independent samples, never one shared count: the median avg
	# rests on games_avg and the peak on games_peak, and today those are 38
	# and 0 for the same player.
	apm = rollup.get("apm") or {}
	median_avg, games_avg = apm.get("median_avg"), apm.get("games_avg") or 0
	median_peak, games_peak = apm.get("median_peak"), apm.get("games_peak") or 0
	if median_avg is not None and games_avg:
		if median_peak is not None and games_peak:
			lines.append(gt(
				"eAPM: **{median}** median over {games} games · peak **{peak}** over {peak_games}"
			).format(median=_num(median_avg), games=games_avg,
			         peak=_num(median_peak), peak_games=games_peak))
		else:
			lines.append(gt("eAPM: **{median}** median over {games} games").format(
				median=_num(median_avg), games=games_avg))

	# The three splits, each on its own. `wins` and `games` inside a split
	# always describe the same set of RESOLVED games, so losses is exactly
	# games - wins; a game mgz could not call is absent from both. That also
	# means a split's `games` can be smaller than the player's total games,
	# which is correct and must never be "reconciled" into agreement.
	for block, key_field, strip_prefix, feature in _SPLIT_BLOCKS:
		rows = rollup.get(block) or []
		if not rows:
			continue
		# compute_rollup ranks by (games desc, key asc), so the first row is
		# the top one -- no re-sorting here, and no re-ranking on win rate:
		# "top" means most-played, and the record beside it is what says how
		# it went.
		top = rows[0]
		games, wins = top.get("games") or 0, top.get("wins") or 0
		name = _label(top.get(key_field), strip_prefix)
		if feature == "strategy":
			lines.append(gt("Top strategy: **{name}** · {wins}W-{losses}L").format(
				name=name, wins=wins, losses=games - wins))
		elif feature == "spawn":
			lines.append(gt("Top spawn: **{name}** · {wins}W-{losses}L").format(
				name=name, wins=wins, losses=games - wins))
		else:
			lines.append(gt("Top unit: **{name}** · {wins}W-{losses}L").format(
				name=name, wins=wins, losses=games - wins))

	return "\n".join(lines) or None


def gaps(rollups_by_user, window_user_ids):
	""" Which report lines this community's data cannot fill yet, as counts
	over the players it has actually seen.

	`rollups_by_user` is {user_id: blob} for the community
	(rollups.fetch_community); `window_user_ids` is the set of Discord users
	who played a reported match here inside the coverage window
	(identity.window_player_ids). Returns a list of
	{feature, gated, of} dicts -- data, not sentences, so `/identity status`
	owns the copy and its translator and this module owns the arithmetic.

	Both populations are the SAME one -- players seen in the window -- so the
	numbers can be read against the coverage line directly. A rollup for
	someone who has not played in months is not a gap anybody can chase, and
	counting it would make the two halves of the command disagree.

	Everything here is counted from the rollups themselves. That is the whole
	point: identity v2 §3 has always required this list, and it was left
	unimplemented rather than hardcoded because until stage 5a no feature
	actually gated on identity, so any list would have been fiction. A
	hardcoded list would be fiction again -- it would name the same features
	on a community where none of them are gated.

	A feature with nothing gated is ABSENT from the list rather than reported
	as zero, the same rule `/identity status` already applies to its conflict
	count: this surface exists to make a real number stand out. """
	rollups_by_user = rollups_by_user or {}
	window = set(window_user_ids or ())
	scouted = [rollups_by_user[user_id] for user_id in window if user_id in rollups_by_user]

	out = []
	if missing := len(window) - len(scouted):
		out.append(dict(feature="report", gated=missing, of=len(window)))
	for block, _key_field, _strip_prefix, feature in _SPLIT_BLOCKS:
		if thin := sum(1 for rollup in scouted if not (rollup or {}).get(block)):
			out.append(dict(feature=feature, gated=thin, of=len(scouted)))
	return out
