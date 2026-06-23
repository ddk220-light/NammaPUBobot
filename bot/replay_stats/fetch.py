# -*- coding: utf-8 -*-
"""Async wrappers over utils/replay_quiz/download.py. The download code is sync (requests);
we run it in a thread so the bot event loop is never blocked. Returns a cached .aoe2record
path or a status string."""
import asyncio
import sys
import os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _download_module():
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from utils.replay_quiz import download   # lazy: pulls requests/mgz only on first use
    return download


async def fetch_replay(aoe2_match_id):
    """Resolve a participant profile_id and download the replay. Returns (path|None, status).
    status: 'ok'/'cached' on success; 'no_profile', '404'/'http_*'/'429_exhausted'/'neterr:*'
    on failure."""
    dl = await asyncio.to_thread(_download_module)
    profile_ids = await asyncio.to_thread(dl.resolve_profile_ids, aoe2_match_id)
    if not profile_ids:
        return None, "no_profile"
    last_status = "no_profile"
    for pid in profile_ids:
        path, status = await asyncio.to_thread(dl.download_replay, aoe2_match_id, pid)
        last_status = status
        if path:
            return path, status
        if status.startswith("http_4") and status != "http_429":
            continue   # try the next participant on a 404 for this one
    return None, last_status


async def read_save_version(path):
    dl = await asyncio.to_thread(_download_module)
    return await asyncio.to_thread(dl.read_save_version, path)
