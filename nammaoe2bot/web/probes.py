# -*- coding: utf-8 -*-
"""Small HTTP probes shared by the dashboard and probe-only deployments.

Railway's readiness check must prove that Discord and MySQL are available
before it routes a new deployment.  Continuous uptime checks must not perform
that MySQL query: doing so every few minutes would keep a serverless database
awake forever.  Keeping these handlers outside :mod:`nammaoe2bot.web.server`
also lets ``WS_ENABLE=False`` expose probes without importing the dashboard,
onboarding, migration and statistics surfaces.
"""
import asyncio
import os
import time

from aiohttp import web

from nammaoe2bot.runtime.client import dc
from nammaoe2bot.runtime.database import db


_boot_time = time.time()


def _runtime_status():
	"""Return the DB-free process/Discord health snapshot."""
	# These imports stay lazy so the minimal probe server does not pull the
	# Discord event graph in merely by being imported during bootstrap.
	from nammaoe2bot.discord import events as discord_events
	from nammaoe2bot.features import elo_sync

	now = time.time()
	discord_ok = bool(dc.app.ready) and dc.is_ready()
	last_tick = getattr(discord_events, "last_tick_at", 0.0) or 0.0
	last_tick_age = int(now - last_tick) if last_tick > 0 else None
	last_elo_sync = getattr(elo_sync, "last_elo_sync_at", 0.0) or 0.0
	return discord_ok, {
		"discord_connected": discord_ok,
		"db_connected": None,
		"database_checked": False,
		"bot_ready": bool(dc.app.ready),
		"active_matches": len(dc.app.active_matches),
		"last_tick_age_seconds": last_tick_age,
		"last_elo_sync_at": int(last_elo_sync) if last_elo_sync > 0 else 0,
		"uptime_seconds": int(now - _boot_time),
	}


async def handle_live(request):
	"""DB-free continuous liveness endpoint.

	``/health`` is a compatibility alias for this handler.  External uptime
	monitors may safely poll either route without waking MySQL.
	"""
	discord_ok, payload = _runtime_status()
	payload["status"] = "ok" if discord_ok else "unhealthy"
	return web.json_response(payload, status=200 if discord_ok else 503)


async def handle_health(request):
	"""Backward-compatible, DB-free alias for :func:`handle_live`."""
	return await handle_live(request)


async def handle_ready(request):
	"""Deployment readiness: runtime health plus one bounded MySQL query."""
	discord_ok, payload = _runtime_status()
	db_ok = False
	try:
		await asyncio.wait_for(db.fetchone("SELECT 1 AS ok"), timeout=2.0)
		db_ok = True
	except Exception:
		db_ok = False
	healthy = discord_ok and db_ok
	payload.update({
		"status": "ok" if healthy else "unhealthy",
		"db_connected": db_ok,
		"database_checked": True,
	})
	return web.json_response(payload, status=200 if healthy else 503)


def create_probe_app():
	"""Create the probe-only app used when the dashboard is disabled."""
	app = web.Application()
	app.router.add_get("/live", handle_live)
	app.router.add_get("/health", handle_health)
	app.router.add_get("/ready", handle_ready)
	return app


async def start_probe_server(port=None):
	"""Start a minimal HTTP server that owns only the three probe routes."""
	if port is None:
		port = int(os.environ.get("PORT", 8080))
	app = create_probe_app()
	runner = web.AppRunner(app)
	await runner.setup()
	site = web.TCPSite(runner, "0.0.0.0", port)
	await site.start()
	print(f"Probe server started on port {port}")
	return runner
