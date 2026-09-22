# -*- coding: utf-8 -*-
import time
import datetime
import asyncio
from nammaoe2bot.runtime.console import log
from nammaoe2bot.runtime.client import dc
from nammaoe2bot.runtime.database import db
from nammaoe2bot.exceptions import Exceptions as Exc
from nammaoe2bot.runtime.utils import iter_to_dict, find, get_nick  # noqa: F401

db.ensure_table(dict(
	tname="player_ratings",
	columns=[
		dict(cname="channel_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="nick", ctype=db.types.str),
		dict(cname="is_hidden", ctype=db.types.bool, default=0),
		dict(cname="rating", ctype=db.types.int),
		dict(cname="deviation", ctype=db.types.int),
		dict(cname="wins", ctype=db.types.int, notnull=True, default=0),
		dict(cname="losses", ctype=db.types.int, notnull=True, default=0),
		dict(cname="draws", ctype=db.types.int, notnull=True, default=0),
		dict(cname="streak", ctype=db.types.int, notnull=True, default=0),
		dict(cname="last_ranked_match_at", ctype=db.types.int, notnull=False)
	],
	primary_keys=["user_id", "channel_id"]
))

db.ensure_table(dict(
	tname="rating_history",
	columns=[
		dict(cname="id", ctype=db.types.int, autoincrement=True),
		dict(cname="channel_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="at", ctype=db.types.int),
		dict(cname="rating_before", ctype=db.types.int),
		dict(cname="rating_change", ctype=db.types.int),
		dict(cname="deviation_before", ctype=db.types.int),
		dict(cname="deviation_change", ctype=db.types.int),
		dict(cname="match_id", ctype=db.types.int),
		dict(cname="reason", ctype=db.types.str)
	],
	primary_keys=["id"]
))

db.ensure_table(dict(
	tname="matches",
	columns=[
		dict(cname="match_id", ctype=db.types.int),
		dict(cname="channel_id", ctype=db.types.int),
		dict(cname="queue_id", ctype=db.types.int),
		dict(cname="queue_name", ctype=db.types.str),
		dict(cname="reported_at", ctype=db.types.int),
		dict(cname="alpha_name", ctype=db.types.str),
		dict(cname="beta_name", ctype=db.types.str),
		dict(cname="ranked", ctype=db.types.bool),
		dict(cname="winner", ctype=db.types.bool),
		dict(cname="alpha_score", ctype=db.types.int),
		dict(cname="beta_score", ctype=db.types.int),
		dict(cname="maps", ctype=db.types.str)
	],
	primary_keys=["match_id"]
))

db.ensure_table(dict(
	tname="match_counter",
	columns=[
		dict(cname="next_id", ctype=db.types.int)
	]
))

db.ensure_table(dict(
	tname="match_players",
	columns=[
		dict(cname="match_id", ctype=db.types.int),
		dict(cname="channel_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="nick", ctype=db.types.str),
		dict(cname="team", ctype=db.types.bool)
	],
	primary_keys=["match_id", "user_id"]
))


async def check_match_id_counter():
	"""Repair the floor under the same lock as restoration and ID allocation."""
	async with db.transaction() as tx:
		counter = await tx.fetchone("SELECT next_id FROM match_counter FOR UPDATE")
		maximum = await tx.fetchone("SELECT COALESCE(MAX(match_id) + 1, 0) AS next_id FROM matches")
		floor = int(maximum["next_id"])
		if counter is None:
			await tx.insert("match_counter", dict(next_id=floor))
		elif counter["next_id"] < floor:
			await tx.execute("UPDATE match_counter SET next_id=%s", [floor])


async def next_match():
	"""Atomically reserve and return one globally unique bot match id.

	The old SELECT followed by an autocommit UPDATE let two simultaneous queue
	starts read the same value. Historical onboarding also reserves ranges, so
	both paths now lock the single counter row through the adapter transaction.
	"""
	async with db.transaction() as tx:
		counter = await tx.fetchone("SELECT next_id FROM match_counter FOR UPDATE")
		if counter is None:
			maximum = await tx.fetchone(
				"SELECT COALESCE(MAX(match_id) + 1, 0) AS next_id FROM matches")
			match_id = int((maximum or {}).get("next_id") or 0)
			await tx.insert("match_counter", {"next_id": match_id + 1})
		else:
			match_id = int(counter["next_id"])
			await tx.execute(
				"UPDATE match_counter SET next_id=%s WHERE next_id=%s",
				[match_id + 1, match_id])
	log.debug(f"Current match_id is {match_id}")
	return match_id


async def reserve_restored_ids(match_ids):
	"""Raise the counter floor once at startup; do not allocate replacement IDs."""
	if not match_ids:
		return
	floor = max(match_ids) + 1
	async with db.transaction() as tx:
		counter = await tx.fetchone("SELECT next_id FROM match_counter FOR UPDATE")
		if counter is None:
			maximum = await tx.fetchone("SELECT COALESCE(MAX(match_id) + 1, 0) AS next_id FROM matches")
			floor = max(floor, int(maximum["next_id"]))
			await tx.insert("match_counter", dict(next_id=floor))
		elif counter["next_id"] < floor:
			await tx.execute("UPDATE match_counter SET next_id=%s", [floor])


async def validate_recorded_match(reader, row, players, teams, rating_channel_id=None):
	"""Reject incomplete legacy results instead of silently treating them as committed."""
	roster = await reader.fetchall(
		"SELECT user_id, team, channel_id FROM match_players WHERE match_id=%s", [row["match_id"]])
	expected = {uid: next((i for i, team in enumerate(teams[:2]) if uid in team), None)
		for uid in players}
	if (len(roster) != len(expected)
			or {p["user_id"]: p["team"] for p in roster} != expected
			or any(p["channel_id"] != row["channel_id"] for p in roster)):
		raise Exc.ValueError(f"Match {row['match_id']} has an inconsistent stored roster; manual review required.")
	if row["ranked"]:
		history = await reader.fetchall(
			"SELECT user_id, channel_id FROM rating_history WHERE match_id=%s", [row["match_id"]])
		if (len(history) != len(expected) or {p["user_id"] for p in history} != set(expected)
				or (rating_channel_id is not None
					and any(p["channel_id"] != rating_channel_id for p in history))):
			raise Exc.ValueError(f"Match {row['match_id']} has incomplete rating history; manual review required.")


async def register_match_unranked(ctx, m, *, notify=True):
	return await _register_match(ctx, m, ranked=False, notify=notify)


async def register_match_ranked(ctx, m, *, notify=True):
	return await _register_match(ctx, m, ranked=True, notify=notify)


async def _register_match(ctx, m, *, ranked, notify):
	now = int(time.time())
	row = dict(
		match_id=m.id, channel_id=m.qc.id, queue_id=m.queue.cfg.p_key, queue_name=m.queue.name,
		alpha_name=m.teams[0].name, beta_name=m.teams[1].name,
		reported_at=now, ranked=int(ranked), winner=m.winner if ranked else None,
		alpha_score=m.scores[0] if ranked else None,
		beta_score=m.scores[1] if ranked else None, maps="\n".join(m.maps))
	result = None
	async with db.transaction() as tx:
		existing = await tx.fetchone("SELECT * FROM matches WHERE match_id=%s FOR UPDATE", [m.id])
		if existing:
			# The timestamp is the time of the first successful report. Everything
			# else must agree: a conflicting retry must never overwrite a result.
			if any(existing[key] != value for key, value in row.items() if key != "reported_at"):
				raise Exc.ValueError(f"Match {m.id} already has a different recorded result.")
			await validate_recorded_match(tx, existing, [p.id for p in m.players],
				[[p.id for p in team] for team in m.teams], m.qc.rating.channel_id)
		else:
			await tx.insert("matches", row)
			if ranked:
				result = await _write_ranked(tx, m, now)
			else:
				await _write_unranked(tx, m)
	# No external requests while holding the pooled connection/row locks.
	m._result_committed = True
	if notify:
		await notify_match_result(ctx, m, result)
	return result


async def _write_unranked(tx, m):
	for p in sorted(m.players, key=lambda p: p.id):
		await tx.insert("player_ratings", dict(channel_id=m.qc.id, user_id=p.id), on_duplicate="ignore")
		nick = get_nick(p)
		await tx.update("player_ratings", dict(nick=nick), keys=dict(channel_id=m.qc.id, user_id=p.id))
		team = next((i for i, team in enumerate(m.teams[:2]) if p in team), None)
		await tx.insert("match_players", dict(match_id=m.id, channel_id=m.qc.id, user_id=p.id, nick=nick, team=team))


async def _write_ranked(tx, m, now):
	for channel_id in sorted({m.qc.id, m.qc.rating.channel_id}):
		await tx.insert_many('player_ratings', (
			dict(channel_id=channel_id, user_id=p.id, nick=get_nick(p))
			for p in sorted(m.players, key=lambda p: p.id)
		), on_duplicate="ignore")

	await tx.fetchall(
		"SELECT user_id FROM player_ratings WHERE channel_id=%s AND user_id IN ("
		+ ",".join(["%s"] * len(m.players)) + ") ORDER BY user_id FOR UPDATE",
		[m.qc.rating.channel_id, *sorted(p.id for p in m.players)])

	results = [[
		await m.qc.rating.get_players((p.id for p in m.teams[0]), transaction=tx),
		await m.qc.rating.get_players((p.id for p in m.teams[1]), transaction=tx),
	]]

	if m.winner is None:  # draw
		after = m.qc.rating.rate(winners=results[0][0], losers=results[0][1], draw=True)
		results.append(after)
	else:  # process actual scores
		n = 0
		while n < m.scores[0] or n < m.scores[1]:
			if n < m.scores[0]:
				after = m.qc.rating.rate(winners=results[-1][0], losers=results[-1][1], draw=False)
				results.append(after)
			if n < m.scores[1]:
				after = m.qc.rating.rate(winners=results[-1][1], losers=results[-1][0], draw=False)
				results.append(after[::-1])
			n += 1

	after = iter_to_dict((*results[-1][0], *results[-1][1]), key='user_id')
	before = iter_to_dict((*results[0][0], *results[0][1]), key='user_id')

	for p in sorted(m.players, key=lambda p: p.id):
		nick = get_nick(p)
		team = 0 if p in m.teams[0] else 1

		await tx.update(
			"player_ratings",
			dict(
				nick=nick,
				rating=after[p.id]['rating'],
				deviation=after[p.id]['deviation'],
				wins=after[p.id]['wins'],
				losses=after[p.id]['losses'],
				draws=after[p.id]['draws'],
				streak=after[p.id]['streak'],
				last_ranked_match_at=now,
			),
			keys=dict(channel_id=m.qc.rating.channel_id, user_id=p.id)
		)

		await tx.insert(
			'match_players',
			dict(match_id=m.id, channel_id=m.qc.id, user_id=p.id, nick=nick, team=team)
		)
		await tx.insert('rating_history', dict(
			channel_id=m.qc.rating.channel_id,
			user_id=p.id,
			at=now,
			rating_before=before[p.id]['rating'],
			rating_change=after[p.id]['rating']-before[p.id]['rating'],
			deviation_before=before[p.id]['deviation'],
			deviation_change=after[p.id]['deviation']-before[p.id]['deviation'],
			match_id=m.id,
			reason=m.queue.name
		))

	return before, after


async def notify_match_result(ctx, m, result):
	await m.qc.app.match_events.emit("result_recorded", m, ctx)
	if result is not None:
		# One notification failure must not suppress the remaining post-commit
		# actions or make the captain think the database report failed.
		try:
			await m.qc.update_rating_roles(*m.players)
		except Exception as exc:
			log.error(f"Rating role update failed after committing match {m.id}: {exc}")
		try:
			await m.print_rating_results(ctx, *result)
		except Exception as exc:
			log.error(f"Result message failed after committing match {m.id}: {exc}")


async def undo_match(ctx, match_id):
	match = await db.select_one(('ranked', 'winner'), 'matches', where=dict(match_id=match_id, channel_id=ctx.qc.id))
	if not match:
		return False

	if match['ranked']:
		p_matches = await db.select(('user_id', 'team'), 'match_players', where=dict(match_id=match_id))
		p_history = iter_to_dict(
			await db.select(
				('user_id', 'rating_change', 'deviation_change'), 'rating_history', where=dict(match_id=match_id)
			), key='user_id'
		)
		stats = iter_to_dict(
			await ctx.qc.rating.get_players((p['user_id'] for p in p_matches)), key='user_id'
		)

		for p in p_matches:
			new = stats[p['user_id']]
			changes = p_history[p['user_id']]

			print(match['winner'])
			if match['winner'] is None:
				new['draws'] = max((new['draws'] - 1, 0))
			elif match['winner'] == p['team']:
				new['wins'] = max((new['wins'] - 1, 0))
			else:
				new['losses'] = max((new['losses'] - 1, 0))

			new['rating'] = max((new['rating']-changes['rating_change'], 0))
			new['deviation'] = max((new['deviation']-changes['deviation_change'], 0))

			await db.update("player_ratings", new, keys=dict(channel_id=ctx.qc.rating.channel_id, user_id=p['user_id']))
		await db.delete("rating_history", where=dict(match_id=match_id))
		members = (ctx.channel.guild.get_member(p['user_id']) for p in p_matches)
		await ctx.qc.update_rating_roles(*(m for m in members if m is not None))

	await db.delete('match_players', where=dict(match_id=match_id))
	await db.delete('matches', where=dict(match_id=match_id))
	return True


async def replace_player(channel_id, user_id1, user_id2, new_nick):
	await db.delete("player_ratings", {'channel_id': channel_id, 'user_id': user_id2})
	where = {'channel_id': channel_id, 'user_id': user_id1}
	await db.update("player_ratings", {'user_id': user_id2, 'nick': new_nick}, where)
	await db.update("rating_history", {'user_id': user_id2}, where)
	await db.update("match_players", {'user_id': user_id2}, where)


async def user_stats(channel_id, user_id):
	data = await db.fetchall(
		"SELECT `queue_name`, COUNT(*) as count FROM `match_players` AS pm " +
		"JOIN `matches` AS m ON pm.match_id=m.match_id " +
		"WHERE pm.channel_id=%s AND user_id=%s " +
		"GROUP BY m.queue_name ORDER BY count DESC",
		(channel_id, user_id)
	)
	stats = dict(total=sum((i['count'] for i in data)))
	stats['queues'] = data
	return stats


async def top(channel_id, time_gap=None):
	total = await db.fetchone(
		"SELECT COUNT(*) as count FROM `matches` WHERE channel_id=%s" + (f" AND reported_at>{time_gap} " if time_gap else ""),
		(channel_id, )
	)

	data = await db.fetchall(
		"SELECT p.nick as nick, COUNT(*) as count FROM `match_players` AS pm " +
		"JOIN `player_ratings` AS p ON pm.user_id=p.user_id AND pm.channel_id=p.channel_id " +
		"JOIN `matches` AS m ON pm.match_id=m.match_id " +
		"WHERE pm.channel_id=%s " +
		(f"AND m.reported_at>{time_gap} " if time_gap else "") +
		"GROUP BY p.user_id ORDER BY count DESC LIMIT 10",
		(channel_id, )
	)
	stats = dict(total=total['count'])
	stats['players'] = data
	return stats


async def last_games(channel_id):
	#  get last played ranked match for all players
	data = await db.fetchall(
		"SELECT tmp.at, p.* " +
		"FROM `player_ratings` AS p " +
		"LEFT JOIN (" +
		"  SELECT MAX(h.at) AS at, h.user_id FROM `rating_history` AS h" +
		"    WHERE h.channel_id=%s AND h.match_id IS NOT NULL" +
		"    GROUP BY h.user_id" +
		") AS tmp ON p.user_id=tmp.user_id " +
		"WHERE p.channel_id=%s",
		(channel_id, channel_id)
	)
	return data


class StatsJobs:

	def __init__(self):
		self.next_decay_at = int(self.next_monday().timestamp())

	@staticmethod
	def next_monday():
		d = datetime.datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
		d += datetime.timedelta(days=1)
		while d.weekday() != 0:  # 0 for monday
			d += datetime.timedelta(days=1)
		return d

	@staticmethod
	def tomorrow():
		d = datetime.datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
		d += datetime.timedelta(days=1)
		return d

	@staticmethod
	async def apply_rating_decays():
		log.info("--- Applying weekly deviation decays ---")
		for qc in dc.app.channels.values():
			await qc.apply_rating_decay()
			await asyncio.sleep(1)

	async def think(self, frame_time):
		if frame_time > self.next_decay_at:
			self.next_decay_at = int(self.next_monday().timestamp())
			asyncio.create_task(self.apply_rating_decays())


jobs = StatsJobs()
