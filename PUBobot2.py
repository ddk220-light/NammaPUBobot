#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import signal
import asyncio
import traceback
from asyncio import sleep as asleep

# Load bot core
from core import config, console, database, locales, cfg_factory
from core.client import dc

loop = asyncio.get_event_loop()
loop.run_until_complete(database.db.connect())

# Load bot
import bot

# Load web server
from bot.web import start_web_server
web_runner = None

log = console.log

# ─── Task supervision ────────────────────────────────────────────────
# Any critical task that dies unexpectedly must bring down the whole
# process so Railway's ON_FAILURE restart policy kicks in. Previously a
# 1015 (Cloudflare rate limit) on Discord login would kill only the
# Discord task while web + think kept the container alive → zombie bot
# for hours. Never again.

def _task_done_callback(task):
	"""Done-callback: if a critical task crashed, stop the loop so the
	process exits non-zero and Railway restarts the container.
	Cancelled tasks and normal completion are silent — the supervisor's
	only job is catching unhandled crashes. (init_web in particular is
	a start-and-return task: it launches the aiohttp runner and returns.
	Its 'completion' is expected and not worth logging.)"""
	if task.cancelled():
		return
	exc = task.exception()
	if exc is None:
		return
	# Uncaught exception — critical failure.
	tb_text = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
	log.error(f"CRITICAL: supervised task '{task.get_name()}' crashed:\n{tb_text}")
	# Best-effort state save before exit — we have periodic snapshots too
	# but an extra save here loses at most a few seconds of in-flight state.
	try:
		bot.save_state()
	except Exception as save_exc:
		log.error(f"Failed to save state during crash: {save_exc}")
	log.error("Stopping event loop — process will exit, Railway will restart the container.")
	try:
		loop.stop()
	except RuntimeError:
		pass


def supervised_task(coro, name):
	"""Wrap a coroutine in a task with crash-supervision attached."""
	task = loop.create_task(coro, name=name)
	task.add_done_callback(_task_done_callback)
	return task


# ─── Signal handlers ─────────────────────────────────────────────────
# Gracefully exit on SIGINT (Ctrl+C locally) or SIGTERM (Railway deploys).
# Without SIGTERM, `saved_state.json` is never written when Railway stops
# the container for a redeploy → in-flight matches are lost every deploy.
original_SIGINT_handler = signal.getsignal(signal.SIGINT)
original_SIGTERM_handler = signal.getsignal(signal.SIGTERM)


def ctrl_c(sig, frame):
	log.info(f"Received signal {sig}, shutting down gracefully...")
	bot.save_state()
	console.terminate()
	# Restore original handlers so a second signal kills immediately
	signal.signal(signal.SIGINT, original_SIGINT_handler)
	signal.signal(signal.SIGTERM, original_SIGTERM_handler)


signal.signal(signal.SIGINT, ctrl_c)
signal.signal(signal.SIGTERM, ctrl_c)


# Background processes loop
async def think():
	for task in dc.events['on_init']:
		await task()

	# Loop runs roughly every 1 second
	while console.alive:
		frame_time = time.time()
		for task in dc.events['on_think']:
			try:
				await task(frame_time)
			except Exception as e:
				log.error('Error running background task from {}: {}\n{}'.format(task.__module__, str(e), traceback.format_exc()))
		await asleep(1)

	# Exit signal received
	for task in dc.events['on_exit']:
		try:
			await task()
		except Exception as e:
			log.error('Error running exit task from {}: {}\n{}'.format(task.__module__, str(e), traceback.format_exc()))

	log.info("Waiting for connection to close...")
	await dc.close()

	log.info("Closing db.")
	await database.db.close()
	if web_runner:
		log.info("Closing web server.")
		await web_runner.cleanup()
	log.info("Closing log.")
	log.close()
	print("Exit now.")
	loop.stop()

# Start web server
async def init_web():
	global web_runner
	try:
		web_runner = await start_web_server()
	except Exception as e:
		log.error(f"Failed to start web server: {e}")

# Login to discord
loop = asyncio.get_event_loop()
supervised_task(init_web(), name="web_server")
supervised_task(think(), name="think_loop")
supervised_task(dc.start(config.cfg.DC_BOT_TOKEN), name="discord_client")

log.info("Connecting to discord...")
loop.run_forever()
