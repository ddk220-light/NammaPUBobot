# -*- coding: utf-8 -*-
"""Where a downloaded replay lives on this machine, and whether it is already
there.

Kept to the STANDARD LIBRARY on purpose -- no `requests`, no `mgz` -- so the
cache rule stays importable, and unit-testable, on an install that has neither
(CI installs pytest and nothing else). That property is inherited from the
retired utils/replay_quiz/manifest.py, which said the same thing for the same
reason; what did not survive is that module's git-tracked CSV manifest, the
bulk downloader's resume state (see download.py).

data/replays/ is gitignored: a fresh checkout has an empty cache and refills it
on demand, one match at a time, through the live ingest.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(ROOT, "data", "replays")


def replay_path(gid, cache_dir=None):
    return os.path.join(cache_dir or CACHE_DIR, f"{gid}.aoe2record")


def is_cached(gid, cache_dir=None):
    """True only for a NON-EMPTY file. A 0-byte leftover is a failed download,
    and treating it as a hit would wedge that match forever: download_replay
    returns the cached path without re-fetching, and the parser then fails on an
    empty file every single sweep."""
    path = replay_path(gid, cache_dir)
    return os.path.exists(path) and os.path.getsize(path) > 0
