# -*- coding: utf-8 -*-
"""Close the loop on the pre-game storylines the moment a match reports.

bot/team_insights.py teases — "does the curse break tonight?" — and until now
nothing ever answered. This module answers, immediately at report time, with no
replay parsing: everything it needs is win/loss and who was on which side.

It does not read back what was posted. It recomputes the same storylines from
the same windowed history with the same seeded RNG, which is why _select and
_phrase take an injected rng. Three of the recompute's inputs are not stable
across a match's lifetime on their own: the 90-day window slides as the match
runs, /subfor and sub_auto can re-split the teams during WAITING_REPORT, and a
restart hands a restored match a brand-new id. bot/team_insights.py pins all
three the moment it posts a tease, stashing them on ``match.storyline_ctx``;
this module reads that stash instead of recomputing window/seed/roster from
scratch, and bails with None when there is no stash (nothing was teased) or
the current roster no longer matches the stashed one (a substitution
invalidated the tease). The storylines themselves are still recomputed — the
stash only pins what they are recomputed *from*.

One drift source is still accepted: a threshold or generator change deployed
between a match forming and it reporting changes the recomputed set. That is a
code change mid-match, not a data drift, and is rare enough not to warrant its
own guard.
"""
import random

from bot import team_insights as ti


def resolve(c, winner, team_of):
	"""Did this storyline's subject side win? None when it cannot be settled.

	Every claim reduces to this one boolean. No type needs a combinatorial
	verdict matrix -- it settles on this and, where relevant, the stored
	pre-game direction, never anything finer-grained.
	"""
	if winner is None:
		return None
	kind, key = ti.subject_of(c)
	side = team_of.get(key) if kind == "player" else key
	if side is None:
		return None
	return int(winner) == int(side)


def _payoff_frame(c, came_true, nick, teams_meta, rosters, *, rng=random):
	"""Past-tense closing clause for a settled storyline.

	Tone follows what happened tonight, not the pre-game direction: the whole
	side shares the result, so a broken curse is good news for the teammates
	too, and a dead streak is bad news for them.
	"""
	who, name, sole = ti.complement_of(c, nick, teams_meta, rosters)
	if not sole:
		backers = ti.rival_backers(c, nick, teams_meta, rosters)
		if backers and all(w for _n, w, _t in backers):
			ids = sorted(c["players"])
			_kind, subj = ti.subject_of(c)
			# subj is a player id for every type that reaches this branch today
			# (h2h, deadlock). A future candidate type could carry a team subject
			# instead, which would not be a member of `ids` -- fall back to the
			# generic clause rather than let .index() raise.
			if subj in ids:
				subj_idx = ids.index(subj)
				_sname, sback, steam = backers[subj_idx]
				_rname, rback, rteam = backers[1 - subj_idx]
				sa, sb = ti._short_backers(sback, steam), ti._short_backers(rback, rteam)
				if came_true:
					return rng.choice([
						f"{sa} got their answer.",
						f"{sa} called it.",
					])
				return rng.choice([
					f"{sb} had the last word.",
					f"{sb} settled it.",
				])
		return rng.choice([
			"Their teammates had their own night.",
			"The other six were along for the ride.",
		])
	if not who:
		return rng.choice([
			f"All of {name}, in it together.",
			f"That was the whole of {name}.",
		])
	if came_true:
		return rng.choice([
			f"{ti._sentence_case(who)} got to enjoy it.",
			f"A good night to be {who}.",
		])
	return rng.choice([
		f"{ti._sentence_case(who)} went down with them.",
		f"A rough one for {who} too.",
	])


def payoff_phrase(c, came_true, nick, teams_meta, rosters, *, rng=random):
	"""One line reacting to a storyline, keyed on whether its side won."""
	d, t = c["data"], c["type"]

	def name(uid):
		return f"**{nick.get(uid, 'someone')}**"

	frame = _payoff_frame(c, came_true, nick, teams_meta, rosters, rng=rng)

	if t == "lineup":
		w, g = d["wins"], d["games"]
		nw, ng = w + (1 if came_true else 0), g + 1
		if came_true:
			return rng.choice([
				f"🃏 The reunion delivered — this exact side is now **{nw}-{ng - nw}**.",
				f"🎰 Same side, same result. **{nw}-{ng - nw}** all told. {frame}",
			])
		return rng.choice([
			f"🃏 The reunion fell flat. This exact side drops to **{nw}-{ng - nw}**.",
			f"🎰 Not this time — **{nw}-{ng - nw}** for this exact side. {frame}",
		])

	if t == "trio":
		who = ti._join_names([name(u) for u in d["ids"]])
		w, g = d["wins"], d["games"]
		nw, ng = w + (1 if came_true else 0), g + 1
		if came_true == d["won"]:
			return rng.choice([
				f"🔱 True to form: {who} move to **{nw}-{ng - nw}** as a three.",
				f"⛓️ The trio held serve — **{nw}-{ng - nw}** together now. {frame}",
			])
		return rng.choice([
			f"🔱 Against the grain: {who} are now **{nw}-{ng - nw}** as a three.",
			f"🕳️ The pattern cracked — **{nw}-{ng - nw}** for the three of them. {frame}",
		])

	if t == "perfect":
		a, b, n = name(d["ids"][0]), name(d["ids"][1]), d["n"]
		if d["won"]:
			return (rng.choice([
				f"💯 Still flawless. {a} & {b} are **{n + 1}-0** together.",
				f"🏆 The perfect record survives — **{n + 1}-0**. {frame}",
			]) if came_true else rng.choice([
				f"💔 It ends here. {a} & {b} lose as a pair for the first time — **{n}-1**.",
				f"🧨 The flawless run is over at {n}. {frame}",
			]))
		return (rng.choice([
			f"🎉 THE CURSE IS DEAD. {a} & {b} finally win together — **1-{n}**.",
			f"🔓 {n} tries, and it lands. {a} & {b} are on the board. {frame}",
		]) if came_true else rng.choice([
			f"🪦 The curse holds. {a} & {b} fall to **0-{n + 1}**.",
			f"💀 Still winless together — **0-{n + 1}**. {frame}",
		]))

	if t == "mate_wr":
		p, q = name(d["p"]), name(d["q"])
		if d["kind"] == "best":
			return (rng.choice([
				f"🚀 The pairing delivered again — {p} keeps cashing in next to {q}.",
				f"🍀 Lucky charm confirmed. {p} & {q} do it again. {frame}",
			]) if came_true else rng.choice([
				f"🌧️ Not tonight. Even alongside {q}, {p} came up short.",
				f"📉 The magic pairing missed this one. {frame}",
			]))
		return (rng.choice([
			f"🎯 History bucked — {p} & {q} finally made it work.",
			f"🔨 The bad pairing broke its own pattern. {frame}",
		]) if came_true else rng.choice([
			f"🔁 History repeats. {p} & {q} still do not click.",
			f"🧊 Same story as ever for {p} beside {q}. {frame}",
		]))

	if t == "h2h":
		w, lo, k = name(d["winner"]), name(d["loser"]), d["k"]
		if came_true:
			return rng.choice([
				f"⚔️ Make it **{k + 1} straight** — {w} still owns {lo}.",
				f"🔒 {lo} still has no answer. {k + 1} in a row to {w}. {frame}",
			])
		return rng.choice([
			f"🎊 The streak dies at {k}. {lo} finally beat {w}.",
			f"⛓️‍💥 {lo} breaks the run. {frame}",
		])

	if t == "mate":
		a, b, k = name(d["ids"][0]), name(d["ids"][1]), d["k"]
		if d["won"]:
			return (rng.choice([
				f"🔥 **{k + 1} in a row** together for {a} & {b}.",
				f"📈 The duo streak lives — {k + 1} straight. {frame}",
			]) if came_true else rng.choice([
				f"❄️ The run ends at {k}. {a} & {b} finally drop one together.",
				f"🛑 Streak over. {frame}",
			]))
		return (rng.choice([
			f"🌅 The skid is over — {a} & {b} win one together at last.",
			f"🩹 {k} straight losses, and then this. {frame}",
		]) if came_true else rng.choice([
			f"🪦 Make it **{k + 1} straight losses** for {a} & {b}.",
			f"❄️ Still ice-cold together — {k + 1} in a row. {frame}",
		]))

	if t == "deadlock":
		a, b, each = name(d["ids"][0]), name(d["ids"][1]), d["each"]
		ahead, behind = (a, b) if came_true else (b, a)
		return rng.choice([
			f"⚖️ Tie broken. {ahead} edges ahead of {behind}, **{each + 1}-{each}**.",
			f"🎯 Someone had to. {ahead} takes the decider. {frame}",
		])

	if t == "form":
		p, k = name(d["p"]), d["k"]
		if d["won"]:
			return (rng.choice([
				f"🚀 **{k + 1} straight** for {p}. Nobody has stopped them yet.",
				f"👑 The heater rolls on — {k + 1} in a row. {frame}",
			]) if came_true else rng.choice([
				f"🛑 The run ends at {k}. {p} finally drops one.",
				f"📉 Streak over for {p}. {frame}",
			]))
		return (rng.choice([
			f"🌅 The slump breaks. {p} snaps a {k}-game skid.",
			f"🎊 {k} losses, then this. {frame}",
		]) if came_true else rng.choice([
			f"🩹 Make it **{k + 1}**. The skid goes on for {p}.",
			f"📉 Still searching — {k + 1} straight for {p}. {frame}",
		]))

	raise ValueError(f"payoff_phrase has no branch for candidate type {t!r}")


async def build_payoff_embed(match):
	"""React to the storylines this match's teams were given. None when there is
	nothing to settle (draw, no tease was ever posted, the roster moved since the
	tease, or nothing fired pre-game)."""
	teams = getattr(match, "teams", None)
	if not teams or len(teams) < 2:
		return None
	winner = getattr(match, "winner", None)
	if winner is None:
		return None
	ctx = getattr(match, "storyline_ctx", None)
	if not ctx:
		return None
	team0 = [p for p in teams[0] if p]
	team1 = [p for p in teams[1] if p]
	if not team0 or not team1:
		return None

	rosters = {0: [p.id for p in team0], 1: [p.id for p in team1]}
	# Membership, not order: a captain swap or a matchmaking re-split can reorder
	# a side without changing who is on it, and the tease's phrasing never
	# depended on list order either.
	if {i: frozenset(ids) for i, ids in rosters.items()} != \
			{i: frozenset(ids) for i, ids in ctx["rosters"].items()}:
		return None

	players = team0 + team1

	rows = await ti._fetch_history(match.qc.id, [p.id for p in players], ctx["since"])
	hist = ti._index_history(rows)
	# This match is already persisted by the time the payoff runs, so the prior
	# has to exclude it explicitly or every storyline would resolve itself. The
	# cutoff is the tease's seed, not this match's own id (from_json hands a
	# restored match a new one) -- but a stashed ctx never survives a restore
	# anyway, since the stash lives only on the in-memory match object.
	prior = [mid for mid in hist.order if mid < ctx["seed"]]
	if not prior:
		return None

	rng = random.Random(ctx["seed"])
	chosen = ti._select(ti._candidates(prior, hist.matches,
	                                   [p.id for p in team0], [p.id for p in team1]),
	                    rng=rng)
	if not chosen:
		return None

	team_of = {**{p.id: 0 for p in team0}, **{p.id: 1 for p in team1}}
	verdicts = [(c, resolve(c, winner, team_of)) for c in chosen]
	verdicts = [(c, v) for c, v in verdicts if v is not None]
	if not verdicts:
		return None

	from nextcord import Colour, Embed

	from core.console import log
	from core.utils import get_nick

	nick = {p.id: get_nick(p) for p in players}
	teams_meta = [
		{"name": teams[0].name, "emoji": teams[0].emoji},
		{"name": teams[1].name, "emoji": teams[1].emoji},
	]
	lines = []
	for c, v in verdicts:
		try:
			lines.append(payoff_phrase(c, v, nick, teams_meta, rosters, rng=rng))
		except Exception as e:
			log.error(f"Storyline payoff render failed ({c.get('type')}): {e}")
	if not lines:
		return None
	embed = Embed(title="⚔️ Final Tale of the Tape", colour=Colour(0xe67e22),
	              description="\n\n".join(lines))
	embed.set_footer(
		text=f"Last {ti.WINDOW_DAYS} days · how the storylines actually ended · just for fun")
	return embed
