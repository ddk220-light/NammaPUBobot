# -*- coding: utf-8 -*-
"""Close the loop on the pre-game storylines the moment a match reports.

bot/team_insights.py teases — "does the curse break tonight?" — and until now
nothing ever answered. This module answers, immediately at report time, with no
replay parsing: everything it needs is win/loss and who was on which side.

It does not read back what was posted. It recomputes the same storylines from
the same windowed history with the same match-id-seeded RNG, which is why
_select and _phrase take an injected rng. Two consequences worth knowing:

  * A threshold change between a match forming and reporting changes the
    recomputed set.
  * Matches overlap. One that formed while this was live can finish first and
    land in the window with a lower match_id, so the recompute can see history
    the pre-game read did not. Rate lines will not move; a streak line
    occasionally will.

Both are accepted — see the design doc for why storing the claims was rejected.
"""
import random

from bot import team_insights as ti


def resolve(c, winner, team_of):
	"""Did this storyline's subject side win? None when it cannot be settled.

	Every claim reduces to this one boolean, which is what keeps the payoff to
	exactly two texts per type instead of a combinatorial mess.
	"""
	if winner is None:
		return None
	kind, key = ti.subject_of(c)
	side = team_of.get(key) if kind == "player" else key
	if side is None:
		return None
	return int(winner) == int(side)


def payoff_phrase(c, came_true, nick, teams_meta, rosters, *, rng=random):
	"""One line reacting to a storyline, keyed on whether its side won."""
	d, t = c["data"], c["type"]

	def name(uid):
		return f"**{nick.get(uid, 'someone')}**"

	frame = ti._frame(c, nick, teams_meta, rosters, rng=rng)

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

	# form
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


async def build_payoff_embed(match):
	"""React to the storylines this match's teams were given. None when there is
	nothing to settle (draw, no history, or nothing fired pre-game)."""
	teams = getattr(match, "teams", None)
	if not teams or len(teams) < 2:
		return None
	winner = getattr(match, "winner", None)
	if winner is None:
		return None
	team0 = [p for p in teams[0] if p]
	team1 = [p for p in teams[1] if p]
	if not team0 or not team1:
		return None

	players = team0 + team1
	import time

	rows = await ti._fetch_history(match.qc.id, [p.id for p in players],
	                               ti.window_start(time.time()))
	hist = ti._index_history(rows)
	# This match is already persisted by the time the payoff runs, so the prior
	# has to exclude it explicitly or every storyline would resolve itself.
	prior = [mid for mid in hist.order if mid < match.id]
	if not prior:
		return None

	rng = random.Random(match.id)
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

	from core.utils import get_nick

	nick = {p.id: get_nick(p) for p in players}
	teams_meta = [
		{"name": teams[0].name, "emoji": teams[0].emoji},
		{"name": teams[1].name, "emoji": teams[1].emoji},
	]
	rosters = {0: [p.id for p in team0], 1: [p.id for p in team1]}
	# _phrase consumed rng for each chosen line pre-game; burn the same draws
	# here so the payoff's variant picks line up with the teased ones.
	for c in chosen:
		ti._phrase(c, nick, teams_meta, rosters, rng=rng)
	lines = [payoff_phrase(c, v, nick, teams_meta, rosters, rng=rng)
	         for c, v in verdicts]
	embed = Embed(title="⚔️ Final Tale of the Tape", colour=Colour(0xe67e22),
	              description="\n\n".join(lines))
	embed.set_footer(
		text=f"Last {ti.WINDOW_DAYS} days · how the storylines actually ended · just for fun")
	return embed
