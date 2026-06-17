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
	"""Insert an open post (message_id filled in after the Discord send). Returns the
	new post id (cur.lastrowid)."""
	return await db.insert("qc_quiz_posts", dict(
		channel_id=channel_id, message_id=None, question_id=q["id"], category=q["category"],
		prompt=q["prompt"], options_json=json.dumps(q["options"]), correct_index=q["correct_index"],
		explanation=q["explanation"], opened_at=opened_at, closes_at=closes_at, status="open"))


async def set_message_id(post_id, message_id):
	await db.update("qc_quiz_posts", {"message_id": message_id}, {"id": post_id})


async def get_post(post_id):
	return await db.select_one(["*"], "qc_quiz_posts", {"id": post_id})


async def due_to_close(now_ts):
	rows = await db.fetchall(
		"SELECT * FROM qc_quiz_posts WHERE status='open' AND closes_at<=%s", [now_ts])
	return rows or []


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
	await db.update("qc_quiz_answers", dict(
		choice_index=choice_index, is_correct=(1 if is_correct else 0),
		answered_at=answered_at, response_ms=response_ms),
		{"post_id": post_id, "user_id": user_id})


async def answers_for_post(post_id):
	rows = await db.fetchall(
		"SELECT * FROM qc_quiz_answers WHERE post_id=%s AND answered_at IS NOT NULL", [post_id])
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
