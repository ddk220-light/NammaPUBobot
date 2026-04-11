#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import signal
import asyncio
import traceback
from asyncio import sleep as asleep

# Sentry — opt-in via SENTRY_DSN env var. Initialized BEFORE any other bot
# imports so that exceptions raised during module import (config parse,
# DB connect, bot.__init__) get reported instead of silently killing the
# container. If SENTRY_DSN is unset, sentry_sdk.init() is skipped entirely
# and all downstream sentry_sdk.capture_exception() calls become no-ops
# (the SDK's documented behavior for an uninitialized client).
_sentry_dsn = os.environ.get('SENTRY_DSN', '').strip()
if _sentry_dsn:
	import sentry_sdk
	sentry_sdk.init(
		dsn=_sentry_dsn,
		# Capture the full stack for every exception. Default is on but
		# stated explicitly so future readers don't wonder.
		attach_stacktrace=True,
		# Don't sample transactions — we're using Sentry as an error
		# reporter, not an APM. Setting this to 0.0 avoids pulling in
		# performance-monitoring instrumentation we don't need.
		traces_sample_rate=0.0,
		# Environment tag from Railway env var if set, else "local".
		environment=os.environ.get('RAILWAY_ENVIRONMENT_NAME', 'local'),
		# Release tag from Railway's commit SHA if set. Shows up in the
		# Sentry UI so we can correlate errors with deploys.
		release=os.environ.get('RAILWAY_GIT_COMMIT_SHA', None),
	)
else:
	sentry_sdk = None

# Load bot core
# Layer 5: `locales` used to be in this import for its side effect
# (it listdir'd locales/compiled/ and built a gettext translation
# table at import time). With the Layer 5 stub, core/locales.py does
# no I/O and needs no eager load — bot/queue_channel.py imports it
# lazily on its own. Dropped from this line.
from core import config, console, database, cfg_factory
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
	# Report to Sentry if configured. No-op when SENTRY_DSN is unset.
	# Wrapped so a Sentry transport failure can't block the exit path.
	if sentry_sdk is not None:
		try:
			with sentry_sdk.push_scope() as scope:
				scope.set_tag("task_name", task.get_name())
				scope.set_tag("critical", "true")
				sentry_sdk.capture_exception(exc)
		except Exception as sentry_exc:
			log.error(f"Sentry capture failed during task crash: {sentry_exc}")
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
