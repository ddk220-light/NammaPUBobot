# -*- coding: utf-8 -*-
import mmap  # noqa: F401
import random
import bot
from nextcord.errors import DiscordException

from core.utils import join_and
from core.console import log  # noqa: F401
from bot.match.subbing import CHECK_IN_TIMEOUT_REVERT, check_in_timeout_action, pick_available, should_warn


class CheckIn:

	READY_EMOJI = "☑"
	NOT_READY_EMOJI = "⛔"
	INT_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6⃣", "7⃣", "8⃣", "9⃣"]

	def __init__(self, match, timeout):
		self.m = match
		self.timeout = timeout
		self.allow_discard = self.m.cfg['check_in_discard']
		self.ready_players = set()
		self.warned = False
		self.message = None

		for p in (p for p in self.m.players if p.id in bot.auto_ready.keys()):
			self.ready_players.add(p)

		if len(self.m.cfg['maps']) > 1 and self.m.cfg['vote_maps']:
			self.maps = self.m.random_maps(self.m.cfg['maps'], self.m.cfg['vote_maps'], self.m.queue.last_maps)
			self.map_votes = [set() for i in self.maps]
		else:
			self.maps = []
			self.map_votes = []

		if self.timeout:
			self.m.states.append(self.m.CHECK_IN)

	@property
	def end_time(self):
		return int(self.m.start_time + self.timeout)

	async def think(self, frame_time):
		not_ready = [m for m in self.m.players if m not in self.ready_players]
		if should_warn(frame_time, self.end_time, self.warned, len(not_ready)):
			self.warned = True
			ctx = bot.SystemContext(self.m.qc)
			await ctx.notice(self.m.gt(
				"⏳ 1 minute left to check in, {members}! If you don't ready up you'll be removed "
				"and replaced by the next queued player, or the queue reverts to gathering."
			).format(members=join_and([m.mention for m in not_ready])))

		timeout_action = check_in_timeout_action(frame_time, self.end_time, len(not_ready))
		if timeout_action:
			ctx = bot.SystemContext(self.m.qc)
			if timeout_action == CHECK_IN_TIMEOUT_REVERT:
				await self.abort_timeout(ctx)
			else:
				await self.finish(ctx)

	async def start(self, ctx):
		text = f"!spawn message {self.m.id}"
		self.message = await ctx.channel.send(text)

		emojis = [self.READY_EMOJI, self.NOT_READY_EMOJI] if self.allow_discard else [self.READY_EMOJI]
		emojis += [self.INT_EMOJIS[n] for n in range(len(self.maps))]
		try:
			for emoji in emojis:
				await self.message.add_reaction(emoji)
		except DiscordException:
			pass
		bot.waiting_reactions[self.message.id] = self.process_reaction
		await self.refresh(ctx)

	async def refresh(self, ctx):
		not_ready = [m for m in self.m.players if m not in self.ready_players]
		if len(not_ready):
			try:
				await self.message.edit(content=None, embed=self.m.embeds.check_in(not_ready))
			except DiscordException:
				pass
		else:
			await self.finish(ctx)

	async def finish(self, ctx):
		bot.waiting_reactions.pop(self.message.id)
		self.ready_players = set()
		if len(self.maps):
			order = list(range(len(self.maps)))
			random.shuffle(order)
			order.sort(key=lambda n: len(self.map_votes[n]), reverse=True)
			self.m.maps = [self.maps[n] for n in order[:self.m.cfg['map_count']]]
		await self.message.delete()

		for p in (p for p in self.m.players if p.id in bot.auto_ready.keys()):
			bot.auto_ready.pop(p.id)

		await self.m.next_state(ctx)

	async def process_reaction(self, reaction, user, remove=False):
		if self.m.state != self.m.CHECK_IN or user not in self.m.players:
			return

		if str(reaction) in self.INT_EMOJIS:
			idx = self.INT_EMOJIS.index(str(reaction))
			if idx <= len(self.maps):
				if remove:
					self.map_votes[idx].discard(user.id)
					self.ready_players.discard(user)
				else:
					self.map_votes[idx].add(user.id)
					self.ready_players.add(user)
				await self.refresh(bot.SystemContext(self.m.queue.qc))

		elif str(reaction) == self.READY_EMOJI:
			if remove:
				self.ready_players.discard(user)
			else:
				self.ready_players.add(user)
			await self.refresh(bot.SystemContext(self.m.queue.qc))

		elif str(reaction) == self.NOT_READY_EMOJI and self.allow_discard:
			if not remove:
				return await self.back_out(bot.SystemContext(self.m.queue.qc), user)

	async def set_ready(self, ctx, member, ready):
		if self.m.state != self.m.CHECK_IN:
			raise bot.Exc.MatchStateError(self.m.gt("The match is not on the check-in stage."))
		if ready:
			self.ready_players.add(member)
			await self.refresh(ctx)
		else:
			if not self.allow_discard:
				raise bot.Exc.PermissionError(self.m.gt("Discarding check-in is not allowed."))
			return await self.back_out(ctx, member)

	async def replace_player(self, ctx, out_member):
		"""Swap out_member for the next available queued player.

		Returns the swapped-in member, or None when no queued player is
		available (the caller decides what to do with None). The new player
		must check in themselves unless they have an active /auto_ready.
		"""
		busy_ids = {p.id for m in bot.active_matches for p in m.players}
		candidate = pick_available(self.m.queue.queue, busy_ids)
		if candidate is None:
			return None

		self.m.players.remove(out_member)
		self.m.players.append(candidate)
		self.ready_players.discard(out_member)
		if candidate.id in bot.auto_ready.keys():
			self.ready_players.add(candidate)

		await self.m.qc.remove_members(candidate, ctx=ctx)
		await bot.remove_players(candidate, reason="pickup started")
		return candidate

	async def back_out(self, ctx, member):
		swapped = await self.replace_player(ctx, member)
		if swapped:
			await ctx.notice(self.m.gt(
				"{out} backed out and was replaced by {sub}. {sub}, please check in!"
			).format(out=member.mention, sub=swapped.mention))
			await self.refresh(ctx)
		else:
			await self.revert_single(ctx, member)

	async def revert_single(self, ctx, member):
		bot.waiting_reactions.pop(self.message.id, None)
		try:
			await self.message.delete()
		except DiscordException:
			pass
		await ctx.notice("\n".join((
			self.m.gt("{member} has aborted the check-in.").format(member=member.mention),
			self.m.gt("Reverting {queue} to the gathering stage...").format(queue=f"**{self.m.queue.name}**")
		)))
		bot.active_matches.remove(self.m)
		await self.m.queue.revert(ctx, [member], [m for m in self.m.players if m != member])

	async def abort_timeout(self, ctx):
		not_ready = [m for m in self.m.players if m not in self.ready_players]
		if self.message:
			bot.waiting_reactions.pop(self.message.id, None)
			try:
				await self.message.delete()
			except DiscordException:
				pass

		bot.active_matches.remove(self.m)

		await ctx.notice("\n".join((
			self.m.gt("{members} was not ready in time.").format(members=join_and([m.mention for m in not_ready])),
			self.m.gt("Reverting {queue} to the gathering stage...").format(queue=f"**{self.m.queue.name}**")
		)))

		await self.m.queue.revert(ctx, not_ready, list(self.ready_players))
