# -*- coding: utf-8 -*-
"""Player-profile data + ELO chart for the /rank command.

Read-only aggregation over qc_rating_history / qc_match_civs / qc_player_matches,
plus a matplotlib renderer for the rating-over-time graph. The chart is rendered
off the event loop (run_in_executor) so it never blocks the 1s think() tick, and
uses the OO Figure API (no pyplot global state) so it's safe to run in a thread.
"""
from datetime import datetime, timedelta, timezone
from time import time

from core.database import db

# Minimum games on a civ before it qualifies for the best/worst lists.
MIN_CIV_GAMES = 15

# Elo chart window, and the timezone its days are bucketed in. IST matches the
# rest of the bot's day bucketing (see /activity and bot/civ_stats.py).
CANDLE_DAYS = 60
IST = timezone(timedelta(hours=5, minutes=30))

# Discord dark-theme palette so the PNG sits cleanly inside an embed.
_BG = "#2b2d31"
_TEXT = "#dbdee1"
_MUTED = "#949ba4"
_GRID = "#3f4248"
_UP = "#3ba55d"
_DOWN = "#ed4245"
_FLAT = "#949ba4"


def web_profile_url(root_url, user_id):
	"""Return the public dashboard URL for a Discord user, if configured."""
	root_url = (root_url or "").strip().rstrip("/")
	return f"{root_url}/player/{user_id}" if root_url else None


def web_profile_link(root_url, user_id, nick):
	"""Return nick as a markdown link to the player's web profile, or plain nick if not configured."""
	url = web_profile_url(root_url, user_id)
	if not url:
		return nick
	safe_nick = nick.replace("[", "(").replace("]", ")")
	return f"[{safe_nick}]({url})"


def daily_candles(history, days=CANDLE_DAYS, now=None):
	"""Bucket rating history into one OHLC candle per day, stock-chart style.

	history: (unix_ts, rating_before, rating_after) ascending, as stored per
	rating change. Returns a list of dicts ascending by date:
	{date, open, high, low, close, change, games}.

	`open` is the rating the player carried into that day's first game, so
	`change` is the day's net swing — the aggregate of every game played that
	day. Days with no rating movement produce no candle (the date axis keeps
	the gap). Pure: `now` is injectable so tests don't depend on the clock.
	"""
	now = int(now if now is not None else time())
	today = datetime.fromtimestamp(now, IST).date()
	first_day = today - timedelta(days=days - 1)

	by_day = {}
	for ts, before, after in history:
		if ts is None or before is None or after is None:
			continue
		day = datetime.fromtimestamp(int(ts), IST).date()
		if not (first_day <= day <= today):
			continue
		before, after = int(before), int(after)
		if (c := by_day.get(day)) is None:
			by_day[day] = dict(
				date=day, open=before, close=after,
				high=max(before, after), low=min(before, after), games=1
			)
		else:
			c["close"] = after
			c["high"] = max(c["high"], after)
			c["low"] = min(c["low"], after)
			c["games"] += 1

	candles = [by_day[d] for d in sorted(by_day)]
	for c in candles:
		c["change"] = c["close"] - c["open"]
	return candles


def render_elo_candles(candles, nick, days=CANDLE_DAYS, now=None):
	"""Render daily Elo candles (from daily_candles) as PNG bytes.

	Reads like a stock chart: one candle per day played, body spanning the day's
	open -> close, wick spanning its intra-day high/low, green up / red down,
	with the day's net change labelled. The x axis always covers the full
	window, so inactive stretches show as gaps rather than being compressed.

	Lazy matplotlib import + OO Figure API: keeps module import light and is
	thread-safe (no shared pyplot state), so it can run in an executor.
	"""
	import io

	import matplotlib
	matplotlib.use("Agg")  # headless backend — no display needed on a server
	from matplotlib.figure import Figure
	from matplotlib import dates as mdates

	xs = [mdates.date2num(c["date"]) for c in candles]
	colours = [_UP if c["change"] > 0 else (_DOWN if c["change"] < 0 else _FLAT) for c in candles]

	lo = min(c["low"] for c in candles)
	hi = max(c["high"] for c in candles)
	pad = max(10.0, (hi - lo) * 0.18)  # headroom for the rotated change labels

	fig = Figure(figsize=(8, 3.6), dpi=110)
	fig.patch.set_facecolor(_BG)
	ax = fig.subplots()
	ax.set_facecolor(_BG)

	# Wicks first, then bodies on top. A flat day (open == close) still gets a
	# visible sliver of body so it doesn't disappear into the wick.
	body_min = max(0.6, (hi - lo) * 0.006)
	ax.vlines(xs, [c["low"] for c in candles], [c["high"] for c in candles], colors=colours, linewidth=1.0)
	ax.bar(
		xs,
		[max(abs(c["change"]), body_min) for c in candles],
		bottom=[min(c["open"], c["close"]) for c in candles],
		width=0.62, color=colours, linewidth=0, zorder=3
	)

	# The day's aggregate change, above an up candle / below a down one.
	label_fs = 7 if len(candles) <= 20 else (6 if len(candles) <= 35 else 5)
	for c, x, colour in zip(candles, xs, colours):
		up = c["change"] >= 0
		ax.annotate(
			("+" if c["change"] > 0 else "") + str(c["change"]),
			(x, c["high"] if up else c["low"]),
			textcoords="offset points", xytext=(0, 4 if up else -4),
			ha="center", va="bottom" if up else "top",
			rotation=90, fontsize=label_fs, color=colour
		)

	# Where the window started, for reading the net move at a glance.
	ax.axhline(candles[0]["open"], color=_MUTED, linestyle="--", linewidth=0.8, alpha=0.5, zorder=1)

	now = int(now if now is not None else time())
	today = datetime.fromtimestamp(now, IST).date()
	ax.set_xlim(mdates.date2num(today - timedelta(days=days - 1)) - 0.8, mdates.date2num(today) + 0.8)
	ax.set_ylim(lo - pad, hi + pad)

	net = candles[-1]["close"] - candles[0]["open"]
	ax.set_title(f"{nick} — Elo · last {days} days", color=_TEXT, fontsize=12, pad=20)
	ax.text(
		0.5, 1.015,
		"{close} now · {sign}{net} over the window · {n} active days".format(
			close=candles[-1]["close"], sign="+" if net > 0 else "", net=net, n=len(candles)
		),
		transform=ax.transAxes, ha="center", va="bottom",
		color=_UP if net > 0 else (_DOWN if net < 0 else _MUTED), fontsize=9
	)

	ax.grid(True, axis="y", color=_GRID, linewidth=0.6, alpha=0.7)
	ax.set_axisbelow(True)
	for spine in ax.spines.values():
		spine.set_visible(False)
	ax.tick_params(colors=_MUTED, labelsize=8)
	ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=10))
	ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))

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
		"SELECT `at`, rating_before, rating_before + rating_change AS rating FROM qc_rating_history "
		"WHERE user_id=%s AND channel_id=%s ORDER BY `at` ASC",
		[user_id, channel_id]
	)
	# Peak stays all-time; the chart windows itself down to CANDLE_DAYS.
	out["elo_points"] = [(h["at"], h["rating"]) for h in hist]
	if out["elo_points"]:
		peak = max(out["elo_points"], key=lambda p: p[1])
		out["peak_rating"], out["peak_at"] = peak[1], peak[0]
	out["elo_candles"] = daily_candles([(h["at"], h["rating_before"], h["rating"]) for h in hist])

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
