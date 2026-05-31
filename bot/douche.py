# -*- coding: utf-8 -*-
"""
"Douche" tracking — a light community/moderation record ported from BombayBot.

A moderator marks that one player "douched" another; we keep a per-guild log
and expose received/given counts plus a leaderboard. The table is created at
import time the same way the rest of the bot's tables are (see
bot/stats/stats.py) — this module is imported during `import bot`, which runs
after database.db.connect() has set db.loop but before the event loop starts
spinning, so db.ensure_table()'s run_until_complete is safe here.
"""
import time
from core.database import db
from core.utils import get_nick

db.ensure_table(dict(
	tname="qc_douche",
	columns=[
		dict(cname="id", ctype=db.types.int, autoincrement=True),
		dict(cname="guild_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="name", ctype=db.types.str),
		dict(cname="target_user_id", ctype=db.types.int),
		dict(cname="target_name", ctype=db.types.str),
		dict(cname="at", ctype=db.types.int),
		dict(cname="by", ctype=db.types.str)
	],
	primary_keys=["id"]
))


class Douche:

	@staticmethod
	async def add(guild_id, member, target, moderator):
		await db.insert('qc_douche', dict(
			guild_id=guild_id,
			user_id=member.id,
			name=get_nick(member),
			target_user_id=target.id,
			target_name=get_nick(target),
			at=int(time.time()),
			by=get_nick(moderator)
		))

	@staticmethod
	async def user_summary(guild_id, member):
		received = await db.fetchone(
			"SELECT COUNT(*) AS count FROM qc_douche WHERE guild_id=%s AND target_user_id=%s",
			[guild_id, member.id]
		)
		given = await db.fetchone(
			"SELECT COUNT(*) AS count FROM qc_douche WHERE guild_id=%s AND user_id=%s",
			[guild_id, member.id]
		)
		recent = await db.fetchall(
			"SELECT target_name, at FROM qc_douche WHERE guild_id=%s AND user_id=%s ORDER BY at DESC LIMIT 5",
			[guild_id, member.id]
		)
		return dict(
			received=received['count'] if received else 0,
			given=given['count'] if given else 0,
			recent=recent or []
		)

	@staticmethod
	async def leaderboard(guild_id, limit=10):
		return await db.fetchall(
			"SELECT user_id, name, COUNT(*) AS count FROM qc_douche "
			"WHERE guild_id=%s GROUP BY user_id, name ORDER BY count DESC LIMIT %s",
			[guild_id, limit]
		)


douche = Douche()
