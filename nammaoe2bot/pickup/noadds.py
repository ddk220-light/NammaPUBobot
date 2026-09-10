# -*- coding: utf-8 -*-
import time
from nammaoe2bot.runtime.database import db
from nammaoe2bot.runtime.utils import get_nick

db.ensure_table(dict(
	tname="queue_bans",
	columns=[
		dict(cname="id", ctype=db.types.int, autoincrement=True),
		dict(cname="guild_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="name", ctype=db.types.str),
		dict(cname="is_active", ctype=db.types.bool, default=1),
		dict(cname="at", ctype=db.types.int),
		dict(cname="duration", ctype=db.types.int),
		dict(cname="reason", ctype=db.types.text),
		dict(cname="by", ctype=db.types.str),
		dict(cname="released_by", ctype=db.types.str)
	],
	primary_keys=["id"]
))


class NoAdds:

	def __init__(self):
		self.next_tick = 0

	@staticmethod
	async def get_user(ctx, member):
		""" seconds left on this member's queue ban, 0 if not banned """
		now = int(time.time())
		m_noadd = await db.fetchone(
			"SELECT duration, at FROM queue_bans WHERE guild_id=%s AND user_id=%s "
			"AND is_active=1 AND at+duration>%s ORDER BY id DESC LIMIT 1",
			[ctx.channel.guild.id, member.id, now])
		return max(0, (m_noadd['duration']+m_noadd['at'])-int(time.time())) if m_noadd else 0

	@staticmethod
	async def noadd(ctx, member, duration, moderator, reason=None):
		await db.update(
			'queue_bans',
			dict(is_active=0, released_by="another noadd"),
			keys=dict(guild_id=ctx.channel.guild.id, user_id=member.id, is_active=1)
		)
		await db.insert('queue_bans', dict(
			guild_id=ctx.channel.guild.id,
			user_id=member.id,
			name=get_nick(member),
			at=int(time.time()),
			duration=duration,
			reason=reason,
			by=get_nick(moderator)
		))

	@staticmethod
	async def forgive(ctx, member, moderator):
		noadd_id = await db.fetchone(
			"SELECT id FROM queue_bans WHERE guild_id=%s AND user_id=%s "
			"AND is_active=1 AND at+duration>%s ORDER BY id DESC LIMIT 1",
			[ctx.channel.guild.id, member.id, int(time.time())])
		if not noadd_id:
			return False
		await db.update(
			'queue_bans',
			dict(is_active=0, released_by=get_nick(moderator)),
			keys=noadd_id
		)
		return True

	@staticmethod
	async def get_noadds(ctx):
		return await db.fetchall(
			"SELECT * FROM queue_bans WHERE guild_id=%s AND is_active=1 "
			"AND at+duration>%s ORDER BY id DESC",
			[ctx.channel.guild.id, int(time.time())])

	async def think(self, frame_time):
		# Expiration is derived at read time.  A once-per-minute UPDATE existed
		# only to maintain a redundant cache bit and kept an otherwise idle MySQL
		# service awake around the clock.
		return None


noadds = NoAdds()
