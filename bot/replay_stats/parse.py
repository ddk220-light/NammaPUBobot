# -*- coding: utf-8 -*-
"""Save-version gate + CPU-bound extraction in a separate process so the bot event loop is
never blocked. extract_match takes a path and returns plain dicts, so it pickles cleanly
across the process boundary."""
import asyncio
import os
import sys
from concurrent.futures import ProcessPoolExecutor

from . import policy
from .fetch import read_save_version

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        _pool = ProcessPoolExecutor(max_workers=1)
    return _pool


def _extract(path, resolved, date_map):
    """Runs in the worker process. Imports lazily there."""
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from utils.replay_quiz.extract import extract_match
    return extract_match(path, resolved, date_map)


async def parse_replay(path, resolved, date_map, timeout=120):
    """Gate on save_version, then extract in a subprocess. Returns
    (result|None, status, save_version). status: 'ok' | 'pending_parser_update' | 'parse_failed'."""
    try:
        sv = await read_save_version(path)
    except Exception:
        sv = None
    if not policy.save_version_supported(sv):
        return None, "pending_parser_update", sv
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(_get_pool(), _extract, path, resolved, date_map), timeout)
        return result, "ok", sv
    except Exception:
        return None, "parse_failed", sv
