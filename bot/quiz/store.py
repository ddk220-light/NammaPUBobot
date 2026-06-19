# -*- coding: utf-8 -*-
"""Async MySQL access for the quiz feature. Thin wrappers over core.database.db;
all aggregation logic lives in bot.quiz.scoring (pure, tested). No nextcord import
here so importing bot.quiz stays test-safe."""
import json
import time  # noqa: F401  (kept for callers that pass explicit timestamps)

from core.database import db


# ── config ───────────────────────────────────────────────────────────────
async def get_config(channel_id=None):
	"""The enabled config row (no channel given) or the row for channel_id. None if
	nothing matches."""
	if channel_id is not None:
		return await db.select_one(["*"], "qc_quiz_config", {"channel_id": channel_id})
	rows = await db.fetchall("SELECT * FROM qc_quiz_config WHERE enabled=1 LIMIT 1")
	return rows[0] if rows else None


async def upsert_config(channel_id, **fields):
	existing = await db.select_one(["channel_id"], "qc_quiz_config", {"channel_id": channel_id})
	if existing:
		await db.update("qc_quiz_config", fields, {"channel_id": channel_id})
	else:
		await db.insert("qc_quiz_config", dict(channel_id=channel_id, **fields))


async def disable_all():
	"""Disable the quiz everywhere — used before enabling a new channel so only one
	channel is ever active (get_config() serves a single enabled row)."""
	await db.execute("UPDATE qc_quiz_config SET enabled=0")


# ── posts ────────────────────────────────────────────────────────────────
async def asked_ids(channel_id):
	rows = await db.fetchall(
		"SELECT question_id FROM qc_quiz_posts WHERE channel_id=%s", [channel_id])
	return {r["question_id"] for r in (rows or [])}


async def recent_categories(channel_id, n=3):
	rows = await db.fetchall(
		"SELECT category FROM qc_quiz_posts WHERE channel_id=%s ORDER BY id DESC LIMIT %s",
		[channel_id, n])
	return [r["category"] for r in (rows or [])]


async def create_post(channel_id, q, opened_at, closes_at):
	"""Insert an open post. q is a schedule entry (carries seq/week/day/correct_indices)."""
	return await db.insert("qc_quiz_posts", dict(
		channel_id=channel_id, message_id=None, question_id=q["id"], category=q["category"],
		prompt=q["prompt"], options_json=json.dumps(q["options"]),
		correct_index=q.get("correct_index"),
		correct_indices=json.dumps(q["correct_indices"]),
		explanation=q["explanation"], opened_at=opened_at, closes_at=closes_at, status="open",
		seq=q["seq"], week=q["week"], day=q["day"]))


async def set_message_id(post_id, message_id):
	await db.update("qc_quiz_posts", {"message_id": message_id}, {"id": post_id})


async def next_seq(channel_id):
	"""The seq the channel should post next = (max posted seq) + 1, starting at 1."""
	rows = await db.fetchall(
		"SELECT MAX(seq) m FROM qc_quiz_posts WHERE channel_id=%s", [channel_id])
	return int((rows[0]["m"] or 0)) + 1 if rows else 1


async def posted_seqs(channel_id):
	rows = await db.fetchall(
		"SELECT seq FROM qc_quiz_posts WHERE channel_id=%s AND seq IS NOT NULL", [channel_id])
	return {r["seq"] for r in (rows or [])}


async def get_post(post_id):
	return await db.select_one(["*"], "qc_quiz_posts", {"id": post_id})


async def due_to_close(now_ts):
	rows = await db.fetchall(
		"SELECT * FROM qc_quiz_posts WHERE status='open' AND closes_at<=%s", [now_ts])
	return rows or []


async def latest_open_post(channel_id):
	"""The most recent still-open post for this channel — i.e. the PREVIOUS question,
	revealed as a fresh announcement right before the next one is posted. None if there
	is no open post."""
	rows = await db.fetchall(
		"SELECT * FROM qc_quiz_posts WHERE channel_id=%s AND status='open' "
		"ORDER BY id DESC LIMIT 1", [channel_id])
	return rows[0] if rows else None


async def close_post(post_id):
	await db.update("qc_quiz_posts", {"status": "closed"}, {"id": post_id})


# ── answers ──────────────────────────────────────────────────────────────
async def get_answer(post_id, user_id):
	return await db.select_one(["*"], "qc_quiz_answers", {"post_id": post_id, "user_id": user_id})


async def record_reveal(post_id, user_id, nick, revealed_at, deadline_at):
	"""Create the answer row at reveal if absent (race-safe via INSERT IGNORE — a
	double-click never resets the deadline). Returns (row, created)."""
	existing = await get_answer(post_id, user_id)
	if existing:
		return existing, False
	await db.insert("qc_quiz_answers", dict(
		post_id=post_id, user_id=user_id, nick=nick, revealed_at=revealed_at,
		deadline_at=deadline_at, choice_index=None, is_correct=None,
		answered_at=None, response_ms=None), on_dublicate="ignore")
	return await get_answer(post_id, user_id), True


async def record_answer(post_id, user_id, choice_index, is_correct, answered_at, response_ms):
	"""Atomically record the answer ONLY if the user has not already answered. The
	`answered_at IS NULL` guard closes the read-then-write TOCTOU race: nextcord
	dispatches each click as its own task, so two near-simultaneous taps both pass the
	in-handler `answered_at is None` check; the DB row lock then serialises these
	UPDATEs and the second matches 0 rows, so the first answer wins and is never
	overwritten. (db.update can't express the conditional WHERE, hence raw execute.)"""
	await db.execute(
		"UPDATE qc_quiz_answers SET choice_index=%s, is_correct=%s, answered_at=%s, response_ms=%s "
		"WHERE post_id=%s AND user_id=%s AND answered_at IS NULL",
		[choice_index, (1 if is_correct else 0), answered_at, response_ms, post_id, user_id])


async def record_answer_multi(post_id, user_id, choice_indices, is_correct, answered_at, response_ms):
	"""Record a multi-select answer once (same answered_at IS NULL TOCTOU guard as
	record_answer). Stores the chosen set as JSON in choice_indices."""
	await db.execute(
		"UPDATE qc_quiz_answers SET choice_indices=%s, is_correct=%s, answered_at=%s, response_ms=%s "
		"WHERE post_id=%s AND user_id=%s AND answered_at IS NULL",
		[json.dumps(sorted(int(i) for i in choice_indices)), (1 if is_correct else 0),
		 answered_at, response_ms, post_id, user_id])


async def answers_for_post(post_id):
	rows = await db.fetchall(
		"SELECT * FROM qc_quiz_answers WHERE post_id=%s AND answered_at IS NOT NULL", [post_id])
	return rows or []


async def week_answers_by_week(channel_id, week):
	"""Answered rows for posts in a given SCHEDULE week — feeds scoring.tally. Robust to
	calendar drift (keys off the schedule week, not a rolling 7-day window)."""
	rows = await db.fetchall(
		"SELECT a.user_id, a.nick, a.is_correct FROM qc_quiz_answers a "
		"JOIN qc_quiz_posts p ON p.id=a.post_id "
		"WHERE p.channel_id=%s AND p.week=%s AND a.answered_at IS NOT NULL",
		[channel_id, week])
	return rows or []


async def week_answers(channel_id, week_start_ts, week_end_ts):
	"""Answered rows in [start, end) for posts in this channel — feeds scoring.tally."""
	rows = await db.fetchall(
		"SELECT a.user_id, a.nick, a.is_correct FROM qc_quiz_answers a "
		"JOIN qc_quiz_posts p ON p.id=a.post_id "
		"WHERE p.channel_id=%s AND a.answered_at>=%s AND a.answered_at<%s "
		"AND a.answered_at IS NOT NULL",
		[channel_id, week_start_ts, week_end_ts])
	return rows or []
