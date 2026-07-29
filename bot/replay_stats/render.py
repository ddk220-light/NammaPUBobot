# -*- coding: utf-8 -*-
"""Async wrapper over chart.py, running matplotlib in a separate process.

The APM chart renders on the think() tick path during ingest. matplotlib is largely
pure Python, so asyncio.to_thread would still hold the GIL for most of a render --
this mirrors parse.py, which runs replay extraction in a single-worker process pool
for the same reason, including tearing the pool down on timeout (a running
ProcessPoolExecutor future cannot be cancelled).
"""
import asyncio
from concurrent.futures import ProcessPoolExecutor

MIN_MINUTES = 3   # below this a line is noise, not a chart

_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        _pool = ProcessPoolExecutor(max_workers=1)
    return _pool


def _reset_pool():
    """Drop the worker pool so the next render builds a fresh one -- recovers from a
    hung worker, exactly as parse.py:27 does."""
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None


def should_render(series):
    """True when there is enough of a match to be worth charting. Pure."""
    if not series:
        return False
    return max(len(s["values"]) for s in series) >= MIN_MINUTES


def _render(series, teams):
    """Runs in the worker process. Imports matplotlib lazily there."""
    from .chart import render_apm_curve
    return render_apm_curve(series, teams)


async def render_apm(series, teams, timeout=30):
    """Render the APM chart off the event loop. Returns a BytesIO, or None when
    there is too little data, the render times out, or it raises -- the caller posts
    without an image rather than losing the whole card."""
    try:
        if not should_render(series):
            return None
        loop = asyncio.get_running_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(_get_pool(), _render, series, teams), timeout)
    except TimeoutError:
        _reset_pool()
        return None
    except Exception:
        return None
