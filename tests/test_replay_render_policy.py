import asyncio
import concurrent.futures

from nammaoe2bot.ingest import render as render_module
from nammaoe2bot.ingest.render import MIN_MINUTES, render_apm, should_render


def _series(n_minutes, n_players=2):
    return [{"player_number": p, "name": f"P{p}", "minutes": list(range(n_minutes)),
             "values": [10] * n_minutes, "peak": 10, "mean_active": 10.0}
            for p in range(1, n_players + 1)]


def test_no_series_is_not_rendered():
    assert should_render([]) is False


def test_too_short_a_match_is_not_rendered():
    # A two-point line is noise, not a chart.
    assert should_render(_series(MIN_MINUTES - 1)) is False


def test_long_enough_match_is_rendered():
    assert should_render(_series(MIN_MINUTES)) is True


def test_single_player_is_still_rendered():
    assert should_render(_series(MIN_MINUTES, n_players=1)) is True


# render_apm must never raise -- too little data, malformed input, a timeout, or any
# other exception must all come back as None so a chart failure never costs the
# Discord post it would have been attached to. These drive the early-return path
# directly via asyncio.run: no matplotlib import, no subprocess, no executor touched.


def test_render_apm_returns_none_for_empty_series():
    assert asyncio.run(render_apm([], {})) is None


def test_render_apm_returns_none_for_none_series():
    assert asyncio.run(render_apm(None, {})) is None


def test_render_apm_returns_none_for_entry_missing_values_key():
    assert asyncio.run(render_apm([{"player_number": 1}], {})) is None


def test_render_apm_returns_none_for_none_entry():
    assert asyncio.run(render_apm([None], {})) is None


def test_render_apm_returns_none_for_too_short_series():
    assert asyncio.run(render_apm(_series(MIN_MINUTES - 1), {})) is None


class _FakeExecutor:
    """Stands in for the ProcessPoolExecutor: records what was submitted and resolves
    immediately, so a well-formed render never touches a real subprocess or
    matplotlib."""

    def __init__(self, result):
        self._result = result
        self.submitted = None

    def submit(self, func, *args):
        self.submitted = (func, args)
        future = concurrent.futures.Future()
        future.set_result(self._result)
        return future


def test_render_apm_reaches_the_executor_for_a_well_formed_series(monkeypatch):
    # The never-raise fix must not also swallow the happy path -- a well-formed,
    # long-enough series should still clear the should_render gate and reach the
    # executor rather than short-circuiting to None.
    sentinel = object()
    fake_executor = _FakeExecutor(sentinel)
    monkeypatch.setattr(render_module, "_get_pool", lambda: fake_executor)

    result = asyncio.run(render_apm(_series(MIN_MINUTES), {}))

    assert result is sentinel
    assert fake_executor.submitted is not None
