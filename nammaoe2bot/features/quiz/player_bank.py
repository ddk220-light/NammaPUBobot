# -*- coding: utf-8 -*-
"""Player-source quiz questions ("which of these four players ranks first?"),
generated LIVE from `metric_boards` at post time.

WHAT THIS REPLACED. Until stage 5b these questions came out of an offline
pipeline: replays parsed into a committed SQLite file (data/replay_quiz.db),
leaderboards computed there, ~1400 questions pre-written into
data/question_bank.json, converted into data/quiz_bank_player.json and
interleaved into a 26-week schedule -- all of it recomputed by hand and
re-committed whenever it went stale. Stage 4 already builds the same
leaderboards live and per community (bot/derived/boards.py), so the offline
half was a second, slowly-rotting copy of data the bot recomputes on a timer.
It is deleted; this module is what took its place.

THE BOARD IS THE ONLY SOURCE. compute_board has already applied the sample
floor (BOARD_MIN_GAMES) and already sorted `leaders` best-first for the
metric's own direction -- "fastest to Feudal" ascends, "highest eAPM"
descends. Neither is re-derived here: bot/derived/boards.py's docstring says
direction knowledge lives in exactly one place, and a second copy that drifted
would silently invert an answer rather than fail.

WHAT MAKES A QUESTION FIT TO ASK (each one omits rather than degrades):
  * MIN_LEADERS distinct leaders on the board, or there is no question. Four,
    because the card renders exactly four lettered options (nammaoe2bot/features/quiz/view.py):
    below that the choice is either padded with invented options or shrunk to
    a card the answer UI cannot render. A young community, or a metric most of
    its players have too few games for, simply gets no player question that
    day -- the caller falls back to the game bank.
  * NO TIE on the answer. If the leader being asked about shares its average
    with another option, "ranks first" has two right answers and the question
    is dropped. This is a real case, not a theoretical one: medal metrics are
    small integers averaged over few games.
  * HIDDEN PLAYERS ARE NEVER OPTIONS. `player_ratings.is_hidden` is how a
    member opts out of being listed publicly (bot/web.py filters every public
    listing on it); naming them in a channel-wide quiz would route around that.

OPTIONS CARRY NAME + ELO, NEVER THE VALUE. The metric value is the answer --
printing "42.0" beside a name turns a quiz into a reading exercise. Values
appear in the reveal, with the sample each rests on. That was the offline
bank's convention and it is preserved here exactly.

NAMES ARE DISPLAY-ONLY. Every question keys on `user_id`: the correct option is
found by identity, never by matching the rendered nick. Two members can share a
display name, one member's name changes between the board being computed and
the question being posted, and `nick` here is the in-game name observed in a
replay (see refresh_community's docstring) -- none of which may decide which
button is right.

The generator is pure over already-fetched rows; `fetch_inputs` is the small
async shell around it, the same split every bot/derived/* module uses and the
reason this file is testable with neither a database nor Discord.
"""
import json
import random

from nammaoe2bot.runtime.database import db

# Exactly the number of lettered options nammaoe2bot/features/quiz/view.py renders (A-D), so the
# floor on leaders is not a taste call: it is the card's arity.
MIN_OPTIONS = 4
MIN_LEADERS = MIN_OPTIONS

# The closeness -> difficulty thresholds the offline bank used
# (utils/quiz_gen/convert_player_bank.py's _difficulty), kept so a channel's
# difficulty mix does not shift the day this module took over.
_HARD, _MEDIUM = 0.85, 0.6

# metric_id -> the card's category line. A display grouping, not a data
# boundary: several metrics deliberately share one. Every id in
# bot/derived/boards.METRICS must appear here (test_metric_categories_cover_the
# _whole_catalog enforces it) so adding a board cannot quietly ship a question
# labelled with the fallback.
CATEGORIES = {
	"villagers": "Villagers",
	"vil_pre_feudal": "Villagers",
	"vil_pre_castle": "Villagers",
	"vil_pre_imperial": "Villagers",
	"military": "Military",
	"mil_pre_feudal": "Military",
	"mil_pre_castle": "Military",
	"mil_pre_imperial": "Military",
	"feudal_s": "Age speed",
	"castle_s": "Age speed",
	"imperial_s": "Age speed",
	"first_tc_s": "Openings",
	"eapm": "eAPM",
	"peak_eapm": "eAPM",
	"military_medal": "Medals",
	"villager_medal": "Medals",
}
_FALLBACK_CATEGORY = "Player trivia"

_ID_PREFIX = "player"


def question_id(metric_id, seq):
	"""The stored question_id. Carries the metric so recent posts can be read
	back for it (metric_of_id) without a second column, and the seq so a
	channel's asked-id set never blocks a metric from ever being asked again."""
	return f"{_ID_PREFIX}:{metric_id}:{seq}"


def metric_of_id(qid):
	"""The metric a stored question_id was built from, or None for a game
	question (whose ids carry no colons)."""
	parts = str(qid or "").split(":")
	return parts[1] if len(parts) == 3 and parts[0] == _ID_PREFIX else None


def metrics_of_ids(qids):
	return [m for m in (metric_of_id(q) for q in qids or ()) if m]


def _fmt_value(value, unit):
	"""A metric value as text. Seconds render as m:ss -- 'castle in 1042' is not
	a number anyone reads -- and everything else as the plainest number that
	does not lose the average's fractional part."""
	if unit == "seconds":
		total = int(round(float(value)))
		return f"{total // 60}:{total % 60:02d}"
	number = round(float(value), 2)
	return str(int(number)) if number == int(number) else str(number)


def option_label(nick, elo):
	"""Name + Elo, and nothing else. No metric value: see the module docstring."""
	name = str(nick or "unknown")
	return f"{name} (Elo {int(elo)})" if elo is not None else name


def _difficulty(window, answer_index):
	"""How close the answer's nearest rival in the window is, as easy/medium/hard.

	Measured against the window's own spread rather than an absolute gap,
	because the same 5-unit lead is decisive in medal places and invisible in
	villager counts."""
	values = [leader["avg"] for leader in window]
	spread = abs(max(values) - min(values))
	rivals = [v for i, v in enumerate(values) if i != answer_index]
	gap = min(abs(values[answer_index] - v) for v in rivals)
	closeness = 1 - (gap / spread) if spread else 1.0
	return "hard" if closeness >= _HARD else "medium" if closeness >= _MEDIUM else "easy", round(closeness, 4)


def build_question(metric_id, board, elos, rng, seq, ask=None, exclude_user_ids=()):
	"""One question from ONE board, or None when this board cannot carry a fair
	one. Pure: `board` is the blob bot/derived/boards.py wrote, `elos` a
	{user_id: rating} map, `rng` a seeded random.Random.

	`ask` ("best"/"worst") is drawn from `rng` when not given. Both directions
	are asked because a board whose leader never changes would otherwise ask the
	same question forever, and "who trails" is answerable from the same rows
	with no extra data."""
	excluded = set(exclude_user_ids)
	leaders = [entry for entry in board.get("leaders") or [] if entry["user_id"] not in excluded]
	if len(leaders) < MIN_LEADERS:
		return None

	ask = ask or rng.choice(("best", "worst"))
	# A contiguous slice of the ranking, not four leaders picked at random: the
	# four are then genuinely close, which is what makes the question hard
	# rather than a formality. leaders is already in the metric's winning order.
	start = rng.randrange(0, len(leaders) - MIN_OPTIONS + 1)
	window = leaders[start:start + MIN_OPTIONS]
	answer = window[0] if ask == "best" else window[-1]
	if sum(1 for entry in window if entry["avg"] == answer["avg"]) > 1:
		return None                     # tied for the place being asked about

	order = list(window)
	rng.shuffle(order)                  # the answer must not sit on slot A every time
	# BY user_id. Never by nick: two members can render the same name.
	answer_index = next(i for i, entry in enumerate(order) if entry["user_id"] == answer["user_id"])
	difficulty, closeness = _difficulty(window, window.index(answer))

	unit = board.get("unit")
	label = board.get("label") or metric_id
	place = "first" if ask == "best" else "last"
	values = " · ".join(
		f"{entry['nick']} {_fmt_value(entry['avg'], unit)} ({entry['n']} games)" for entry in order)
	return {
		"id": question_id(metric_id, seq),
		"category": CATEGORIES.get(metric_id, _FALLBACK_CATEGORY),
		"question_type": "board_window",
		"grouping": ask,
		"difficulty": difficulty,
		"prompt": f"{label} — which of these four players ranks {place}?",
		"options": [option_label(entry["nick"], elos.get(entry["user_id"])) for entry in order],
		"correct_indices": [answer_index],
		"correct_index": answer_index,
		"multi": False,
		"explanation": (
			f"**{answer['nick']}** ranks {place} of the four. "
			f"Averages — {values}."),
		"source": "player",
		"score": closeness,
		"meta": {
			"metric_id": metric_id,
			"ask": ask,
			"closeness": closeness,
			"answer_user_id": answer["user_id"],
			"option_user_ids": [entry["user_id"] for entry in order],
		},
	}


def generate(boards, elos, seq, rng, avoid_metrics=(), exclude_user_ids=()):
	"""A question from whichever of this community's boards can carry one, or
	None when none can. Pure.

	Two passes: the metrics a channel has not asked recently first, then all of
	them. Without the second pass a community with three usable boards would
	stop producing player questions entirely as soon as it had asked all three
	-- the avoid list is a variety preference, never a reason to go silent."""
	avoid = set(avoid_metrics)
	for candidates in ([m for m in sorted(boards) if m not in avoid], sorted(boards)):
		order = list(candidates)
		rng.shuffle(order)
		for metric_id in order:
			question = build_question(metric_id, boards[metric_id], elos, rng, seq,
			                          exclude_user_ids=exclude_user_ids)
			if question:
				return question
	return None


# ── the async shell ──────────────────────────────────────────────────────
_BOARDS_SQL = "SELECT metric_id, board FROM metric_boards WHERE community_id=%s"

# Ratings are per (channel, user); a community owns a set of channels. Joining
# through community_channels keeps a partner server's Elo out of this
# community's card for a player who is in both.
_RATINGS_SQL = (
	"SELECT pr.user_id AS user_id, pr.rating AS rating, pr.is_hidden AS is_hidden, "
	"pr.last_ranked_match_at AS last_at FROM player_ratings pr "
	"JOIN community_channels cc ON cc.channel_id = pr.channel_id "
	"WHERE cc.community_id = %s")


def parse_boards(rows):
	"""{metric_id: board blob}. A row whose JSON will not parse is dropped, not
	raised on: one corrupt blob must not cost the community every other board."""
	out = {}
	for row in rows or []:
		try:
			board = json.loads(row["board"])
		except (TypeError, ValueError):
			continue
		if isinstance(board, dict):
			out[row["metric_id"]] = board
	return out


def pick_elos(rows):
	"""-> ({user_id: rating}, {hidden user_ids}). Pure.

	A user with rows in several of the community's channels gets the most
	recently ranked one (then the higher rating, so the choice never depends on
	row order). `is_hidden` is read as a per-user flag the same way bot/web.py
	reads it: hidden in any channel means hidden."""
	best, hidden = {}, set()
	for row in rows or []:
		uid = row["user_id"]
		if row.get("is_hidden"):
			hidden.add(uid)
		if row.get("rating") is None:
			continue
		key = (row.get("last_at") or 0, row["rating"])
		if uid not in best or key > best[uid][0]:
			best[uid] = (key, row["rating"])
	return {uid: rating for uid, (_key, rating) in best.items()}, hidden


async def fetch_inputs(community_id):
	"""-> (boards, elos, hidden_user_ids). Two SELECTs, both handed straight to
	the pure reducers above."""
	boards = parse_boards(await db.fetchall(_BOARDS_SQL, [community_id]) or [])
	elos, hidden = pick_elos(await db.fetchall(_RATINGS_SQL, [community_id]) or [])
	return boards, elos, hidden


async def question_for_channel(channel_id, seq, avoid_metrics=()):
	"""The channel's live player question for `seq`, or None (no community, no
	usable board). Seeded on (community, seq) so re-deriving the same slot --
	a retry after a failed send -- yields the same question rather than a
	different one each attempt."""
	from nammaoe2bot import community
	community_id = await community.community_for_channel(channel_id)
	if community_id is None:
		return None
	boards, elos, hidden = await fetch_inputs(community_id)
	return generate(boards, elos, seq, random.Random(f"{community_id}:{seq}"),
	                avoid_metrics=avoid_metrics, exclude_user_ids=hidden)
