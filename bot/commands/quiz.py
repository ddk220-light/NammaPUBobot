# -*- coding: utf-8 -*-
"""Slash-command handlers for the opt-in AoE2 quiz. Thin: scheduling/posting lives
in bot.quiz.jobs, persistence in bot.quiz.store, rendering in bot.quiz.embeds. All
bot.quiz imports are lazy (inside the handlers) so this module loads during the
`from . import commands` step without pulling nextcord-heavy quiz modules early."""
import time

__all__ = ["quiz_leaderboard", "quiz_enable", "quiz_disable", "quiz_config", "quiz_post_now"]

_INT_FIELDS = ("quiz_hour", "answer_window", "open_window", "leaderboard_dow", "leaderboard_hour")


async def quiz_leaderboard(ctx):
	from bot.quiz import embeds, scoring, store
	cfg = await store.get_config()
	channel_id = (cfg or {}).get("channel_id") or ctx.channel.id
	now = int(time.time())
	rows = await store.week_answers(channel_id, now - 7 * 86400, now)
	await ctx.reply(embed=embeds.leaderboard_embed(scoring.tally(rows), "this week"))


async def quiz_enable(ctx, channel, hour=9):
	ctx.check_perms(ctx.Perms.ADMIN)
	from bot.quiz import store
	if not (0 <= int(hour) <= 23):
		return await ctx.error("Hour must be 0-23 (UTC).")
	await store.disable_all()
	await store.upsert_config(
		channel.id, enabled=1, quiz_hour=int(hour), answer_window=180, open_window=86400,
		leaderboard_dow=7, leaderboard_hour=18, last_post_ymd="", last_leaderboard_ymd="")
	await ctx.success(
		f"Daily quiz enabled in {channel.mention} at {int(hour):02d}:00 UTC. "
		"Weekly leaderboard posts Sundays 18:00 UTC. Times are UTC.", title="Quiz enabled")


async def quiz_disable(ctx):
	ctx.check_perms(ctx.Perms.ADMIN)
	from bot.quiz import store
	await store.disable_all()
	await ctx.success("Daily quiz disabled. Any open quiz will still resolve.", title="Quiz disabled")


async def quiz_config(ctx, field, value):
	ctx.check_perms(ctx.Perms.ADMIN)
	from bot.quiz import store
	cfg = await store.get_config()
	if not cfg:
		return await ctx.error("No quiz channel is enabled — run /quiz enable first.")
	field = field.strip()
	if field in _INT_FIELDS:
		try:
			await store.upsert_config(cfg["channel_id"], **{field: int(value)})
		except ValueError:
			return await ctx.error(f"{field} must be an integer.")
	elif field == "min_difficulty":
		await store.upsert_config(cfg["channel_id"], min_difficulty=value.strip())
	else:
		allowed = ", ".join((*_INT_FIELDS, "min_difficulty"))
		return await ctx.error(f"Unknown field. One of: {allowed}.")
	await ctx.success(f"Set {field} = {value}.", title="Quiz config")


async def quiz_post_now(ctx):
	ctx.check_perms(ctx.Perms.ADMIN)
	from bot.quiz import store
	from bot.quiz.jobs import jobs as quiz_jobs
	cfg = await store.get_config()
	channel_id = (cfg or {}).get("channel_id") or ctx.channel.id
	post_id = await quiz_jobs.force_post(channel_id)
	if post_id:
		await ctx.success(f"Posted quiz #{post_id}.", title="Quiz")
	else:
		await ctx.error("Could not post — the question pool may be exhausted.")
