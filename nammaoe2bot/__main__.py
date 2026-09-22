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
# DB connect, bootstrap) get reported instead of silently killing the
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

# Load the runtime layer.
# Layer 5: `locales` used to be in this import for its side effect
# (it listdir'd locales/compiled/ and built a gettext translation
# table at import time). With the Layer 5 stub, nammaoe2bot/runtime/locales.py does
# no I/O and needs no eager load — nammaoe2bot/pickup/channel.py imports it
# lazily on its own. Dropped from this line.
from nammaoe2bot.runtime import config, console, database, cfg_factory
from nammaoe2bot.runtime.client import dc

loop = asyncio.get_event_loop()
loop.run_until_complete(database.db.connect())

# Schema migrations MUST run before bootstrap(): every feature package
# auto-CREATEs its declared tables at import, and the adapter cannot rename —
# see nammaoe2bot/runtime/migrations.py for why this ordering is load-bearing.
from nammaoe2bot.runtime import migrations
loop.run_until_complete(migrations.run_all(database.db))

# The one Application for this process. It holds everything that used to be a
# module-level global in the old bot/__init__.py.
#
# It hangs off the Discord client rather than off a module: `dc` is the one
# process-level object every handler already imports, so `dc.app` gives the
# state one home without inventing a second global. Classes that need it
# (Match, QueueChannel, PickupQueue) take it in their constructor and keep it
# as self.app — they must not reach for dc.app inside a method. See
# nammaoe2bot/app.py.
from nammaoe2bot.app import Application
dc.app = Application(client=dc)

# Boot wiring. Every import inside bootstrap() exists for a side effect —
# ensure_table declarations, the job singletons the tick drives, and the
# registration of the 44 slash commands. It runs AFTER dc.app is built, so no
# handler can be registered against a world that does not exist yet, and after
# migrations because those packages CREATE the tables they declare.
from nammaoe2bot.bootstrap import bootstrap
bootstrap(dc.app)

from nammaoe2bot.state import save_state_if_changed

# WS_ENABLE controls the dashboard, not Railway readiness. A disabled dashboard
# still starts a tiny probe-only app so /ready can protect deployments without
# importing the onboarding, migration and statistics web surfaces.
if config.cfg.WS_ENABLE:
	from nammaoe2bot.web.server import start_web_server
else:
	from nammaoe2bot.web.probes import start_probe_server as start_web_server
web_runner = None

log = console.log
_fatal_exit = False
_shutdown_task = None


def _process_exit_code():
	"""The process status Railway's ON_FAILURE restart policy observes."""
	return 1 if _fatal_exit else 0

# ─── Task supervision ────────────────────────────────────────────────
# Any critical task that dies unexpectedly must bring down the whole
# process so Railway's ON_FAILURE restart policy kicks in. Previously a
# 1015 (Cloudflare rate limit) on Discord login would kill only the
# Discord task while web + think kept the container alive → zombie bot
# for hours. Never again.

def _task_done_callback(task):
	"""Done-callback: if a critical task crashed, stop the loop so the
	process exits non-zero after bounded cleanup and Railway restarts it.
	Cancelled tasks and normal completion are silent — the supervisor's
	only job is catching unhandled crashes. (init_web in particular is
	a start-and-return task: it launches the aiohttp runner and returns.
	Its 'completion' is expected and not worth logging.)"""
	global _fatal_exit
	if task.cancelled():
		return
	exc = task.exception()
	if exc is None:
		return
	# Uncaught exception — critical failure.
	_fatal_exit = True
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
	_request_shutdown()


def supervised_task(coro, name):
	"""Wrap a coroutine in a task with crash-supervision attached."""
	task = loop.create_task(coro, name=name)
	task.add_done_callback(_task_done_callback)
	return task


# ─── Signal handlers ─────────────────────────────────────────────────
# Gracefully exit on SIGINT (Ctrl+C locally) or SIGTERM (Railway deploys).
# Stop new commands and drain pending work before the bounded durable flush.
original_SIGINT_handler = signal.getsignal(signal.SIGINT)
original_SIGTERM_handler = signal.getsignal(signal.SIGTERM)


def ctrl_c(sig, frame):
	log.info(f"Received signal {sig}, shutting down gracefully...")
	_request_shutdown()
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

	_request_shutdown()


def _request_shutdown():
	global _shutdown_task
	dc.app.shutting_down = True
	dc.app.ready = False
	console.terminate()
	if _shutdown_task is None:
		_shutdown_task = loop.create_task(_shutdown(), name="shutdown")


async def _shutdown():
	"""Drain mutation tasks, flush once, then close the existing services."""
	try:
		if web_runner:
			# Stop accepting HTTP mutations before taking the task snapshot.
			for site in tuple(web_runner.sites):
				await asyncio.wait_for(site.stop(), timeout=2)
		current = asyncio.current_task()
		pending = [task for task in asyncio.all_tasks() if task is not current and not task.done()]
		for task in pending:
			task.cancel()
		if pending:
			_done, pending = await asyncio.wait(pending, timeout=2)
		if pending:
			log.error("Shutdown tasks did not drain; retaining the previous durable snapshot.")
		else:
			for hook in getattr(dc, "events", {}).get("on_exit", []):
				try:
					await asyncio.wait_for(hook(), timeout=1)
				except Exception as exc:
					log.error(f"Shutdown hook failed: {exc}")
			try:
				await asyncio.wait_for(save_state_if_changed(dc.app), timeout=8)
			except Exception as exc:
				log.error(f"Shutdown state flush failed: {exc}")

		async def close_services():
			await dc.close()
			if web_runner:
				await web_runner.cleanup()
			await database.db.close()

		try:
			await asyncio.wait_for(close_services(), timeout=5)
		except Exception as exc:
			log.error(f"Shutdown cleanup failed: {exc}")
	finally:
		log.close()
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
raise SystemExit(_process_exit_code())
