# Per-minute eAPM Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store per-minute effective-APM per player per match, and post an APM-over-time chart with the existing post-match cards.

**Architecture:** Bucketing happens at extract time (raw actions would be ~32k rows per match vs ~320 bucketed) into a new `rs_player_apm` table. A new `render.py` wraps `chart.py` in a process pool, mirroring how `parse.py` wraps `extract.py` and `fetch.py` wraps `download.py`. Forward-only — no backfill.

**Tech Stack:** Python 3.11, mgz (sanduckhan fork, pinned), aiomysql via `core.database.db`, matplotlib (Agg), nextcord.

**Spec:** [docs/superpowers/specs/2026-07-28-eapm-pipeline-design.md](../specs/2026-07-28-eapm-pipeline-design.md)

## Global Constraints

- **Indentation is per-file, not per-repo.** Match the file you are editing. 4 spaces: `utils/replay_quiz/extract.py`, `bot/replay_stats/{__init__,shape,store,jobs,chart,query}.py`, `bot/commands/player_details.py`. **Tabs:** `bot/post_game.py`.
- **`ruff check .` must pass.** Config in `ruff.toml`: line-length 120, target py311. `utils/replay_quiz/` is excluded from lint.
- **Pure functions stay pure.** No `core` imports, no DB, no matplotlib at module scope in anything CI imports. CI installs only `pytest==8.3.3`; `tests/conftest.py` stubs the heavy imports.
- **eAPM filter is exactly mgz's:** count an action when it has a player **and** its type is not `AI_ORDER`. Anything else diverges from the stored `rs_player_games.eapm` and breaks the parity invariant.
- **Bucket size is 60 seconds.** `minute` is a 0-based index from game start.
- **Forward-only.** Never add a step that re-parses history. `reopen_pending_parser_update` only reopens `pending_parser_update` rows and must not be repurposed.
- **Test command:** `python3 -m pytest tests/ -q` (any Python 3.11 with pytest; CI pins 3.11).

---

### Task 1: Extract per-minute eAPM buckets

**Files:**
- Modify: `utils/replay_quiz/extract.py` (add `apm_buckets`, collect in the second action pass, add `apm` to the return dict, bump `EXTRACT_VERSION`)
- Test: `tests/test_replay_apm_buckets.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `extract.apm_buckets(actions, bucket_s=60) -> list[dict]` where each dict is `{"player_number": int, "minute": int, "actions": int}`; and `extract_match(...)` gains an `"apm"` key holding that list.

`extract.py` imports only `csv` and `os` at module scope (mgz is imported *inside* `extract_match`), so `apm_buckets` is importable in CI without mgz.

- [ ] **Step 1: Write the failing test**

Create `tests/test_replay_apm_buckets.py`:

```python
from utils.replay_quiz.extract import apm_buckets


def test_buckets_count_actions_per_minute():
    # 3 actions in minute 0, 2 in minute 1, for player 1
    actions = [(1, 0), (1, 30), (1, 59), (1, 60), (1, 119)]
    assert apm_buckets(actions) == [
        {"player_number": 1, "minute": 0, "actions": 3},
        {"player_number": 1, "minute": 1, "actions": 2},
    ]


def test_buckets_separate_players():
    actions = [(1, 10), (2, 10), (2, 20)]
    assert apm_buckets(actions) == [
        {"player_number": 1, "minute": 0, "actions": 1},
        {"player_number": 2, "minute": 0, "actions": 2},
    ]


def test_null_timestamps_are_dropped():
    # Same rule the queue timeline already uses: no timestamp, nowhere to plot it.
    actions = [(1, None), (1, 5)]
    assert apm_buckets(actions) == [{"player_number": 1, "minute": 0, "actions": 1}]


def test_empty_input():
    assert apm_buckets([]) == []


def test_buckets_reconcile_to_mgz_eapm():
    # mgz: total non-AI_ORDER actions / game minutes. 120 actions over 240s -> 30 eapm.
    actions = [(1, t) for t in range(0, 240, 2)]
    buckets = apm_buckets(actions)
    total = sum(b["actions"] for b in buckets)
    assert total == 120
    assert round(total / (240 / 60)) == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_replay_apm_buckets.py -v`
Expected: FAIL with `ImportError: cannot import name 'apm_buckets'`

- [ ] **Step 3: Add the pure bucketing function**

In `utils/replay_quiz/extract.py`, add after `_nearest` (around line 45), using **4-space** indentation:

```python
def apm_buckets(actions, bucket_s=60):
    """(player_number, t_s) pairs -> per-bucket action counts.

    Replicates mgz's effective-APM filter: the caller passes only actions that
    have a player and are not AI_ORDER (mgz/model/__init__.py:281-288), so the
    bucket sum over game minutes reproduces the stored eapm exactly. Actions with
    no timestamp are dropped -- the same rule the queue timeline already uses.
    Pure: no mgz, no DB.
    """
    counts = {}
    for pnum, t_s in actions:
        if t_s is None:
            continue
        key = (pnum, int(t_s) // bucket_s)
        counts[key] = counts.get(key, 0) + 1
    return [dict(player_number=pn, minute=mi, actions=n)
            for (pn, mi), n in sorted(counts.items())]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_replay_apm_buckets.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Collect the actions inside extract_match**

In `utils/replay_quiz/extract.py`, next to the other accumulators (near the `events = []` line, around line 129), add:

```python
    apm_actions = []                    # (pnum, t_s) for every non-AI_ORDER action with a player
```

Then in the **second** action pass (the loop at `# second pass: production + buildings + deletes`), immediately after the existing `t = a.type.name` line and **before** the `if t == "DE_QUEUE":` branch, add:

```python
        if t != "AI_ORDER":
            apm_actions.append((pnum, ts))
```

The loop already does `if not a.player: continue` at the top, which is exactly mgz's "action has a player" condition, and `ts = _secs(a.timestamp)` is already computed.

- [ ] **Step 6: Add `apm` to the return dict and bump the version**

At the end of `extract_match`, change the return statement to include `apm`:

```python
    return dict(match=match, players=out_players, units=out_units, techs=out_techs,
                buildings=out_buildings, events=events, apm=apm_buckets(apm_actions))
```

And bump the cache version (around line 31):

```python
EXTRACT_VERSION = "v5"   # parse-cache version; bump when extract_match output changes.
                         # v5: per-minute eAPM buckets (apm)
                         # v4: per-player settle_tc_xy + nearest gold/stone/food distances + vil_perim
```

- [ ] **Step 7: Run the full suite and lint**

Run: `python3 -m pytest tests/ -q && ruff check .`
Expected: all tests pass (548+ passed, 1 skipped), `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add utils/replay_quiz/extract.py tests/test_replay_apm_buckets.py
git commit -m "feat(replay-stats): extract per-minute eAPM buckets

Replicates mgz's own filter -- every action with a player whose type is not
AI_ORDER -- so the bucket sum over game minutes reproduces the stored eapm
exactly rather than approximating it."
```

---

### Task 2: Store the buckets in `rs_player_apm`

**Files:**
- Modify: `bot/replay_stats/__init__.py` (new table, bump `PARSER_VERSION`)
- Modify: `bot/replay_stats/shape.py` (add `_APM_FIELDS`, `apm_rows`)
- Modify: `bot/replay_stats/store.py:96-98` (delete list) and the insert block
- Test: `tests/test_replay_stats_shape.py` (extend)

**Interfaces:**
- Consumes: `extract_match(...)["apm"]` from Task 1.
- Produces: `shape.apm_rows(aoe2_match_id, apm, pnum2profile) -> list[dict]` with keys `aoe2_match_id`, `player_number`, `minute`, `profile_id`, `actions`. Table `rs_player_apm` with PK `(aoe2_match_id, player_number, minute)`.

- [ ] **Step 1: Write the failing test**

In `tests/test_replay_stats_shape.py`, add to the `EXTRACTED` fixture dict a new key (after `"buildings"`):

```python
    "apm": [{"player_number": 1, "minute": 0, "actions": 42},
            {"player_number": 2, "minute": 0, "actions": 31}],
```

Then append these tests:

```python
def test_apm_rows_denormalize_profile_id():
    rows = shape.apm_rows(999, EXTRACTED["apm"], shape.pnum_to_profile(EXTRACTED["players"]))
    assert len(rows) == 2
    assert rows[0] == {"aoe2_match_id": 999, "player_number": 1, "minute": 0,
                       "profile_id": 111, "actions": 42}
    assert rows[1]["profile_id"] == 222


def test_apm_rows_empty():
    assert shape.apm_rows(999, [], {}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_replay_stats_shape.py -v -k apm`
Expected: FAIL with `AttributeError: module 'bot.replay_stats.shape' has no attribute 'apm_rows'`

- [ ] **Step 3: Add the shape transform**

In `bot/replay_stats/shape.py` (**4 spaces**), add to the field tuples (after `_EVENT_FIELDS`, line 18):

```python
_APM_FIELDS = ("player_number", "minute", "actions")
```

And add after `event_rows` (line 83):

```python
def apm_rows(aoe2_match_id, apm, pnum2profile):
    """Per-minute eAPM buckets -> rs_player_apm rows. The PK is
    (match, player, minute), so extract_match's already-unique buckets need no seq."""
    return _long_rows(aoe2_match_id, apm, pnum2profile, _APM_FIELDS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_replay_stats_shape.py -v -k apm`
Expected: PASS, 2 tests

- [ ] **Step 5: Declare the table**

In `bot/replay_stats/__init__.py` (**4 spaces**), add after the `rs_player_events` block (which ends around line 132):

```python
db.ensure_table(dict(
    tname="rs_player_apm",
    # Per-minute effective-APM buckets. Bucketed at extract time on purpose: the raw
    # action stream is ~32k rows for a 40-minute 8-player game, against ~320 bucketed.
    # PK (aoe2_match_id, player_number, minute) makes re-ingest idempotent.
    columns=[
        dict(cname="aoe2_match_id", ctype=db.types.int),
        dict(cname="player_number", ctype=db.types.int),
        dict(cname="minute", ctype=db.types.int),        # 0-based, from game start
        dict(cname="profile_id", ctype=db.types.int, notnull=False),
        dict(cname="actions", ctype=db.types.int, notnull=False),
    ],
    primary_keys=["aoe2_match_id", "player_number", "minute"],
))
```

- [ ] **Step 6: Bump `PARSER_VERSION`**

In `bot/replay_stats/__init__.py:12`:

```python
PARSER_VERSION = "mgz-a1683d8+4"   # +4: per-minute eAPM buckets -> rs_player_apm
                                   # +3: emit per-queue production events -> rs_player_events
```

This is metadata only. `reopen_pending_parser_update` reopens rows with status `pending_parser_update` (shelved for a too-new save version) — it does **not** trigger a re-parse of completed matches, so this stays forward-only.

- [ ] **Step 7: Write the rows in `write_match`**

In `bot/replay_stats/store.py`, add the table to the idempotent-delete tuple (line 96-98):

```python
    for t in ("rs_player_games", "rs_player_units", "rs_player_techs", "rs_player_buildings",
              "rs_player_events", "rs_player_apm"):
        await db.execute(f"DELETE FROM {t} WHERE aoe2_match_id=%s", [aoe2_id])
```

And add the insert immediately after the `rs_player_events` insert:

```python
    apm = shape.apm_rows(aoe2_id, extracted.get("apm", []), p2p)
    if apm:
        await db.insert_many("rs_player_apm", apm, on_dublicate="replace")
```

`extracted.get("apm", [])` — not `extracted["apm"]` — so a cached parse from before Task 1 cannot raise.

- [ ] **Step 8: Run the full suite and lint**

Run: `python3 -m pytest tests/ -q && ruff check .`
Expected: all pass, `All checks passed!`

- [ ] **Step 9: Commit**

```bash
git add bot/replay_stats/__init__.py bot/replay_stats/shape.py bot/replay_stats/store.py tests/test_replay_stats_shape.py
git commit -m "feat(replay-stats): persist per-minute eAPM to rs_player_apm

Forward-only: the PARSER_VERSION bump is metadata, since reopen_pending_parser_update
only reopens save-version-shelved rows and never re-parses completed matches."
```

---

### Task 3: Shape the stored buckets into chart series

**Files:**
- Create: `bot/replay_stats/apm_query.py`
- Test: `tests/test_replay_apm_series.py` (create)

**Interfaces:**
- Consumes: `rs_player_apm` rows from Task 2.
- Produces:
  - `apm_series(rows, names) -> list[dict]` — pure. Each dict is `{"player_number": int, "name": str, "minutes": list[int], "values": list[int], "peak": int, "mean": float}`, with gaps zero-filled from minute 0 to the match's last minute so lines are continuous.
  - `rolling_mean(values, window) -> list[float]` — pure, trailing window, shorter at the start.
  - `async fetch_match_apm(aoe2_match_id) -> list[dict]` — the DB read.

A separate module rather than an addition to `query.py`: `query.py` is 230 lines of profile/timing queries with a different lifecycle, and the pure series shaping needs to stay importable without matplotlib.

- [ ] **Step 1: Write the failing test**

Create `tests/test_replay_apm_series.py`:

```python
from bot.replay_stats.apm_query import apm_series, rolling_mean


ROWS = [
    {"player_number": 1, "minute": 0, "actions": 30},
    {"player_number": 1, "minute": 2, "actions": 50},
    {"player_number": 2, "minute": 0, "actions": 20},
    {"player_number": 2, "minute": 1, "actions": 40},
    {"player_number": 2, "minute": 2, "actions": 60},
]
NAMES = {1: "Alice", 2: "Bob"}


def test_series_zero_fills_gaps():
    s = apm_series(ROWS, NAMES)
    alice = next(x for x in s if x["player_number"] == 1)
    # minute 1 is absent from ROWS -> filled with 0 so the line stays continuous
    assert alice["minutes"] == [0, 1, 2]
    assert alice["values"] == [30, 0, 50]


def test_series_pads_all_players_to_the_same_length():
    s = apm_series(ROWS, NAMES)
    assert len({len(x["values"]) for x in s}) == 1


def test_series_peak_and_mean():
    s = apm_series(ROWS, NAMES)
    bob = next(x for x in s if x["player_number"] == 2)
    assert bob["peak"] == 60
    assert bob["mean"] == 40.0


def test_series_uses_names_and_falls_back():
    s = apm_series(ROWS, {1: "Alice"})
    assert {x["name"] for x in s} == {"Alice", "Player 2"}


def test_series_empty():
    assert apm_series([], {}) == []


def test_rolling_mean_trailing_window():
    assert rolling_mean([0, 3, 6, 9], 2) == [0.0, 1.5, 4.5, 7.5]


def test_rolling_mean_window_one_is_identity():
    assert rolling_mean([1, 2, 3], 1) == [1.0, 2.0, 3.0]


def test_rolling_mean_empty():
    assert rolling_mean([], 3) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_replay_apm_series.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bot.replay_stats.apm_query'`

- [ ] **Step 3: Write the module**

Create `bot/replay_stats/apm_query.py` (**4 spaces**):

```python
# -*- coding: utf-8 -*-
"""Read + pure shaping for the per-minute eAPM series.

Kept separate from query.py so the pure helpers import cleanly under the CI shim
(no matplotlib, no DB at import time). The DB read is the only async function here.
"""
from core.database import db


def rolling_mean(values, window):
    """Trailing mean over `window` samples; the first samples average over what exists.
    Used to smooth a 1-minute-resolution series into a readable line. Pure."""
    out = []
    for i in range(len(values)):
        chunk = values[max(0, i - window + 1):i + 1]
        out.append(sum(chunk) / len(chunk))
    return out


def apm_series(rows, names):
    """rs_player_apm rows -> one zero-filled series per player.

    Every player is padded to the match's last minute so the lines share an x-axis
    and a player who was eliminated reads as falling to zero, which is the honest
    picture (see the spec's note on mgz's whole-game denominator). Pure.
    """
    if not rows:
        return []
    by_player = {}
    last = 0
    for r in rows:
        pn = r["player_number"]
        mi = int(r["minute"])
        by_player.setdefault(pn, {})[mi] = int(r["actions"] or 0)
        last = max(last, mi)
    out = []
    for pn in sorted(by_player):
        buckets = by_player[pn]
        values = [buckets.get(i, 0) for i in range(last + 1)]
        out.append(dict(
            player_number=pn,
            name=names.get(pn) or f"Player {pn}",
            minutes=list(range(last + 1)),
            values=values,
            peak=max(values),
            mean=sum(values) / len(values),
        ))
    return out


async def fetch_match_apm(aoe2_match_id):
    """Per-minute buckets for one match, ordered for apm_series."""
    rows = await db.fetchall(
        "SELECT player_number, minute, actions FROM rs_player_apm "
        "WHERE aoe2_match_id=%s ORDER BY player_number, minute",
        [aoe2_match_id])
    return rows or []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_replay_apm_series.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Run the full suite and lint**

Run: `python3 -m pytest tests/ -q && ruff check .`
Expected: all pass, `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add bot/replay_stats/apm_query.py tests/test_replay_apm_series.py
git commit -m "feat(replay-stats): pure eAPM series shaping + per-match read

Zero-fills gaps so every player shares an x-axis and an eliminated player reads
as falling to zero rather than vanishing from the chart."
```

---

### Task 4: Render the APM chart

**Files:**
- Modify: `bot/replay_stats/chart.py` (add `render_apm_curve`)
- Test: none — `chart.py` has no CI coverage by design (no matplotlib in CI), consistent with `render_timeline` and `render_growth_curve`. Task 8's validation sample covers it.

**Interfaces:**
- Consumes: `apm_series(...)` output from Task 3.
- Produces: `render_apm_curve(series, teams, smooth=3) -> io.BytesIO` — a PNG. `teams` maps `player_number -> 0 | 1`.

- [ ] **Step 1: Add the renderer**

Append to `bot/replay_stats/chart.py` (**4 spaces**), following the existing convention exactly — `matplotlib` imported *inside* the function, `Agg`, the OO `Figure` API, never `pyplot`:

```python
# Two hue families so team shape reads at a glance; four distinguishable steps each,
# which covers 4v4 (the largest size the luck gate admits).
_APM_TEAM_COLOURS = {
    0: ["#1f77b4", "#4a9fd8", "#7fc4f0", "#0d4f7a"],
    1: ["#d62728", "#f0663f", "#f89b6c", "#8c1a1a"],
}


def render_apm_curve(series, teams, smooth=3):
    """Per-minute eAPM over the course of a match, one line per player.

    `series` is bot.replay_stats.apm_query.apm_series output; `teams` maps
    player_number -> 0/1. Lines are smoothed with a trailing rolling mean because a
    1-minute-resolution 8-player chart is unreadable raw -- peaks are reported as
    numbers on the card instead. Returns a BytesIO PNG.
    """
    import io

    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    from .apm_query import rolling_mean

    fig = Figure(figsize=(14, 7))
    ax = fig.subplots()

    used = {0: 0, 1: 0}
    for s in series:
        team = teams.get(s["player_number"])
        palette = _APM_TEAM_COLOURS.get(team, ["#777777"])
        colour = palette[used.get(team, 0) % len(palette)]
        if team in used:
            used[team] += 1
        ax.plot(s["minutes"], rolling_mean(s["values"], smooth),
                label=f"{_short(s['name'])} (peak {s['peak']})",
                color=colour, linewidth=2.0)

    ax.set_xlabel("Minute")
    ax.set_ylabel("Actions per minute (eAPM)")
    ax.set_title(f"Activity over the match · {smooth}-minute rolling average")
    ax.grid(True, alpha=0.2)
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left", fontsize=9, ncol=2)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    buf.seek(0)
    return buf
```

`_short` already exists at `bot/replay_stats/chart.py:129` and truncates to 16 characters.

- [ ] **Step 2: Verify it imports without matplotlib**

Run: `python3 -m pytest tests/ -q`
Expected: all pass — importing `chart` must not pull matplotlib at module scope.

- [ ] **Step 3: Lint**

Run: `ruff check .`
Expected: `All checks passed!`

- [ ] **Step 4: Commit**

```bash
git add bot/replay_stats/chart.py
git commit -m "feat(replay-stats): APM-over-match chart renderer

Smoothed with a trailing rolling mean -- eight lines at 1-minute resolution are
unreadable raw, so peaks are reported as numbers instead of chart spikes."
```

---

### Task 5: Render off the event loop in a process pool

**Files:**
- Create: `bot/replay_stats/render.py`
- Test: `tests/test_replay_render_policy.py` (create)

**Interfaces:**
- Consumes: `chart.render_apm_curve` from Task 4.
- Produces: `async render_apm(series, teams, timeout=30) -> io.BytesIO | None` — returns `None` on timeout, failure, or fewer than `MIN_MINUTES` of data. Also `should_render(series) -> bool` (pure).

**Why a process pool, not `asyncio.to_thread`:** this runs on the `think()` tick path, and matplotlib is largely pure Python, so a thread holds the GIL through most of a render. `bot/replay_stats/parse.py:20-35` already solves exactly this for replay extraction — including tearing the pool down on timeout, because a running `ProcessPoolExecutor` future cannot be cancelled. This mirrors that structure.

- [ ] **Step 1: Write the failing test**

Create `tests/test_replay_render_policy.py`:

```python
from bot.replay_stats.render import MIN_MINUTES, should_render


def _series(n_minutes, n_players=2):
    return [{"player_number": p, "name": f"P{p}", "minutes": list(range(n_minutes)),
             "values": [10] * n_minutes, "peak": 10, "mean": 10.0}
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_replay_render_policy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bot.replay_stats.render'`

- [ ] **Step 3: Write the module**

Create `bot/replay_stats/render.py` (**4 spaces**):

```python
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
    if not should_render(series):
        return None
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_get_pool(), _render, series, teams), timeout)
    except TimeoutError:
        _reset_pool()
        return None
    except Exception:
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_replay_render_policy.py -v`
Expected: PASS, 4 tests

- [ ] **Step 5: Run the full suite and lint**

Run: `python3 -m pytest tests/ -q && ruff check .`
Expected: all pass, `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add bot/replay_stats/render.py tests/test_replay_render_policy.py
git commit -m "feat(replay-stats): render the APM chart in a process pool

This runs on the think() tick, and matplotlib is largely pure Python -- a thread
would keep the GIL through most of a render. Mirrors parse.py, teardown included."
```

---

### Task 6: Attach the chart to the post-match cards

**Files:**
- Modify: `bot/post_game.py` (`post_match_analysis`, around lines 775-798)
- Test: none — this is Discord I/O with no pure surface. Task 8 covers it end-to-end.

**Interfaces:**
- Consumes: `apm_query.fetch_match_apm`, `apm_query.apm_series`, `render.render_apm`.
- Produces: no new callable. `post_match_analysis(bot_match_id)` keeps its `-> bool` contract.

**`bot/post_game.py` uses TABS.**

- [ ] **Step 1: Add the chart builder**

Add above `post_match_analysis` in `bot/post_game.py`:

```python
async def _apm_chart_file(bot_match_id):
	"""Rendered APM chart for a match, or None. Best-effort: every failure path
	returns None so the cards still post without an image."""
	try:
		from core.database import db
		from bot.replay_stats import render
		from bot.replay_stats.apm_query import apm_series, fetch_match_apm

		row = await db.fetchone(
			"SELECT aoe2_match_id FROM rs_matches WHERE bot_match_id=%s", [bot_match_id])
		if not row:
			return None
		aoe2_id = row["aoe2_match_id"]
		rows = await fetch_match_apm(aoe2_id)
		if not rows:
			return None
		meta = await db.fetchall(
			"SELECT player_number, identity, team FROM rs_player_games WHERE aoe2_match_id=%s",
			[aoe2_id])
		names = {m["player_number"]: m.get("identity") for m in meta or []}
		sides = sorted({m.get("team") for m in meta or [] if m.get("team") is not None})
		teams = {m["player_number"]: sides.index(m["team"])
		         for m in meta or [] if m.get("team") in sides}
		buf = await render.render_apm(apm_series(rows, names), teams)
		if buf is None:
			return None
		from nextcord import File
		return File(fp=buf, filename="apm.png")
	except Exception as e:
		from core.console import log
		log.error(f"APM chart build failed (bot match {bot_match_id}): {e}")
		return None
```

`rs_player_games.team` is a string (see the schema at `bot/replay_stats/__init__.py:49`), so it is mapped to a 0/1 index via `sides` rather than cast.

- [ ] **Step 2: Attach it in `post_match_analysis`**

Replace the send block in `post_match_analysis` (currently lines 787-795) with:

```python
		cards = await build_match_cards_embed(channel_id, bot_match_id)
		embed = await build_match_analysis_embed(channel_id, bot_match_id)
		if cards is None and embed is None:
			return False
		chart_file = await _apm_chart_file(bot_match_id)
		target = cards if cards is not None else embed
		if chart_file is not None:
			target.set_image(url="attachment://apm.png")
		embeds = [e for e in (cards, embed) if e is not None]
		if chart_file is not None:
			await channel.send(embeds=embeds, file=chart_file)
		else:
			await channel.send(embeds=embeds)
		return True
```

Discord allows one image per embed, so the chart attaches to the cards embed when it exists and to the analysis embed otherwise.

- [ ] **Step 3: Run the full suite and lint**

Run: `python3 -m pytest tests/ -q && ruff check .`
Expected: all pass, `All checks passed!`

- [ ] **Step 4: Commit**

```bash
git add bot/post_game.py
git commit -m "feat(post-game): attach the APM chart to the match cards

Every failure path returns None so a chart problem costs the image, never the post."
```

---

### Task 7: Stop `/player_details` rendering on the event loop

**Files:**
- Modify: `bot/commands/player_details.py:52`
- Test: none — behaviour is unchanged; this moves work off the loop.

**Interfaces:** none changed.

This is the pre-existing inconsistency the spec calls out: [player_details.py:52](bot/commands/player_details.py:52) calls `chart.render_growth_curve` synchronously, while `bot/commands/stats.py:496` and `bot/player_profile.py` correctly offload. It is user-triggered rather than tick-driven, so the blast radius is smaller — but it still stalls the bot, including the 1-second tick, for the whole render.

- [ ] **Step 1: Offload the render**

In `bot/commands/player_details.py` (**4 spaces**), add to the imports at the top:

```python
import asyncio
```

And change line 52 from:

```python
    png = chart.render_growth_curve(get_nick(target), curve, days, curve2=curve2, name2=name2)
```

to:

```python
    # matplotlib is CPU-bound and blocks the 1s think() tick if run inline --
    # same offload as bot/commands/stats.py:496.
    png = await asyncio.to_thread(
        chart.render_growth_curve, get_nick(target), curve, days, curve2=curve2, name2=name2)
```

`to_thread` rather than the process pool here: this is user-triggered, already behind an interaction defer, and matches the established pattern for command-path renders.

- [ ] **Step 2: Run the full suite and lint**

Run: `python3 -m pytest tests/ -q && ruff check .`
Expected: all pass, `All checks passed!`

- [ ] **Step 3: Commit**

```bash
git add bot/commands/player_details.py
git commit -m "fix(player-details): render the growth curve off the event loop

It was the only chart caller still rendering inline, stalling the tick for the
duration of every /player_details."
```

---

### Task 8: Validation sample — 5 recent matches

**Files:**
- Create: `utils/validate_apm.py`
- Test: none — this *is* the test harness.

**Interfaces:**
- Consumes: everything above.
- Produces: a CLI that re-parses recent matches and writes PNGs to disk for review. Writes **no** database rows.

This is the spec's validation gate, not a backfill. It answers three questions: does the parity invariant hold, is an 8-line chart legible, and what share of actions carry usable timestamps.

- [ ] **Step 1: Write the script**

Create `utils/validate_apm.py` (**4 spaces**):

```python
#!/usr/bin/env python3
"""Validation sample for the per-minute eAPM pipeline. Re-parses the N most recent
cached replays, checks the mgz parity invariant, and writes charts to disk.

NOT a backfill: writes no database rows. See
docs/superpowers/specs/2026-07-28-eapm-pipeline-design.md.

Usage:
    PYTHONPATH=. python3 utils/validate_apm.py [--limit 5] [--out /tmp/apm]
"""
import argparse
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bot.replay_stats.apm_query import apm_series          # noqa: E402
from bot.replay_stats.chart import render_apm_curve        # noqa: E402
from utils.replay_quiz.extract import extract_match, load_resolved   # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--out", default="/tmp/apm")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    paths = sorted(glob.glob(os.path.join(ROOT, "data", "replays", "*.aoe2record")),
                   key=os.path.getmtime, reverse=True)[:args.limit]
    if not paths:
        print("No cached replays in data/replays -- nothing to validate.")
        return 1

    resolved = load_resolved()
    failures = 0
    for path in paths:
        name = os.path.basename(path)
        try:
            result = extract_match(path, resolved, {})
        except Exception as e:
            print(f"{name}: PARSE FAILED -- {e}")
            failures += 1
            continue

        apm = result.get("apm") or []
        duration_s = (result["match"].get("duration_s") or 0)
        minutes = duration_s / 60 if duration_s else 0
        print(f"\n{name}  ({len(result['players'])} players, {minutes:.1f} min)")

        # 1. Parity: bucket sum / game minutes must reproduce the stored eapm.
        for p in result["players"]:
            pn = p["player_number"]
            total = sum(b["actions"] for b in apm if b["player_number"] == pn)
            computed = round(total / minutes) if minutes else 0
            stored = p.get("eapm")
            ok = stored is not None and abs(computed - stored) <= 1
            if not ok:
                failures += 1
            print(f"  p{pn} {str(p.get('identity'))[:16]:16} "
                  f"computed={computed:4} stored={str(stored):>4}  {'OK' if ok else 'MISMATCH'}")

        # 2. Legibility: render and eyeball.
        names = {p["player_number"]: p.get("identity") for p in result["players"]}
        sides = sorted({p.get("team") for p in result["players"] if p.get("team") is not None})
        teams = {p["player_number"]: sides.index(p["team"])
                 for p in result["players"] if p.get("team") in sides}
        series = apm_series(apm, names)
        if series:
            out = os.path.join(args.out, name.replace(".aoe2record", ".png"))
            with open(out, "wb") as f:
                f.write(render_apm_curve(series, teams).read())
            print(f"  chart -> {out}")

    print(f"\n{'FAILURES: ' + str(failures) if failures else 'All parity checks passed.'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Populate the replay cache**

`data/replays/` does **not** exist in a fresh checkout — there is nothing cached to
validate against, so it has to be fetched first. `data/match_id_map.csv` is present
(2,649 rows), and `window_ids` sorts descending, so `--limit 5` takes the five
newest matches in the map.

Run: `python3 utils/replay_quiz/download.py --limit 5 --space 5`

Expected: `Window: N matches ...; to attempt: 5`, then five `.aoe2record` files in
`data/replays/`.

aoe.ms serves only 65–80% of matches and rate-limits per IP, so some will 404 or
back off. If fewer than three download, raise `--limit` and re-run — the script is
resumable and skips what it already has.

- [ ] **Step 3: Run it**

Requires a Python with `mgz` installed — the pytest venv does not have it. Locally
that is `/usr/local/bin/python3.11`.

Run: `PYTHONPATH=. /usr/local/bin/python3.11 utils/validate_apm.py --limit 5`

Expected: a per-player parity table for each match with every row `OK`, and 5 PNGs written to `/tmp/apm`.

If any row reports `MISMATCH`, **stop** — the bucketing has diverged from mgz's filter. The most likely cause is an action type being counted that mgz excludes, or vice versa; re-check the `t != "AI_ORDER"` condition against `mgz/model/__init__.py:281-288`.

- [ ] **Step 4: Review the charts**

Open the PNGs. Confirm 8 lines are distinguishable at 3-minute smoothing. If not, the spec names the fallbacks: plot team averages instead of individuals, or plot only the top four by peak.

- [ ] **Step 5: Lint and commit**

Run: `ruff check .`
Expected: `All checks passed!`

```bash
git add utils/validate_apm.py
git commit -m "test(replay-stats): validation sample for the eAPM pipeline

Re-parses recent cached replays to check the mgz parity invariant and render
charts for review. Writes no rows -- this is the pre-ship gate, not a backfill."
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| Definition — mgz parity | 1 (filter + reconcile test), 8 (live parity check) |
| Extraction, `EXTRACT_VERSION`, `PARSER_VERSION` | 1, 2 |
| Storage (`rs_player_apm`, idempotency) | 2 |
| Chart (rolling average, team colours, legend) | 4 |
| Discord integration | 6 |
| Rendering off the event loop (process pool) | 5 |
| `player_details.py` inline-render fix | 7 |
| Error handling (no rows, <2 min, render failure) | 5 (`should_render`, `None` returns), 6 (guards) |
| Validation before ship (parity, legibility, timestamps) | 8 |
| Testing (pure bucketing, parity, shaping, idempotency) | 1, 2, 3, 5 |
| Forward-only, no backfill | Enforced by Global Constraints; Task 2 Step 6 documents why the version bump is safe |

**Type consistency:** `apm_buckets` (Task 1) emits `{player_number, minute, actions}`; `_APM_FIELDS` (Task 2) reads exactly those three; `apm_series` (Task 3) consumes `player_number`/`minute`/`actions` and emits `player_number`/`name`/`minutes`/`values`/`peak`/`mean`; `render_apm_curve` (Task 4) reads `minutes`/`values`/`name`/`peak`/`player_number`; `should_render` and `_render` (Task 5) read `values`. `MIN_MINUTES` is defined in Task 5 and imported by its test only. Consistent throughout.

**Placeholder scan:** no TBDs. Every code step carries complete code; every run step carries an exact command and expected output.

**Known gap, deliberately left:** the spec's third validation question — what share of actions lack timestamps — is answered indirectly. `apm_buckets` drops null-timestamp actions, so if a material share were untimestamped the Task 8 parity check would fail as a systematic under-count. That is the detection mechanism; no separate instrumentation is needed.
