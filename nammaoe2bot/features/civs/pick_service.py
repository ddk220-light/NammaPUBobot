"""Civ-pick lifecycle and bounded Discord rendering in the existing bot process."""
import asyncio
import time
import weakref
from collections import OrderedDict

from nammaoe2bot.runtime.console import log
from . import picking, pick_store as store


class PickService:
	def __init__(self, app):
		self.app = app
		self.locks = weakref.WeakValueDictionary()
		self.due = {}
		self.deadlines = {}
		self.attempts = {}
		self.recent = OrderedDict()
		self.loaded = False
		self.task = None
		self.retry_at = 0

	def lock(self, key):
		lock = self.locks.get(key)
		if lock is None:
			lock = asyncio.Lock()
			self.locks[key] = lock
		return lock

	def throttled(self, user_id):
		now = time.monotonic()
		last = self.recent.pop(user_id, 0)
		self.recent[user_id] = now
		while len(self.recent) > 1024:
			self.recent.popitem(last=False)
		return now - last < 1

	def match(self, key):
		return next((m for m in self.app.active_matches if (m.qc.id, m.id) == key), None)

	def validate(self, key, roster):
		match = self.match(key)
		error = picking.validate_match(match)
		if error:
			raise ValueError(error)
		if not match.cfg.get('civpick_enabled', True):
			raise ValueError('Civ picking is disabled for this queue.')
		if picking.roster_for(match) != roster:
			raise ValueError('The roster changed. Run the command again with the new teams.')

	async def refresh(self, key):
		"""Caller holds local lock, so earlier card edits cannot finish after later ones."""
		self.due.pop(key, None)
		row = await store.get(*key)
		if not row or not row['state']:
			return
		state = row['state']
		if state['status'] == 'open':
			self.deadlines[key] = state['closes_at']
			self.due[key] = state['closes_at']
		else:
			self.deadlines.pop(key, None)
			self.due.pop(key, None)
		if row['dirty'] or not row['message_id']:
			try:
				await self.render(key, row)
			except Exception:
				self.due[key] = time.time() + 5
				raise
		self.attempts.pop(key, None)

	async def render(self, key, row):
		import nextcord
		from .pick_view import card
		channel = self.app.client.get_channel(key[0])
		if channel is None:
			raise RuntimeError('Civ-pick channel is unavailable.')
		embed, view = card(key[1], row['state'])
		kwargs = dict(embed=embed, view=view, allowed_mentions=nextcord.AllowedMentions.none())
		message_id = row['message_id']
		if message_id:
			try:
				await channel.get_partial_message(message_id).edit(**kwargs)
			except nextcord.NotFound:
				message_id = None
		if not message_id:
			message = await channel.send(**kwargs)
			message_id = message.id
		await store.rendered(*key, row, message_id)

	def schedule(self, key):
		self.attempts.pop(key, None)
		self.due[key] = time.time()

	def think(self):
		# Zero DB work while idle; at most one background worker, never block the tick.
		if not self.app.ready or (self.task and not self.task.done()) or time.time() < self.retry_at:
			return
		if not self.loaded or any(at <= time.time() for at in self.due.values()):
			self.task = asyncio.create_task(self._work())

	async def _work(self):
		try:
			if not self.loaded:
				for row in await store.pending():
					self.due[(row['channel_id'], row['match_id'])] = 0
				self.loaded = True
			for key in [k for k, at in self.due.items() if at <= time.time()]:
				async with self.lock(key):
					try:
						def reconcile(state, now, key=key):
							if state['status'] != 'open':
								return
							try:
								self.validate(key, state['roster'])
							except ValueError:
								state['status'] = 'invalidated'
								return
							picking.expire(state, now)
						await store.change(*key, reconcile)
						await self.refresh(key)
					except Exception as e:
						count = self.attempts.get(key, 0) + 1
						self.attempts[key] = count
						log.error(f'Civ-pick refresh {key} failed ({count}/3): {e}')
						if count < 3:
							self.due[key] = time.time() + 10 * count
						else:
							self.due.pop(key, None)
							deadline = self.deadlines.pop(key, 0)
							if deadline > time.time():
								self.due[key] = deadline
							self.attempts.pop(key, None)
		except Exception as e:
			log.error(f'Civ-pick recovery failed: {e}')
			self.retry_at = time.time() + 60

	async def invalidate(self, match, _ctx):
		key = (match.qc.id, match.id)
		if self.loaded and key not in self.due and key not in self.deadlines:
			return
		async with self.lock(key):
			def close(state, _now):
				if state['status'] == 'open':
					state['status'] = 'invalidated'
			await store.change(*key, close)
			self.schedule(key)
