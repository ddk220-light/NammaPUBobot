# -*- coding: utf-8 -*-
"""Player-profile data + ELO chart for the /rank command.

Read-only aggregation over rating_history / civ_picks / match_players,
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


def _bucket_of(day):
	"""(week_monday, slot, kind) for an IST date.

	Pickups run Fri/Sat/Sun, so each of those gets its own slot. Mon-Thu sees
	few games and collapses into a single slot at the head of its week, which
	keeps the axis dense instead of four mostly-empty columns.
	"""
	monday = day - timedelta(days=day.weekday())
	wd = day.weekday()  # Mon=0 .. Sun=6
	if wd <= 3:
		return monday, 0, "MT"
	return monday, wd - 3, ("FRI", "SAT", "SUN")[wd - 4]


def bucket_candles(history, days=CANDLE_DAYS, now=None):
	"""Bucket rating history into OHLC candles over Mon-Thu / Fri / Sat / Sun slots.

	history: (unix_ts, rating_before, rating_after) ascending, as stored per
	rating change. Returns every slot in the window in chronological order —
	including empty ones — so the x axis has the same shape for every player:
	{index, kind, label, start, end, days, games, open, high, low, close, change}.
	Empty slots carry games=0 and None OHLC.

	`open` is the rating carried into the slot's first game, so `change` is the
	slot's net swing. Pure: `now` is injectable so tests don't depend on the clock.
	"""
	now = int(now if now is not None else time())
	today = datetime.fromtimestamp(now, IST).date()
	first_day = today - timedelta(days=days - 1)

	# Lay out every slot the window touches first, so an inactive player gets
	# the same axis as an active one.
	slots = {}
	for n in range(days):
		day = first_day + timedelta(days=n)
		key = _bucket_of(day)
		if (s := slots.get(key)) is None:
			slots[key] = dict(
				kind=key[2], label={"MT": "M–T", "FRI": "F", "SAT": "Sa", "SUN": "Su"}[key[2]],
				week=key[0], start=day, end=day, days=0, games=0,
				open=None, high=None, low=None, close=None, change=None, _seen=set()
			)
		else:
			s["end"] = day

	for ts, before, after in history:
		if ts is None or before is None or after is None:
			continue
		day = datetime.fromtimestamp(int(ts), IST).date()
		if not (first_day <= day <= today):
			continue
		c = slots[_bucket_of(day)]
		before, after = int(before), int(after)
		if not c["games"]:
			c.update(open=before, close=after, high=max(before, after), low=min(before, after))
		else:
			c["close"] = after
			c["high"] = max(c["high"], after)
			c["low"] = min(c["low"], after)
		c["games"] += 1
		c["_seen"].add(day)

	out = []
	for index, key in enumerate(sorted(slots)):
		c = slots[key]
		c["days"] = len(c.pop("_seen"))
		c["index"] = index
		if c["games"]:
			c["change"] = c["close"] - c["open"]
		out.append(c)
	return out


def render_elo_candles(slots, nick, days=CANDLE_DAYS):
	"""Render Elo candles over the Mon-Thu / Fri / Sat / Sun slots from bucket_candles.

	Reads like a stock chart: body spans the slot's open -> close, wick spans its
	high/low, green up / red down, with the slot's aggregate change labelled.
	Slots are evenly spaced rather than laid out on a real date axis — pickups
	cluster on Fri/Sat/Sun, so a calendar axis spent most of its width on empty
	weekdays. Empty slots are still drawn as blank columns, which keeps the axis
	identical for every player.

	Lazy matplotlib import + OO Figure API: keeps module import light and is
	thread-safe (no shared pyplot state), so it can run in an executor.
	"""
	import io

	import matplotlib
	matplotlib.use("Agg")  # headless backend — no display needed on a server
	from matplotlib.figure import Figure

	played = [c for c in slots if c["games"]]
	xs = [c["index"] for c in played]
	colours = [_UP if c["change"] > 0 else (_DOWN if c["change"] < 0 else _FLAT) for c in played]

	lo = min(c["low"] for c in played)
	hi = max(c["high"] for c in played)
	pad = max(10.0, (hi - lo) * 0.18)  # headroom for the rotated change labels

	fig = Figure(figsize=(8, 3.8), dpi=110)
	fig.patch.set_facecolor(_BG)
	ax = fig.subplots()
	ax.set_facecolor(_BG)

	# Alternating week shading so the Fri/Sat/Sun runs read as weeks. Spans are
	# derived from the slots each week actually has — the window's first and
	# last weeks are usually partial.
	weeks = []
	for c in slots:
		if not weeks or weeks[-1][0] != c["week"]:
			weeks.append((c["week"], c["index"], c["index"]))
		else:
			weeks[-1] = (c["week"], weeks[-1][1], c["index"])
	for n, (_week, first, last) in enumerate(weeks):
		if n % 2:
			ax.axvspan(first - 0.5, last + 0.5, color=_TEXT, alpha=0.03, zorder=0)

	# Wicks first, then bodies on top. A flat slot (open == close) still gets a
	# visible sliver of body so it doesn't disappear into the wick.
	body_min = max(0.6, (hi - lo) * 0.006)
	ax.vlines(xs, [c["low"] for c in played], [c["high"] for c in played], colors=colours, linewidth=1.2)
	ax.bar(
		xs,
		[max(abs(c["change"]), body_min) for c in played],
		bottom=[min(c["open"], c["close"]) for c in played],
		width=0.62, color=colours, linewidth=0, zorder=3
	)

	# The slot's aggregate change, above an up candle / below a down one.
	label_fs = 7 if len(played) <= 20 else (6 if len(played) <= 35 else 5)
	for c, x, colour in zip(played, xs, colours):
		up = c["change"] >= 0
		ax.annotate(
			("+" if c["change"] > 0 else "") + str(c["change"]),
			(x, c["high"] if up else c["low"]),
			textcoords="offset points", xytext=(0, 4 if up else -4),
			ha="center", va="bottom" if up else "top",
			rotation=90, fontsize=label_fs, color=colour
		)

	# Where the window started, for reading the net move at a glance.
	ax.axhline(played[0]["open"], color=_MUTED, linestyle="--", linewidth=0.8, alpha=0.5, zorder=1)

	ax.set_xlim(-0.8, len(slots) - 0.2)
	ax.set_ylim(lo - pad, hi + pad)

	net = played[-1]["close"] - played[0]["open"]
	active = sum(c["days"] for c in played)
	ax.set_title(f"{nick} — Elo · last {days} days", color=_TEXT, fontsize=12, pad=20)
	ax.text(
		0.5, 1.015,
		"{close} now · {sign}{net} over the window · played {n} day{s}".format(
			close=played[-1]["close"], sign="+" if net > 0 else "", net=net,
			n=active, s="" if active == 1 else "s"
		),
		transform=ax.transAxes, ha="center", va="bottom",
		color=_UP if net > 0 else (_DOWN if net < 0 else _MUTED), fontsize=9
	)

	ax.grid(True, axis="y", color=_GRID, linewidth=0.6, alpha=0.7)
	ax.set_axisbelow(True)
	for spine in ax.spines.values():
		spine.set_visible(False)
	ax.tick_params(colors=_MUTED, labelsize=8)
	# Day letter on every slot; the Mon-Thu column also carries the week's date.
	ax.set_xticks([c["index"] for c in slots])
	ax.set_xticklabels(
		[c["label"] + (f"\n{c['start'].day} {c['start']:%b}" if c["kind"] == "MT" else "") for c in slots],
		fontsize=6.5
	)

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
		"SELECT `at`, rating_before, rating_before + rating_change AS rating FROM rating_history "
		"WHERE user_id=%s AND channel_id=%s ORDER BY `at` ASC",
		[user_id, channel_id]
	)
	# Peak stays all-time; the chart windows itself down to CANDLE_DAYS.
	out["elo_points"] = [(h["at"], h["rating"]) for h in hist]
	if out["elo_points"]:
		peak = max(out["elo_points"], key=lambda p: p[1])
		out["peak_rating"], out["peak_at"] = peak[1], peak[0]
	out["elo_candles"] = bucket_candles([(h["at"], h["rating_before"], h["rating"]) for h in hist])

	recent = await db.fetchall(
		"SELECT m.winner, pm.team FROM match_players pm "
		"JOIN matches m ON m.match_id = pm.match_id "
		"WHERE pm.user_id=%s AND pm.channel_id=%s AND m.ranked=1 "
		"ORDER BY m.match_id DESC LIMIT 10",
		[user_id, channel_id]
	)
	out["recent_form"] = form_from_results(recent)

	civs = await db.fetchall(
		"SELECT civ, SUM(result='W') wins, COUNT(*) games "
		"FROM civ_picks WHERE user_id=%s AND channel_id=%s AND civ IS NOT NULL "
		"GROUP BY civ",
		[user_id, channel_id]
	)
	out["civs"] = civ_breakdown(civs)

	nem = await db.fetchall(
		"SELECT opp.nick, COUNT(*) losses FROM match_players me "
		"JOIN matches m ON m.match_id = me.match_id "
		"JOIN match_players opp ON opp.match_id = me.match_id "
		"  AND opp.team <> me.team AND opp.user_id <> me.user_id "
		"WHERE me.user_id=%s AND me.channel_id=%s AND m.winner IS NOT NULL AND m.winner <> me.team "
		"GROUP BY opp.user_id, opp.nick ORDER BY losses DESC LIMIT 1",
		[user_id, channel_id]
	)
	if nem and nem[0]["losses"] >= 3:
		out["nemesis"] = (nem[0]["nick"], int(nem[0]["losses"]))

	mate = await db.fetchall(
		"SELECT mate.nick, SUM(m.winner = me.team) wins, COUNT(*) games "
		"FROM match_players me "
		"JOIN matches m ON m.match_id = me.match_id "
		"JOIN match_players mate ON mate.match_id = me.match_id "
		"  AND mate.team = me.team AND mate.user_id <> me.user_id "
		"WHERE me.user_id=%s AND me.channel_id=%s AND m.winner IS NOT NULL "
		"GROUP BY mate.user_id, mate.nick HAVING games >= 5 "
		"ORDER BY wins/games DESC, games DESC LIMIT 1",
		[user_id, channel_id]
	)
	if mate:
		out["best_mate"] = (mate[0]["nick"], int(mate[0]["wins"] or 0), int(mate[0]["games"]))

	return out
