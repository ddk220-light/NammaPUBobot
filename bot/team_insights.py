# -*- coding: utf-8 -*-
"""Post recency-weighted "storyline" stats when a match's teams are formed.

The point isn't lifetime trivia (in a tight pickup community everyone has played
everyone hundreds of times, so "A vs B, 279-262" is noise). The point is *live
narrative tension* — active streaks and "will it flip TONIGHT?" hooks drawn from
recent ranked history:

  * Perfect / cursed pair   — teammates who have NEVER lost (or never won) together
  * Best / worst teammate   — a player teamed today with the mate who most lifts /
                              tanks their win-rate ("flip your win-rate")
  * H2H streak              — opponents where one has won the last K meetings
  * Teammate streak         — teammates on a K-game win/loss run together
  * Deadlock decider        — opponents dead-even over their recent meetings
  * Form streak             — a player on a personal K-game heater / skid
  * Trio record             — a three-player subset of one side with a lopsided shared record
  * Exact lineup            — this exact side has shared a team before, inside the window

All history is read once, ordered by match_id, and every bit of analysis is pure
Python (unit-testable without a DB). ``build_insights_embed`` returns an ``Embed``
or ``None``. Heavy imports (nextcord) are deferred so the pure helpers import
cleanly under the CI test shim.

Selection caps how many lines any single player or type can own (a past version
let one player saturate the embed), and favours a diverse, dramatic 3-4.
"""
import math
import random
import time
from collections import Counter, namedtuple

from core.database import db

# ── Tunables ─────────────────────────────────────────────────────────────
MAX_BULLETS = 4

WINDOW_DAYS = 90           # hard cutoff: nothing older than this is loaded at all

PERFECT_MIN = 5            # T1: decisive games, 100% one-way

BW_MIN_OVERALL = 10        # T2: min overall decisive games for a baseline
BW_MIN_GAMES = 6           # T2: min games with a teammate to judge the pairing
BW_SWING = 0.15            # T2: min win-rate swing vs baseline

H2H_MIN_STREAK = 4         # T3: trailing one-sided meetings
H2H_MIN_SERIES = 4         # T3: min total meetings

MATE_MIN_STREAK = 4        # T4: trailing same-result games together
MATE_MIN_TOGETHER = 6      # T4: min games as teammates

DEADLOCK_MIN = 6           # T5: min recent meetings to call a deadlock
DEADLOCK_WINDOW = 8        # T5: only the last N meetings count

FORM_MIN_STREAK = 5        # T6: trailing personal win/loss run

TRIO_MIN_GAMES = 5         # T7: min decisive games this exact trio shared a side
TRIO_MIN_SHARE = 0.75      # T7: fraction of them going one direction

LINEUP_MIN_GAMES = 2       # T8: min prior decisive games as this exact side
LINEUP_MIN_SIDE = 3        # T8: below this the lineup is just the pair line

# Selection caps
PER_PLAYER_CAP = 2
PER_TYPE_CAP = 2
DEADLOCK_TYPE_CAP = 1
TRIO_TYPE_CAP = 1
LINEUP_TYPE_CAP = 1

# Types capped harder than PER_TYPE_CAP. These are the ones whose candidates can
# overlap without being subsets of each other, which _overlaps cannot dedup.
_TYPE_CAPS = {"deadlock": DEADLOCK_TYPE_CAP, "trio": TRIO_TYPE_CAP,
              "lineup": LINEUP_TYPE_CAP}

# Drama weights — a single comparable axis across types.
W_PERFECT = 6.0
W_TRIO = 7.0
# The (10 + games) term is what keeps the jackpot on top: a plain multiple of
# sample size would let a long perfect-pair run (W_PERFECT * n) outrank it.
W_LINEUP = 9.0
W_MATE_WR = 4.0
W_H2H = 3.0
W_MATE = 2.6
W_DEADLOCK = 2.3
W_FORM = 2.2
H2H_MILESTONE_BONUS = 2.0
LOSS_BIAS = 1.10
WORST_BIAS = 1.15
PERFECT_COND = 1.25

# Every type _candidates can emit. _phrase must handle all of them: an
# unrenderable candidate raises inside build_insights_embed's list
# comprehension and silently costs the whole embed.
CANDIDATE_TYPES = ("lineup", "perfect", "mate_wr", "h2h", "mate", "trio",
                   "deadlock", "form")

OrderedHistory = namedtuple("OrderedHistory", "order matches nicks")


def window_start(now_ts, days=WINDOW_DAYS):
	"""Unix cutoff for the rolling history window."""
	return int(now_ts) - days * 86400


# ── Ordered history (pure) ───────────────────────────────────────────────
def _index_history(rows):
	"""Fold raw ``(match_id, user_id, nick, team, winner)`` rows into an ordered
	history. NULL-team rows (never-picked players) are skipped. ``order`` is the
	sorted match_id list, so it's correct regardless of row order."""
	matches, nicks = {}, {}
	for r in rows:
		team = r["team"]
		if team is None:
			continue
		mid = r["match_id"]
		entry = matches.get(mid)
		if entry is None:
			entry = matches[mid] = {"winner": r["winner"], "teams": {}}
		entry["teams"][r["user_id"]] = int(team)
		if r.get("nick"):
			nicks[r["user_id"]] = r["nick"]
	return OrderedHistory(order=sorted(matches), matches=matches, nicks=nicks)


# ── Series + primitives (pure) ───────────────────────────────────────────
def _h2h_series(prior, matches, a, b):
	"""Ordered winners (a|b|None) for decisive/draw games where a,b were opponents."""
	out = []
	for mid in prior:
		t = matches[mid]["teams"]
		if a in t and b in t and t[a] != t[b]:
			w = matches[mid]["winner"]
			out.append(None if w is None else (a if int(w) == t[a] else b))
	return out


def _teammate_series(prior, matches, a, b):
	"""Ordered results (True win / False loss / None draw) for games a,b teamed."""
	out = []
	for mid in prior:
		t = matches[mid]["teams"]
		if a in t and b in t and t[a] == t[b]:
			w = matches[mid]["winner"]
			out.append(None if w is None else int(w) == t[a])
	return out


def _group_series(prior, matches, group):
	"""Ordered results (True win / False loss) for prior matches where every
	member of ``group`` shared one side.

	Unlike ``_teammate_series`` this drops draws rather than recording None:
	trio and lineup lines quote a record, and a draw belongs in neither column.
	"""
	out = []
	for mid in prior:
		t = matches[mid]["teams"]
		if not group <= t.keys():
			continue
		sides = {t[u] for u in group}
		if len(sides) != 1:
			continue
		w = matches[mid]["winner"]
		if w is None:
			continue
		out.append(int(w) == next(iter(sides)))
	return out


def _form_series(prior, matches, p):
	"""Ordered results (True/False/None) for every game p played."""
	out = []
	for mid in prior:
		t = matches[mid]["teams"]
		if p in t:
			w = matches[mid]["winner"]
			out.append(None if w is None else int(w) == t[p])
	return out


def _trailing_streak(series):
	"""(length, value) of the trailing run of equal non-None values. A trailing
	draw (None) breaks the narrative streak -> (0, None)."""
	if not series or series[-1] is None:
		return 0, None
	last = series[-1]
	k = 0
	for v in reversed(series):
		if v == last:
			k += 1
		else:
			break
	return k, last


def _overall_record(prior, matches, p):
	"""(wins, decisive_games) for player p."""
	wins = games = 0
	for mid in prior:
		t = matches[mid]["teams"]
		if p not in t:
			continue
		w = matches[mid]["winner"]
		if w is None:
			continue
		games += 1
		wins += int(int(w) == t[p])
	return wins, games


def _teammate_winrates(prior, matches, p, min_games):
	"""{q: (wins, games)} for every teammate q of p with >= min_games together."""
	rec = {}
	for mid in prior:
		t = matches[mid]["teams"]
		if p not in t:
			continue
		w = matches[mid]["winner"]
		if w is None:
			continue
		won = int(w) == t[p]
		for q, tq in t.items():
			if q == p or tq != t[p]:
				continue
			wins, games = rec.get(q, (0, 0))
			rec[q] = (wins + (1 if won else 0), games + 1)
	return {q: (w, g) for q, (w, g) in rec.items() if g >= min_games}


def _pairs(ids):
	"""Unordered within-team pairs (avoids itertools just for 2-combos)."""
	for i in range(len(ids)):
		for j in range(i + 1, len(ids)):
			yield ids[i], ids[j]


def _trios(ids):
	"""Unordered within-team triples, in a deterministic order (mirrors _pairs)."""
	ids = sorted(ids)
	for i in range(len(ids)):
		for j in range(i + 1, len(ids)):
			for k in range(j + 1, len(ids)):
				yield ids[i], ids[j], ids[k]


# ── Candidate builders (pure) — each attaches a comparable drama `score` ──
def _perfect_candidates(prior, matches, t0_ids, t1_ids):
	"""T1 — same-team pair whose entire decisive shared history is one-way."""
	cands = []
	for team_idx, ids in ((0, t0_ids), (1, t1_ids)):
		for a, b in _pairs(ids):
			dec = [v for v in _teammate_series(prior, matches, a, b) if v is not None]
			if len(dec) < PERFECT_MIN:
				continue
			if all(dec):
				won = True
			elif not any(dec):
				won = False
			else:
				continue
			cands.append({
				"type": "perfect",
				"score": W_PERFECT * len(dec) * (LOSS_BIAS if not won else 1.0),
				"players": frozenset((a, b)),
				"teams": frozenset((team_idx,)),
				"data": {"ids": [a, b], "n": len(dec), "won": won, "team_idx": team_idx},
			})
	return cands


def _mate_wr_candidates(prior, matches, t0_ids, t1_ids):
	"""T2 — best/worst teammate present today (the "flip your win-rate" signal)."""
	cands = []
	for team_idx, ids in ((0, t0_ids), (1, t1_ids)):
		for p in ids:
			ow, og = _overall_record(prior, matches, p)
			if og < BW_MIN_OVERALL:
				continue
			base = ow / og
			wr = _teammate_winrates(prior, matches, p, BW_MIN_GAMES)
			best = worst = None
			for q in ids:
				if q == p or q not in wr:
					continue
				w, g = wr[q]
				r = w / g
				if r - base >= BW_SWING and (best is None or r - base > best[2] - best[1]):
					best = (q, base, r, g)
				if base - r >= BW_SWING and (worst is None or base - r > worst[1] - worst[2]):
					worst = (q, base, r, g)
			for kind, pick in (("best", best), ("worst", worst)):
				if pick is None:
					continue
				q, base_, r, g = pick
				score = W_MATE_WR * abs(r - base_) * math.sqrt(min(g, 25))
				if kind == "worst":
					score *= WORST_BIAS
				if r in (0.0, 1.0):
					score *= PERFECT_COND
				cands.append({
					"type": "mate_wr",
					"score": score,
					"players": frozenset((p, q)),
					"teams": frozenset((team_idx,)),
					"data": {"p": p, "q": q, "wr": r, "base": base_, "games": g, "kind": kind},
				})
	return cands


def _h2h_candidates(prior, matches, t0_ids, t1_ids):
	"""T3 — opposing pair where one has won the last K>=4 meetings."""
	cands = []
	for a in t0_ids:
		for b in t1_ids:
			s = _h2h_series(prior, matches, a, b)
			k, who = _trailing_streak(s)
			if k < H2H_MIN_STREAK or len(s) < H2H_MIN_SERIES:
				continue
			loser = b if who == a else a
			decisive = len([v for v in s if v is not None])
			sweep = k == decisive
			score = W_H2H * (k - 2) + (H2H_MILESTONE_BONUS if sweep else 0.0)
			cands.append({
				"type": "h2h",
				"score": score,
				"players": frozenset((a, b)),
				"teams": frozenset((0, 1)),
				"data": {"winner": who, "loser": loser, "k": k, "series": len(s), "sweep": sweep},
			})
	return cands


def _mate_candidates(prior, matches, t0_ids, t1_ids):
	"""T4 — same-team pair on a K>=4 win/loss run; lifetime total is the flavour."""
	cands = []
	for team_idx, ids in ((0, t0_ids), (1, t1_ids)):
		for a, b in _pairs(ids):
			s = _teammate_series(prior, matches, a, b)
			k, val = _trailing_streak(s)
			if k < MATE_MIN_STREAK or len(s) < MATE_MIN_TOGETHER:
				continue
			depth = 1 + 0.4 * math.log10(len(s) / MATE_MIN_TOGETHER)
			score = W_MATE * (k - 2) * depth * (LOSS_BIAS if not val else 1.0)
			cands.append({
				"type": "mate",
				"score": score,
				"players": frozenset((a, b)),
				"teams": frozenset((team_idx,)),
				"data": {"ids": [a, b], "k": k, "series": len(s), "won": val, "team_idx": team_idx},
			})
	return cands


def _trio_candidates(prior, matches, t0_ids, t1_ids):
	"""T7 — a three-player subset of one side with a lopsided shared record."""
	cands = []
	for team_idx, ids in ((0, t0_ids), (1, t1_ids)):
		for a, b, c in _trios(ids):
			group = frozenset((a, b, c))
			s = _group_series(prior, matches, group)
			if len(s) < TRIO_MIN_GAMES:
				continue
			wins = sum(s)
			share = max(wins, len(s) - wins) / len(s)
			if share < TRIO_MIN_SHARE:
				continue
			won = wins * 2 > len(s)
			cands.append({
				"type": "trio",
				"score": W_TRIO * len(s) * share * (LOSS_BIAS if not won else 1.0),
				"players": group,
				"teams": frozenset((team_idx,)),
				"data": {"ids": [a, b, c], "wins": wins, "games": len(s),
				         "won": won, "team_idx": team_idx},
			})
	return cands


def _lineup_candidates(prior, matches, t0_ids, t1_ids):
	"""T8 — this exact side has shared a team before, inside the window.

	The rarest thing the module can say: only about one team-side in ten has
	*ever* played together before within 90 days, so the line leans on the
	reunion itself rather than on the record.
	"""
	cands = []
	for team_idx, ids in ((0, t0_ids), (1, t1_ids)):
		if len(ids) < LINEUP_MIN_SIDE:
			continue
		group = frozenset(ids)
		s = _group_series(prior, matches, group)
		if len(s) < LINEUP_MIN_GAMES:
			continue
		wins = sum(s)
		one_way = wins == 0 or wins == len(s)
		cands.append({
			"type": "lineup",
			"score": W_LINEUP * (10 + len(s)) * (PERFECT_COND if one_way else 1.0),
			"players": group,
			"teams": frozenset((team_idx,)),
			"data": {"ids": sorted(ids), "wins": wins, "games": len(s),
			         "one_way": one_way, "won": wins * 2 > len(s),
			         "team_idx": team_idx},
		})
	return cands


def _deadlock_candidates(prior, matches, t0_ids, t1_ids):
	"""T5 — opposing pair tied over their recent meetings (the decider)."""
	cands = []
	for a in t0_ids:
		for b in t1_ids:
			dec = [v for v in _h2h_series(prior, matches, a, b) if v is not None]
			recent = dec[-DEADLOCK_WINDOW:]
			if len(recent) < DEADLOCK_MIN:
				continue
			ca, cb = recent.count(a), recent.count(b)
			if ca != cb:
				continue
			cands.append({
				"type": "deadlock",
				# A tie is dramatic but flat — a reliable mid-tier filler, NOT the
				# marquee. Kept just below a fresh streak so streaks/teammate-swings
				# lead and deadlock fills for variety (depth adds only a nudge).
				"score": W_DEADLOCK + 0.4 * len(recent),
				"players": frozenset((a, b)),
				"teams": frozenset((0, 1)),
				"data": {"ids": [a, b], "each": ca, "n": len(recent)},
			})
	return cands


def _form_candidates(prior, matches, all_ids, team_of):
	"""T6 — a single player on a K>=5 personal win/loss run."""
	cands = []
	for p in all_ids:
		k, val = _trailing_streak(_form_series(prior, matches, p))
		if k < FORM_MIN_STREAK:
			continue
		cands.append({
			"type": "form",
			"score": W_FORM * (k - 3) * (LOSS_BIAS if not val else 1.0),
			"players": frozenset((p,)),
			"teams": frozenset((team_of[p],)),
			"data": {"p": p, "k": k, "won": val},
		})
	return cands


# ── Selection (pure) ─────────────────────────────────────────────────────
def _overlaps(players, covered):
	return any(players == s or players <= s or s <= players for s in covered)


def _select(candidates, *, limit=MAX_BULLETS, rng=random):
	"""Pick up to ``limit`` diverse, dramatic lines. Caps how many lines any one
	player or type can own (kills single-player saturation), de-dups overlapping
	player-sets, tries to mention both teams, then relaxes the type cap to fill."""
	if not candidates:
		return []
	keyed = [(c["score"], rng.random(), c) for c in candidates]
	ordered = [c for _s, _r, c in sorted(keyed, key=lambda x: (-x[0], x[1]))]

	chosen, per_player, per_type, covered = [], Counter(), Counter(), []

	def type_cap(t):
		return _TYPE_CAPS.get(t, PER_TYPE_CAP)

	def eligible(c):
		if per_type[c["type"]] >= type_cap(c["type"]):
			return False
		if any(per_player[u] >= PER_PLAYER_CAP for u in c["players"]):
			return False
		return not _overlaps(c["players"], covered)

	def take(c):
		chosen.append(c)
		per_type[c["type"]] += 1
		covered.append(c["players"])
		for u in c["players"]:
			per_player[u] += 1

	def drop(c):
		chosen.remove(c)
		per_type[c["type"]] -= 1
		covered.remove(c["players"])
		for u in c["players"]:
			per_player[u] -= 1

	# PASS 1 — greedy with caps + dedup.
	for c in ordered:
		if len(chosen) >= limit:
			break
		if eligible(c):
			take(c)

	# PASS 2 — make sure both line-ups get a mention.
	covered_teams = set().union(*(c["teams"] for c in chosen)) if chosen else set()
	if chosen and ({0, 1} - covered_teams):
		missing = next(iter({0, 1} - covered_teams))
		for c in ordered:
			if missing not in c["teams"] or not eligible(c):
				continue
			if len(chosen) < limit:
				take(c)
			else:
				removable = [
					x for x in chosen
					if (set().union(*(y["teams"] for y in chosen if y is not x)) or set()) >= ({0, 1} - {missing})
				]
				if removable:
					drop(min(removable, key=lambda x: x["score"]))
					take(c)
			break

	# PASS 3 — relax the generic type cap (NOT the player cap, NOT dedup, NOT the
	# hard per-type caps) to fill a thin match rather than show fewer lines.
	if len(chosen) < limit:
		for c in ordered:
			if len(chosen) >= limit:
				break
			if any(c is x for x in chosen):
				continue
			if c["type"] in _TYPE_CAPS and per_type[c["type"]] >= _TYPE_CAPS[c["type"]]:
				continue
			if any(per_player[u] >= PER_PLAYER_CAP for u in c["players"]):
				continue
			if _overlaps(c["players"], covered):
				continue
			take(c)

	chosen.sort(key=lambda c: c["score"], reverse=True)
	return chosen[:limit]


# ── Phrasing (pure) ──────────────────────────────────────────────────────
def _join_names(names):
	"""Oxford-free join: 'a', 'a & b', 'a, b & c'. Local so the pure layer keeps
	no core.* dependency (utils/preview_insights.py loads this module by path)."""
	if not names:
		return ""
	if len(names) == 1:
		return names[0]
	return ", ".join(names[:-1]) + f" & {names[-1]}"


def _positive(c):
	"""Is this storyline good news for its subject side?"""
	t, d = c["type"], c["data"]
	if t == "mate_wr":
		return d["kind"] == "best"
	if t in ("perfect", "mate", "trio", "lineup", "form"):
		return bool(d.get("won"))
	return True   # h2h and deadlock are neutral until the match settles them


def subject_of(c):
	"""Whose win makes this storyline's tease come true.

	Returns ("team", team_idx) or ("player", user_id). The player form exists
	because h2h/deadlock/form/mate_wr candidates carry no team_idx — the caller
	resolves the player to a side. bot/storyline_payoff.py depends on this.
	"""
	t, d = c["type"], c["data"]
	if t == "h2h":
		return ("player", d["winner"])
	if t == "deadlock":
		return ("player", d["ids"][0])
	if t == "form":
		return ("player", d["p"])
	if t == "mate_wr":
		return ("player", d["p"])
	return ("team", d["team_idx"])


def complement_of(c, nick, teams_meta, rosters):
	"""Who on the subject's own side a line did not already name.

	Returns ``(who, team_name, sole_team)``. ``who`` is "" when the line already
	names everyone on that side. ``sole_team`` is False for opposing-pair lines
	(h2h, deadlock), which have no single subject side. Public because
	bot/storyline_payoff.py builds a past-tense frame from the same complement.
	"""
	teams = sorted(c["teams"])
	if len(teams) != 1:
		return "", "", False
	team_idx = teams[0]
	name = (teams_meta[team_idx]["name"] if team_idx < len(teams_meta)
	        else f"Team {team_idx}")
	rest = [u for u in (rosters.get(team_idx) or []) if u not in c["players"]]
	if not rest:
		return "", name, True
	who = (f"the rest of {name}" if len(rest) > 2
	       else _join_names([f"**{nick.get(u, 'someone')}**" for u in rest]))
	return who, name, True


def _frame(c, nick, teams_meta, rosters, *, rng=random):
	"""A closing clause pulling the rest of the side into the story."""
	who, name, sole = complement_of(c, nick, teams_meta, rosters)
	if not sole:
		return rng.choice([
			"Do their teammates get a say?",
			"Six other players would like a word.",
		])
	if not who:
		return rng.choice([
			f"That is the whole of {name}.",
			f"All of {name}, back together.",
		])
	if _positive(c):
		return rng.choice([
			f"{who} along for the ride.",
			f"Good day to be {who}.",
		])
	return rng.choice([
		f"Good luck to {who}.",
		f"{who} inherit the problem.",
	])


def _phrase(c, nick, teams_meta, rosters, *, rng=random):
	"""Render one candidate as a fun "will it flip tonight?" one-liner."""
	def name(uid):
		return f"**{nick.get(uid, 'someone')}**"

	d = c["data"]
	t = c["type"]
	frame = _frame(c, nick, teams_meta, rosters, rng=rng)

	if t == "lineup":
		who = _join_names([name(u) for u in d["ids"]])
		w, g = d["wins"], d["games"]
		if d["one_way"] and w == g:
			opts = [
				f"🃏 Jackpot: this exact side has shared a team {g} times and never lost — **{w}-0**. {frame}",
				f"🎰 {who} — the same {len(d['ids'])} that are **{w}-0** together. Lightning, twice. {frame}",
			]
		elif d["one_way"]:
			opts = [
				f"🃏 This exact side has been assembled {g} times and won none of them (**0-{g}**). {frame}",
				f"🎰 {who}, back for another go at a **0-{g}** record. {frame}",
			]
		else:
			opts = [
				f"🃏 Rare reunion: this exact side has only played together {g} times before — **{w}-{g - w}**. {frame}",
				f"🎰 {who} ride again. Their shared record: **{w}-{g - w}**. {frame}",
			]
		return rng.choice(opts)

	if t == "trio":
		who = _join_names([name(u) for u in d["ids"]])
		w, g = d["wins"], d["games"]
		if d["won"]:
			opts = [
				f"🔱 {who} are **{w}-{g - w}** as a three. {frame}",
				f"⛓️ The trio that keeps delivering: {who}, **{w}-{g - w}** together. {frame}",
			]
		else:
			opts = [
				f"🕳️ {who} are **{w}-{g - w}** whenever all three line up. {frame}",
				f"🥀 History is unkind to {who} as a three — **{w}-{g - w}**. {frame}",
			]
		return rng.choice(opts)

	if t == "perfect":
		a, b = name(d["ids"][0]), name(d["ids"][1])
		n = d["n"]
		if d["won"]:
			opts = [
				f"💯 {a} & {b} have **never lost** as a pair — a flawless **{n}-0**. {frame}",
				f"🏆 Perfect record on the line: {a} & {b} are **{n}-0** together. {frame}",
			]
		else:
			opts = [
				f"🪦 The cursed duo returns: {a} & {b} have **never won** together (0-{n}). {frame}",
				f"💀 {a} & {b} are **0-{n}** as teammates — paired up again. {frame}",
			]
		return rng.choice(opts)

	if t == "mate_wr":
		p, q = name(d["p"]), name(d["q"])
		wr, base, g = round(d["wr"] * 100), round(d["base"] * 100), d["games"]
		if d["kind"] == "worst":
			if d["wr"] == 0.0:
				opts = [f"🪦 {p} has **never** won a game with {q} (0-fer over {g}). {frame}"]
			else:
				opts = [
					f"🧨 {p} sinks to **{wr}%** beside {q} (vs **{base}%** overall, {g}g) — paired again. {frame}",
					f"💀 History says {p} & {q} don't click: **{wr}%** together vs **{base}%** apart. {frame}",
				]
		else:
			if d["wr"] == 1.0:
				opts = [f"🏆 {p} & {q} have **never lost** as a duo — {p}'s {base}% leaps to a perfect 100%. {frame}"]
			else:
				opts = [
					f"🚀 {p} is a different player next to {q} — **{wr}%** together vs **{base}%** otherwise ({g}g). {frame}",
					f"🍀 {q} is {p}'s lucky charm: **{wr}%** as a duo vs **{base}%** overall ({g}g). {frame}",
				]
		return rng.choice(opts)

	if t == "h2h":
		winner, loser, k, series = name(d["winner"]), name(d["loser"]), d["k"], d["series"]
		if d["sweep"]:
			opts = [
				f"⚔️ {winner} has beaten {loser} **{k} straight** — {loser} has *never* won this one. {frame}",
				f"😤 {loser} is 0-for-the-last-{k} against {winner}. {frame}",
			]
		else:
			opts = [
				f"🔁 {winner} owns this lately: **{k} in a row** over {loser} (of {series} meetings). {frame}",
				f"🔒 {k} straight to {winner} over {loser}. {frame}",
			]
		return rng.choice(opts)

	if t == "mate":
		a, b, k, series = name(d["ids"][0]), name(d["ids"][1]), d["k"], d["series"]
		if d["won"]:
			opts = [
				f"🔥 {a} & {b} are on fire — **{k} straight wins** together (of {series}). {frame}",
				f"📈 {a} & {b} just keep winning side-by-side ({k} in a row, {series} all-time). {frame}",
			]
		else:
			opts = [
				f"🪦 {a} & {b} have **lost their last {k}** as teammates (of {series}). {frame}",
				f"❄️ {series} games as a duo and {a} & {b} are ice-cold: **{k} straight losses**. {frame}",
			]
		return rng.choice(opts)

	if t == "deadlock":
		a, b, each, n = name(d["ids"][0]), name(d["ids"][1]), d["each"], d["n"]
		opts = [
			f"⚖️ Deadlocked: {a} & {b} have split their last {n} meetings **{each}-{each}**. {frame}",
			f"🎯 The decider: {a} vs {b} is knotted **{each}-{each}** over their last {n}. {frame}",
			f"🪢 {n} recent meetings, **{each}-{each}** — {a} & {b} can't be separated. {frame}",
		]
		return rng.choice(opts)

	if t == "form":
		p, k = name(d["p"]), d["k"]
		if d["won"]:
			opts = [
				f"🚀 {p} rolls in on a **{k}-game win streak**. {frame}",
				f"👑 **{k} straight wins** for {p}. {frame}",
			]
		else:
			opts = [
				f"🩹 {p} is on a **{k}-game skid**. {frame}",
				f"📉 Rough patch for {p}: **{k} losses** in a row. {frame}",
			]
		return rng.choice(opts)

	raise ValueError(f"_phrase has no branch for candidate type {t!r}")


# ── DB read + embed (deferred heavy imports) ─────────────────────────────
async def _fetch_history(channel_id, user_ids, since_ts):
	"""Ranked-match rows in this channel involving any of ``user_ids``, no older
	than ``since_ts``, ordered by match_id so streaks are reconstructable.

	The window is a hard cutoff applied in SQL. It is not a stylistic choice:
	over a full history everyone's win-rate with everyone converges to their own
	baseline, so the best/worst-teammate swing stops clearing and the line dies.
	"""
	if len(user_ids) < 2:
		return []
	placeholders = ", ".join(["%s"] * len(user_ids))
	rows = await db.fetchall(
		"SELECT pm.match_id, pm.user_id, pm.nick, pm.team, m.winner "
		"FROM qc_player_matches pm "
		"JOIN qc_matches m ON m.match_id = pm.match_id AND m.channel_id = pm.channel_id "
		"WHERE pm.channel_id = %s AND m.ranked = 1 AND pm.team IS NOT NULL "
		"AND m.at >= %s "
		"AND pm.user_id IN (" + placeholders + ") "
		"ORDER BY pm.match_id ASC",
		[channel_id, since_ts, *user_ids]
	)
	return rows or []


def _candidates(prior, matches, t0_ids, t1_ids):
	"""All scored candidates across the eight insight types (pure)."""
	team_of = {**{u: 0 for u in t0_ids}, **{u: 1 for u in t1_ids}}
	return (
		_lineup_candidates(prior, matches, t0_ids, t1_ids)
		+ _perfect_candidates(prior, matches, t0_ids, t1_ids)
		+ _mate_wr_candidates(prior, matches, t0_ids, t1_ids)
		+ _h2h_candidates(prior, matches, t0_ids, t1_ids)
		+ _mate_candidates(prior, matches, t0_ids, t1_ids)
		+ _trio_candidates(prior, matches, t0_ids, t1_ids)
		+ _deadlock_candidates(prior, matches, t0_ids, t1_ids)
		+ _form_candidates(prior, matches, t0_ids + t1_ids, team_of)
	)


async def build_insights_embed(match):
	"""Recency-weighted storylines for a freshly-formed match. Returns an
	``Embed``, or ``None`` when there aren't two teams or nothing dramatic
	surfaced."""
	teams = getattr(match, "teams", None)
	if not teams or len(teams) < 2:
		return None
	team0 = [p for p in teams[0] if p]
	team1 = [p for p in teams[1] if p]
	if not team0 or not team1:
		return None

	players = team0 + team1
	rows = await _fetch_history(match.qc.id, [p.id for p in players],
	                            window_start(time.time()))
	hist = _index_history(rows)
	if not hist.order:
		return None

	# The freshly-formed match isn't persisted yet, so all of history is "prior".
	# One seeded RNG for the whole embed: bot/storyline_payoff.py recomputes
	# these same lines at report time and must land on the same choices.
	rng = random.Random(match.id)
	chosen = _select(_candidates(hist.order, hist.matches,
	                             [p.id for p in team0], [p.id for p in team1]), rng=rng)
	if not chosen:
		return None

	from nextcord import Colour, Embed

	from core.utils import get_nick

	nick = {p.id: get_nick(p) for p in players}
	teams_meta = [
		{"name": teams[0].name, "emoji": teams[0].emoji},
		{"name": teams[1].name, "emoji": teams[1].emoji},
	]
	rosters = {0: [p.id for p in team0], 1: [p.id for p in team1]}
	lines = [_phrase(c, nick, teams_meta, rosters, rng=rng) for c in chosen]
	title = "⚔️ Tale of the Tape"
	embed = Embed(title=title, colour=Colour(0xe67e22), description="\n\n".join(lines))
	embed.set_footer(text=f"Last {WINDOW_DAYS} days · {len(hist.order)} ranked games · just for fun")
	return embed
