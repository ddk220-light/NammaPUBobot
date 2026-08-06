# -*- coding: utf-8 -*-
"""Slash-command handlers for the opt-in AoE2 quiz. Thin: scheduling/posting lives
in bot.quiz.jobs, persistence in bot.quiz.store, rendering in bot.quiz.embeds. All
bot.quiz imports are lazy (inside the handlers) so this module loads during the
`from . import commands` step without pulling nextcord-heavy quiz modules early."""

__all__ = ['quiz_leaderboard', 'quiz_disable']


async def quiz_leaderboard(ctx):
	from bot.quiz import embeds, schedule, scoring, store
	cfg = await store.get_config()
	channel_id = (cfg or {}).get("channel_id") or ctx.channel.id
	# Show the current CALENDAR week (the week the latest post fell in) so the
	# on-demand board matches the auto-posted "Week N" one rather than a rolling
	# 7-day window. Derived from the channel's own post counter, not from the
	# schedule file — half the questions were never in it.
	posted = await store.posted_seqs(channel_id)
	week = schedule.slot_for_seq(max(posted))[0] if posted else 1
	rows = await store.week_answers_by_week(channel_id, week)
	await ctx.reply(embed=embeds.leaderboard_embed(scoring.tally(rows), f"Week {week} (so far)"))


async def quiz_disable(ctx):
	"""Stop the daily quiz. ONE-WAY: nothing re-enables it.

	/quiz enable went with the rest of the quiz config commands, and no env var
	or web page sets enabled=1 -- bot/web.py has no quiz surface at all. Pulling
	this leaves the quiz off until that page exists or someone runs SQL against
	quiz_settings. That is the intended shape of an emergency stop, but it is
	worth knowing before you press it rather than after.
	"""
	ctx.check_perms(ctx.Perms.ADMIN)
	from bot.quiz import store
	await store.disable_all()
	await ctx.success(
		"Daily quiz disabled. Any open quiz will still resolve.\n"
		"**Nothing in the bot re-enables it** — that needs the web dashboard or a DB change.",
		title="Quiz disabled")

