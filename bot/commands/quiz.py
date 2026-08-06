# -*- coding: utf-8 -*-
"""Slash-command handlers for the opt-in AoE2 quiz. Thin: scheduling/posting lives
in bot.quiz.jobs, persistence in bot.quiz.store, rendering in bot.quiz.embeds. All
bot.quiz imports are lazy (inside the handlers) so this module loads during the
`from . import commands` step without pulling nextcord-heavy quiz modules early."""
import time

__all__ = ["quiz_leaderboard", "quiz_enable", "quiz_disable", "quiz_config", "quiz_post_now",
		   "quiz_status", "quiz_skip", "quiz_reveal_now"]

_INT_FIELDS = ("quiz_hour", "open_window", "leaderboard_dow", "leaderboard_hour", "test_interval")


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


async def quiz_enable(ctx, channel, hour=9):
	ctx.check_perms(ctx.Perms.ADMIN)
	from bot.quiz import store
	if not (0 <= int(hour) <= 23):
		return await ctx.error("Hour must be 0-23 (UTC).")
	await store.disable_all()
	await store.upsert_config(
		channel.id, enabled=1, quiz_hour=int(hour), open_window=86400,
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


async def quiz_status(ctx):
	from bot.quiz import store
	from bot.quiz.jobs import jobs as quiz_jobs
	cfg = await store.get_config()
	channel_id = (cfg or {}).get("channel_id") or ctx.channel.id
	seq, week, day, entry = await quiz_jobs.next_up(channel_id)
	nxt = (f"#{seq} (Week {week} Day {day}, {entry['source']}: {entry['category']})"
		   if entry else f"#{seq} (Week {week} Day {day}) — no question available")
	enabled = bool(cfg and cfg.get("enabled"))
	await ctx.reply(
		f"Quiz **{'ON' if enabled else 'OFF'}** · next: {nxt} · "
		f"last leaderboard: week {(cfg or {}).get('last_leaderboard_week') or 0}"
		+ (f" · test cadence: every {cfg.get('test_interval')}s" if cfg and cfg.get("test_interval") else ""))


async def quiz_skip(ctx):
	ctx.check_perms(ctx.Perms.ADMIN)
	from bot.quiz import store
	from bot.quiz.jobs import jobs as quiz_jobs
	cfg = await store.get_config()
	channel_id = (cfg or {}).get("channel_id") or ctx.channel.id
	seq, _week, _day, entry = await quiz_jobs.next_up(channel_id)
	if not entry:
		return await ctx.error("Nothing to skip — no question available for the next slot.")
	now = int(time.time())
	pid = await store.create_post(channel_id, entry, now, now)
	await store.close_post(pid)
	# Only a GAME question can be blocklisted: a player question is generated
	# from live boards at post time and has no bank entry to exclude. Skipping
	# one burns the slot; the next player day builds a fresh question anyway.
	tail = ("Add its id to data/quiz_blocklist.json and regenerate the schedule to drop it "
			"permanently." if entry.get("source") == "game" else
			"It was generated live — the next player day builds a new one.")
	await ctx.success(f"Skipped #{seq} ({entry['id']}). {tail}", title="Quiz")


async def quiz_reveal_now(ctx):
	ctx.check_perms(ctx.Perms.ADMIN)
	from bot.quiz import store
	from bot.quiz.jobs import jobs as quiz_jobs
	cfg = await store.get_config()
	channel_id = (cfg or {}).get("channel_id") or ctx.channel.id
	await quiz_jobs.reveal_now(channel_id)
	await ctx.success("Revealed the previous question (if any was open).", title="Quiz")
