"""Unit tests for the pure logic in bot/web_guard.py — the TTL cache, the fixed-window per-IP rate
limiter, and the client-IP extraction. These need no event loop and no aiohttp (the test harness
stubs aiohttp out), so the middleware glue itself is verified separately by an out-of-pytest smoke
test. Clock values are injected, so every assertion is deterministic."""
import types

from bot.web_guard import RateLimiter, TTLCache, client_ip


def test_ttl_cache_hit_then_expires():
    c = TTLCache(ttl=10)
    assert c.get("k", now=100) is None           # empty
    c.set("k", "v", now=100)
    assert c.get("k", now=109) == "v"            # inside the window
    assert c.get("k", now=110) is None           # now >= 100+10 -> expired & evicted


def test_ttl_cache_overwrite_refreshes_expiry():
    c = TTLCache(ttl=10)
    c.set("k", "v1", now=0)
    c.set("k", "v2", now=5)                       # refresh -> expires at 15, not 10
    assert c.get("k", now=12) == "v2"


def test_ttl_cache_gc_bounds_size():
    c = TTLCache(ttl=1, max_entries=2)
    c.set("a", 1, now=0)
    c.set("b", 2, now=0)
    c.set("c", 3, now=5)                          # at capacity + new key -> gc drops expired a, b
    assert c.get("a", now=5) is None
    assert c.get("c", now=5) == 3


def test_rate_limiter_fixed_window():
    r = RateLimiter(limit=2, window=10)
    assert r.check("ip", now=0) is True          # 1
    assert r.check("ip", now=1) is True          # 2
    assert r.check("ip", now=2) is False         # 3 > limit
    assert r.check("ip", now=10) is True         # now - start >= window -> fresh window


def test_rate_limiter_is_per_key():
    r = RateLimiter(limit=1, window=10)
    assert r.check("a", now=0) is True
    assert r.check("b", now=0) is True           # separate key, own budget
    assert r.check("a", now=0) is False


def test_client_ip_prefers_forwarded_for():
    req = types.SimpleNamespace(headers={"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}, remote="9.9.9.9")
    assert client_ip(req) == "1.2.3.4"
    assert client_ip(types.SimpleNamespace(headers={}, remote="9.9.9.9")) == "9.9.9.9"
    assert client_ip(types.SimpleNamespace(headers={}, remote=None)) == "unknown"
