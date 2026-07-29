# -*- coding: utf-8 -*-
"""One single-worker process pool, shared by parse.py (replay extraction) and render.py
(matplotlib charts).

Both run CPU-bound work that would otherwise hold the GIL on the bot's 1-second think()
tick, both need exactly one worker, and both need the same recovery story: a
ProcessPoolExecutor future cannot be cancelled, and a broken worker cannot be repaired --
the only fix for either is to throw the whole pool away and let the next call build a
fresh one. That lifecycle lives here so the two callers cannot drift apart.

Callers are responsible for calling reset() on the two conditions that wedge a pool:
  * TimeoutError   -- the worker is alive but stuck; the pool would stay busy forever.
  * BrokenProcessPool -- the worker died (segfault in native code, OOM kill); every
    subsequent submit against this executor raises immediately. Note it subclasses
    RuntimeError, so it must be caught BEFORE any generic `except Exception`.
"""
import multiprocessing
from concurrent.futures import ProcessPoolExecutor


class WorkerPool:
    """Lazily-built single-worker ProcessPoolExecutor with an explicit reset.

    The start method is pinned to `fork` deliberately. Both workloads submit a
    module-level function, which pickles by reference -- the child resolves it by
    re-importing the module unless it inherited sys.modules. Under `spawn` /
    `forkserver` (the macOS default, and the Linux default from Python 3.14) that
    re-import pulls bot/replay_stats/__init__.py, whose module-scope db.ensure_table()
    calls need a live database handle the child does not have, and the worker dies.
    Forking inherits the parent's already-imported modules, so nothing is re-run.
    """

    def __init__(self, name):
        self.name = name        # for logging only; identifies which pool broke
        self._pool = None

    def get(self):
        if self._pool is None:
            self._pool = ProcessPoolExecutor(
                max_workers=1, mp_context=multiprocessing.get_context("fork"))
        return self._pool

    def reset(self):
        """Drop the pool so the next get() builds a fresh worker."""
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
