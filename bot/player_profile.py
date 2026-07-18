# -*- coding: utf-8 -*-
"""Player-profile data + ELO chart for the /rank command.

Read-only aggregation over qc_rating_history / qc_match_civs / qc_player_matches,
plus a matplotlib renderer for the rating-over-time graph. The chart is rendered
off the event loop (run_in_executor) so it never blocks the 1s think() tick, and
uses the OO Figure API (no pyplot global state) so it's safe to run in a thread.
"""
from core.database import db

# Minimum games on a civ before it qualifies for the best/worst lists.
MIN_CIV_GAMES = 3

# Discord dark-theme palette so the PNG sits cleanly inside an embed.
_BG = "#2b2d31"
_LINE = "#5865f2"
_FILL = "#5865f2"
_PEAK = "#57f287"
_TEXT = "#dbdee1"
_MUTED = "#949ba4"
_GRID = "#3f4248"


def web_profile_url(root_url, user_id):
	"""Return the public dashboard URL for a Discord user, if configured."""
	root_url = (root_url or "").strip().rstrip("/")
	return f"{root_url}/player/{user_id}" if root_url else None


def render_elo_chart(points, nick):
	"""points: list of (unix_ts, rating) ascending. Returns PNG bytes.

	Lazy matplotlib import + OO Figure API: keeps module import light and is
	thread-safe (no shared pyplot state), so it can run in an executor.
	"""
	import io
	from datetime import datetime, timezone

	import matplotlib
	matplotlib.use("Agg")  # headless backend — no display needed on a server
	from matplotlib.figure import Figure
	from matplotlib import dates as mdates

	xs = [datetime.fromtimestamp(t, timezone.utc) for t, _ in points]
	ys = [r for _, r in points]
	lo = min(ys)

	fig = Figure(figsize=(8, 3.4), dpi=110)
	fig.patch.set_facecolor(_BG)
	ax = fig.subplots()
	ax.set_facecolor(_BG)

	ax.plot(xs, ys, color=_LINE, linewidth=2.0, solid_capstyle="round")
	ax.fill_between(xs, ys, lo, color=_FILL, alpha=0.12)

	# Peak marker + label.
	peak_i = max(range(len(ys)), key=lambda i: ys[i])
	ax.scatter([xs[peak_i]], [ys[peak_i]], color=_PEAK, s=30, zorder=5)
	ax.annotate(
		f"peak {ys[peak_i]}", (xs[peak_i], ys[peak_i]),
		textcoords="offset points", xytext=(0, 8), ha="center", color=_PEAK, fontsize=9
	)
	# Current rating dot.
	ax.scatter([xs[-1]], [ys[-1]], color=_TEXT, s=22, zorder=5)

	ax.set_title(f"{nick} — rating over time", color=_TEXT, fontsize=12, pad=10)
	ax.grid(True, color=_GRID, linewidth=0.6, alpha=0.7)
	for spine in ax.spines.values():
		spine.set_visible(False)
	ax.tick_params(colors=_MUTED, labelsize=8)
	ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))

	buf = io.BytesIO()
	fig.savefig(buf, format="png", facecolor=_BG, bbox_inches="tight")
	buf.seek(0)
	return buf.getvalue()


def civ_breakdown(rows):
	"""rows: dicts with civ/wins/games. -> {best, worst, most_played, total}.

	Pure: best/worst by win-rate among civs with >= MIN_CIV_GAMES games.
	"""
	parsed = []
	for r in rows:
		games = int(r["games"] or 0)
		if not games:
			continue
		wins = int(r["wins"] or 0)
		parsed.append({"civ": r["civ"], "wins": wins, "games": games, "wr": wins / games})
	most_played = max(parsed, key=lambda c: c["games"], default=None)
	qualified = [c for c in parsed if c["games"] >= MIN_CIV_GAMES]
	by_wr = sorted(qualified, key=lambda c: (-c["wr"], -c["games"]))
	best = by_wr[:3]
	# Only a separate worst list when top-3 and bottom-3 can't overlap (> 6 civs).
	worst = by_wr[-3:][::-1] if len(by_wr) > 6 else []
	return {"best": best, "worst": worst, "most_played": most_played, "total": len(qualified)}


def form_from_results(rows):
	"""rows: dicts with winner/team (newest first). -> list of 'W'/'L'/'D'. Pure."""
	form = []
	for r in rows:
		if r["winner"] is None:
			form.append("D")
		elif r["team"] is not None and r["team"] == r["winner"]:
			form.append("W")
		else:
			form.append("L")
	return form


async def gather_profile(channel_id, user_id):
	"""Read-only profile aggregation for /rank. Best-effort: a piece with no
	data is simply omitted from the returned dict."""
	out = {}

	hist = await db.fetchall(
		"SELECT `at`, rating_before + rating_change AS rating FROM qc_rating_history "
		"WHERE user_id=%s AND channel_id=%s ORDER BY `at` ASC",
		[user_id, channel_id]
	)
	out["elo_points"] = [(h["at"], h["rating"]) for h in hist]
	if out["elo_points"]:
		peak = max(out["elo_points"], key=lambda p: p[1])
		out["peak_rating"], out["peak_at"] = peak[1], peak[0]

	recent = await db.fetchall(
		"SELECT m.winner, pm.team FROM qc_player_matches pm "
		"JOIN qc_matches m ON m.match_id = pm.match_id "
		"WHERE pm.user_id=%s AND pm.channel_id=%s AND m.ranked=1 "
		"ORDER BY m.match_id DESC LIMIT 10",
		[user_id, channel_id]
	)
	out["recent_form"] = form_from_results(recent)

	civs = await db.fetchall(
		"SELECT civ, SUM(result='W') wins, COUNT(*) games "
		"FROM qc_match_civs WHERE user_id=%s AND channel_id=%s AND civ IS NOT NULL "
		"GROUP BY civ",
		[user_id, channel_id]
	)
	out["civs"] = civ_breakdown(civs)

	nem = await db.fetchall(
		"SELECT opp.nick, COUNT(*) losses FROM qc_player_matches me "
		"JOIN qc_matches m ON m.match_id = me.match_id "
		"JOIN qc_player_matches opp ON opp.match_id = me.match_id "
		"  AND opp.team <> me.team AND opp.user_id <> me.user_id "
		"WHERE me.user_id=%s AND me.channel_id=%s AND m.winner IS NOT NULL AND m.winner <> me.team "
		"GROUP BY opp.user_id, opp.nick ORDER BY losses DESC LIMIT 1",
		[user_id, channel_id]
	)
	if nem and nem[0]["losses"] >= 3:
		out["nemesis"] = (nem[0]["nick"], int(nem[0]["losses"]))

	mate = await db.fetchall(
		"SELECT mate.nick, SUM(m.winner = me.team) wins, COUNT(*) games "
		"FROM qc_player_matches me "
		"JOIN qc_matches m ON m.match_id = me.match_id "
		"JOIN qc_player_matches mate ON mate.match_id = me.match_id "
		"  AND mate.team = me.team AND mate.user_id <> me.user_id "
		"WHERE me.user_id=%s AND me.channel_id=%s AND m.winner IS NOT NULL "
		"GROUP BY mate.user_id, mate.nick HAVING games >= 5 "
		"ORDER BY wins/games DESC, games DESC LIMIT 1",
		[user_id, channel_id]
	)
	if mate:
		out["best_mate"] = (mate[0]["nick"], int(mate[0]["wins"] or 0), int(mate[0]["games"]))

	return out
