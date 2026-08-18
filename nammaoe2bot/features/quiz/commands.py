# -*- coding: utf-8 -*-
"""Slash-command handlers for the opt-in AoE2 quiz. Thin: scheduling/posting lives
in nammaoe2bot.features.quiz.jobs, persistence in nammaoe2bot.features.quiz.store, rendering in nammaoe2bot.features.quiz.embeds. All
nammaoe2bot.features.quiz imports are lazy (inside the handlers) so this module loads during the
`from . import commands` step without pulling nextcord-heavy quiz modules early."""

__all__ = ['quiz_leaderboard', 'quiz_disable']


async def quiz_leaderboard(ctx):
	from nammaoe2bot import community
	from nammaoe2bot.features.quiz import embeds, schedule, scoring, store
	community_id = await community.community_for_channel(ctx.channel.id)
	configs = await store.configs_for_community(community_id) if community_id is not None else []
	active = next((cfg for cfg in configs if cfg.get("enabled")), None)
	channel_id = (active or {}).get("channel_id") or ctx.channel.id
	# Show the current CALENDAR week (the week the latest post fell in) so the
	# on-demand board matches the auto-posted "Week N" one rather than a rolling
	# 7-day window. Derived from the channel's own post counter, not from the
	# schedule file — half the questions were never in it.
	posted = await store.posted_seqs(channel_id)
	week = schedule.slot_for_seq(max(posted))[0] if posted else 1
	rows = await store.week_answers_by_week(channel_id, week)
	await ctx.reply(embed=embeds.leaderboard_embed(scoring.tally(rows), f"Week {week} (so far)"))


async def quiz_disable(ctx):
	"""Emergency-stop the daily quiz for this community."""
	ctx.check_perms(ctx.Perms.ADMIN)
	from nammaoe2bot import community
	from nammaoe2bot.features.quiz import store
	community_id = await community.community_for_channel(ctx.channel.id)
	if community_id is None:
		return await ctx.reply("This channel is not enrolled in a bot community.")
	await store.disable_for_community(community_id)
	await ctx.success(
		"Daily quiz disabled for this community. Any open quiz will still resolve.\n"
		"A community administrator can re-enable it in the web dashboard.",
		title="Quiz disabled")
