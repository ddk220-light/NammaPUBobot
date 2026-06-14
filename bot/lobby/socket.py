# -*- coding: utf-8 -*-
"""Live WebSocket client for the aoe2companion lobby feed.

Connects to ``wss://socket.aoe2companion.com/listen?handler=lobbies`` (optionally
filtered to one match id) and async-generates decoded event frames. Each TEXT
frame is a JSON array of ``{"type","data"}`` events (Phase 0); single-event
frames are wrapped in a list and ``pong`` keepalives are dropped. Reconnects with
capped exponential backoff and tears down cleanly on cancellation.

``aiohttp`` is imported lazily so the rest of the lobby package — and its unit
tests — never need it. This is an unofficial/undocumented endpoint, so it is
treated as strictly best-effort: every network error is logged and retried, never
raised to callers. The watcher that consumes this is itself isolated, so a socket
outage can never reach the core match flow.
"""
import asyncio
import json

from core.console import log

_BASE = "wss://socket.aoe2companion.com/listen?handler=lobbies"
_UA = {"User-Agent": "NammaPUBobot/1.0"}
_MAX_BACKOFF = 30


def _decode(data):
	"""Decode one TEXT frame to a list of events, or None to skip it."""
	try:
		frame = json.loads(data)
	except ValueError:
		return None
	if isinstance(frame, list):
		return frame
	if isinstance(frame, dict) and frame.get("type") != "pong":
		return [frame]
	return None


async def iter_frames(match_id=None):
	"""Yield a ``list[dict]`` of events per socket frame until the consuming task
	is cancelled. Never raises for network errors — it logs and reconnects."""
	import aiohttp  # lazy: keep the package import-light + unit-test-friendly

	url = _BASE + (f"&match_ids={match_id}" if match_id else "")
	backoff = 1
	while True:
		try:
			async with aiohttp.ClientSession(headers=_UA) as session:
				async with session.ws_connect(url, heartbeat=30) as ws:
					backoff = 1
					async for msg in ws:
						if msg.type == aiohttp.WSMsgType.TEXT:
							events = _decode(msg.data)
							if events:
								yield events
						elif msg.type in (
							aiohttp.WSMsgType.CLOSED,
							aiohttp.WSMsgType.CLOSING,
							aiohttp.WSMsgType.ERROR,
						):
							break
		except asyncio.CancelledError:
			raise
		except Exception as e:  # ClientError, TimeoutError, OSError, ...
			log.warning(f"Lobby socket error (will retry): {e}")
		await asyncio.sleep(min(backoff, _MAX_BACKOFF))
		backoff = min(backoff * 2, _MAX_BACKOFF)
