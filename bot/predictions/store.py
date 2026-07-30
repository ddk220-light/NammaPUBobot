# -*- coding: utf-8 -*-
"""Async MySQL access for audience predictions. Thin wrappers over
core.database.db; all grading logic lives in bot.predictions.scoring (pure,
tested). No nextcord import here so importing bot.predictions stays test-safe."""
from core.database import db


# ── posts ────────────────────────────────────────────────────────────────
async def create_post(channel_id, match_id, team0_name, team1_name, opened_at, freezes_at):
	"""Insert an open post and return its id."""
	return await db.insert("qc_prediction_posts", dict(
		channel_id=channel_id, match_id=match_id, message_id=None,
		team0_name=team0_name, team1_name=team1_name,
		opened_at=opened_at, freezes_at=freezes_at, status="open"))


async def set_message_id(post_id, message_id):
	await db.update("qc_prediction_posts", {"message_id": message_id}, {"id": post_id})


async def due_to_freeze(now):
	"""Open posts whose voting window has elapsed."""
	return await db.fetchall(
		"SELECT * FROM qc_prediction_posts WHERE status='open' AND freezes_at<=%s", [now]) or []


async def live_for_match(match_id):
	"""The post still riding on this match (open or frozen), or None."""
	rows = await db.fetchall(
		"SELECT * FROM qc_prediction_posts "
		"WHERE match_id=%s AND status IN ('open','frozen') ORDER BY id DESC LIMIT 1", [match_id])
	return rows[0] if rows else None


async def freeze(post_id, votes0, votes1):
	await db.update("qc_prediction_posts",
					{"status": "frozen", "votes0": votes0, "votes1": votes1}, {"id": post_id})


async def resolve(post_id, winner_idx, now):
	await db.update("qc_prediction_posts",
					{"status": "resolved", "winner_idx": winner_idx, "resolved_at": now},
					{"id": post_id})


async def void(post_id, now):
	"""Drop a post out of scoring. Votes are left in place for the audit trail but
	never counted — the leaderboard only joins resolved posts."""
	await db.update("qc_prediction_posts", {"status": "void", "resolved_at": now}, {"id": post_id})


# ── votes ────────────────────────────────────────────────────────────────
async def save_ballots(post_id, ballots, nicks, voted_at):
	"""Persist the frozen tally. ballots is {user_id: team_idx}."""
	if not ballots:
		return
	await db.insert_many("qc_prediction_votes", (
		dict(post_id=post_id, user_id=uid, nick=nicks.get(uid, str(uid)),
			 team_idx=idx, voted_at=voted_at)
		for uid, idx in ballots.items()
	), on_duplicate="ignore")


async def ballots_for(post_id):
	"""{user_id: team_idx} as frozen, plus {user_id: nick}."""
	rows = await db.fetchall(
		"SELECT user_id, nick, team_idx FROM qc_prediction_votes WHERE post_id=%s", [post_id]) or []
	return ({r["user_id"]: r["team_idx"] for r in rows},
			{r["user_id"]: r["nick"] for r in rows})


async def mark_correct(post_id, results):
	"""results is {user_id: bool} from scoring.grade."""
	for uid, ok in results.items():
		await db.update("qc_prediction_votes", {"is_correct": 1 if ok else 0},
						{"post_id": post_id, "user_id": uid})


# ── aggregates ───────────────────────────────────────────────────────────
_LB_SQL = (
	"SELECT v.user_id, MAX(v.nick) AS nick, "
	"       COALESCE(SUM(v.is_correct), 0) AS correct, COUNT(*) AS total "
	"FROM qc_prediction_votes v "
	"JOIN qc_prediction_posts p ON p.id = v.post_id "
	"WHERE p.status='resolved'{channel} "
	"GROUP BY v.user_id "
	"ORDER BY correct DESC, total ASC"
)


async def leaderboard(channel_id=None):
	"""[{user_id, nick, correct, total}] best-first across resolved posts."""
	if channel_id is None:
		return await db.fetchall(_LB_SQL.format(channel="")) or []
	return await db.fetchall(_LB_SQL.format(channel=" AND p.channel_id=%s"), [channel_id]) or []


async def user_stats(user_id, channel_id=None):
	"""(correct, total) for one user across resolved posts."""
	sql = (
		"SELECT COALESCE(SUM(v.is_correct), 0) AS correct, COUNT(*) AS total "
		"FROM qc_prediction_votes v "
		"JOIN qc_prediction_posts p ON p.id = v.post_id "
		"WHERE p.status='resolved' AND v.user_id=%s"
	)
	params = [user_id]
	if channel_id is not None:
		sql += " AND p.channel_id=%s"
		params.append(channel_id)
	rows = await db.fetchall(sql, params)
	if not rows:
		return 0, 0
	return int(rows[0]["correct"] or 0), int(rows[0]["total"] or 0)
