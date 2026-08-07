# -*- coding: utf-8 -*-
"""The scouting report `/rank` prints, rendered from one player_rollups blob.

Pure -- no DB, no Discord, no I/O -- for the same reason
nammaoe2bot/derived/rollups.py's compute is: every copy rule below is a claim about
what is honest to SHOW, and a claim you cannot test without a database is a
claim nobody tests. The caller (nammaoe2bot/discord/commands/stats.py) resolves the community,
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
    NULL on every row ingested before the bucket-capturing parser shipped, so
    for now `median_peak` is null and `games_peak` is 0 on most players: the
    eAPM line renders the median alone rather than a blank, an em-dash, or a 0
    standing in for a peak nobody measured. As buckets accumulate the peak
    appears on its own, with its own count.
  * NOTHING IS RENDERED BELOW MIN_GAMES. compute_rollup has already applied
    that floor to the three splits -- a split below it is absent from the blob
    entirely rather than flagged -- and this module applies the same floor to
    the medal and eAPM lines, which the blob cannot pre-filter because their
    denominators live inside them. There is no low-sample warning anywhere and
    there must never be one.

-- THE WINDOW ---------------------------------------------------------------

The blob covers the last `window_days` (rollups.WINDOW_DAYS, 60), not all of
history, which gives this module a state the old lifetime report could not have:
a player who IS linked, HAS games, and has played none of them recently. That is
neither "pending linking" nor "nothing to say", and rendering it as either would
be a lie about a different thing each time -- so `render` distinguishes four
outcomes, and the difference between them is load-bearing. See its docstring.

-- HOW THE WINS-MOST / LOSES-MOST CLAUSES ARE CHOSEN ------------------------

The two sentences name the strategy, spawn position and unit a player does best
and worst with. Which one that is, is a question with three plausible answers
and two of them produce false sentences on real data:

  MOST WINS (and most losses) is really "most played", because both counts
  scale with volume. Measured against production, it picks the same clause for
  both sentences most of the time, and it routinely picks a clause with a
  LOSING record for the wins-most line -- "wins most opening Safe Castle
  (12W-18L)" is a sentence this report would have printed.

  HIGHEST WIN RATE inverts the failure. At a 5-game floor the extreme rates are
  always the smallest samples, so it reliably picks the 6-game fluke over the
  40-game strength: "massing Arambai (5W-1L)" instead of "massing Trebuchet
  (27W-10L)" for the same player, on the same data.

  SHRUNK WIN RATE, which is what this does, ranks on the win rate a split would
  have if it were pooled with SHRINK_K imaginary games played at the player's
  OWN baseline rate. A big sample barely moves and keeps its real rate; a
  6-game sample is pulled most of the way back to the player's average and has
  to be genuinely extreme to survive it. That is the standard fix for exactly
  this problem, and on production data it picks the 37-game strength over the
  6-game fluke without ever picking a losing record as a strength.

The baseline is the player's own record over the same window, not 0.5 and not
the community's: shrinking a 40%-win player's clauses toward 50% would rank
their least-bad option as a strength.

-- WHAT gaps() IS FOR -------------------------------------------------------

`gaps()` answers the other half of the same question for `/identity status`
(identity v2 SS3): given what a community's rollups actually hold, which of
these lines can it not fill? Counted, never asserted -- a hardcoded list of
"features that might be gated" would be fiction again, which is precisely
why that half of the spec waited until stage 5 to be implementable.
"""

# The exact copy identity v2 SS5 pins for every analysis surface gated behind
# linking. Spelled once, here, so `/rank`, `/identity status`'s explanation of
# it, and every surface that cuts over in 5b-5d cannot drift into three
# slightly different sentences.
PENDING = "Statistics pending linking"

# The display floor, applied to the medal and eAPM lines. Equal to
# rollups.SPLIT_MIN_GAMES by intent -- one report should not report a strategy
# over 5 games and a medal rate over 2 -- and pinned equal by
# tests/test_scouting_report.py rather than imported, because importing
# nammaoe2bot.derived.rollups drags in nammaoe2bot.runtime.database and costs this module the purity
# its whole test suite rests on.
MIN_GAMES = 5

# Pseudo-games of the player's own baseline mixed into every split before it is
# ranked. See the module docstring for why shrinkage at all; 10 is chosen against
# the floor: a split at exactly MIN_GAMES=5 games is weighted 1:2 against the
# prior and so has to be extreme to win, while a 40-game split keeps 80% of its
# own rate. Raising it toward the sample sizes in play (25 games is the median
# player's whole window) would flatten every clause back to the baseline and pick
# on volume again; lowering it to ~3 reproduces the raw-rate failure.
# Measured on production before it was fixed: sweeping 10 / 20 / 30 over the
# eight busiest players changed exactly ONE of 48 clauses. The picks are not
# sensitive to this constant on real data, so it is not a tuning knob anyone
# needs to revisit -- the shrinkage is doing structural work (suppressing
# floor-level flukes), not fine-grained ranking, and the visible W-L record is
# what lets a reader judge a thin one anyway.
SHRINK_K = 10.0

# Medals are counted per game, never as a percentage, and the glyphs are the ones
# nammaoe2bot/features/postgame/card.MEDAL_GLYPHS already stamps on the match card for the same two
# awards -- a player who sees a crossed sword on the card should see the same
# mark here. Copied rather than imported: nammaoe2bot/features/postgame/card.py reaches Discord and
# the database, and this module's whole test suite rests on importing nothing
# that does. tests/test_scouting_report.py pins the two pairs equal.
#
# Rate and per-game count are the SAME NUMBER here -- a player can hold at most
# one military medal in a game -- so this is a framing choice, not a different
# measurement. "0.35 military medals per game" is a quantity somebody can
# picture; "35% military" invites the reader to ask 35% of what, and the honest
# answer ("of the games in which the parser ranked you") is not something a
# percentage sign conveys.
_MILITARY_GLYPH = "⚔"
_VILLAGER_GLYPH = "\U0001f33e"

# (blob block, the key field inside its rows, feature id used by gaps(),
#  how the clause reads inside a sentence). The key field differs by block
# because compute_rollup names it after what it holds -- `key` for a classifier
# label, `unit` for a unit name -- and flattening that here would only hide which
# block a bug came from.
_SPLIT_BLOCKS = (
	("strategies", "key", "strategy", "opening {name}"),
	("spawns", "key", "spawn", "{name}"),
	("units", "unit", "unit", "massing {name}"),
)

# The three spawn labels the report can reach (nammaoe2bot/derived/game_labels.
# POSITION_KEYS), as they read inside "wins most when ...". Written out rather
# than derived from the key, because "Spawn Isolated" is not English and the
# sentence has to be: the clause is the only one of the three that is a phrase
# rather than a name.
_POSITION_PHRASES = {
	"spawn_near_enemy": "spawning next to the enemy",
	"spawn_isolated": "spawning alone",
	"spawn_near_ally": "spawning with their team",
}


def _identity(text):
	return text


def _num(value):
	""" A median as text. compute_rollup deliberately does NOT round an
	even-length sample to an integer -- 62.5 and 62 are different samples and
	must not print as the same number -- so the fractional half survives here
	while a whole number still renders without a pointless `.0`. """
	rounded = round(float(value), 1)
	return str(int(rounded)) if rounded == int(rounded) else str(rounded)


def _label(key):
	""" A stored label key as a human name: `archer_rush` -> `Archer Rush`.

	The same fallback bot/web.py and nammaoe2bot/ingest/card_query.py already
	apply to these keys. Deliberately NOT a hand-written display map: there
	are 17 strategy keys, a map would have to be kept in step with a classifier
	registry that lives in another package, and a key missing from it would
	render as nothing at all. """
	return str(key or "").replace("_", " ").title()


def _record(split):
	""" (games, wins, losses) off one split row. `wins` and `games` always
	describe the same set of RESOLVED games (rollups.has_known_outcome), so
	losses is exactly games - wins and a game mgz could not call is in neither.
	"""
	games, wins = split.get("games") or 0, split.get("wins") or 0
	return games, wins, games - wins


def _shrunk(split, baseline_rate):
	""" The split's win rate pulled SHRINK_K games toward the player's own. """
	games, wins, _ = _record(split)
	return (wins + SHRINK_K * baseline_rate) / (games + SHRINK_K)


def _extremes(rows, key_field, baseline_rate):
	""" (best, worst) split out of one block, or (None, None) / (row, None).

	The two are always DIFFERENT rows: the worst is the last of the same
	ordering, so a block holding ONE split contributes to the wins-most sentence
	and not to the loses-most one. That asymmetry is deliberate -- "this is both
	what they are best and what they are worst at" is not a fact about a player,
	it is a fact about having one data point.

	Ties break on the larger sample first (between two equal rates, the better
	measured one is the more useful claim) and then on the key, so the ordering
	is total: an unchanged rollup renders identically forever, which is what
	makes "did this player's report actually change?" answerable at all.
	"""
	if not rows:
		return None, None
	ranked = sorted(rows, key=lambda r: (-_shrunk(r, baseline_rate),
	                                     -(r.get("games") or 0), str(r.get(key_field) or "")))
	return ranked[0], (ranked[-1] if len(ranked) > 1 else None)


def _display_name(key, key_field, gt):
	""" A split's key as it reads inside a sentence.

	A unit name is already a display name ("Bombard Cannon") and is passed
	through untouched -- _label would title-case it into something subtly wrong
	-- and no pluralisation is attempted at all, because "Arambais" and
	"Mangudais" are what a naive plural does to half this game's unique units. """
	if key_field == "unit":
		return str(key or "")
	if key in _POSITION_PHRASES:
		return gt(_POSITION_PHRASES[key])
	return _label(key)


def baseline_rate(rollup):
	""" The player's own win rate over the window -- the prior every clause is
	shrunk toward.

	0.5 when the window holds no resolved game, which is the only defensible
	stand-in but can never actually be reached from a rendered report: a player
	with no resolved game has no split above the floor either, so there is no
	clause for it to rank. """
	baseline = (rollup or {}).get("baseline") or {}
	games = baseline.get("games") or 0
	return (baseline.get("wins") or 0) / games if games else 0.5


def highlights(rollup, gt=None):
	""" The chosen wins-most and loses-most clauses, as DATA rather than prose.

	{"wins_most": [{dimension, key, name, games, wins, losses}, ...],
	 "loses_most": [...]}, each list holding at most one entry per dimension and
	ordered strategy, spawn, unit.

	SEPARATE FROM render() SO THE SELECTION HAS EXACTLY ONE IMPLEMENTATION.
	`/rank` renders these as two sentences and bot/web.py's player page lays the
	same two out as a card, and the choice of WHICH strategy is a player's
	strength is a shrunk-rate calculation against their own baseline (see the
	module docstring) -- not a formatting detail either surface should be
	repeating. The web layer used to re-pick "the top row of each list" in
	JavaScript, which was survivable while the rule was "the first one"; it is
	not survivable now, and a second copy of the shrinkage would drift silently,
	with both pages still looking entirely plausible. """
	gt = gt or _identity
	rate = baseline_rate(rollup)
	out = dict(wins_most=[], loses_most=[])
	for block, key_field, feature, _template in _SPLIT_BLOCKS:
		best, worst = _extremes((rollup or {}).get(block) or [], key_field, rate)
		for split, side in ((best, "wins_most"), (worst, "loses_most")):
			if not split:
				continue
			key = split.get(key_field)
			games, wins, losses = _record(split)
			out[side].append(dict(dimension=feature, key=key,
			                      name=_display_name(key, key_field, gt),
			                      games=games, wins=wins, losses=losses))
	return out


def _clause(highlight, template, gt):
	""" One clause of a wins-most / loses-most sentence, with its record. """
	return (gt(template).format(name="**{}**".format(highlight["name"]))
	        + " ({}W-{}L)".format(highlight["wins"], highlight["losses"]))


def _join(parts, gt):
	""" "a", "a and b", "a, b and c" -- so a sentence missing a clause still
	reads as a sentence rather than as a list with a hole in it. """
	if len(parts) == 1:
		return parts[0]
	return gt("{head} and {tail}").format(head=", ".join(parts[:-1]), tail=parts[-1])


def _sentence(lead, picks, gt):
	if not picks:
		return None
	return f"{gt(lead)} {_join(picks, gt)}."


def render(rollup, gt=None):
	""" The scouting-report field's text for one player, or None when there is
	no field to render.

	`rollup` is the blob nammaoe2bot/derived/rollups.py wrote (see its compute_rollup),
	or None when the player has no row at all. `gt` is the caller's translator
	(`ctx.qc.gt`); it defaults to the identity function so this module stays
	testable and importable with no Discord context.

	FOUR distinct answers, and the difference between them is the whole reason
	this function is not three lines:

	  None rollup    -> PENDING. The player is not linked (or owns no game this
	                    community can reach), so there is nothing measured to
	                    report and the copy says exactly that.
	  row, 0 games   -> "No games in the last N days". They ARE linked and their
	                    history is right there; it is simply all older than the
	                    window. PENDING here would tell a linked player to link,
	                    and silence would read as "we have nothing on you".
	  row, thin      -> "Only N games in the last M days". Some games, none of
	                    the lines above their floor. Saying nothing would leave
	                    the reader unable to tell a quiet player from a broken
	                    report; the count says which it is without printing a
	                    single figure that rests on it.
	  otherwise      -> the report: one line per block with something to stand
	                    on, then the two sentences.
	"""
	gt = gt or _identity
	if rollup is None:
		return gt(PENDING)

	window_days = rollup.get("window_days")
	window_games = (rollup.get("baseline") or {}).get("games") or 0

	if window_days is not None and not window_games:
		return gt("No games in the last {days} days").format(days=window_days)

	lines = []

	# Medals, as a count per game rather than a percentage -- see _MEDAL_GLYPHS.
	# The denominator is games_ranked, games the parser actually scored this
	# player in, NOT games played, and the two are different numbers
	# (rollups.was_medal_eligible). It is printed for exactly that reason: 0.35
	# and 0.36 summing past a half is correct and expected, and only the
	# denominator makes that legible.
	medals = rollup.get("medal_rates") or {}
	ranked = medals.get("games_ranked") or 0
	military, villager = medals.get("military"), medals.get("villager")
	if ranked >= MIN_GAMES and military is not None and villager is not None:
		lines.append(gt(
			"{mil_glyph} **{military}** military · {vil_glyph} **{villager}** villager "
			"medals per game, over {games} ranked games"
		).format(mil_glyph=_MILITARY_GLYPH, military=f"{military:.2f}",
		         vil_glyph=_VILLAGER_GLYPH, villager=f"{villager:.2f}", games=ranked))

	# eAPM. Two independent samples, never one shared count: the median avg
	# rests on games_avg and the peak on games_peak, and for most players today
	# those are 173 and 0.
	apm = rollup.get("apm") or {}
	median_avg, games_avg = apm.get("median_avg"), apm.get("games_avg") or 0
	median_peak, games_peak = apm.get("median_peak"), apm.get("games_peak") or 0
	#
	# BOTH FIGURES ARE MEDIANS AND THE COPY SAYS SO ON BOTH. A bare "peak 140"
	# reads as a maximum -- this player's busiest minute ever -- when it is the
	# median of their per-game busiest minutes, a typical hard moment rather than
	# a record. The two are wildly different numbers on a heavy-tailed measure,
	# and the one a reader would assume is the one this deliberately does not
	# report: a max over a season is a single parse artefact away from fiction,
	# which is why rollups takes a median on both axes in the first place.
	if median_avg is not None and games_avg >= MIN_GAMES:
		if median_peak is not None and games_peak >= MIN_GAMES:
			lines.append(gt(
				"eAPM: median **{median}** over {games} games · "
				"median peak **{peak}** over {peak_games}"
			).format(median=_num(median_avg), games=games_avg,
			         peak=_num(median_peak), peak_games=games_peak))
		else:
			lines.append(gt("eAPM: median **{median}** over {games} games").format(
				median=_num(median_avg), games=games_avg))

	# The two sentences. Each block contributes at most one clause to each, and
	# a block with nothing above the floor contributes to neither -- so a player
	# with only a unit split gets a one-clause sentence rather than no sentence,
	# and the sentence still reads as English (see _join).
	templates = {feature: template for _b, _k, feature, template in _SPLIT_BLOCKS}
	picked = highlights(rollup, gt)
	for lead, side in (("Wins most", "wins_most"), ("Loses most", "loses_most")):
		parts = [_clause(h, templates[h["dimension"]], gt) for h in picked[side]]
		if line := _sentence(lead, parts, gt):
			lines.append(line)

	if lines:
		return "\n".join(lines)
	if window_days is not None:
		# Two whole sentences rather than one with a spliced-in "s". The singular
		# case is the common one here -- somebody who played once this month is
		# exactly who this line exists for -- and a translator handed
		# "Only {games} game{s}" cannot render a language whose plural is not a
		# suffix.
		if window_games == 1:
			return gt("Only 1 game in the last {days} days").format(days=window_days)
		return gt("Only {games} games in the last {days} days").format(
			games=window_games, days=window_days)
	return None


