"""Availability hardening for the PUBLIC, unauthenticated stats endpoints
(``/api/match-stats``, ``/api/leaderboard``, ``/api/player-stats``). Each of those runs several DB
queries on the bot's *shared* asyncio loop + aiomysql pool, so an anonymous request flood could
starve the live Discord bot. Two tiny, dependency-free, in-process guards:

  * ``TTLCache`` — the responses are user-agnostic (keyed only by path + query string), so cache the
    serialized JSON body for a few seconds; concurrent/repeated identical requests then collapse to
    a single DB round-trip per window.
  * ``RateLimiter`` — a fixed-window per-client-IP cap that returns HTTP 429 on abuse.

Both are plain data structures driven by an injected ``now`` (monotonic seconds), so they unit-test
deterministically with no event loop and no aiohttp. ``aiohttp`` is imported lazily inside
``make_guard_middleware`` only, because the unit-test harness (tests/conftest.py) stubs ``aiohttp``
out — keeping the import lazy lets this module load there for the pure-logic tests."""
import time


class TTLCache:
	"""Tiny time-to-live cache. ``get``/``set`` take an explicit ``now`` (monotonic seconds). On
	overflow past ``max_entries`` a cheap sweep drops already-expired keys to bound memory."""

	def __init__(self, ttl, max_entries=2048):
		self.ttl = ttl
		self.max_entries = max_entries
		self._d = {}

	def get(self, key, now):
		ent = self._d.get(key)
		if ent is None:
			return None
		expires, value = ent
		if now >= expires:
			self._d.pop(key, None)
			return None
		return value

	def set(self, key, value, now):
		if len(self._d) >= self.max_entries and key not in self._d:
			self._gc(now)
		self._d[key] = (now + self.ttl, value)

	def _gc(self, now):
		for k in [k for k, (expires, _) in self._d.items() if now >= expires]:
			self._d.pop(k, None)


class RateLimiter:
	"""Fixed-window limiter: at most ``limit`` hits per ``window`` seconds per key. ``check`` records
	the hit and returns ``True`` if it is allowed. A sweep past ``max_keys`` drops stale windows."""

	def __init__(self, limit, window, max_keys=8192):
		self.limit = limit
		self.window = window
		self.max_keys = max_keys
		self._hits = {}

	def check(self, key, now):
		start, count = self._hits.get(key, (now, 0))
		if now - start >= self.window:
			start, count = now, 0
		count += 1
		self._hits[key] = (start, count)
		if len(self._hits) > self.max_keys:
			self._gc(now)
		return count <= self.limit

	def _gc(self, now):
		for k in [k for k, (start, _) in self._hits.items() if now - start >= self.window]:
			self._hits.pop(k, None)


def client_ip(request):
	"""Best-effort client IP. Behind Railway's proxy the real client is the first hop of
	``X-Forwarded-For``; fall back to the socket peer."""
	xff = request.headers.get('X-Forwarded-For')
	if xff:
		return xff.split(',')[0].strip()
	return request.remote or 'unknown'


def make_guard_middleware(paths, cache, limiter, clock=time.monotonic):
	"""Build an aiohttp middleware that rate-limits then response-caches the given ``paths`` (an
	iterable of exact request paths). Unguarded paths pass straight through untouched. ``aiohttp`` is
	imported here (not at module top) so the module stays importable under the aiohttp-stubbed test
	harness."""
	from aiohttp import web

	guarded = frozenset(paths)

	@web.middleware
	async def guard(request, handler):
		if request.path not in guarded:
			return await handler(request)
		now = clock()
		if not limiter.check(client_ip(request), now):
			return web.json_response(
				{"error": "rate limited"}, status=429,
				headers={"Retry-After": str(int(limiter.window))})
		key = request.path + "?" + request.query_string
		hit = cache.get(key, now)
		if hit is not None:
			body, content_type = hit
			return web.Response(body=body, content_type=content_type, headers={"X-Cache": "HIT"})
		resp = await handler(request)
		if resp.status == 200 and getattr(resp, "body", None) is not None:
			cache.set(key, (resp.body, resp.content_type), now)
			resp.headers["X-Cache"] = "MISS"
		return resp

	return guard
