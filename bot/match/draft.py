# -*- coding: utf-8 -*-
import bot
from core.utils import find
from nextcord import DiscordException

from .subbing import pick_available


class Draft:
	"""Substitutions and roster edits for a live match.

	Named for the captain draft it used to host. That stage is gone: "draft" is
	no longer a selectable pick_teams value, so DRAFT is never appended to a
	match's states and cap_me/cap_for/pick had no reachable caller. What is left
	is substitution -- sub_me, sub_for, sub_auto -- plus put. The DRAFT constant
	and the state checks below stay as defensive guards; they simply never match.
	"""

	def __init__(self, match, captains_role_id):
		self.m = match
		self.captains_role_id = captains_role_id
		self.sub_queue = []

	async def start(self, ctx):
		await self.refresh(ctx)

	async def print(self, ctx):
		try:
			await ctx.notice(embed=self.m.embeds.draft())
		except DiscordException:
			pass

	async def refresh(self, ctx):
		if self.m.state != self.m.DRAFT:  # noqa: SIM114
			await self.print(ctx)
		elif len(self.m.teams[2]) and any((len(t) < self.m.cfg['team_size'] for t in self.m.teams)):
			await self.print(ctx)
		else:
			await self.m.next_state(ctx)

	async def put(self, ctx, player, team_name):
		if (team := find(lambda t: t.name.lower() == team_name.lower(), self.m.teams)) is None:
			raise bot.Exc.SyntaxError(self.m.gt("Specified team name not found."))
		if self.m.state not in [self.m.DRAFT, self.m.WAITING_REPORT]:
			raise bot.Exc.MatchStateError(self.m.gt("The match must be on the draft or waiting report stage."))

		if (old_team := find(lambda t: player in t, self.m.teams)) is not None:
			old_team.remove(player)
		else:
			self.m.players.append(player)
			self.m.ratings = {
				p['user_id']: p['rating'] for p in await self.m.qc.rating.get_players((p.id for p in self.m.players))
			}

		team.append(player)
		await self.m.qc.remove_members(player, ctx=ctx)
		await self.refresh(ctx)

	async def sub_me(self, ctx, author):
		if self.m.state not in [self.m.DRAFT, self.m.WAITING_REPORT]:
			raise bot.Exc.MatchStateError(self.m.gt("The match must be on the draft or waiting report stage."))

		if author in self.sub_queue:
			self.sub_queue.remove(author)
			await ctx.success(self.m.gt("You have stopped looking for a substitute."))
		else:
			self.sub_queue.append(author)
			await ctx.success(self.m.gt("You are now looking for a substitute."))

	async def sub_for(self, ctx, player1, player2, force=False):
		if self.m.state not in [self.m.CHECK_IN, self.m.DRAFT, self.m.WAITING_REPORT]:
			raise bot.Exc.MatchStateError(self.m.gt("The match must be on the check-in, draft or waiting report stage."))
		elif not force and player1 not in self.sub_queue:
			raise bot.Exc.PermissionError(self.m.gt("Specified player is not looking for a substitute."))

		team = find(lambda t: player1 in t, self.m.teams)
		team[team.index(player1)] = player2
		self.m.players.remove(player1)
		self.m.players.append(player2)
		if player1 in self.sub_queue:
			self.sub_queue.remove(player1)
		self.m.ratings = {
			p['user_id']: p['rating'] for p in await self.m.qc.rating.get_players((p.id for p in self.m.players))
		}
		await self.m.qc.remove_members(player2, ctx=ctx)
		await bot.remove_players(player2, reason="pickup started")

		# A ROSTER CHANGE UNDER A LIVE BOOK IS A CHANGE TO THE SIDES PEOPLE
		# STAKED ON — the same reason /subauto refunds and re-opens, and no
		# weaker for swapping one player instead of rebalancing both teams.
		# The book is open for ten minutes and this command is permitted in
		# CHECK_IN, DRAFT and WAITING_REPORT, so it is ordinary operation, not
		# a race. Without this, a spectator who staked on Alpha can be
		# substituted onto Bravo and now profits by LOSING — precisely the
		# position the own-team rule exists to make impossible — and cannot
		# even correct it, because the composite-PK side lock refuses the
		# other side. `is_player` would stay stale at 0 as well, so the
		# post-match report would not name them either.
		# Best-effort, like every other prediction call site: restart_for_match
		# swallows its own failures so a book can never break a match.
		if self.m.ranked:
			from bot.predictions import restart_for_match
			await restart_for_match(self.m)

		if self.m.state == self.m.CHECK_IN:
			await self.m.check_in.refresh()
		elif self.m.state == self.m.WAITING_REPORT:
			await ctx.notice(embed=self.m.embeds.final_message())
		else:
			await self.print(ctx)

	async def sub_auto(self, ctx, out_member):
		if self.m.state not in [self.m.DRAFT, self.m.WAITING_REPORT]:
			raise bot.Exc.MatchStateError(self.m.gt("The match must be on the draft or waiting report stage."))

		# Grab the next queued player who isn't already committed to another
		# active match. busy_ids spans every active match, so it also excludes
		# this match's own players (the caller included).
		busy_ids = {p.id for m in bot.active_matches for p in m.players}
		candidate = pick_available(self.m.queue.queue, busy_ids)
		if candidate is None:
			raise bot.Exc.NotFoundError(self.m.gt("There are no available players in the queue to substitute in."))

		# Swap the caller out for the candidate and recompute ratings for the
		# new roster so the rebalance below sees correct ELOs.
		self.m.players.remove(out_member)
		self.m.players.append(candidate)
		if out_member in self.sub_queue:
			self.sub_queue.remove(out_member)
		self.m.ratings = {
			p['user_id']: p['rating'] for p in await self.m.qc.rating.get_players((p.id for p in self.m.players))
		}

		# Pull the candidate out of the queue and expire timers, like /subfor.
		await self.m.qc.remove_members(candidate, ctx=ctx)
		await bot.remove_players(candidate, reason="pickup started")

		# Full re-matchmaking: re-split everyone into the two most ELO-balanced
		# teams. Reuses the proven matchmaking path; teams[0][0]/teams[1][0]
		# become each team's reporting captain (sorted by rating).
		self.m.init_teams("matchmaking")

		await ctx.notice(self.m.gt("{old} was substituted by {new}. Teams have been rebalanced.").format(
			old=out_member.mention, new=candidate.mention
		))

		# Both teams just changed, so the sides the audience voted on no longer
		# exist — discard those ballots and re-open voting on the new teams.
		if self.m.ranked:
			from bot.predictions import restart_for_match
			await restart_for_match(self.m)

		if self.m.state == self.m.WAITING_REPORT:
			await ctx.notice(embed=self.m.embeds.final_message())
		else:
			await self.refresh(ctx)
