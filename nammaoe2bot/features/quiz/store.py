# -*- coding: utf-8 -*-
"""Async MySQL access for the quiz feature. Thin wrappers over nammaoe2bot.runtime.database.db;
all aggregation logic lives in nammaoe2bot.features.quiz.scoring (pure, tested). No nextcord import
here so importing nammaoe2bot.features.quiz stays test-safe."""
import json
import time

from nammaoe2bot.runtime.database import db


# ── config ───────────────────────────────────────────────────────────────
async def get_config(channel_id):
	"""The settings row for one channel, or None when it was never configured."""
	return await db.select_one(["*"], "quiz_settings", {"channel_id": channel_id})


async def enabled_configs():
	"""Every enabled quiz whose channel is still enrolled in a community."""
	return await db.fetchall(
		"SELECT qs.*, cc.community_id FROM quiz_settings qs "
		"JOIN community_channels cc ON cc.channel_id=qs.channel_id "
		"WHERE qs.enabled=1 ORDER BY cc.community_id, qs.channel_id") or []


async def configs_for_community(community_id):
	"""All configured quiz channels inside one explicit tenant."""
	return await db.fetchall(
		"SELECT qs.* FROM quiz_settings qs "
		"JOIN community_channels cc ON cc.channel_id=qs.channel_id "
		"WHERE cc.community_id=%s ORDER BY qs.channel_id", [int(community_id)]) or []


async def upsert_config(channel_id, **fields):
	existing = await db.select_one(["channel_id"], "quiz_settings", {"channel_id": channel_id})
	if existing:
		await db.update("quiz_settings", fields, {"channel_id": channel_id})
	else:
		await db.insert("quiz_settings", dict(channel_id=channel_id, **fields))


async def configure_for_community(
		community_id, channel_id, *, enabled, quiz_hour, open_window):
	"""Atomically select the community's one quiz channel and update its schedule.

	The community row is the serialization lock. Without it, two concurrent web
	saves could each disable the old row and then enable a different channel,
	leaving both active and paying two daily rewards from one community economy.
	"""
	if not isinstance(enabled, bool):
		raise ValueError("enabled must be a boolean")
	if isinstance(quiz_hour, bool) or not isinstance(quiz_hour, int) or not 0 <= quiz_hour <= 23:
		raise ValueError("quiz_hour must be an integer from 0 to 23")
	if (isinstance(open_window, bool) or not isinstance(open_window, int)
			or not 900 <= open_window <= 86400):
		raise ValueError("open_window must be between 900 and 86400 seconds")
	community_id = int(community_id)
	channel_id = int(channel_id)
	async with db.transaction() as tx:
		root = await tx.fetchone(
			"SELECT community_id FROM communities WHERE community_id=%s FOR UPDATE",
			[community_id])
		if root is None:
			raise ValueError("Community not found")
		link = await tx.fetchone(
			"SELECT channel_id FROM community_channels "
			"WHERE community_id=%s AND channel_id=%s", [community_id, channel_id])
		if link is None:
			raise ValueError("Quiz channel does not belong to this community")
		await tx.execute(
			"UPDATE quiz_settings qs JOIN community_channels cc "
			"ON cc.channel_id=qs.channel_id SET qs.enabled=0 "
			"WHERE cc.community_id=%s", [community_id])
		existing = await tx.fetchone(
			"SELECT channel_id FROM quiz_settings WHERE channel_id=%s FOR UPDATE",
			[channel_id])
		values = [int(bool(enabled)), int(quiz_hour), int(open_window), channel_id]
		if existing:
			await tx.execute(
				"UPDATE quiz_settings SET enabled=%s, quiz_hour=%s, open_window=%s "
				"WHERE channel_id=%s", values)
		else:
			await tx.insert("quiz_settings", {
				"channel_id": channel_id,
				"enabled": int(bool(enabled)),
				"quiz_hour": int(quiz_hour),
				"open_window": int(open_window),
			})


async def disable_for_community(community_id):
	"""Emergency stop for one tenant; open posts still resolve normally."""
	community_id = int(community_id)
	async with db.transaction() as tx:
		root = await tx.fetchone(
			"SELECT community_id FROM communities WHERE community_id=%s FOR UPDATE",
			[community_id])
		if root is None:
			return False
		await tx.execute(
			"UPDATE quiz_settings qs JOIN community_channels cc "
			"ON cc.channel_id=qs.channel_id SET qs.enabled=0 "
			"WHERE cc.community_id=%s", [community_id])
		return True


# ── posts ────────────────────────────────────────────────────────────────
async def asked_ids(channel_id):
	rows = await db.fetchall(
		"SELECT question_id FROM quiz_posts WHERE channel_id=%s", [channel_id])
	return {r["question_id"] for r in (rows or [])}


async def recent_question_ids(channel_id, n=6):
	"""The channel's n most recently posted question_ids, newest first. Feeds the
	player bank's "don't repeat a metric two player-days running" preference —
	the metric is recoverable from the id (nammaoe2bot/features/quiz/player_bank.question_id), so
	this needs no column of its own."""
	rows = await db.fetchall(
		"SELECT question_id FROM quiz_posts WHERE channel_id=%s ORDER BY id DESC LIMIT %s",
		[channel_id, n])
	return [r["question_id"] for r in (rows or [])]


async def create_post(channel_id, q, opened_at, closes_at):
	"""Insert an open post. q is a schedule entry (carries seq/week/day/correct_indices)."""
	return await db.insert("quiz_posts", dict(
		channel_id=channel_id, message_id=None, question_id=q["id"], category=q["category"],
		difficulty=q.get("difficulty"),
		prompt=q["prompt"], options_json=json.dumps(q["options"]),
		correct_index=q.get("correct_index"),
		correct_indices=json.dumps(q["correct_indices"]),
		explanation=q["explanation"], opened_at=opened_at, closes_at=closes_at, status="open",
		seq=q["seq"], week=q["week"], day=q["day"], source=q.get("source")))


async def set_message_id(post_id, message_id):
	await db.update("quiz_posts", {"message_id": message_id}, {"id": post_id})


async def next_seq(channel_id):
	"""The seq the channel should post next = (max posted seq) + 1, starting at 1."""
	rows = await db.fetchall(
		"SELECT MAX(seq) m FROM quiz_posts WHERE channel_id=%s", [channel_id])
	return int((rows[0]["m"] or 0)) + 1 if rows else 1


async def posted_seqs(channel_id):
	rows = await db.fetchall(
		"SELECT seq FROM quiz_posts WHERE channel_id=%s AND seq IS NOT NULL", [channel_id])
	return {r["seq"] for r in (rows or [])}


async def get_post(post_id):
	return await db.select_one(["*"], "quiz_posts", {"id": post_id})


async def due_to_close(now_ts):
	rows = await db.fetchall(
		"SELECT * FROM quiz_posts WHERE status='open' AND closes_at<=%s", [now_ts])
	return rows or []


async def latest_open_post(channel_id):
	"""The most recent still-open post for this channel — i.e. the PREVIOUS question,
	revealed as a fresh announcement right before the next one is posted. None if there
	is no open post."""
	rows = await db.fetchall(
		"SELECT * FROM quiz_posts WHERE channel_id=%s AND status='open' "
		"ORDER BY id DESC LIMIT 1", [channel_id])
	return rows[0] if rows else None


async def clamp_closes_at(post_id, now):
	"""Pull a post's deadline back to `now` (never forward — LEAST, not an
	assignment, so a re-entered resolve cannot un-close an already-past poll).

	This is the FIRST thing nammaoe2bot/features/quiz/jobs.py::_reveal does, and it has to be:
	a vote may only be written while `now < closes_at`, so clamping BEFORE the
	snapshot is what makes the snapshot final. Without it, a resolve started
	while the poll is still nominally open (/quiz reveal_now, or the daily
	cadence firing before closes_at) reads its votes, spends several Discord
	round-trips, and closes — while every press landing in that window is
	accepted and written AFTER the snapshot, then shut out by the close
	forever: never graded, never paid, and undetectable, because the ledger and
	the balance cache still agree.

	THE ROW LOCK AND THE STORE'S OWN CLOCK ARE WHAT MAKE THAT TRUE, not the
	ordering on its own, and not the lock on its own either. Clamping first
	only helps if a press cannot straddle the clamp, and a press IS two
	round-trips (the router's read, then the write) with unbounded time in
	between. record_vote / record_vote_multi close that by writing only inside a
	transaction that has taken THIS SAME row lock and re-evaluated the gate
	under it, so this statement and any concurrent press are strictly ordered:
	a press already holding the row commits before this returns — and is
	therefore in the snapshot that follows — while a press arriving after it
	blocks here, and then refuses.

	The second half is why that refusal actually happens. Serialisation alone
	does not refuse anything: a waiting press re-reads the freshly-clamped
	closes_at, and if it compares that against a clock its CALLER read before
	the wait began, `now < closes_at` is still true and the write lands anyway
	— exactly the outcome the lock was taken to prevent. So `_open_for_voting`
	reads the clock ITSELF, after its `FOR UPDATE` returns; that reading cannot
	predate the lock, hence cannot predate this clamp's commit, so a press that
	waited here always compares a later clock against the pulled-back deadline
	and always refuses. Lock plus store-owned clock leave exactly two outcomes
	and no third, which is the property `_reveal` needs and could not have
	before.

	The SELECT ... FOR UPDATE is written out rather than left implicit in the
	UPDATE's own exclusive row lock. The two are equivalent today; spelling it
	out makes the serialisation a stated property of this function instead of a
	side effect of how one statement happens to be phrased, and keeps it true if
	the clamp is ever re-expressed (a read-then-write clamp computing LEAST in
	Python would silently lose it). Same belt-and-braces reasoning recorded on
	nammaoe2bot/features/betting/gold.py::cancel_bet's second row lock.

	The post stays status='open'. That is deliberate — `status='open' AND
	closes_at<=now` (due_to_close) IS the retry ticket, and this clamp does not
	spend it; it guarantees it, since a resolve that dies after the clamp now
	matches that query for certain."""
	async with db.transaction() as tx:
		await tx.fetchone(
			"SELECT closes_at FROM quiz_posts WHERE id=%s FOR UPDATE", [post_id])
		await tx.execute(
			"UPDATE quiz_posts SET closes_at=LEAST(closes_at, %s) WHERE id=%s", [now, post_id])


async def close_post(post_id):
	await db.update("quiz_posts", {"status": "closed"}, {"id": post_id})


# ── answers ──────────────────────────────────────────────────────────────
# The reveal era's writers (record_reveal / record_answer / record_answer_multi
# and the get_answer they were built on) are gone with it: an answer is no
# longer a private, timed, one-shot lock-in but a public vote a user may change
# until the poll closes, and correctness is no longer decided at press time but
# at lock time by write_grade below.


async def _open_for_voting(tx, post_id):
	"""Row-lock the post and answer, UNDER THAT LOCK, whether a vote may still
	be written to it — as the timestamp the vote must be stamped with, or None
	when the poll is shut. This is THE authority on the question; the
	identical-looking check in nammaoe2bot/features/quiz/interactions.py is a cheap fast path
	that proves nothing, because the row it read can be flipped a millisecond
	later.

	THE CLOCK IS READ HERE, AFTER THE LOCK, AND THAT IS THE POINT. `FOR UPDATE`
	takes the same exclusive row lock clamp_closes_at takes, so a press racing a
	resolve waits here until the clamp commits — but waiting only helps if what
	it compares the pulled-back deadline against is a reading it could not have
	taken before the wait. A `now` handed in by the router was read at the top
	of the interaction, seconds and several round-trips ago; against a deadline
	clamped to a moment AFTER that reading, `now < closes_at` still holds and
	the write lands anyway. Serialising the two and then judging with a stale
	clock refuses nothing. So this function takes no timestamp: the clock
	reading below cannot predate the lock, hence cannot predate the clamp that
	held it, and the refusal is real. It is also the value written to
	answered_at, so the row records when the vote was ACCEPTED — the instant
	the gate opened for it — not when the button happened to be pressed.

	No advisory `now` parameter survives for a caller to get wrong: that is
	precisely how the race stayed open through its first fix. See the two
	callers below and clamp_closes_at's docstring for the ordering half."""
	row = await tx.fetchone(
		"SELECT status, closes_at FROM quiz_posts WHERE id=%s FOR UPDATE", [post_id])
	now = int(time.time())
	if row is None or row["status"] != "open" or now >= int(row["closes_at"]):
		return None
	return now


async def record_vote(post_id, user_id, nick, choice_index):
	"""UPSERT the user's vote — the PK (post_id, user_id) IS the one-vote
	rule, and REPLACE makes a changed mind overwrite the row (also wiping any
	old-era timing fields, which is correct: this row now means a poll vote).
	is_correct stays NULL until the post locks; answered_at is the latest
	change, which keeps answers_for_post's cast-a-vote filter true.

	NO `now` PARAMETER, deliberately: the timestamp comes back from the gate,
	which reads the clock under the post's row lock. See _open_for_voting.

	-> True when the vote landed, False when the poll was already shut. The
	caller MUST honour False: a re-render over a refused write tells the user
	their vote counted when no row exists.

	GATE AND WRITE ARE ONE STEP, under the post's row lock. They used to be two
	independent round-trips — the router read the post, checked it, and wrote
	later — and a press whose read completed microseconds before
	nammaoe2bot/features/quiz/jobs.py::_reveal clamped the deadline passed that check and
	committed AFTER _reveal's vote snapshot. Such a vote is never graded
	(is_correct stays NULL), never paid its 10-or-50 gold, and the close then
	puts the post beyond due_to_close forever; nothing downstream can even
	notice, because the ledger and the balance cache still agree. A vote CHANGE
	in that window is worse still: this REPLACE resets is_correct to NULL after
	write_grade ran and after gold was paid on that verdict, so scoring.tally
	scores the user differently from what the payout assumed.

	With the lock — and with the gate reading the clock under it — there are
	exactly two orders and no third: this transaction takes the row first, so
	the clamp waits on it and the snapshot that follows the clamp contains this
	vote — or the clamp takes it first, and the gate, whose clock reading is
	necessarily later than that commit, sees the pulled-back closes_at and
	refuses. Same discipline, and the same class of silently-destroyed money,
	as nammaoe2bot/features/betting/gold.py::place_bet."""
	async with db.transaction() as tx:
		now = await _open_for_voting(tx, post_id)
		if now is None:
			return False
		await tx.insert("quiz_answers", dict(
			post_id=post_id, user_id=user_id, nick=nick,
			revealed_at=None, deadline_at=None,
			choice_index=int(choice_index), choice_indices=None,
			is_correct=None, answered_at=now, response_ms=None),
			on_duplicate="replace")
		return True


async def record_vote_multi(post_id, user_id, nick, choice_indices):
	"""The multi-answer variant: the submitted set replaces the previous one
	wholesale (JSON-sorted, the grade_multi convention). Same transactional
	gate, same store-owned clock, same True/False contract, same reasons — see
	record_vote."""
	async with db.transaction() as tx:
		now = await _open_for_voting(tx, post_id)
		if now is None:
			return False
		await tx.insert("quiz_answers", dict(
			post_id=post_id, user_id=user_id, nick=nick,
			revealed_at=None, deadline_at=None,
			choice_index=None,
			choice_indices=json.dumps(sorted(int(i) for i in choice_indices)),
			is_correct=None, answered_at=now, response_ms=None),
			on_duplicate="replace")
		return True


async def write_grade(post_id, user_id, is_correct):
	"""Lock-time grading write. Deterministic input -> safe to re-run."""
	await db.execute(
		"UPDATE quiz_answers SET is_correct=%s WHERE post_id=%s AND user_id=%s",
		[(1 if is_correct else 0), post_id, user_id])


async def answers_for_post(post_id):
	rows = await db.fetchall(
		"SELECT * FROM quiz_answers WHERE post_id=%s AND answered_at IS NOT NULL", [post_id])
	return rows or []


async def week_answers_by_week(channel_id, week):
	"""Answered rows for posts in a given SCHEDULE week — feeds scoring.tally. Robust to
	calendar drift (keys off the schedule week, not a rolling 7-day window)."""
	rows = await db.fetchall(
		"SELECT a.user_id, a.nick, a.is_correct FROM quiz_answers a "
		"JOIN quiz_posts p ON p.id=a.post_id "
		"WHERE p.channel_id=%s AND p.week=%s AND a.answered_at IS NOT NULL",
		[channel_id, week])
	return rows or []


async def week_answers(channel_id, week_start_ts, week_end_ts):
	"""Answered rows in [start, end) for posts in this channel — feeds scoring.tally."""
	rows = await db.fetchall(
		"SELECT a.user_id, a.nick, a.is_correct FROM quiz_answers a "
		"JOIN quiz_posts p ON p.id=a.post_id "
		"WHERE p.channel_id=%s AND a.answered_at>=%s AND a.answered_at<%s "
		"AND a.answered_at IS NOT NULL",
		[channel_id, week_start_ts, week_end_ts])
	return rows or []
