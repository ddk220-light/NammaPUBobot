# -*- coding: utf-8 -*-
"""Post fun "storyline" stats when a match's teams are formed.

After teams are picked, this scans the channel's ranked match history and
surfaces 3-4 of the most interesting facts about the two line-ups, phrased
for fun:

  * Synergy   — pairs / trios / quads now on the SAME team who have played
                together before, and how that went (great chemistry, or a
                cursed combo that keeps losing).
  * Rivalry   — players now on OPPOSITE teams who have a head-to-head history
                (a one-sided "nemesis", or a dead-even classic rivalry).

The bigger the group and the more lopsided the record, the juicier the fact,
so a 4-stack that always wins outranks a single hot pairing.

Design mirrors bot/civ_stats.py: one cheap DB read, all the analysis is pure
Python (so it's unit-testable without a DB), and ``build_insights_embed``
returns an ``Embed`` or ``None`` (no embed when there's not enough history).
Heavy imports (nextcord / core.utils) are intentionally deferred into the
functions that need them so the pure helpers import cleanly under CI.
"""
import math
import random
from itertools import combinations

from core.database import db

# ── Tunables ─────────────────────────────────────────────────────────────
# Per group-size: minimum decisive (non-draw) games together before we'll
# call it out, a score weight (so larger stacks win the spotlight), and the
# win-rate thresholds for "good synergy" (hi) vs "cursed combo" (lo). Larger
# groups get more leeway because the same N people teaming up repeatedly is
# itself notable.
SIZE_CFG = {
	2: {"min_games": 4, "weight": 1.0, "hi": 0.65, "lo": 0.35},
	3: {"min_games": 3, "weight": 2.3, "hi": 0.60, "lo": 0.40},
	4: {"min_games": 2, "weight": 4.2, "hi": 0.58, "lo": 0.42},
}
# Teams larger than this skip the trio/quad scan (combinatorial guard); pairs
# are always considered. AoE2 pickups are usually <= 4v4 so this rarely bites.
MAX_COMBO_TEAM = 6

MIN_RIVALRY_GAMES = 4        # min decisive head-to-head games to mention a rivalry
RIVALRY_DOMINANT = 0.70      # leader's H2H win-rate at/above this => a "nemesis"
MIN_EVEN_RIVALRY_GAMES = 6   # a close rivalry needs more games to be noteworthy

MAX_BULLETS = 4


# ── Pure analysis helpers (no DB / Discord — unit tested) ────────────────
def _index_history(rows):
	"""Fold raw ``(match_id, user_id, team, winner)`` rows into
	``{match_id: {"winner": w, "players": {user_id: team}}}``.

	Rows with a NULL team (never-picked players) are skipped.
	"""
	by_match = {}
	for r in rows:
		team = r["team"]
		if team is None:
			continue
		mid = r["match_id"]
		entry = by_match.get(mid)
		if entry is None:
			entry = by_match[mid] = {"winner": r["winner"], "players": {}}
		entry["players"][r["user_id"]] = int(team)
	return by_match


def _synergy_record(by_match, group):
	"""(wins, losses, draws) for ``group`` across games where they were ALL
	present AND all on the same team. ``winner is None`` counts as a draw."""
	group = set(group)
	n = len(group)
	wins = losses = draws = 0
	for entry in by_match.values():
		players = entry["players"]
		teams = [players[u] for u in group if u in players]
		if len(teams) != n or len(set(teams)) != 1:
			continue  # not everyone present, or not on the same team
		t = teams[0]
		w = entry["winner"]
		if w is None:
			draws += 1
		elif int(w) == t:
			wins += 1
		else:
			losses += 1
	return wins, losses, draws


def _rivalry_record(by_match, a, b):
	"""(a_wins, b_wins, draws) across games where ``a`` and ``b`` were both
	present and on OPPOSITE teams."""
	a_wins = b_wins = draws = 0
	for entry in by_match.values():
		players = entry["players"]
		if a not in players or b not in players or players[a] == players[b]:
			continue
		w = entry["winner"]
		if w is None:
			draws += 1
		elif int(w) == players[a]:
			a_wins += 1
		else:
			b_wins += 1
	return a_wins, b_wins, draws


def _synergy_candidates(by_match, team_ids, team_idx):
	"""Build scored synergy candidates for every qualifying pair/trio/quad
	within one team."""
	cands = []
	n = len(team_ids)
	sizes = [2]
	if n <= MAX_COMBO_TEAM:
		if n >= 3:
			sizes.append(3)
		if n >= 4:
			sizes.append(4)
	for size in sizes:
		cfg = SIZE_CFG[size]
		for combo in combinations(team_ids, size):
			wins, losses, draws = _synergy_record(by_match, combo)
			decisive = wins + losses
			if decisive < cfg["min_games"]:
				continue
			wr = wins / decisive
			if wr >= cfg["hi"]:
				good = True
			elif wr <= cfg["lo"]:
				good = False
			else:
				continue
			cands.append({
				"type": "synergy",
				"good": good,
				"team_idx": team_idx,
				"players": frozenset(combo),
				"ids": list(combo),
				"size": size,
				"wins": wins, "losses": losses, "draws": draws,
				"wr": wr,
				"score": cfg["weight"] * abs(wr - 0.5) * math.sqrt(decisive),
			})
	return cands


def _rivalry_candidates(by_match, team0_ids, team1_ids):
	"""Build scored rivalry candidates for every cross-team pair with enough
	head-to-head history."""
	cands = []
	for a in team0_ids:
		for b in team1_ids:
			a_wins, b_wins, draws = _rivalry_record(by_match, a, b)
			decisive = a_wins + b_wins
			if decisive < MIN_RIVALRY_GAMES:
				continue
			if a_wins >= b_wins:
				leader, lw, trail, tw = a, a_wins, b, b_wins
			else:
				leader, lw, trail, tw = b, b_wins, a, a_wins
			wr = lw / decisive
			if wr >= RIVALRY_DOMINANT:
				kind = "dominant"
				score = 1.6 * (wr - 0.5) * math.sqrt(decisive)
			elif decisive >= MIN_EVEN_RIVALRY_GAMES:
				kind = "even"
				score = 0.9 * math.sqrt(decisive) * (1.0 - abs(wr - 0.5) * 2)
			else:
				continue
			cands.append({
				"type": "rivalry",
				"kind": kind,
				"leader": leader, "trail": trail,
				"leader_wins": lw, "trail_wins": tw,
				"draws": draws,
				"wr": wr,
				"score": score,
			})
	return cands


def _select(synergy, rivalry, limit=MAX_BULLETS):
	"""Pick the most interesting, non-redundant facts.

	Highest score first; a synergy fact is dropped if its player set nests
	with one already chosen (e.g. a pair inside an already-shown quad). If at
	least one rivalry exists but none made the cut, swap in the best rivalry
	so we always get to "call out the other team" too.
	"""
	chosen = []
	chosen_sets = []
	for c in sorted(synergy + rivalry, key=lambda c: c["score"], reverse=True):
		if len(chosen) >= limit:
			break
		if c["type"] == "synergy":
			s = c["players"]
			if any(s <= e or e <= s for e in chosen_sets):
				continue
			chosen_sets.append(s)
		chosen.append(c)

	if rivalry and not any(c["type"] == "rivalry" for c in chosen):
		best_riv = max(rivalry, key=lambda c: c["score"])
		synergy_in = [c for c in chosen if c["type"] == "synergy"]
		if len(chosen) >= limit and synergy_in:
			chosen.remove(min(synergy_in, key=lambda c: c["score"]))
			chosen.append(best_riv)
		elif len(chosen) < limit:
			chosen.append(best_riv)

	chosen.sort(key=lambda c: c["score"], reverse=True)
	return chosen[:limit]


# ── DB read + embed building (deferred heavy imports) ────────────────────
async def _fetch_history(channel_id, user_ids):
	"""Every ranked-match row in this channel involving any of ``user_ids``."""
	if len(user_ids) < 2:
		return []
	placeholders = ", ".join(["%s"] * len(user_ids))
	rows = await db.fetchall(
		"SELECT pm.match_id, pm.user_id, pm.team, m.winner "
		"FROM qc_player_matches pm "
		"JOIN qc_matches m ON m.match_id = pm.match_id AND m.channel_id = pm.channel_id "
		"WHERE pm.channel_id = %s AND m.ranked = 1 AND pm.team IS NOT NULL "
		"AND pm.user_id IN (" + placeholders + ")",
		[channel_id, *user_ids]
	)
	return rows or []


def _phrase(c, nick, teams_meta):
	"""Render one candidate as a fun one-liner. Lazy-imports join_and so the
	pure helpers above stay importable without nextcord/prettytable."""
	from core.utils import join_and

	def names(ids):
		return join_and([f"**{nick.get(i, 'someone')}**" for i in ids])

	if c["type"] == "synergy":
		meta = teams_meta[c["team_idx"]]
		team = f"{meta['emoji']} {meta['name']}".strip()
		g = names(c["ids"])
		w, ls = c["wins"], c["losses"]
		decisive = w + ls
		pct = round(c["wr"] * 100)
		if c["good"]:
			opts = [
				f"🔥 {g} are lethal together — **{w}-{ls}** ({pct}%) as teammates. {team} got the band back together.",
				f"🤝 {g} just *click*: **{w} wins in {decisive}** games side-by-side ({pct}%).",
				f"📈 History loves {g} on the same side — **{w}-{ls}** ({pct}%). {team} should feel good about this one.",
			]
			if ls == 0 and w >= 3:
				opts.append(f"🏆 {g} have **never lost** together — a perfect **{w}-0**. {team} is stacked.")
		else:
			opts = [
				f"💀 Uh oh — {g} as teammates are a rough **{w}-{ls}** ({pct}%). {team}, prove the numbers wrong.",
				f"🪦 The cursed combo is back: {g} have won just **{w} of {decisive}** together ({pct}%).",
				f"🎭 {team} reunited {g}, who are a shaky **{w}-{ls}** ({pct}%) whenever they share a team.",
			]
			if w == 0 and ls >= 3:
				opts.append(f"🃏 {g} have **never won** as teammates (0-{ls}). {team} is feeling brave today.")
		return random.choice(opts)

	# rivalry
	leader = f"**{nick.get(c['leader'], 'someone')}**"
	trail = f"**{nick.get(c['trail'], 'someone')}**"
	lw, tw = c["leader_wins"], c["trail_wins"]
	if c["kind"] == "dominant":
		if tw == 0:
			opts = [
				f"⚔️ {leader} flat-out **owns** {trail} — a flawless **{lw}-0**. {trail}, time to break the curse.",
				f"😈 {trail} has *never* beaten {leader} (**0-{lw}**)… and here they are on opposite teams again.",
			]
		else:
			opts = [
				f"⚔️ Nemesis alert: {leader} leads {trail} **{lw}-{tw}** head-to-head — and they're matched up again.",
				f"😈 {leader} is {trail}'s kryptonite (**{lw}-{tw}**). Opposite sides once more — revenge is on the menu.",
			]
	else:  # even
		opts = [
			f"🤜🤛 Dead heat: {leader} vs {trail} sits at **{lw}-{tw}** all-time. Tiebreaker today?",
			f"🔥 The rivalry rolls on — {leader} and {trail} are locked at **{lw}-{tw}** and face off again.",
		]
	return random.choice(opts)


async def build_insights_embed(match):
	"""Synergy/rivalry storylines for a freshly-formed match.

	Auto-posted from Match.final_message. Returns an ``Embed``, or ``None``
	when there aren't two teams or there's no interesting history to show.
	"""
	teams = getattr(match, "teams", None)
	if not teams or len(teams) < 2:
		return None
	team0 = [p for p in teams[0] if p]
	team1 = [p for p in teams[1] if p]
	if not team0 or not team1:
		return None

	players = team0 + team1
	rows = await _fetch_history(match.qc.id, [p.id for p in players])
	by_match = _index_history(rows)
	if not by_match:
		return None

	t0_ids = [p.id for p in team0]
	t1_ids = [p.id for p in team1]
	synergy = _synergy_candidates(by_match, t0_ids, 0) + _synergy_candidates(by_match, t1_ids, 1)
	rivalry = _rivalry_candidates(by_match, t0_ids, t1_ids)
	chosen = _select(synergy, rivalry)
	if not chosen:
		return None

	from nextcord import Embed, Colour
	from core.utils import get_nick

	nick = {p.id: get_nick(p) for p in players}
	teams_meta = [
		{"name": teams[0].name, "emoji": teams[0].emoji},
		{"name": teams[1].name, "emoji": teams[1].emoji},
	]
	lines = [_phrase(c, nick, teams_meta) for c in chosen]
	title = random.choice([
		"🔮 The Tale of the Tape",
		"📊 Match Intel",
		"🧠 Pre-Game Storylines",
		"📜 History Speaks",
	])
	embed = Embed(title=title, colour=Colour(0xe67e22), description="\n\n".join(lines))
	embed.set_footer(text=f"Based on {len(by_match)} past ranked games · just for fun")
	return embed
