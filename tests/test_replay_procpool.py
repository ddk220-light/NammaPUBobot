"""Regression tests for the shared single-worker process pool and the two conditions
that permanently wedge it.

Both parse.py and render.py hold one WorkerPool. A future that never returns
(TimeoutError) leaves the single worker busy forever, and a worker that dies
(BrokenProcessPool) leaves an executor that rejects every later submit. Neither is
repairable — the pool must be dropped so the next call builds a fresh one.

The parse.py case is the severe one: parse_failed increments attempts,
policy.parse_failed_exhausted gives up after 3, store.find_due_retry never reconsiders
a 'gave_up' row, and find_new_match skips anything already in replay_ingest. So one dead
worker silently writes off every pending and newly-arriving match, permanently.

Nothing here touches a real subprocess, matplotlib, mgz, or a database: the executor is
faked, and ProcessPoolExecutor itself is monkeypatched where its construction is checked.
"""
import asyncio
import concurrent.futures
from concurrent.futures.process import BrokenProcessPool

import pytest

from nammaoe2bot.ingest import parse as parse_module
from nammaoe2bot.ingest import procpool
from nammaoe2bot.ingest import render as render_module


# ── WorkerPool lifecycle ─────────────────────────────────────────────────


class _FakeExecutorFactory:
    """Records the kwargs each ProcessPoolExecutor would have been built with, and hands
    back a cheap stand-in so no subprocess is ever created."""

    def __init__(self):
        self.calls = []
        self.built = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        made = _ShutdownRecorder()
        self.built.append(made)
        return made


class _ShutdownRecorder:
    def __init__(self):
        self.shutdowns = []

    def shutdown(self, **kwargs):
        self.shutdowns.append(kwargs)


@pytest.fixture
def fake_executors(monkeypatch):
    factory = _FakeExecutorFactory()
    monkeypatch.setattr(procpool, "ProcessPoolExecutor", factory)
    return factory


def test_pool_pins_the_fork_start_method(fake_executors):
    # Load-bearing: _render and _extract are pickled by reference, so the worker only
    # resolves them if it inherited sys.modules. Under spawn/forkserver (the macOS
    # default, and the Linux default from Python 3.14) the child re-imports
    # nammaoe2bot/ingest/__init__.py, whose module-scope db.ensure_table() calls need a
    # live DB handle it does not have — and the worker dies on every single job.
    procpool.WorkerPool("t").get()
    assert fake_executors.calls[0]["max_workers"] == 1
    assert fake_executors.calls[0]["mp_context"].get_start_method() == "fork"


def test_pool_is_built_once_and_reused(fake_executors):
    pool = procpool.WorkerPool("t")
    assert pool.get() is pool.get()
    assert len(fake_executors.calls) == 1


def test_reset_shuts_down_and_the_next_get_builds_a_fresh_worker(fake_executors):
    pool = procpool.WorkerPool("t")
    first = pool.get()
    pool.reset()
    second = pool.get()

    assert second is not first
    assert first.shutdowns == [dict(wait=False, cancel_futures=True)]
    assert len(fake_executors.calls) == 2


def test_reset_on_an_unbuilt_pool_is_a_no_op(fake_executors):
    procpool.WorkerPool("t").reset()
    assert fake_executors.calls == []


# ── shared harness for the two call sites ────────────────────────────────


class _RaisingExecutor:
    """Submits fail the way a dead pool's do: immediately, with BrokenProcessPool."""

    def __init__(self, exc):
        self._exc = exc

    def submit(self, _func, *_args):
        raise self._exc


class _HangingExecutor:
    """Returns a future that never resolves, so asyncio.wait_for times out."""

    def submit(self, _func, *_args):
        return concurrent.futures.Future()


class _RecordingPool:
    """Stands in for the module's WorkerPool: hands out a fixed executor and counts resets."""

    def __init__(self, executor):
        self._executor = executor
        self.resets = 0

    def get(self):
        return self._executor

    def reset(self):
        self.resets += 1


def _install_pool(monkeypatch, module, executor):
    pool = _RecordingPool(executor)
    monkeypatch.setattr(module, "_POOL", pool)
    return pool


# ── render.py ────────────────────────────────────────────────────────────


def _series(n_minutes=5, n_players=2):
    return [{"player_number": p, "name": f"P{p}", "minutes": list(range(n_minutes)),
             "values": [10] * n_minutes, "peak": 10, "mean_active": 10.0}
            for p in range(1, n_players + 1)]


def test_render_resets_the_pool_on_timeout(monkeypatch):
    pool = _install_pool(monkeypatch, render_module, _HangingExecutor())

    assert asyncio.run(render_module.render_apm(_series(), {}, timeout=0.01)) is None
    assert pool.resets == 1


def test_render_resets_the_pool_when_the_worker_dies(monkeypatch):
    pool = _install_pool(monkeypatch, render_module,
                         _RaisingExecutor(BrokenProcessPool("worker died")))

    assert asyncio.run(render_module.render_apm(_series(), {})) is None
    assert pool.resets == 1, "BrokenProcessPool must replace the pool, not fall through to " \
                             "the generic handler that leaves the dead executor in place"


def test_render_does_not_reset_the_pool_on_an_ordinary_render_error(monkeypatch):
    # A bad figure is the worker's problem, not the pool's — throwing away a healthy
    # worker on every malformed series would just churn subprocesses.
    pool = _install_pool(monkeypatch, render_module, _RaisingExecutor(ValueError("bad input")))

    assert asyncio.run(render_module.render_apm(_series(), {})) is None
    assert pool.resets == 0


# ── parse.py ─────────────────────────────────────────────────────────────


def _stub_save_version(monkeypatch, version=68.0):
    """parse_replay gates on save_version before it ever reaches the pool; short-circuit
    the (threaded, mgz-backed) read so the tests stay pure."""
    async def _read(_path):
        return version

    monkeypatch.setattr(parse_module, "read_save_version", _read)


def test_parse_resets_the_pool_on_timeout(monkeypatch):
    _stub_save_version(monkeypatch)
    pool = _install_pool(monkeypatch, parse_module, _HangingExecutor())

    result, status, sv = asyncio.run(parse_module.parse_replay("p.aoe2record", {}, timeout=0.01))

    assert (result, status, sv) == (None, "parse_failed", 68.0)
    assert pool.resets == 1


def test_parse_resets_the_pool_when_the_worker_dies(monkeypatch):
    _stub_save_version(monkeypatch)
    pool = _install_pool(monkeypatch, parse_module,
                         _RaisingExecutor(BrokenProcessPool("worker died")))

    result, status, sv = asyncio.run(parse_module.parse_replay("p.aoe2record", {}))

    assert (result, status, sv) == (None, "parse_failed", 68.0)
    assert pool.resets == 1, "a dead worker must be replaced — otherwise every match " \
                             "parse_fails x3 into the terminal 'gave_up' state"


def test_parse_does_not_reset_the_pool_on_an_ordinary_parse_error(monkeypatch):
    _stub_save_version(monkeypatch)
    pool = _install_pool(monkeypatch, parse_module, _RaisingExecutor(ValueError("corrupt file")))

    _result, status, _sv = asyncio.run(parse_module.parse_replay("p.aoe2record", {}))

    assert status == "parse_failed"
    assert pool.resets == 0


def test_parse_still_gates_on_save_version_before_touching_the_pool(monkeypatch):
    # Behaviour that must survive the pool refactor: an unsupported replay is shelved as
    # pending_parser_update and never reaches a worker at all.
    _stub_save_version(monkeypatch, version=999.0)
    pool = _install_pool(monkeypatch, parse_module, _RaisingExecutor(AssertionError("submitted!")))

    result, status, sv = asyncio.run(parse_module.parse_replay("p.aoe2record", {}))

    assert (result, status, sv) == (None, "pending_parser_update", 999.0)
    assert pool.resets == 0
