# -*- coding: utf-8 -*-
"""Async wrapper over chart.py, running matplotlib in a separate process.

The APM chart renders on the think() tick path during ingest. matplotlib is largely
pure Python, so asyncio.to_thread would still hold the GIL for most of a render --
this mirrors parse.py, which runs replay extraction in a single-worker process pool
for the same reason, including tearing the pool down on timeout (a running
ProcessPoolExecutor future cannot be cancelled).
"""
import asyncio
from concurrent.futures.process import BrokenProcessPool

from .procpool import WorkerPool

MIN_MINUTES = 3   # below this a line is noise, not a chart

_POOL = WorkerPool("render")


def _get_pool():
    return _POOL.get()


def _reset_pool():
    """Drop the worker pool so the next render builds a fresh one -- recovers from a
    hung worker or a dead one, exactly as parse.py does."""
    _POOL.reset()


def should_render(series):
    """True when there is enough of a match to be worth charting. Pure."""
    if not series:
        return False
    return max(len(s["values"]) for s in series) >= MIN_MINUTES


def _render(series, teams):
    """Runs in the worker process. Imports matplotlib lazily there."""
    from .chart import render_apm_curve
    return render_apm_curve(series, teams)


def _log():
    """Lazy so importing this module stays CI-safe (no core.* at module scope)."""
    from core.console import log
    return log


async def render_apm(series, teams, timeout=30):
    """Render the APM chart off the event loop. Returns a BytesIO, or None when
    there is too little data, the render times out, or it raises -- the caller posts
    without an image rather than losing the whole card.

    Every genuine failure is logged: the chart showing up is this feature's only
    production signal, so a permanently broken renderer must not be indistinguishable
    from a match that predates the feature. The too-little-data return is normal and
    stays silent."""
    try:
        if not should_render(series):
            return None
        loop = asyncio.get_running_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(_get_pool(), _render, series, teams), timeout)
    except TimeoutError:
        _reset_pool()
        _log().error(f"APM chart render timed out after {timeout}s — worker pool reset")
        return None
    except BrokenProcessPool:
        # Worker died (OOM kill, native crash). Must precede `except Exception` —
        # BrokenProcessPool subclasses RuntimeError — or the pool is never replaced and
        # every later render fails against the same dead executor.
        _reset_pool()
        _log().error("APM chart render worker died (BrokenProcessPool) — worker pool reset")
        return None
    except Exception as e:
        _log().error(f"APM chart render failed: {e!r}")
        return None
