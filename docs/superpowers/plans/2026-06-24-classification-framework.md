# Player Classification Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable, offline-first classification framework (trigger + factors + data-requirements per classification, stored in MySQL `cls_*` tables) with **archer rush** as the first classification, plus a `/classification` Discord slash command that reports who used it and whether they won.

**Architecture:** Pure, DB-free `trigger(game, pnum)` / `factors(game, pnum)` modules registered into a registry. An offline runner (`python -m utils.classifications.runner`) parses the kept replay corpus once per game (via the existing `utils/replay_quiz/extract.py`), runs every registered classification, and idempotently upserts results into MySQL `cls_*` over a date window. The bot only reads `cls_*` for the command.

**Tech Stack:** Python 3.12, `aiomysql` (via `utils/db_helpers.create_pool` offline and `core.database.db` in the bot), `nextcord` slash commands, the vendored `mgz` fork (offline parse only), `pytest`, `ruff` (tabs in `bot/`, 4-space in `utils/`).

---

## File Structure

**Offline framework — `utils/classifications/` (4-space indent, the `utils/` convention):**
- `__init__.py` — empty package marker (no heavy imports, so tests can import submodules).
- `gamedata.py` — pure accessors over an `extract_match()` output dict (`player`, `archer_queue_events`, `tech_click_s`).
- `contract.py` — the `Classification` dataclass + `req()` helper (the trigger/factors/requirements container).
- `defs/__init__.py`, `defs/archer_rush.py` — the archer-rush trigger, factors, and requirements ledger.
- `registry.py` — collects all `defs/*` into `REGISTRY` (key → Classification).
- `shape.py` — pure row builders: `result_row()`, `metric_rows()` (factors dict → `cls_result_metrics` rows).
- `schema.py` — raw `CREATE TABLE IF NOT EXISTS` SQL for the `cls_*` tables (used by the offline runner).
- `dbio.py` — async DB helpers using a raw `aiomysql` pool: `ensure_tables`, `window_matches`, `upsert_classification`, `upsert_results`.
- `runner.py` — CLI orchestrator: window → corpus (cache/download) → parse (cache) → classify → push → report.

**Bot read path:**
- `bot/classifications/__init__.py` — `cls_*` schema via `db.ensure_table` (mirrors `bot/replay_stats/__init__.py`).
- `bot/classifications/query.py` — pure `summarize()` + a DB fetch that feeds it.
- `bot/commands/classification.py` — the `/classification` handler.
- Modify `bot/commands/__init__.py`, `bot/__init__.py`, `bot/context/slash/commands.py` to register it.

**Tests (`tests/`, mirror `tests/test_replay_stats_shape.py` style — pure, no DB/network):**
- `test_classifications_gamedata.py`, `test_classifications_archer_rush.py`, `test_classifications_contract.py`, `test_classifications_shape.py`, `test_classifications_query.py`.

A shared synthetic-game fixture (one match, a clear rusher + a fast-castle player) is repeated in each test file that needs it (the engineer may read tasks out of order).

---

### Task 1: Pure game accessors (`gamedata.py`)

**Files:**
- Create: `utils/classifications/__init__.py`
- Create: `utils/classifications/gamedata.py`
- Test: `tests/test_classifications_gamedata.py`

- [ ] **Step 1: Create the package marker**

Create `utils/classifications/__init__.py` with exactly:

```python
"""Offline player-classification framework (trigger + factors + data-requirements per
classification). Pure logic here is DB- and mgz-free so it unit-tests cleanly; the runner
(runner.py) is the only module that touches replays, the network, or the database."""
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_classifications_gamedata.py`:

```python
import utils.classifications.gamedata as gd

GAME = {
    "players": [
        {"player_number": 1, "feudal_s": 600, "castle_s": 1200, "winner": True, "eapm": 80},
        {"player_number": 2, "feudal_s": 640, "castle_s": 900, "winner": False, "eapm": 70},
    ],
    "techs": [
        {"player_number": 1, "tech": "Fletching", "click_s": 780, "phase": "feudal"},
        {"player_number": 2, "tech": "Loom", "click_s": 120, "phase": "dark"},
    ],
    "events": [
        {"player_number": 1, "category": "archer_line", "name": "Archer", "amount": 3, "t_s": 720},
        {"player_number": 1, "category": "archer_line", "name": "Archer", "amount": 4, "t_s": 700},
        {"player_number": 1, "category": "skirmisher", "name": "Skirmisher", "amount": 2, "t_s": 710},
        {"player_number": 2, "category": "archer_line", "name": "Archer", "amount": 5, "t_s": 950},
        {"player_number": 1, "category": "archer_line", "name": "Archer", "amount": 1, "t_s": None},
    ],
}


def test_player_lookup():
    assert gd.player(GAME, 1)["eapm"] == 80
    assert gd.player(GAME, 99) is None


def test_archer_queue_events_excludes_skirmishers_and_null_ts_and_sorts():
    evs = gd.archer_queue_events(GAME, 1)
    assert [e["t_s"] for e in evs] == [700, 720]   # skirmisher + null-t_s dropped, time-sorted


def test_tech_click_s():
    assert gd.tech_click_s(GAME, 1, "Fletching") == 780
    assert gd.tech_click_s(GAME, 1, "Loom") is None     # not this player
    assert gd.tech_click_s(GAME, 2, "Loom") == 120
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_classifications_gamedata.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'utils.classifications.gamedata'`

- [ ] **Step 4: Write minimal implementation**

Create `utils/classifications/gamedata.py`:

```python
"""Pure read accessors over an extract_match() output dict. No DB, no mgz."""


def player(game, pnum):
    for p in game.get("players", []):
        if p["player_number"] == pnum:
            return p
    return None


def player_numbers(game):
    return [p["player_number"] for p in game.get("players", [])]


def archer_queue_events(game, pnum):
    """Foot-archer-line queue events for a player, timestamped, sorted by time.
    Excludes skirmishers/cav-archers (separate categories) and null-timestamp queues."""
    evs = [e for e in game.get("events", [])
           if e["player_number"] == pnum
           and e.get("category") == "archer_line"
           and e.get("t_s") is not None]
    return sorted(evs, key=lambda e: e["t_s"])


def tech_click_s(game, pnum, tech):
    for t in game.get("techs", []):
        if t["player_number"] == pnum and t.get("tech") == tech:
            return t.get("click_s")
    return None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_classifications_gamedata.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add utils/classifications/__init__.py utils/classifications/gamedata.py tests/test_classifications_gamedata.py
git commit -m "feat(classifications): pure game-data accessors over extract output"
```

---

### Task 2: Classification contract (`contract.py`)

**Files:**
- Create: `utils/classifications/contract.py`
- Test: `tests/test_classifications_contract.py` (extended again in Task 5)

- [ ] **Step 1: Write the failing test**

Create `tests/test_classifications_contract.py`:

```python
from utils.classifications.contract import Classification, req


def test_req_builds_requirement():
    r = req("castle_click_s", source="extract.players.castle_s", status="available", note="age click")
    assert r == {"field": "castle_click_s", "source": "extract.players.castle_s",
                 "status": "available", "note": "age click"}


def test_classification_holds_callables_and_metadata():
    c = Classification(
        key="dummy", title="Dummy", version=1, trigger_spec="always true",
        requirements=[req("x", source="s", status="available")],
        trigger=lambda game, pnum: True,
        factors=lambda game, pnum: {"x": 1.0},
    )
    assert c.key == "dummy" and c.version == 1
    assert c.trigger({}, 1) is True
    assert c.factors({}, 1) == {"x": 1.0}
    assert c.requirements[0]["status"] == "available"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_classifications_contract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'utils.classifications.contract'`

- [ ] **Step 3: Write minimal implementation**

Create `utils/classifications/contract.py`:

```python
"""The classification contract: each classification is a trigger predicate + a factors
function + a static data-requirements ledger, all keyed under a stable string `key`."""
from dataclasses import dataclass, field
from typing import Callable


def req(field_name, source, status, note=""):
    """One data-requirement row. status is 'available' or 'missing'."""
    assert status in ("available", "missing"), status
    return {"field": field_name, "source": source, "status": status, "note": note}


@dataclass
class Classification:
    key: str
    title: str
    version: int
    trigger_spec: str                  # human-readable description of the trigger
    requirements: list                 # list of req() dicts
    trigger: Callable                  # (game, pnum) -> bool   (pure)
    factors: Callable                  # (game, pnum) -> dict[str, float|None]  (pure)
    status: str = "active"             # 'active' or 'draft'
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_classifications_contract.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add utils/classifications/contract.py tests/test_classifications_contract.py
git commit -m "feat(classifications): Classification contract dataclass + req() helper"
```

---

### Task 3: Archer-rush trigger

**Files:**
- Create: `utils/classifications/defs/__init__.py`
- Create: `utils/classifications/defs/archer_rush.py`
- Test: `tests/test_classifications_archer_rush.py` (extended in Task 4)

- [ ] **Step 1: Create the defs package marker**

Create `utils/classifications/defs/__init__.py` with exactly:

```python
"""One module per classification. Each exports a module-level `CLASSIFICATION` (a
contract.Classification). registry.py imports them."""
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_classifications_archer_rush.py`:

```python
from utils.classifications.defs.archer_rush import trigger

# A clear feudal archer rush (player 1): archers queued before the castle click (1200).
# A fast-castle player (player 2): clicks Castle early (700); archers only AFTER the click.
GAME = {
    "players": [
        {"player_number": 1, "feudal_s": 600, "castle_s": 1200, "winner": True, "eapm": 80},
        {"player_number": 2, "feudal_s": 600, "castle_s": 700, "winner": False, "eapm": 75},
        {"player_number": 3, "feudal_s": None, "castle_s": None, "winner": False, "eapm": 40},
    ],
    "techs": [{"player_number": 1, "tech": "Fletching", "click_s": 780, "phase": "feudal"}],
    "events": [
        {"player_number": 1, "category": "archer_line", "name": "Archer", "amount": 3, "t_s": 700},
        {"player_number": 1, "category": "archer_line", "name": "Archer", "amount": 4, "t_s": 760},
        {"player_number": 2, "category": "archer_line", "name": "Archer", "amount": 3, "t_s": 760},
        {"player_number": 1, "category": "skirmisher", "name": "Skirmisher", "amount": 9, "t_s": 720},
    ],
}


def test_trigger_fires_for_pre_castle_archers():
    assert trigger(GAME, 1) is True


def test_trigger_skips_fast_castle_archers_after_click():
    # player 2's only archer (t_s 760) is AFTER their castle click (700) -> not a rush
    assert trigger(GAME, 2) is False


def test_trigger_skips_player_who_never_reached_feudal():
    assert trigger(GAME, 3) is False


def test_trigger_ignores_skirmishers():
    skirmisher_only = {
        "players": [{"player_number": 1, "feudal_s": 600, "castle_s": 1200}],
        "techs": [],
        "events": [{"player_number": 1, "category": "skirmisher", "name": "Skirmisher",
                    "amount": 20, "t_s": 700}],
    }
    assert trigger(skirmisher_only, 1) is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_classifications_archer_rush.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'utils.classifications.defs.archer_rush'`

- [ ] **Step 4: Write minimal implementation**

Create `utils/classifications/defs/archer_rush.py`:

```python
"""Archer rush: the player queued >=1 foot Archer (archer line; NOT skirmisher) before the
Castle-age CLICK. Rationale: a fast-castle->crossbow player clicks Castle first, so their
archers land after the click and score zero pre-castle archers; any archer before the click
reveals aggressive-feudal intent (even a botched, low-count attempt). Rush != win — execution
is graded by factors() (Task 4)."""
from utils.classifications import gamedata as gd

W_SECONDS = 180            # "shortly after Feudal" window for the tempo factor
COMMIT_ARCHERS = 10        # the "committed" archer count for commit_to_castle_s


def _before_castle(t, castle_s):
    return t is not None and (castle_s is None or t < castle_s)


def trigger(game, pnum):
    p = gd.player(game, pnum)
    if not p or p.get("feudal_s") is None:
        return False
    castle_s = p.get("castle_s")
    return any(_before_castle(e["t_s"], castle_s) for e in gd.archer_queue_events(game, pnum))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_classifications_archer_rush.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add utils/classifications/defs/__init__.py utils/classifications/defs/archer_rush.py tests/test_classifications_archer_rush.py
git commit -m "feat(classifications): archer-rush trigger (>=1 foot archer before castle click)"
```

---

### Task 4: Archer-rush factors

**Files:**
- Modify: `utils/classifications/defs/archer_rush.py` (add `factors`)
- Test: `tests/test_classifications_archer_rush.py` (add factor tests)

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_classifications_archer_rush.py`:

```python
from utils.classifications.defs.archer_rush import factors


def test_factors_counts_and_timing():
    f = factors(GAME, 1)
    assert f["archers_pre_castle"] == 7.0                 # 3 + 4 (skirmishers excluded)
    assert f["feudal_s"] == 600.0 and f["castle_s"] == 1200.0
    assert f["reached_castle"] == 1.0
    assert f["feudal_to_castle_s"] == 600.0
    assert f["first_archer_s"] == 700.0
    assert f["first_archer_after_feudal_s"] == 100.0
    assert f["archers_within_3min_of_feudal"] == 7.0      # both queues within 600+180=780
    assert f["fletching_pre_castle"] == 1.0
    assert f["fletching_after_feudal_s"] == 180.0         # 780 - 600


def test_factors_commit_to_castle_none_when_under_ten_archers():
    # only 7 archers (<10) -> commit_to_castle_s undefined
    assert factors(GAME, 1)["commit_to_castle_s"] is None


def test_factors_commit_to_castle_when_committed():
    game = {
        "players": [{"player_number": 1, "feudal_s": 600, "castle_s": 1400, "eapm": 90}],
        "techs": [{"player_number": 1, "tech": "Fletching", "click_s": 800}],
        "events": [
            {"player_number": 1, "category": "archer_line", "name": "Archer", "amount": 6, "t_s": 700},
            {"player_number": 1, "category": "archer_line", "name": "Archer", "amount": 6, "t_s": 900},
        ],
    }
    f = factors(game, 1)
    assert f["archers_pre_castle"] == 12.0
    # 10th archer reached at the 900 queue; commit = max(900, fletch 800) = 900; 1400-900 = 500
    assert f["commit_to_castle_s"] == 500.0


def test_factors_fletching_after_castle_does_not_count():
    game = {
        "players": [{"player_number": 1, "feudal_s": 600, "castle_s": 900, "eapm": 50}],
        "techs": [{"player_number": 1, "tech": "Fletching", "click_s": 1000}],   # after castle
        "events": [{"player_number": 1, "category": "archer_line", "name": "Archer",
                    "amount": 3, "t_s": 700}],
    }
    f = factors(game, 1)
    assert f["fletching_pre_castle"] == 0.0
    assert f["fletching_after_feudal_s"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_classifications_archer_rush.py -v`
Expected: FAIL with `ImportError: cannot import name 'factors'`

- [ ] **Step 3: Add the implementation**

Append to `utils/classifications/defs/archer_rush.py`:

```python
def _f(x):
    return float(x) if x is not None else None


def _diff(a, b):
    return (a - b) if (a is not None and b is not None) else None


def factors(game, pnum):
    """Execution-quality factors for a matched archer-rush player-game. All values are
    floats or None (None = the factor didn't apply, e.g. never reached Castle)."""
    p = gd.player(game, pnum)
    feudal_s = p.get("feudal_s")
    castle_s = p.get("castle_s")
    evs = [e for e in gd.archer_queue_events(game, pnum) if _before_castle(e["t_s"], castle_s)]

    archers_pre_castle = sum((e.get("amount") or 1) for e in evs)
    first_archer_s = evs[0]["t_s"] if evs else None
    within = sum((e.get("amount") or 1) for e in evs
                 if feudal_s is not None and e["t_s"] <= feudal_s + W_SECONDS)

    fl = gd.tech_click_s(game, pnum, "Fletching")
    fletch_pre_castle = _before_castle(fl, castle_s)
    fletch_s = fl if fletch_pre_castle else None

    tenth_s, cum = None, 0
    for e in evs:
        cum += (e.get("amount") or 1)
        if cum >= COMMIT_ARCHERS:
            tenth_s = e["t_s"]
            break
    commit_to_castle_s = None
    if tenth_s is not None and fletch_s is not None and castle_s is not None:
        commit_s = max(tenth_s, fletch_s)
        if castle_s > commit_s:
            commit_to_castle_s = castle_s - commit_s

    return {
        "archers_pre_castle": float(archers_pre_castle),
        "feudal_s": _f(feudal_s),
        "castle_s": _f(castle_s),
        "reached_castle": 1.0 if castle_s is not None else 0.0,
        "feudal_to_castle_s": _f(_diff(castle_s, feudal_s)),
        "first_archer_s": _f(first_archer_s),
        "first_archer_after_feudal_s": _f(_diff(first_archer_s, feudal_s)),
        "archers_within_3min_of_feudal": float(within),
        "fletching_pre_castle": 1.0 if fletch_pre_castle else 0.0,
        "fletching_after_feudal_s": _f(_diff(fletch_s, feudal_s)),
        "commit_to_castle_s": _f(commit_to_castle_s),
        "eapm": _f(p.get("eapm")),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_classifications_archer_rush.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add utils/classifications/defs/archer_rush.py tests/test_classifications_archer_rush.py
git commit -m "feat(classifications): archer-rush execution factors (timing, count, fletching, commit-to-castle)"
```

---

### Task 5: Register archer rush + requirements ledger (`registry.py`)

**Files:**
- Modify: `utils/classifications/defs/archer_rush.py` (add module-level `CLASSIFICATION`)
- Create: `utils/classifications/registry.py`
- Test: `tests/test_classifications_contract.py` (add registry tests)

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_classifications_contract.py`:

```python
from utils.classifications.registry import REGISTRY


def test_registry_contains_archer_rush():
    assert "archer_rush" in REGISTRY
    c = REGISTRY["archer_rush"]
    assert c.title == "Archer Rush"
    assert callable(c.trigger) and callable(c.factors)


def test_archer_rush_requirements_all_available():
    c = REGISTRY["archer_rush"]
    assert c.requirements, "archer_rush must declare its data requirements"
    assert all(r["status"] == "available" for r in c.requirements)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_classifications_contract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'utils.classifications.registry'`

- [ ] **Step 3: Add the module-level Classification to archer_rush.py**

Append to `utils/classifications/defs/archer_rush.py`:

```python
from utils.classifications.contract import Classification, req   # noqa: E402

CLASSIFICATION = Classification(
    key="archer_rush",
    title="Archer Rush",
    version=1,
    trigger_spec="Queued >=1 foot Archer (archer line; NOT skirmisher) before the Castle-age click.",
    requirements=[
        req("foot_archer_queue_events", source="extract.events[category=archer_line]",
            status="available", note="per-queue timestamps; emitted by extract.py:147"),
        req("feudal_click_s", source="extract.players.feudal_s", status="available"),
        req("castle_click_s", source="extract.players.castle_s", status="available"),
        req("fletching_click_s", source="extract.techs[Fletching].click_s", status="available"),
        req("winner", source="extract.players.winner", status="available"),
        req("eapm", source="extract.players.eapm", status="available"),
    ],
    trigger=trigger,
    factors=factors,
)
```

- [ ] **Step 4: Create the registry**

Create `utils/classifications/registry.py`:

```python
"""Collects every classification module under defs/ into REGISTRY (key -> Classification).
Add a new classification by importing its module here and appending its CLASSIFICATION."""
from utils.classifications.defs import archer_rush

_ALL = [
    archer_rush.CLASSIFICATION,
]

REGISTRY = {c.key: c for c in _ALL}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_classifications_contract.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add utils/classifications/defs/archer_rush.py utils/classifications/registry.py tests/test_classifications_contract.py
git commit -m "feat(classifications): register archer_rush + its data-requirements ledger"
```

---

### Task 6: `cls_*` schema (offline raw SQL + bot ensure_table)

**Files:**
- Create: `utils/classifications/schema.py`
- Create: `bot/classifications/__init__.py`

No new unit test (schema is exercised by the runner in Task 8 and the bot at startup); the column sets in both files MUST match — they are listed side by side here.

- [ ] **Step 1: Create the offline raw-SQL schema**

Create `utils/classifications/schema.py`:

```python
"""Raw CREATE TABLE IF NOT EXISTS for the cls_* tables, used by the offline runner (which
connects via aiomysql, not the bot adapter). The bot mirrors these exact columns via
db.ensure_table in bot/classifications/__init__.py — keep the two in sync."""

CLS_TABLES = [
    """CREATE TABLE IF NOT EXISTS cls_classifications (
        `key` VARCHAR(191) NOT NULL,
        title VARCHAR(191),
        description VARCHAR(2000),
        trigger_spec VARCHAR(2000),
        version BIGINT,
        status VARCHAR(191),
        updated_at BIGINT,
        PRIMARY KEY (`key`)
    )""",
    """CREATE TABLE IF NOT EXISTS cls_data_requirements (
        `key` VARCHAR(191) NOT NULL,
        `field` VARCHAR(191) NOT NULL,
        source VARCHAR(191),
        status VARCHAR(191),
        note VARCHAR(2000),
        PRIMARY KEY (`key`, `field`)
    )""",
    """CREATE TABLE IF NOT EXISTS cls_results (
        `key` VARCHAR(191) NOT NULL,
        aoe2_match_id BIGINT NOT NULL,
        player_number BIGINT NOT NULL,
        profile_id BIGINT,
        identity VARCHAR(191),
        civ VARCHAR(191),
        team VARCHAR(191),
        winner TINYINT(1),
        played_at BIGINT,
        PRIMARY KEY (`key`, aoe2_match_id, player_number),
        INDEX cls_results_window (`key`, played_at),
        INDEX cls_results_profile (`key`, profile_id)
    )""",
    """CREATE TABLE IF NOT EXISTS cls_result_metrics (
        `key` VARCHAR(191) NOT NULL,
        aoe2_match_id BIGINT NOT NULL,
        player_number BIGINT NOT NULL,
        metric VARCHAR(191) NOT NULL,
        value FLOAT,
        PRIMARY KEY (`key`, aoe2_match_id, player_number, metric),
        INDEX cls_metrics_metric (`key`, metric)
    )""",
]
```

- [ ] **Step 2: Create the bot-side schema declaration**

Create `bot/classifications/__init__.py` (mirrors `bot/replay_stats/__init__.py`; tabs):

```python
# -*- coding: utf-8 -*-
"""Read-side of the classification framework: the cls_* tables (written offline by
utils/classifications/runner.py) are declared here via ensure_table so the bot can read them
for /classification. Columns mirror utils/classifications/schema.py exactly."""
from core.database import db

db.ensure_table(dict(
	tname="cls_classifications",
	columns=[
		dict(cname="key", ctype=db.types.str),
		dict(cname="title", ctype=db.types.str, notnull=False),
		dict(cname="description", ctype=db.types.text, notnull=False),
		dict(cname="trigger_spec", ctype=db.types.text, notnull=False),
		dict(cname="version", ctype=db.types.int, notnull=False),
		dict(cname="status", ctype=db.types.str, notnull=False),
		dict(cname="updated_at", ctype=db.types.int, notnull=False),
	],
	primary_keys=["key"],
))

db.ensure_table(dict(
	tname="cls_data_requirements",
	columns=[
		dict(cname="key", ctype=db.types.str),
		dict(cname="field", ctype=db.types.str),
		dict(cname="source", ctype=db.types.str, notnull=False),
		dict(cname="status", ctype=db.types.str, notnull=False),
		dict(cname="note", ctype=db.types.text, notnull=False),
	],
	primary_keys=["key", "field"],
))

db.ensure_table(dict(
	tname="cls_results",
	columns=[
		dict(cname="key", ctype=db.types.str),
		dict(cname="aoe2_match_id", ctype=db.types.int),
		dict(cname="player_number", ctype=db.types.int),
		dict(cname="profile_id", ctype=db.types.int, notnull=False),
		dict(cname="identity", ctype=db.types.str, notnull=False),
		dict(cname="civ", ctype=db.types.str, notnull=False),
		dict(cname="team", ctype=db.types.str, notnull=False),
		dict(cname="winner", ctype=db.types.bool, notnull=False),
		dict(cname="played_at", ctype=db.types.int, notnull=False),
	],
	primary_keys=["key", "aoe2_match_id", "player_number"],
))

db.ensure_table(dict(
	tname="cls_result_metrics",
	columns=[
		dict(cname="key", ctype=db.types.str),
		dict(cname="aoe2_match_id", ctype=db.types.int),
		dict(cname="player_number", ctype=db.types.int),
		dict(cname="metric", ctype=db.types.str),
		dict(cname="value", ctype=db.types.float, notnull=False),
	],
	primary_keys=["key", "aoe2_match_id", "player_number", "metric"],
))
```

- [ ] **Step 3: Verify both files import cleanly (no DB needed for the offline one)**

Run: `python -c "import utils.classifications.schema as s; print(len(s.CLS_TABLES))"`
Expected: prints `4`

- [ ] **Step 4: Commit**

```bash
git add utils/classifications/schema.py bot/classifications/__init__.py
git commit -m "feat(classifications): cls_* schema (offline raw SQL + bot ensure_table mirror)"
```

---

### Task 7: Pure row builders (`shape.py`)

**Files:**
- Create: `utils/classifications/shape.py`
- Test: `tests/test_classifications_shape.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_classifications_shape.py`:

```python
import utils.classifications.shape as shape


def test_result_row():
    player = {"player_number": 1, "profile_id": 111, "identity": "Alice", "civ": "Mayans",
              "team": "1", "winner": True}
    row = shape.result_row("archer_rush", 999, player, played_at=1700000000)
    assert row == {"key": "archer_rush", "aoe2_match_id": 999, "player_number": 1,
                   "profile_id": 111, "identity": "Alice", "civ": "Mayans", "team": "1",
                   "winner": 1, "played_at": 1700000000}


def test_result_row_winner_none_stays_none():
    player = {"player_number": 2, "profile_id": 222, "identity": "Bob", "civ": "Franks",
              "team": "2", "winner": None}
    assert shape.result_row("archer_rush", 999, player, played_at=1)["winner"] is None


def test_metric_rows_skips_none_values():
    factors = {"archers_pre_castle": 12.0, "commit_to_castle_s": None, "reached_castle": 1.0}
    rows = shape.metric_rows("archer_rush", 999, 1, factors)
    by_metric = {r["metric"]: r["value"] for r in rows}
    assert by_metric == {"archers_pre_castle": 12.0, "reached_castle": 1.0}   # None dropped
    assert all(r["key"] == "archer_rush" and r["aoe2_match_id"] == 999 and r["player_number"] == 1
               for r in rows)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_classifications_shape.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'utils.classifications.shape'`

- [ ] **Step 3: Write minimal implementation**

Create `utils/classifications/shape.py`:

```python
"""Pure transforms: a matched player + its factors dict -> cls_results / cls_result_metrics
row dicts. No DB. None-valued metrics are dropped (a missing row = the factor didn't apply)."""


def result_row(key, aoe2_match_id, player, played_at):
    winner = player.get("winner")
    return {
        "key": key,
        "aoe2_match_id": aoe2_match_id,
        "player_number": player["player_number"],
        "profile_id": player.get("profile_id"),
        "identity": player.get("identity"),
        "civ": player.get("civ"),
        "team": str(player.get("team")) if player.get("team") is not None else None,
        "winner": None if winner is None else (1 if winner else 0),
        "played_at": played_at,
    }


def metric_rows(key, aoe2_match_id, player_number, factors):
    rows = []
    for metric, value in factors.items():
        if value is None:
            continue
        rows.append({"key": key, "aoe2_match_id": aoe2_match_id,
                     "player_number": player_number, "metric": metric, "value": float(value)})
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_classifications_shape.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add utils/classifications/shape.py tests/test_classifications_shape.py
git commit -m "feat(classifications): pure row builders for cls_results + cls_result_metrics"
```

---

### Task 8: Offline DB I/O + runner CLI

**Files:**
- Create: `utils/classifications/dbio.py`
- Create: `utils/classifications/runner.py`

This task is I/O-bound (MySQL + replay parsing); it is validated by a real run against the corpus, not a unit test. Keep functions small.

- [ ] **Step 1: Create the async DB layer**

Create `utils/classifications/dbio.py`:

```python
"""Async DB layer for the OFFLINE runner. Uses a raw aiomysql pool (utils/db_helpers), not the
bot adapter. Idempotent: a re-run of a window overwrites a match's rows for a classification."""
import time

from utils.classifications.schema import CLS_TABLES


async def _exec(pool, sql, args=None):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args or [])
            return cur


async def ensure_tables(pool):
    for ddl in CLS_TABLES:
        await _exec(pool, ddl)


async def window_matches(pool, days):
    """aoe2_match_id + played_at (epoch) for completed games in the last `days`, newest-first,
    deduped (qc_match_civs has ~8 rows per match). Same source as the live ingest find query."""
    since = int(time.time()) - days * 86400
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT mc.aoe2_match_id AS aoe2_match_id, MAX(m.at) AS played_at "
                "FROM qc_match_civs mc JOIN qc_matches m ON m.match_id = mc.bot_match_id "
                "WHERE mc.aoe2_match_id IS NOT NULL AND m.at >= %s "
                "GROUP BY mc.aoe2_match_id ORDER BY played_at DESC", [since])
            return await cur.fetchall()   # list of dicts (DictCursor)


async def upsert_classification(pool, c):
    """Write the registry row + its data-requirements ledger for one Classification."""
    await _exec(pool,
        "REPLACE INTO cls_classifications (`key`, title, description, trigger_spec, version, "
        "status, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        [c.key, c.title, c.trigger_spec, c.trigger_spec, c.version, c.status, int(time.time())])
    await _exec(pool, "DELETE FROM cls_data_requirements WHERE `key`=%s", [c.key])
    for r in c.requirements:
        await _exec(pool,
            "INSERT INTO cls_data_requirements (`key`, `field`, source, status, note) "
            "VALUES (%s,%s,%s,%s,%s)", [c.key, r["field"], r["source"], r["status"], r["note"]])


async def upsert_results(pool, key, aoe2_match_id, result_rows, metric_rows):
    """Replace all rows for (key, aoe2_match_id): delete then insert. Idempotent re-ingest."""
    await _exec(pool, "DELETE FROM cls_results WHERE `key`=%s AND aoe2_match_id=%s",
                [key, aoe2_match_id])
    await _exec(pool, "DELETE FROM cls_result_metrics WHERE `key`=%s AND aoe2_match_id=%s",
                [key, aoe2_match_id])
    for row in result_rows:
        cols = list(row.keys())
        await _exec(pool,
            "INSERT INTO cls_results ({}) VALUES ({})".format(
                ", ".join("`{}`".format(c) for c in cols), ", ".join(["%s"] * len(cols))),
            [row[c] for c in cols])
    for row in metric_rows:
        cols = list(row.keys())
        await _exec(pool,
            "INSERT INTO cls_result_metrics ({}) VALUES ({})".format(
                ", ".join("`{}`".format(c) for c in cols), ", ".join(["%s"] * len(cols))),
            [row[c] for c in cols])
```

- [ ] **Step 2: Create the runner CLI**

Create `utils/classifications/runner.py`:

```python
#!/usr/bin/env python3
"""Offline classification runner. For a date window: list matches (MySQL) -> ensure each
replay is cached in data/replays/ (download if missing) -> parse once (cached) -> run every
registered classification -> upsert results to MySQL cls_* tables -> print a report.

Run from the repo root with the vendored mgz fork importable:
    PYTHONPATH=.replay_scratch python -m utils.classifications.runner --days 90

Replays are NEVER deleted (kept for ongoing analysis)."""
import argparse
import asyncio
import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, ".replay_scratch"))   # vendored mgz fork

from utils.db_helpers import create_pool                      # noqa: E402
from utils.classifications import dbio, shape                 # noqa: E402
from utils.classifications.registry import REGISTRY           # noqa: E402

CACHE_DIR = os.path.join(_ROOT, "data", ".replay_extract_cache")
REPLAY_DIR = os.path.join(_ROOT, "data", "replays")
EXTRACT_VERSION = "v1"     # bump to invalidate the parse cache when extract output changes


def _cache_path(aoe2_match_id):
    return os.path.join(CACHE_DIR, "{}.{}.json".format(aoe2_match_id, EXTRACT_VERSION))


async def _ensure_replay(aoe2_match_id):
    """Return a path to the cached .aoe2record, downloading if absent. None if unavailable."""
    path = os.path.join(REPLAY_DIR, "{}.aoe2record".format(aoe2_match_id))
    if os.path.exists(path):
        return path
    from utils.replay_quiz import download as dl
    pids = await dl.resolve_profile_ids(aoe2_match_id)
    for pid in pids:
        got, status = await dl.download_replay(aoe2_match_id, pid)
        if got and os.path.exists(got):
            return got
    return None


def _extract_cached(path, aoe2_match_id, resolved, date_map):
    """Parse once; cache the JSON-serializable extract output keyed by id + EXTRACT_VERSION."""
    cp = _cache_path(aoe2_match_id)
    if os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            return json.load(f)
    from utils.replay_quiz.extract import extract_match
    data = extract_match(path, resolved, date_map)
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


async def run(days, only_key=None, no_download=False):
    pool = await create_pool()
    if pool is None:
        print("No DB pool (check config.cfg DB_URI).", file=sys.stderr)
        return 1
    from utils.replay_quiz.extract import load_resolved, load_date_map
    resolved, date_map = load_resolved(), load_date_map()
    classifications = [c for c in REGISTRY.values()
                       if only_key is None or c.key == only_key]

    try:
        await dbio.ensure_tables(pool)
        for c in classifications:
            await dbio.upsert_classification(pool, c)

        matches = await dbio.window_matches(pool, days)
        print("window: {} matches in last {}d across {} classification(s)".format(
            len(matches), days, len(classifications)))
        stats = {c.key: 0 for c in classifications}
        scanned = fetched = failed = 0

        for m in matches:
            mid = m["aoe2_match_id"]
            played_at = m["played_at"]
            path = None if no_download else await _ensure_replay(mid)
            if not path:
                failed += 1
                continue
            try:
                game = _extract_cached(path, mid, resolved, date_map)
            except Exception as e:                       # corrupt/unsupported replay -> skip
                failed += 1
                print("  parse failed {}: {}".format(mid, e))
                continue
            scanned += 1
            for c in classifications:
                result_rows, metric_rows = [], []
                for p in game.get("players", []):
                    pnum = p["player_number"]
                    if not c.trigger(game, pnum):
                        continue
                    result_rows.append(shape.result_row(c.key, mid, p, played_at))
                    metric_rows.extend(shape.metric_rows(c.key, mid, pnum, c.factors(game, pnum)))
                if result_rows:
                    await dbio.upsert_results(pool, c.key, mid, result_rows, metric_rows)
                    stats[c.key] += len(result_rows)

        print("scanned={} fetched/cached_ok={} failed/unavailable={}".format(
            scanned, scanned - fetched, failed))
        for k, n in stats.items():
            print("  {}: {} matched player-games".format(k, n))
        return 0
    finally:
        pool.close()
        await pool.wait_closed()


def main():
    ap = argparse.ArgumentParser(description="Offline classification runner")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--key", default=None, help="run only this classification key")
    ap.add_argument("--no-download", action="store_true",
                    help="only use replays already cached in data/replays/")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.days, args.key, args.no_download)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Syntax-check both modules (no DB/mgz needed for import of dbio/shape)**

Run: `python -c "import utils.classifications.dbio, utils.classifications.shape; print('ok')"`
Expected: prints `ok`

- [ ] **Step 4: Smoke-test the runner against cached replays only (small window, no network)**

Run: `PYTHONPATH=.replay_scratch python -m utils.classifications.runner --days 7 --key archer_rush --no-download`
Expected: prints a `window: N matches ...` line and an `archer_rush: M matched player-games` line with no traceback. (If `data/replays/` has no games in the last 7 days, widen with `--days 365`.) Then spot-check in MySQL: `SELECT COUNT(*) FROM cls_results WHERE key='archer_rush';` is > 0.

- [ ] **Step 5: Commit**

```bash
git add utils/classifications/dbio.py utils/classifications/runner.py
git commit -m "feat(classifications): offline DB I/O + runner CLI (corpus cache, parse cache, idempotent push)"
```

---

### Task 9: Bot read path — `summarize()` + DB fetch (`query.py`)

**Files:**
- Create: `bot/classifications/query.py`
- Test: `tests/test_classifications_query.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_classifications_query.py`:

```python
from bot.classifications.query import summarize


def _g(identity, profile_id, winner, archers, fletch):
    return {"identity": identity, "profile_id": profile_id, "winner": winner,
            "archers_pre_castle": archers, "fletching_pre_castle": fletch}


GAMES = [
    _g("Alice", 111, True, 17, 1.0),
    _g("Alice", 111, False, 4, 0.0),
    _g("Bob", 222, True, 12, 1.0),
    _g("Bob", 222, None, 20, 1.0),    # unknown result -> excluded from win rate
]


def test_summarize_counts_and_overall_winrate():
    s = summarize(GAMES)
    assert s["n_games"] == 4
    assert s["n_players"] == 2
    # known-result games: Alice W, Alice L, Bob W -> 2/3
    assert s["overall"] == {"wins": 2, "known": 3, "rate": round(2 / 3, 3)}


def test_summarize_winrate_by_fletching():
    s = summarize(GAMES)
    fl = s["by_fletching"]
    # with fletching: Alice(W), Bob(W), Bob(None->excluded) -> 2/2 ; without: Alice(L) -> 0/1
    assert fl["with"] == {"wins": 2, "known": 2, "rate": 1.0}
    assert fl["without"] == {"wins": 0, "known": 1, "rate": 0.0}


def test_summarize_top_players():
    s = summarize(GAMES)
    top = {p["identity"]: p for p in s["top_players"]}
    assert top["Alice"]["games"] == 2 and top["Bob"]["games"] == 2
    assert top["Alice"]["wins"] == 1 and top["Alice"]["known"] == 2     # rate 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_classifications_query.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bot.classifications.query'`

- [ ] **Step 3: Write minimal implementation**

Create `bot/classifications/query.py` (tabs; `summarize` is pure, `fetch_games` hits the DB):

```python
# -*- coding: utf-8 -*-
"""Read aggregations for /classification. summarize() is pure (unit-tested); fetch_games()
pulls a classification's matched player-games (with the two metrics summarize needs) from
cls_results + cls_result_metrics over a date window."""
import time

from core.database import db

_BUCKETS = [(1, 3, "1-3"), (4, 10, "4-10"), (11, 20, "11-20"), (21, 10 ** 9, "21+")]


def _winrate(games):
    known = [g for g in games if g.get("winner") in (0, 1, True, False) and g.get("winner") is not None]
    wins = sum(1 for g in known if g["winner"]) if known else 0
    return {"wins": wins, "known": len(known), "rate": round(wins / len(known), 3) if known else 0.0}


def summarize(games):
    """games: list of dicts {identity, profile_id, winner(bool|None), archers_pre_castle(float),
    fletching_pre_castle(float 0/1)}. Returns the report structure the command renders."""
    by_player = {}
    for g in games:
        p = by_player.setdefault(g["profile_id"], {"identity": g["identity"], "rows": []})
        p["rows"].append(g)

    top = []
    for pid, p in by_player.items():
        wr = _winrate(p["rows"])
        top.append({"identity": p["identity"], "profile_id": pid, "games": len(p["rows"]),
                    "wins": wr["wins"], "known": wr["known"], "rate": wr["rate"]})
    top.sort(key=lambda t: (-t["games"], t["identity"]))

    by_commit = []
    for lo, hi, label in _BUCKETS:
        sub = [g for g in games if lo <= (g.get("archers_pre_castle") or 0) <= hi]
        if sub:
            wr = _winrate(sub)
            by_commit.append({"bucket": label, "games": len(sub), **wr})

    with_f = [g for g in games if (g.get("fletching_pre_castle") or 0) >= 1]
    without_f = [g for g in games if (g.get("fletching_pre_castle") or 0) < 1]

    return {
        "n_games": len(games),
        "n_players": len(by_player),
        "overall": _winrate(games),
        "by_commit": by_commit,
        "by_fletching": {"with": _winrate(with_f), "without": _winrate(without_f)},
        "top_players": top[:10],
    }


async def fetch_games(key, days, profile_ids=None):
    """Matched player-games for `key` in the last `days`, with the archers_pre_castle and
    fletching_pre_castle metrics joined in. profile_ids: optional filter (a single player)."""
    since = int(time.time()) - days * 86400
    args = [key, since]
    pid_clause = ""
    if profile_ids:
        pid_clause = " AND r.profile_id IN ({})".format(", ".join(["%s"] * len(profile_ids)))
        args.extend(profile_ids)
    rows = await db.fetchall(
        "SELECT r.aoe2_match_id, r.player_number, r.profile_id, r.identity, r.winner, "
        "MAX(CASE WHEN m.metric='archers_pre_castle' THEN m.value END) AS archers_pre_castle, "
        "MAX(CASE WHEN m.metric='fletching_pre_castle' THEN m.value END) AS fletching_pre_castle "
        "FROM cls_results r LEFT JOIN cls_result_metrics m "
        "ON m.`key`=r.`key` AND m.aoe2_match_id=r.aoe2_match_id AND m.player_number=r.player_number "
        "WHERE r.`key`=%s AND r.played_at >= %s" + pid_clause +
        " GROUP BY r.aoe2_match_id, r.player_number, r.profile_id, r.identity, r.winner", args)
    return [dict(r) for r in (rows or [])]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_classifications_query.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add bot/classifications/query.py tests/test_classifications_query.py
git commit -m "feat(classifications): bot read path - pure summarize() + cls_* fetch"
```

---

### Task 10: `/classification` command + wiring + final validation

**Files:**
- Create: `bot/commands/classification.py`
- Modify: `bot/commands/__init__.py`
- Modify: `bot/__init__.py`
- Modify: `bot/context/slash/commands.py`

- [ ] **Step 1: Create the command handler**

Create `bot/commands/classification.py` (tabs; mirrors `bot/commands/player_details.py`):

```python
# -*- coding: utf-8 -*-
"""/classification <key> [days] [player]: who used a play-style classification (e.g. archer_rush)
in the last N days and whether it won. Reads cls_* via bot.classifications.query."""
__all__ = ["classification"]

from nextcord import Member, Embed

from core.database import db

import bot


def _wr(d):
	return "{}/{} ({:.0%})".format(d["wins"], d["known"], d["rate"]) if d["known"] else "n/a"


async def classification(ctx, key: str = "archer_rush", days: int = 90, player: Member = None):
	from bot.classifications import query

	try:
		days = max(1, min(int(days), 365))
	except (TypeError, ValueError):
		days = 90

	interaction = getattr(ctx, "interaction", None)
	if interaction is not None and not interaction.response.is_done():
		await interaction.response.defer()

	reg = await db.select_one(["*"], "cls_classifications", {"key": key})
	if not reg:
		return ctx.error("Unknown classification '{}'.".format(key), title="Classification")
	title = reg.get("title") or key

	profile_ids = None
	if player:
		target = await ctx.get_member(player)
		if not target:
			raise bot.Exc.NotFoundError(ctx.qc.gt("Specified user not found."))
		profile_ids = await query.resolve_profile_ids(target.id) if hasattr(query, "resolve_profile_ids") else None

	games = await query.fetch_games(key, days, profile_ids=profile_ids)
	if not games:
		return ctx.error("No {} games found in the last {} days.".format(title, days),
		                 title=title)

	s = query.summarize(games)
	embed = Embed(title="{} - last {} days".format(title, days))
	embed.add_field(name="Games / players", value="{} games, {} players".format(
		s["n_games"], s["n_players"]), inline=False)
	embed.add_field(name="Win rate (overall)", value=_wr(s["overall"]), inline=False)
	embed.add_field(name="By commitment (archers before Castle)",
	                value="\n".join("{}: {} games, {}".format(b["bucket"], b["games"], _wr(b))
	                                for b in s["by_commit"]) or "n/a", inline=False)
	embed.add_field(name="Fletching before Castle",
	                value="with: {}\nwithout: {}".format(_wr(s["by_fletching"]["with"]),
	                                                     _wr(s["by_fletching"]["without"])),
	                inline=False)
	embed.add_field(name="Top players",
	                value="\n".join("{} - {} games, {}/{} ({:.0%})".format(
		                t["identity"], t["games"], t["wins"], t["known"],
		                (t["wins"] / t["known"]) if t["known"] else 0) for t in s["top_players"]) or "n/a",
	                inline=False)
	await ctx.reply(embed=embed)
```

Note: `query.resolve_profile_ids` already exists in `bot/replay_stats/query.py`; the `hasattr`
guard keeps this command working even though the read module is `bot.classifications.query` — in
Step 2 we re-export it so `player:` filtering works.

- [ ] **Step 2: Re-export `resolve_profile_ids` for the player filter**

Append to `bot/classifications/query.py`:

```python
async def resolve_profile_ids(user_id):
	"""Reuse the replay-stats resolver: discord user_id -> the AoE2 profile_ids linked to it."""
	from bot.replay_stats import query as rs_query
	return await rs_query.resolve_profile_ids(user_id)
```

- [ ] **Step 3: Register the command in the command package**

In `bot/commands/__init__.py`, find the line importing the player_details command (a line like
`from .player_details import *`) and add directly after it:

```python
from .classification import *
```

- [ ] **Step 4: Ensure cls_* tables on bot startup**

In `bot/__init__.py`, find where `bot.replay_stats` is imported (search for `replay_stats`) and
add directly after that import:

```python
from . import classifications  # noqa: F401  (cls_* ensure_table side effect)
```

- [ ] **Step 5: Register the slash command**

In `bot/context/slash/commands.py`, add a new command block next to the other `@dc.slash_command`
handlers (e.g. after the `lobby2` block around line 551). Use the exact pattern below:

```python
@dc.slash_command(
	name='classification',
	description='Show who used a play-style (e.g. archer_rush) recently and whether it won.',
	**guild_kwargs
)
async def _classification(
		interaction: Interaction,
		key: str = SlashOption(name="key", description="Classification key", required=False, default="archer_rush"),
		days: int = SlashOption(name="days", description="Lookback window in days (default 90)", required=False, default=90),
		player: Member = SlashOption(name="player", description="Filter to one player", required=False, default=None, verify=False),
): await run_slash(bot.commands.classification, interaction=interaction, key=key, days=days, player=player)
```

- [ ] **Step 6: Run the full test suite + lint**

Run: `python -m pytest tests/ -q`
Expected: PASS (all tests, including the 5 new `test_classifications_*` files).

Run: `ruff check .`
Expected: no errors. (Fix any reported issues — note `utils/` is 4-space, `bot/` is tabs.)

- [ ] **Step 7: Full validation run + manual command check**

Run: `PYTHONPATH=.replay_scratch python -m utils.classifications.runner --days 90 --key archer_rush`
Expected: a window line, then `archer_rush: M matched player-games` (M roughly on the order of the
267-game calibration population if the 90-day corpus overlaps it; smaller windows yield fewer).
Then in MySQL confirm rows exist:
`SELECT COUNT(*) FROM cls_results WHERE key='archer_rush';` and
`SELECT metric, COUNT(*) FROM cls_result_metrics WHERE key='archer_rush' GROUP BY metric;`
Then in Discord run `/classification key:archer_rush days:90` and confirm an embed with overall
win rate, the commitment buckets, the Fletching split, and a top-players list.

- [ ] **Step 8: Commit**

```bash
git add bot/commands/classification.py bot/commands/__init__.py bot/__init__.py bot/context/slash/commands.py bot/classifications/query.py
git commit -m "feat(classifications): /classification slash command + wiring"
```

---

## Self-Review

**Spec coverage:**
- Framework (trigger + factors + data-requirements) → Tasks 2–5. ✓
- `cls_*` tables (registry, requirements, results, generic long-form metrics) → Task 6. ✓
- Archer-rush trigger (≥1 foot archer before Castle click, skirmishers excluded) → Task 3. ✓
- Archer-rush factors incl. fine timing + `commit_to_castle_s` → Task 4. ✓
- Offline runner: window → corpus(cache+download, never delete) → parse-once cache → classify → idempotent push → report → Task 8. ✓
- Reuse `extract.py` / `download.py` unchanged → Task 8 (imports, no edits). ✓
- Good-vs-bad = win-rate by factor (commitment curve, Fletching split) → Tasks 9–10. ✓
- New `/classification` slash command reading `cls_*` → Task 10. ✓
- Data-requirements ledger demonstrated all-`available` for archer rush → Task 5. ✓
- Pure, DB-free, mgz-free testable logic → Tasks 1–5, 7, 9 (all unit-tested). ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code; I/O steps that can't be unit-tested have explicit run commands + expected output. ✓

**Type consistency:** `Classification` fields (Task 2) match construction in Task 5. `result_row`/`metric_rows` keys (Task 7) match the `cls_results`/`cls_result_metrics` columns (Task 6) and the runner's calls (Task 8). `summarize()` input keys (Task 9 test) match `fetch_games()` output columns (Task 9) and the command's use (Task 10). Metric names in `factors()` (Task 4) match those `fetch_games()` pivots (`archers_pre_castle`, `fletching_pre_castle`). ✓

**Note for the executor:** Steps 3–5 of Task 10 modify existing files by *locating an anchor line* (not a fixed line number) — read the file first and place the snippet at the described anchor. The `cls_results.played_at` column is an **epoch int** (matches `qc_matches.at`), not the `rs_matches.played_at` date string — the window query and `fetch_games` both rely on this.
