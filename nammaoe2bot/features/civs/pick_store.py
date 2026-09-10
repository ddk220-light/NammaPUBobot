"""One durable JSON snapshot per match. All changes share one row lock.

Suggested picks never enter civ_picks, which records actual played games.
"""
import json
import time

from nammaoe2bot.runtime.database import db
from . import picking


db.ensure_table(dict(
	tname='civ_pick_rounds',
	columns=[
		dict(cname='channel_id', ctype=db.types.int),
		dict(cname='match_id', ctype=db.types.int),
		dict(cname='state_json', ctype=db.types.dict),
		dict(cname='status', ctype=db.types.str),
		dict(cname='dirty', ctype=db.types.bool),
		dict(cname='message_id', ctype=db.types.int, notnull=False),
	],
	primary_keys=['channel_id', 'match_id'],
	indexes=[('idx_civ_round_status', ['status']), ('idx_civ_round_dirty', ['dirty'])],
))


db.ensure_table(dict(
	tname='civ_pick_history',
	columns=[
		dict(cname='channel_id', ctype=db.types.int),
		dict(cname='match_id', ctype=db.types.int),
		dict(cname='generation', ctype=db.types.int),
		dict(cname='user_id', ctype=db.types.int),
		dict(cname='civ', ctype=db.types.str),
		dict(cname='at', ctype=db.types.int),
	],
	primary_keys=['channel_id', 'match_id', 'generation', 'user_id'],
	indexes=[('idx_civ_pick_history_channel_at', ['channel_id', 'at'])],
))


def unpack(row):
	if row:
		return {**row, 'state': json.loads(row['state_json'])}
	return None


async def get(channel_id, match_id):
	return unpack(await db.fetchone(
		'SELECT * FROM civ_pick_rounds WHERE channel_id=%s AND match_id=%s', [channel_id, match_id]))


async def pending():
	return [unpack(r) for r in await db.fetchall(
		"SELECT * FROM civ_pick_rounds WHERE status='open' "
		"UNION SELECT * FROM civ_pick_rounds WHERE dirty=1") or []]


async def _write(tx, channel_id, match_id, state):
	await tx.execute(
		'UPDATE civ_pick_rounds SET state_json=%s, status=%s, dirty=1 '
		'WHERE channel_id=%s AND match_id=%s',
		[json.dumps(state), state['status'], channel_id, match_id])


async def start(channel_id, match_id, roster, user_id, minutes, redo, admin=False, validate=None):
	"""Insert a lock anchor, then re-read. Concurrent starts cannot overwrite picks.

	validate rechecks the live roster after lock acquisition; it is synchronous.
	"""
	async with db.transaction() as tx:
		await tx.execute(
			"INSERT INTO civ_pick_rounds (channel_id,match_id,state_json,status,dirty) "
			"VALUES (%s,%s,'{}','new',0) ON DUPLICATE KEY UPDATE match_id=VALUES(match_id)",
			[channel_id, match_id])
		row = await tx.fetchone(
			'SELECT * FROM civ_pick_rounds WHERE channel_id=%s AND match_id=%s FOR UPDATE',
			[channel_id, match_id])
		previous = json.loads(row['state_json'])
		if previous and not redo:
			return False
		if validate:
			validate()
		now = int(time.time())
		if previous:
			if not admin and previous['initiator'] != user_id:
				raise ValueError('Only the original initiator or a channel admin can redo this round.')
			if now - previous['opened_at'] < 30:
				raise ValueError('Wait 30 seconds between starts or redos for this match.')
		# Same connection: no nested checkout while holding the session lock.
		recent = await tx.fetchall(
			'SELECT civ, COUNT(*) AS uses, MAX(at) AS last_at FROM civ_pick_history '
			'WHERE channel_id=%s AND at >= %s AND at <= %s GROUP BY civ',
			[channel_id, now - 86400, now]) or []
		options = picking.select_pool(recent)
		state = picking.new_round(roster, options, user_id, now, minutes, previous)
		await _write(tx, channel_id, match_id, state)
		# Opportunistic, bounded cleanup; no new periodic job or idle DB work.
		await tx.execute('DELETE FROM civ_pick_history WHERE channel_id=%s AND at < %s LIMIT 100',
			[channel_id, now - 86400])
		return True


async def change(channel_id, match_id, operation):
	"""operation(state, now) runs under the database lock, including its clock read."""
	async with db.transaction() as tx:
		row = await tx.fetchone(
			'SELECT * FROM civ_pick_rounds WHERE channel_id=%s AND match_id=%s FOR UPDATE',
			[channel_id, match_id])
		if not row:
			return 'This civ-pick round no longer exists.'
		state = json.loads(row['state_json'])
		before = json.dumps(state)
		prior_picks = set(state['picks'])
		now = int(time.time())
		result = operation(state, now)
		if json.dumps(state) != before:
			# Record only newly accepted explicit choices, in the SAME transaction
			# as the pick. Random (including auto-timeouts) never enters history.
			for uid in state['picks'].keys() - prior_picks:
				choice = state['picks'][uid]
				if choice != picking.RANDOM:
					await tx.insert('civ_pick_history', dict(channel_id=channel_id, match_id=match_id,
						generation=state['generation'], user_id=int(uid), civ=state['options'][choice], at=now),
						on_duplicate='ignore')
			await _write(tx, channel_id, match_id, state)
		return result


async def rendered(channel_id, match_id, row, message_id):
	# Clear dirty only if the exact snapshot that was rendered still is current.
	await db.execute(
		'UPDATE civ_pick_rounds SET message_id=%s, dirty=0 '
		'WHERE channel_id=%s AND match_id=%s AND state_json=%s',
		[message_id, channel_id, match_id, row['state_json']])
