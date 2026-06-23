# Live Replay-Stats Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an in-bot, self-updating pipeline that fetches each completed game's replay, parses in-game metrics, and stores them in MySQL — backfilled for the last 90 days, newest-first — so a future `/player_details` command can read them.

**Architecture:** A new `bot/replay_stats/` package (mirrors `bot/quiz/`) runs a self-isolating `think()` job on the existing 1s tick. Each sweep takes one not-yet-ingested `aoe2_match_id` (from `qc_match_civs`), downloads the replay (reusing `utils/replay_quiz/download.py` via `asyncio.to_thread`), parses it (reusing `utils/replay_quiz/extract.py` in a process pool), attributes players to Discord ids via a seeded `rs_profiles` map, and writes raw normalized rows to new `rs_*` MySQL tables. Pure logic (save-version gate, retry policy, row-shaping) lives in import-safe modules and is unit-tested; I/O is smoke-tested against `replay_quiz.db` ground truth.

**Tech Stack:** Python 3.11, nextcord, aiomysql (via `core.database.db`), the `sanduckhan/aoc-mgz` fork + `aocref` (replay parsing), `requests` (sync HTTP, run off-thread). Spec: `docs/superpowers/specs/2026-06-22-live-replay-stats-pipeline-design.md`.

**Conventions:** This package uses **4-space indentation** (like `bot/civ_stats.py` / `utils/`), not tabs. Run a single test with `pytest tests/<file>::<test> -v`. Full suite: `pytest tests/ -v`. Lint: `ruff check .`.

---

## File structure

**Create:**
- `bot/replay_stats/__init__.py` — `rs_*` + `rs_config` table declarations; `PARSER_VERSION`; exposes `jobs`.
- `bot/replay_stats/policy.py` — pure: save-version gate + retry/backoff decisions. **Unit-tested.**
- `bot/replay_stats/shape.py` — pure: turn `extract_match()` output into `rs_*` row dicts (denormalize `profile_id`, attribute `user_id`). **Unit-tested.**
- `bot/replay_stats/store.py` — async DB reads/writes (find-next, idempotent write, ingest status, profile seeding).
- `bot/replay_stats/fetch.py` — async wrappers over `download.py` (`to_thread`).
- `bot/replay_stats/parse.py` — save-version gate + process-pool `extract_match`.
- `bot/replay_stats/jobs.py` — the `ReplayStatsJobs.think()` loop + `jobs` singleton.
- `bot/replay_stats/backfill.py` — resumable, newest-first 90-day backfill (callable + `__main__`).
- `bot/commands/replay_stats.py` — `/replaystats` admin handlers.
- `tests/test_replay_stats_policy.py`, `tests/test_replay_stats_shape.py` — unit tests.
- `tests/test_replay_stats_parity.py` — offline smoke test vs `replay_quiz.db`.

**Modify:**
- `bot/__init__.py` — `from . import replay_stats`.
- `bot/events.py` — call `bot.replay_stats.jobs.think(frame_time)` in `on_think`.
- `bot/commands/__init__.py` — `from .replay_stats import *`.
- `bot/context/slash/groups.py` — `admin_replaystats` group.
- `bot/context/slash/commands.py` — `/replaystats` subcommands.
- `requirements.txt`, `Dockerfile` (no change needed; installs `requirements.txt`), `ruff.toml` (exclude offline module if linted).

---

## Task 1: Tables + package skeleton

**Files:**
- Create: `bot/replay_stats/__init__.py`
- Modify: `bot/__init__.py` (after the `from . import quiz` line)

- [ ] **Step 1: Create the package with table declarations**

Create `bot/replay_stats/__init__.py`:

```python
# -*- coding: utf-8 -*-
"""Live replay-stats subsystem — strictly additive, opt-in (off until rs_config.enabled=1).
Mirrors bot/quiz/ isolation: dedicated rs_* tables declared here via ensure_table at import,
imported by bot/__init__.py for that side effect and the ReplayStatsJobs singleton. Heavy
imports (mgz, requests) stay lazy inside fetch.py/parse.py so importing this package is
test-safe under the conftest stubs."""
from core.database import db

# Bumped whenever the mgz pin or SUPPORTED_SAVE_VERSIONS policy changes (see policy.py).
# Stored on every parsed match; a bump auto-reopens pending_parser_update rows.
PARSER_VERSION = "mgz-a1683d8+1"

db.ensure_table(dict(
    tname="rs_config",
    columns=[
        dict(cname="id", ctype=db.types.int),          # always 1 (single-row global config)
        dict(cname="enabled", ctype=db.types.bool, notnull=True, default=0),
    ],
    primary_keys=["id"],
))

db.ensure_table(dict(
    tname="rs_matches",
    columns=[
        dict(cname="aoe2_match_id", ctype=db.types.int),
        dict(cname="bot_match_id", ctype=db.types.int, notnull=False),
        dict(cname="map", ctype=db.types.str, notnull=False),
        dict(cname="save_version", ctype=db.types.float, notnull=False),
        dict(cname="duration_s", ctype=db.types.int, notnull=False),
        dict(cname="played_at", ctype=db.types.str, notnull=False),   # date string from extract
        dict(cname="replay_url", ctype=db.types.str, notnull=False),
        dict(cname="parsed_at", ctype=db.types.int, notnull=False),
        dict(cname="parser_version", ctype=db.types.str, notnull=False),
    ],
    primary_keys=["aoe2_match_id"],
))

db.ensure_table(dict(
    tname="rs_player_games",
    columns=[
        dict(cname="aoe2_match_id", ctype=db.types.int),
        dict(cname="profile_id", ctype=db.types.int),
        dict(cname="player_number", ctype=db.types.int),
        dict(cname="user_id", ctype=db.types.int, notnull=False),
        dict(cname="identity", ctype=db.types.str, notnull=False),
        dict(cname="attribution", ctype=db.types.str, notnull=False),
        dict(cname="civ", ctype=db.types.str, notnull=False),
        dict(cname="team", ctype=db.types.str, notnull=False),
        dict(cname="winner", ctype=db.types.bool, notnull=False),
        dict(cname="eapm", ctype=db.types.int, notnull=False),
        dict(cname="age_reliable", ctype=db.types.bool, notnull=False),
        dict(cname="tc_relocations", ctype=db.types.int, notnull=False),
        dict(cname="feudal_s", ctype=db.types.int, notnull=False),
        dict(cname="castle_s", ctype=db.types.int, notnull=False),
        dict(cname="imperial_s", ctype=db.types.int, notnull=False),
        dict(cname="first_tc_s", ctype=db.types.int, notnull=False),
        dict(cname="villagers", ctype=db.types.int, notnull=False),
        dict(cname="vil_pre_feudal", ctype=db.types.int, notnull=False),
        dict(cname="vil_pre_castle", ctype=db.types.int, notnull=False),
        dict(cname="vil_pre_imperial", ctype=db.types.int, notnull=False),
        dict(cname="military", ctype=db.types.int, notnull=False),
        dict(cname="mil_pre_feudal", ctype=db.types.int, notnull=False),
        dict(cname="mil_pre_castle", ctype=db.types.int, notnull=False),
        dict(cname="mil_pre_imperial", ctype=db.types.int, notnull=False),
    ],
    primary_keys=["aoe2_match_id", "profile_id"],
))

db.ensure_table(dict(
    tname="rs_player_units",
    columns=[
        dict(cname="aoe2_match_id", ctype=db.types.int),
        dict(cname="player_number", ctype=db.types.int),
        dict(cname="unit", ctype=db.types.str),
        dict(cname="profile_id", ctype=db.types.int, notnull=False),
        dict(cname="category", ctype=db.types.str, notnull=False),
        dict(cname="is_military", ctype=db.types.bool, notnull=False),
        dict(cname="total", ctype=db.types.int, notnull=False),
        dict(cname="pre_feudal", ctype=db.types.int, notnull=False),
        dict(cname="pre_castle", ctype=db.types.int, notnull=False),
        dict(cname="pre_imperial", ctype=db.types.int, notnull=False),
    ],
    primary_keys=["aoe2_match_id", "player_number", "unit"],
))

db.ensure_table(dict(
    tname="rs_player_techs",
    columns=[
        dict(cname="aoe2_match_id", ctype=db.types.int),
        dict(cname="player_number", ctype=db.types.int),
        dict(cname="tech", ctype=db.types.str),
        dict(cname="profile_id", ctype=db.types.int, notnull=False),
        dict(cname="click_s", ctype=db.types.int, notnull=False),
        dict(cname="phase", ctype=db.types.str, notnull=False),
    ],
    primary_keys=["aoe2_match_id", "player_number", "tech"],
))

db.ensure_table(dict(
    tname="rs_player_buildings",
    columns=[
        dict(cname="aoe2_match_id", ctype=db.types.int),
        dict(cname="player_number", ctype=db.types.int),
        dict(cname="building", ctype=db.types.str),
        dict(cname="profile_id", ctype=db.types.int, notnull=False),
        dict(cname="count", ctype=db.types.int, notnull=False),
    ],
    primary_keys=["aoe2_match_id", "player_number", "building"],
))

db.ensure_table(dict(
    tname="rs_ingest",
    columns=[
        dict(cname="aoe2_match_id", ctype=db.types.int),
        dict(cname="status", ctype=db.types.str, notnull=True),
        dict(cname="save_version", ctype=db.types.float, notnull=False),
        dict(cname="parser_version", ctype=db.types.str, notnull=False),
        dict(cname="attempts", ctype=db.types.int, notnull=True, default=0),
        dict(cname="first_seen_at", ctype=db.types.int, notnull=False),
        dict(cname="last_attempt_at", ctype=db.types.int, notnull=False),
        dict(cname="next_attempt_at", ctype=db.types.int, notnull=False),
        dict(cname="error_reason", ctype=db.types.str, notnull=False),
    ],
    primary_keys=["aoe2_match_id"],
))

db.ensure_table(dict(
    tname="rs_profiles",
    columns=[
        dict(cname="profile_id", ctype=db.types.int),
        dict(cname="user_id", ctype=db.types.int, notnull=False),
        dict(cname="name", ctype=db.types.str, notnull=False),
        dict(cname="last_seen_at", ctype=db.types.int, notnull=False),
    ],
    primary_keys=["profile_id"],
))

from .jobs import jobs  # noqa: E402,F401  (ReplayStatsJobs singleton)
```

- [ ] **Step 2: Create a stub jobs module so the import at the bottom resolves**

Create `bot/replay_stats/jobs.py` (filled in fully in Task 7):

```python
# -*- coding: utf-8 -*-
"""Replay-stats ingest job on the shared think() tick. Filled in Task 7."""


class ReplayStatsJobs:
    async def think(self, frame_time):
        return


jobs = ReplayStatsJobs()
```

- [ ] **Step 3: Register the package import**

In `bot/__init__.py`, immediately after the existing line `from . import quiz  # ...`, add:

```python
from . import replay_stats  # noqa: F401  (rs_* ensure_table + ReplayStatsJobs instance)
```

- [ ] **Step 4: Verify it imports without error**

Run: `python -c "import bot.replay_stats; print(bot.replay_stats.PARSER_VERSION)"`
Expected: prints `mgz-a1683d8+1` with no traceback. (Requires a reachable DB via `config.cfg`; if running where the DB is unreachable, instead run `ruff check bot/replay_stats/` and confirm no syntax errors.)

- [ ] **Step 5: Commit**

```bash
git add bot/replay_stats/__init__.py bot/replay_stats/jobs.py bot/__init__.py
git commit -m "feat(replay-stats): rs_* table schema + package skeleton"
```

---

## Task 2: `policy.py` — save-version gate + retry policy (pure, TDD)

**Files:**
- Create: `bot/replay_stats/policy.py`
- Test: `tests/test_replay_stats_policy.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_replay_stats_policy.py`:

```python
import bot.replay_stats.policy as p


def test_save_version_supported():
    assert p.save_version_supported(67.2) is True
    assert p.save_version_supported(66.6) is True
    assert p.save_version_supported(68.0) is False   # a future patch
    assert p.save_version_supported(None) is False


def test_unavailable_backoff_escalates_then_caps():
    assert p.unavailable_backoff(0) == 600
    assert p.unavailable_backoff(1) == 3600
    assert p.unavailable_backoff(2) == 21600
    assert p.unavailable_backoff(3) == 86400
    assert p.unavailable_backoff(99) == 86400   # caps at the last step


def test_should_give_up_unavailable_after_7_days():
    now = 1_000_000
    assert p.should_give_up_unavailable(now - 6 * 86400, now) is False
    assert p.should_give_up_unavailable(now - 7 * 86400, now) is True


def test_parse_failed_exhausted_at_3():
    assert p.parse_failed_exhausted(2) is False
    assert p.parse_failed_exhausted(3) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_replay_stats_policy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.replay_stats.policy'`.

- [ ] **Step 3: Write the implementation**

Create `bot/replay_stats/policy.py`:

```python
# -*- coding: utf-8 -*-
"""Pure decision logic for the replay-stats ingest job: which save versions the pinned
parser handles, and how to back off / give up on retries. No DB, no nextcord, no mgz —
unit-tested in isolation (tests/test_replay_stats_policy.py)."""

# The sanduckhan/aoc-mgz fork (pinned in requirements.txt) parses up to AoE2 DE save 67.x;
# base mgz handles older versions. Anything newer is an un-parseable future patch until the
# fork is bumped (then raise this and bump PARSER_VERSION in __init__.py).
MAX_SUPPORTED_SAVE = 67.99

# Backoff (seconds) for replays not yet on aoe.ms, by prior attempt count.
UNAVAILABLE_BACKOFF = [600, 3600, 21600, 86400]   # 10m, 1h, 6h, 24h
GIVE_UP_UNAVAILABLE_S = 7 * 86400                  # stop retrying a 404 after 7 days
MAX_PARSE_ATTEMPTS = 3                             # corrupt/parse error give-up threshold


def save_version_supported(v):
    """True iff the pinned parser can read this replay's save_version."""
    return v is not None and v <= MAX_SUPPORTED_SAVE


def unavailable_backoff(attempts):
    """Seconds to wait before the next 404 retry, escalating then capping."""
    idx = min(attempts, len(UNAVAILABLE_BACKOFF) - 1)
    return UNAVAILABLE_BACKOFF[idx]


def should_give_up_unavailable(first_seen_at, now):
    """True once a perpetually-404 match has been pending too long."""
    return (now - first_seen_at) >= GIVE_UP_UNAVAILABLE_S


def parse_failed_exhausted(attempts):
    """True once a supported-version replay has failed to parse too many times."""
    return attempts >= MAX_PARSE_ATTEMPTS
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_replay_stats_policy.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add bot/replay_stats/policy.py tests/test_replay_stats_policy.py
git commit -m "feat(replay-stats): save-version gate + retry policy (pure, tested)"
```

---

## Task 3: `shape.py` — extract output → rs_* rows (pure, TDD)

**Files:**
- Create: `bot/replay_stats/shape.py`
- Test: `tests/test_replay_stats_shape.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_replay_stats_shape.py`:

```python
import bot.replay_stats.shape as shape

# Minimal extract_match()-shaped fixture: 1 match, 2 players.
EXTRACTED = {
    "match": {"aoe2_match_id": 999, "map": "Arabia", "save_version": 67.2,
              "duration_s": 1500, "date": "2026-06-20 10:00", "winner_team": None},
    "players": [
        {"player_number": 1, "profile_id": 111, "identity": "Alice", "attribution": "seed",
         "civ": "Mayans", "team": "1", "winner": True, "eapm": 80, "age_reliable": True,
         "tc_relocations": 0, "feudal_s": 600, "castle_s": 1200, "imperial_s": None,
         "first_tc_s": 20, "villagers": 90, "vil_pre_feudal": 20, "vil_pre_castle": 50,
         "vil_pre_imperial": 90, "military": 30, "mil_pre_feudal": 0, "mil_pre_castle": 10,
         "mil_pre_imperial": 30},
        {"player_number": 2, "profile_id": 222, "identity": "Bob", "attribution": "unmapped",
         "civ": "Franks", "team": "2", "winner": False, "eapm": 70, "age_reliable": True,
         "tc_relocations": 1, "feudal_s": 650, "castle_s": None, "imperial_s": None,
         "first_tc_s": 25, "villagers": 70, "vil_pre_feudal": 18, "vil_pre_castle": 40,
         "vil_pre_imperial": 70, "military": 40, "mil_pre_feudal": 5, "mil_pre_castle": 20,
         "mil_pre_imperial": 40},
    ],
    "units": [{"player_number": 1, "identity": "Alice", "civ": "Mayans", "unit": "Archer",
               "category": "archer_line", "is_military": True, "total": 25, "pre_feudal": 0,
               "pre_castle": 10, "pre_imperial": 25}],
    "techs": [{"player_number": 2, "identity": "Bob", "civ": "Franks", "tech": "Loom",
               "click_s": 120, "phase": "dark"}],
    "buildings": [{"player_number": 1, "identity": "Alice", "civ": "Mayans",
                   "building": "House", "count": 5}],
}
PROFMAP = {111: 5001}   # profile 111 -> discord user; 222 unmapped


def test_match_row():
    row = shape.match_row(EXTRACTED["match"], bot_match_id=7, parsed_at=123, parser_version="pv1")
    assert row["aoe2_match_id"] == 999
    assert row["bot_match_id"] == 7
    assert row["played_at"] == "2026-06-20 10:00"
    assert row["replay_url"] == "https://www.aoe2insights.com/match/999/"
    assert row["parsed_at"] == 123 and row["parser_version"] == "pv1"
    assert "winner_team" not in row   # dropped


def test_pnum_to_profile():
    assert shape.pnum_to_profile(EXTRACTED["players"]) == {1: 111, 2: 222}


def test_player_game_rows_attributes_user_id():
    rows = shape.player_game_rows(999, EXTRACTED["players"], PROFMAP)
    by_pid = {r["profile_id"]: r for r in rows}
    assert by_pid[111]["user_id"] == 5001
    assert by_pid[222]["user_id"] is None        # unmapped -> NULL
    assert by_pid[111]["aoe2_match_id"] == 999
    assert by_pid[111]["villagers"] == 90


def test_unit_rows_denormalize_profile_id():
    rows = shape.unit_rows(999, EXTRACTED["units"], {1: 111, 2: 222})
    assert rows[0]["aoe2_match_id"] == 999
    assert rows[0]["player_number"] == 1
    assert rows[0]["profile_id"] == 111
    assert rows[0]["unit"] == "Archer"


def test_profile_upserts():
    rows = shape.profile_upserts(EXTRACTED["players"], PROFMAP, now=555)
    by_pid = {r["profile_id"]: r for r in rows}
    assert by_pid[111]["user_id"] == 5001 and by_pid[111]["name"] == "Alice"
    assert by_pid[222]["user_id"] is None and by_pid[222]["last_seen_at"] == 555
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_replay_stats_shape.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.replay_stats.shape'`.

- [ ] **Step 3: Write the implementation**

Create `bot/replay_stats/shape.py`:

```python
# -*- coding: utf-8 -*-
"""Pure transforms from extract_match() output to rs_* MySQL row dicts. Adds aoe2_match_id,
denormalizes profile_id onto the long-form tables (via the per-match player_number->profile_id
map), and attributes Discord user_id from a profile_id->user_id map. No DB — unit-tested."""

REPLAY_URL = "https://www.aoe2insights.com/match/{id}/"

_PLAYER_GAME_FIELDS = (
    "player_number", "profile_id", "identity", "attribution", "civ", "team", "winner",
    "eapm", "age_reliable", "tc_relocations", "feudal_s", "castle_s", "imperial_s",
    "first_tc_s", "villagers", "vil_pre_feudal", "vil_pre_castle", "vil_pre_imperial",
    "military", "mil_pre_feudal", "mil_pre_castle", "mil_pre_imperial",
)
_UNIT_FIELDS = ("player_number", "unit", "category", "is_military",
                "total", "pre_feudal", "pre_castle", "pre_imperial")
_TECH_FIELDS = ("player_number", "tech", "click_s", "phase")
_BUILDING_FIELDS = ("player_number", "building", "count")


def match_row(m, bot_match_id, parsed_at, parser_version):
    aoe2_id = m["aoe2_match_id"]
    return dict(
        aoe2_match_id=aoe2_id, bot_match_id=bot_match_id, map=m.get("map"),
        save_version=m.get("save_version"), duration_s=m.get("duration_s"),
        played_at=m.get("date") or None, replay_url=REPLAY_URL.format(id=aoe2_id),
        parsed_at=parsed_at, parser_version=parser_version,
    )


def pnum_to_profile(players):
    return {p["player_number"]: p["profile_id"] for p in players}


def player_game_rows(aoe2_match_id, players, profmap):
    out = []
    for p in players:
        row = {k: p.get(k) for k in _PLAYER_GAME_FIELDS}
        row["aoe2_match_id"] = aoe2_match_id
        row["user_id"] = profmap.get(p["profile_id"])
        out.append(row)
    return out


def _long_rows(aoe2_match_id, records, pnum2profile, fields):
    out = []
    for r in records:
        row = {k: r.get(k) for k in fields}
        row["aoe2_match_id"] = aoe2_match_id
        row["profile_id"] = pnum2profile.get(r["player_number"])
        out.append(row)
    return out


def unit_rows(aoe2_match_id, units, pnum2profile):
    return _long_rows(aoe2_match_id, units, pnum2profile, _UNIT_FIELDS)


def tech_rows(aoe2_match_id, techs, pnum2profile):
    return _long_rows(aoe2_match_id, techs, pnum2profile, _TECH_FIELDS)


def building_rows(aoe2_match_id, buildings, pnum2profile):
    return _long_rows(aoe2_match_id, buildings, pnum2profile, _BUILDING_FIELDS)


def profile_upserts(players, profmap, now):
    return [dict(profile_id=p["profile_id"], user_id=profmap.get(p["profile_id"]),
                 name=p.get("identity"), last_seen_at=now) for p in players]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_replay_stats_shape.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add bot/replay_stats/shape.py tests/test_replay_stats_shape.py
git commit -m "feat(replay-stats): pure row-shaping with profile_id denorm + user_id attribution"
```

---

## Task 4: `store.py` — async DB layer

**Files:**
- Create: `bot/replay_stats/store.py`

> Store talks to MySQL, so it is not unit-tested under the conftest stubs; it is exercised by the parity smoke test (Task 8) and live. Implement carefully against the `db` API.

- [ ] **Step 1: Implement the store**

Create `bot/replay_stats/store.py`:

```python
# -*- coding: utf-8 -*-
"""Async DB layer for replay-stats: enable flag, find-next, idempotent per-match write,
ingest status bookkeeping, and rs_profiles seeding/lookup. All access via core.database.db."""
import csv
import os
import time

from core.database import db

from . import shape

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


# ── enable flag ──────────────────────────────────────────────────────────
async def is_enabled():
    row = await db.select_one(["*"], "rs_config", {"id": 1})
    return bool(row and row.get("enabled"))


async def set_enabled(on):
    await db.insert("rs_config", dict(id=1, enabled=1 if on else 0), on_dublicate="replace")


# ── find work ────────────────────────────────────────────────────────────
async def find_new_match(max_age_days=None):
    """Newest aoe2_match_id (deduped) present in qc_match_civs but absent from rs_ingest.
    qc_match_civs has ~8 rows per match, so GROUP BY; join qc_matches for the timestamp.
    Returns dict(aoe2_match_id, bot_match_id, at) or None."""
    age_clause = ""
    args = []
    if max_age_days is not None:
        age_clause = "AND m.at >= %s "
        args.append(int(time.time()) - max_age_days * 86400)
    rows = await db.fetchall(
        "SELECT mc.aoe2_match_id AS aoe2_match_id, MAX(mc.bot_match_id) AS bot_match_id, "
        "MAX(m.at) AS at FROM qc_match_civs mc JOIN qc_matches m ON m.match_id = mc.bot_match_id "
        "WHERE mc.aoe2_match_id IS NOT NULL " + age_clause +
        "AND mc.aoe2_match_id NOT IN (SELECT aoe2_match_id FROM rs_ingest) "
        "GROUP BY mc.aoe2_match_id ORDER BY at DESC LIMIT 1", args)
    return rows[0] if rows else None


async def find_due_retry(now):
    """Oldest ingest row eligible for another attempt (404/parse_failed, due, under cap)."""
    rows = await db.fetchall(
        "SELECT * FROM rs_ingest WHERE status IN ('unavailable','parse_failed') "
        "AND (next_attempt_at IS NULL OR next_attempt_at <= %s) "
        "ORDER BY next_attempt_at ASC LIMIT 1", [now])
    return rows[0] if rows else None


async def reopen_pending_parser_update(current_parser_version):
    """A deploy with a newer parser reopens games shelved on an old parser version."""
    await db.execute(
        "UPDATE rs_ingest SET status='unavailable', next_attempt_at=0 "
        "WHERE status='pending_parser_update' AND (parser_version IS NULL OR parser_version <> %s)",
        [current_parser_version])


async def reset_stale_processing(now):
    """Recover matches orphaned in 'processing' by a crash/redeploy mid-ingest: reset them to
    the retryable 'unavailable' status. Run once per process at first sweep — this process has
    not written any 'processing' row yet, so every existing one is from a dead process."""
    await db.execute(
        "UPDATE rs_ingest SET status='unavailable', next_attempt_at=%s WHERE status='processing'",
        [now])


# ── ingest status ────────────────────────────────────────────────────────
async def get_ingest(aoe2_match_id):
    return await db.select_one(["*"], "rs_ingest", {"aoe2_match_id": aoe2_match_id})


async def upsert_ingest(aoe2_match_id, **fields):
    cur = await get_ingest(aoe2_match_id) or dict(aoe2_match_id=aoe2_match_id, attempts=0,
                                                  first_seen_at=int(time.time()))
    cur.update(fields)
    await db.insert("rs_ingest", cur, on_dublicate="replace")


# ── per-match write (idempotent) ─────────────────────────────────────────
async def load_profile_user_map():
    rows = await db.fetchall("SELECT profile_id, user_id FROM rs_profiles WHERE user_id IS NOT NULL")
    return {r["profile_id"]: r["user_id"] for r in rows}


async def write_match(extracted, bot_match_id, parsed_at, parser_version):
    """Idempotent: replace this match's rows. Returns count of player rows written."""
    aoe2_id = extracted["match"]["aoe2_match_id"]
    profmap = await load_profile_user_map()
    p2p = shape.pnum_to_profile(extracted["players"])

    # clear any prior rows for this match (idempotent re-ingest)
    for t in ("rs_player_games", "rs_player_units", "rs_player_techs", "rs_player_buildings"):
        await db.execute(f"DELETE FROM {t} WHERE aoe2_match_id=%s", [aoe2_id])

    await db.insert("rs_matches",
                    shape.match_row(extracted["match"], bot_match_id, parsed_at, parser_version),
                    on_dublicate="replace")
    pg = shape.player_game_rows(aoe2_id, extracted["players"], profmap)
    if pg:
        await db.insert_many("rs_player_games", pg, on_dublicate="replace")
    units = shape.unit_rows(aoe2_id, extracted["units"], p2p)
    if units:
        await db.insert_many("rs_player_units", units, on_dublicate="replace")
    techs = shape.tech_rows(aoe2_id, extracted["techs"], p2p)
    if techs:
        await db.insert_many("rs_player_techs", techs, on_dublicate="replace")
    builds = shape.building_rows(aoe2_id, extracted["buildings"], p2p)
    if builds:
        await db.insert_many("rs_player_buildings", builds, on_dublicate="replace")
    profs = shape.profile_upserts(extracted["players"], profmap, parsed_at)
    if profs:
        await db.insert_many("rs_profiles", profs, on_dublicate="replace")
    return len(pg)


# ── profile seeding (one-time / idempotent) ──────────────────────────────
async def seed_profiles_from_csv():
    """Seed rs_profiles from data/profile_resolved.csv (cols: profile_id,user_id,nick,...).
    Only inserts profiles not already present (preserves learned user_ids)."""
    path = os.path.join(_ROOT, "data", "profile_resolved.csv")
    if not os.path.exists(path):
        return 0
    existing = {r["profile_id"] for r in await db.fetchall("SELECT profile_id FROM rs_profiles")}
    rows, now = [], int(time.time())
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                pid = int(r["profile_id"])
            except (ValueError, KeyError):
                continue
            if pid in existing:
                continue
            uid = r.get("user_id")
            rows.append(dict(profile_id=pid, user_id=int(uid) if uid else None,
                             name=r.get("nick") or r.get("aoe2_name") or "", last_seen_at=now))
    if rows:
        await db.insert_many("rs_profiles", rows, on_dublicate="ignore")
    return len(rows)
```

- [ ] **Step 2: Lint it**

Run: `ruff check bot/replay_stats/store.py`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add bot/replay_stats/store.py
git commit -m "feat(replay-stats): async store (find-next, idempotent write, profile seeding)"
```

---

## Task 5: `fetch.py` — async download wrappers

**Files:**
- Create: `bot/replay_stats/fetch.py`

> Reuses the proven sync functions in `utils/replay_quiz/download.py` via `asyncio.to_thread` (I/O-bound → keeps the event loop free, no aiohttp rewrite needed). Lazy imports keep `requests`/`mgz` out of the import path until first use.

- [ ] **Step 1: Implement**

Create `bot/replay_stats/fetch.py`:

```python
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
    status: 'ok'/'cached' on success; 'no_profile' when no participant resolved; otherwise the
    last download_replay status — e.g. 'http_404'/'neterr:*'/'bad_zip'/'no_record_in_zip' (each
    participant tried), or 'http_429'/'429_exhausted' (aoe.ms rate-limited — we stop early)."""
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
        if status in ("http_429", "429_exhausted"):
            break   # aoe.ms rate-limits globally (per-IP) — another participant won't help
        # otherwise (404 / neterr / http_5xx / bad_zip): try the next participant
    return None, last_status


async def read_save_version(path):
    dl = await asyncio.to_thread(_download_module)
    return await asyncio.to_thread(dl.read_save_version, path)
```

- [ ] **Step 2: Lint**

Run: `ruff check bot/replay_stats/fetch.py`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add bot/replay_stats/fetch.py
git commit -m "feat(replay-stats): async fetch wrappers over download.py (to_thread)"
```

---

## Task 6: `parse.py` — version gate + process-pool extract

**Files:**
- Create: `bot/replay_stats/parse.py`

- [ ] **Step 1: Implement**

Create `bot/replay_stats/parse.py`:

```python
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
```

- [ ] **Step 2: Lint**

Run: `ruff check bot/replay_stats/parse.py`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add bot/replay_stats/parse.py
git commit -m "feat(replay-stats): save-version gate + process-pool extract"
```

---

## Task 7: `jobs.py` — the ingest loop

**Files:**
- Modify (replace stub): `bot/replay_stats/jobs.py`

- [ ] **Step 1: Implement the full job (mirrors QuizJobs isolation)**

Replace `bot/replay_stats/jobs.py` with:

```python
# -*- coding: utf-8 -*-
"""Replay-stats ingest job on the shared 1-s think() tick. Self-isolating and cadence-gated
like QuizJobs — a failure here can never break the tick. Does nothing unless rs_config.enabled.
One match per sweep (bounded load, polite to aoe.ms)."""
import asyncio
import os
import time

from core.console import log

from . import policy, store
from .fetch import fetch_replay
from .parse import parse_replay
from . import PARSER_VERSION

_pending = set()


class ReplayStatsJobs:
    POLL_INTERVAL = 150     # seconds between ingest sweeps

    def __init__(self):
        self.next_run = 0
        self._running = False
        self._reopened = False   # one-time parser-version reopen per process

    async def think(self, frame_time):
        try:
            if self._running or frame_time < self.next_run:
                return
            self.next_run = frame_time + self.POLL_INTERVAL
            self._running = True
            task = asyncio.create_task(self._run())

            def _done(t):
                self._running = False
                _pending.discard(t)
                if not t.cancelled() and t.exception() is not None:
                    log.error(f"Replay-stats job crashed: {t.exception()}")

            _pending.add(task)
            task.add_done_callback(_done)
        except Exception as e:
            self._running = False
            log.error(f"Replay-stats think() error (ignored): {e}")

    async def _run(self):
        if not await store.is_enabled():
            return
        now = int(time.time())
        if not self._reopened:
            await store.reopen_pending_parser_update(PARSER_VERSION)
            await store.reset_stale_processing(now)
            self._reopened = True
        work = await store.find_new_match()
        if work:
            await self.ingest_one(work["aoe2_match_id"], work.get("bot_match_id"),
                                  work.get("at"), now)
            return
        retry = await store.find_due_retry(now)
        if retry:
            await self.ingest_one(retry["aoe2_match_id"], None, None, now,
                                  attempts=retry.get("attempts") or 0,
                                  first_seen_at=retry.get("first_seen_at") or now)

    async def ingest_one(self, aoe2_match_id, bot_match_id, played_at_epoch, now,
                         attempts=0, first_seen_at=None):
        """Run one match through fetch -> gate/parse -> store. Updates rs_ingest. Bulletproof."""
        first_seen_at = first_seen_at or now
        try:
            await store.upsert_ingest(aoe2_match_id, status="processing", attempts=attempts,
                                      first_seen_at=first_seen_at, last_attempt_at=now)
            path, fstatus = await fetch_replay(aoe2_match_id)
            if not path:
                if fstatus in ("http_429", "429_exhausted"):
                    # Global aoe.ms rate-limit (per-IP) — cool down WITHOUT counting an attempt,
                    # so a busy backfill doesn't penalize matches toward longer backoff.
                    return await store.upsert_ingest(
                        aoe2_match_id, status="unavailable", attempts=attempts,
                        first_seen_at=first_seen_at, next_attempt_at=now + 1800,
                        error_reason=fstatus)
                return await self._mark_unavailable(aoe2_match_id, attempts, first_seen_at, now, fstatus)

            try:
                resolved = await asyncio.to_thread(_load_resolved)
                date_map = {aoe2_match_id: _date_str(played_at_epoch)} if played_at_epoch else {}
                result, pstatus, sv = await parse_replay(path, resolved, date_map)
            finally:
                _safe_unlink(path)   # remove the temp replay on every path (success or error)

            if pstatus == "pending_parser_update":
                await store.upsert_ingest(aoe2_match_id, status="pending_parser_update",
                                          save_version=sv, parser_version=PARSER_VERSION,
                                          attempts=attempts, error_reason="save_version too new")
                return
            if pstatus != "ok" or not result:
                if policy.parse_failed_exhausted(attempts + 1):
                    return await store.upsert_ingest(aoe2_match_id, status="gave_up",
                                                     attempts=attempts + 1, error_reason="parse_failed")
                return await store.upsert_ingest(aoe2_match_id, status="parse_failed",
                                                 save_version=sv, attempts=attempts + 1,
                                                 next_attempt_at=now + 3600, error_reason="parse error")

            await store.write_match(result, bot_match_id, now, PARSER_VERSION)
            await store.upsert_ingest(aoe2_match_id, status="done", save_version=sv,
                                      parser_version=PARSER_VERSION, attempts=attempts + 1)
            log.info(f"Replay-stats ingested aoe2 match {aoe2_match_id} (save {sv}).")
        except Exception as e:
            log.error(f"Replay-stats ingest({aoe2_match_id}) failed: {e}")
            await store.upsert_ingest(aoe2_match_id, status="parse_failed", attempts=attempts + 1,
                                      next_attempt_at=now + 3600, error_reason=str(e)[:180])

    async def _mark_unavailable(self, aoe2_match_id, attempts, first_seen_at, now, reason):
        if policy.should_give_up_unavailable(first_seen_at, now):
            return await store.upsert_ingest(aoe2_match_id, status="gave_up", attempts=attempts,
                                             error_reason=f"unavailable:{reason}")
        await store.upsert_ingest(aoe2_match_id, status="unavailable", attempts=attempts + 1,
                                  first_seen_at=first_seen_at,
                                  next_attempt_at=now + policy.unavailable_backoff(attempts),
                                  error_reason=reason)


def _load_resolved():
    import sys
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if root not in sys.path:
        sys.path.insert(0, root)
    from utils.replay_quiz.extract import load_resolved
    return load_resolved()


def _date_str(epoch):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(epoch)))


def _safe_unlink(path):
    try:
        os.remove(path)
    except OSError:
        pass


jobs = ReplayStatsJobs()
```

- [ ] **Step 2: Lint**

Run: `ruff check bot/replay_stats/`
Expected: no errors.

- [ ] **Step 3: Verify package still imports**

Run: `python -c "import bot.replay_stats; print('ok', bot.replay_stats.jobs.POLL_INTERVAL)"`
Expected: `ok 150` (or, if DB unreachable here, `ruff check bot/replay_stats/` clean).

- [ ] **Step 4: Commit**

```bash
git add bot/replay_stats/jobs.py
git commit -m "feat(replay-stats): ingest loop (find->fetch->gate->parse->store->status)"
```

---

## Task 8: Parity smoke test (offline)

**Files:**
- Create: `tests/test_replay_stats_parity.py`

> This test needs the mgz fork installed and a replay file present, so it is **skipped unless** `RS_PARITY_REPLAY` (path to a `.aoe2record`) and `RS_PARITY_MATCH_ID` env vars are set. Run it manually after Task 12 to validate that live extraction equals the offline `replay_quiz.db` numbers.

- [ ] **Step 1: Write the test**

Create `tests/test_replay_stats_parity.py`:

```python
import os
import sqlite3
import sys

import pytest

REPLAY = os.environ.get("RS_PARITY_REPLAY")
MATCH_ID = os.environ.get("RS_PARITY_MATCH_ID")
QUIZ_DB = os.path.join(os.path.dirname(__file__), "..", "data", "replay_quiz.db")

pytestmark = pytest.mark.skipif(
    not (REPLAY and MATCH_ID and os.path.exists(QUIZ_DB)),
    reason="set RS_PARITY_REPLAY + RS_PARITY_MATCH_ID and have data/replay_quiz.db to run")


def test_live_extract_matches_offline_facts():
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from utils.replay_quiz.extract import extract_match, load_resolved
    out = extract_match(REPLAY, load_resolved(), {})
    assert out["match"]["aoe2_match_id"] == int(MATCH_ID)

    con = sqlite3.connect(QUIZ_DB)
    con.row_factory = sqlite3.Row
    offline = {r["profile_id"]: r for r in con.execute(
        "SELECT profile_id, villagers, feudal_s, military FROM facts WHERE aoe2_match_id=?",
        [int(MATCH_ID)])}
    assert offline, "match not present in offline replay_quiz.db"
    for p in out["players"]:
        o = offline.get(p["profile_id"])
        if not o:
            continue
        assert p["villagers"] == o["villagers"]
        assert p["military"] == o["military"]
        assert (p["feudal_s"] or None) == (o["feudal_s"] or None)
```

- [ ] **Step 2: Verify it skips cleanly in CI conditions**

Run: `pytest tests/test_replay_stats_parity.py -v`
Expected: `1 skipped` (env vars not set).

- [ ] **Step 3: Commit**

```bash
git add tests/test_replay_stats_parity.py
git commit -m "test(replay-stats): offline parity smoke test vs replay_quiz.db"
```

---

## Task 9: `/replaystats` admin commands

**Files:**
- Create: `bot/commands/replay_stats.py`
- Modify: `bot/commands/__init__.py`, `bot/context/slash/groups.py`, `bot/context/slash/commands.py`

- [ ] **Step 1: Implement the handlers**

Create `bot/commands/replay_stats.py`:

```python
# -*- coding: utf-8 -*-
"""Slash-command handlers for the replay-stats pipeline (admin). Thin: logic lives in
bot.replay_stats. All bot.replay_stats imports are lazy so this module loads during the
`from . import commands` step without pulling heavy modules early."""
__all__ = ["replaystats_status", "replaystats_enable", "replaystats_disable",
           "replaystats_backfill", "replaystats_reingest"]


async def replaystats_status(ctx):
    from bot.replay_stats import store, PARSER_VERSION
    from core.database import db
    counts = await db.fetchall("SELECT status, COUNT(*) n FROM rs_ingest GROUP BY status")
    done = await db.fetchall("SELECT MAX(parsed_at) m, COUNT(*) n FROM rs_matches")
    pend = await db.fetchall(
        "SELECT save_version, COUNT(*) n FROM rs_ingest WHERE status='pending_parser_update' "
        "GROUP BY save_version")
    enabled = await store.is_enabled()
    parts = [f"Replay-stats **{'ON' if enabled else 'OFF'}** · parser `{PARSER_VERSION}`"]
    parts.append("Ingest: " + (", ".join(f"{r['status']}={r['n']}" for r in counts) or "none"))
    if done and done[0]["n"]:
        parts.append(f"Parsed matches: {done[0]['n']} (latest parsed_at {done[0]['m']})")
    if pend:
        parts.append("Pending parser update: " + ", ".join(f"save {r['save_version']}×{r['n']}" for r in pend))
    await ctx.reply("\n".join(parts))


async def replaystats_enable(ctx):
    ctx.check_perms(ctx.Perms.ADMIN)
    from bot.replay_stats import store
    await store.set_enabled(True)
    await ctx.success("Replay-stats ingestion enabled.", title="Replay-stats")


async def replaystats_disable(ctx):
    ctx.check_perms(ctx.Perms.ADMIN)
    from bot.replay_stats import store
    await store.set_enabled(False)
    await ctx.success("Replay-stats ingestion disabled.", title="Replay-stats")


async def replaystats_backfill(ctx, days=90):
    ctx.check_perms(ctx.Perms.ADMIN)
    from bot.replay_stats import backfill
    started = await backfill.kick_off(int(days))
    if started:
        await ctx.success(f"Backfill started for the last {int(days)} days (newest first). "
                          "Watch progress with /replaystats status.", title="Replay-stats")
    else:
        await ctx.error("A backfill is already running.")


async def replaystats_reingest(ctx, match_id):
    ctx.check_perms(ctx.Perms.ADMIN)
    from bot.replay_stats.jobs import jobs
    import time
    try:
        mid = int(match_id)
    except ValueError:
        return await ctx.error("match_id must be a numeric aoe2 match id.")
    if jobs._running:
        return await ctx.error("A replay-stats sweep is in progress — try again in a moment.")
    jobs._running = True   # coarse lock: keep the tick's sweep from overlapping this match
    try:
        await jobs.ingest_one(mid, None, None, int(time.time()))
    finally:
        jobs._running = False
    await ctx.success(f"Re-ingested aoe2 match {mid} (see /replaystats status).",
                      title="Replay-stats")
```

- [ ] **Step 2: Add the star-import**

In `bot/commands/__init__.py`, after the existing `from .quiz import *` line, add:

```python
from .replay_stats import *
```

- [ ] **Step 3: Add the slash group**

In `bot/context/slash/groups.py`, after the `admin_quiz` group definition, add:

```python
@dc.slash_command(name='replaystats', **guild_kwargs)
async def admin_replaystats(interaction: Interaction):
    pass
```

- [ ] **Step 4: Add the subcommands**

In `bot/context/slash/commands.py`, after the quiz command block, add:

```python
# ── Replay-stats pipeline (opt-in, admin) ─────────────────────────────────
@groups.admin_replaystats.subcommand(name='status', description='Show replay-stats ingest status.')
async def _replaystats_status(
        interaction: Interaction,
): await run_slash(bot.commands.replaystats_status, interaction=interaction)


@groups.admin_replaystats.subcommand(name='enable', description='Enable replay-stats ingestion (admin).')
async def _replaystats_enable(
        interaction: Interaction,
): await run_slash(bot.commands.replaystats_enable, interaction=interaction)


@groups.admin_replaystats.subcommand(name='disable', description='Disable replay-stats ingestion (admin).')
async def _replaystats_disable(
        interaction: Interaction,
): await run_slash(bot.commands.replaystats_disable, interaction=interaction)


@groups.admin_replaystats.subcommand(name='backfill', description='Backfill recent replays, newest-first (admin).')
async def _replaystats_backfill(
        interaction: Interaction,
        days: int = SlashOption(name="days", description="How many days back to backfill.", required=False, default=90),
): await run_slash(bot.commands.replaystats_backfill, interaction=interaction, days=days)


@groups.admin_replaystats.subcommand(name='reingest', description='Force re-ingest one aoe2 match (admin).')
async def _replaystats_reingest(
        interaction: Interaction,
        match_id: str = SlashOption(name="match_id", description="The aoe2 match id."),
): await run_slash(bot.commands.replaystats_reingest, interaction=interaction, match_id=match_id)
```

- [ ] **Step 5: Lint**

Run: `ruff check bot/commands/replay_stats.py bot/context/slash/`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add bot/commands/replay_stats.py bot/commands/__init__.py bot/context/slash/groups.py bot/context/slash/commands.py
git commit -m "feat(replay-stats): /replaystats admin commands (status/enable/disable/backfill/reingest)"
```

---

## Task 10: Wire the job into the tick

**Files:**
- Modify: `bot/events.py`

- [ ] **Step 1: Add the think() call**

In `bot/events.py`, in `on_think`, immediately after the line
`await bot.quiz.jobs.think(frame_time)    # opt-in quiz feature; ...`, add:

```python
		await bot.replay_stats.jobs.think(frame_time)  # opt-in replay-stats; think() is self-isolating
```

> Match the surrounding **tab** indentation in `bot/events.py` (this file uses tabs).

- [ ] **Step 2: Lint**

Run: `ruff check bot/events.py`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add bot/events.py
git commit -m "feat(replay-stats): run the ingest job on the think() tick"
```

---

## Task 11: Backfill script

**Files:**
- Create: `bot/replay_stats/backfill.py`

- [ ] **Step 1: Implement**

Create `bot/replay_stats/backfill.py`:

```python
# -*- coding: utf-8 -*-
"""One-time, resumable, newest-first backfill. Reuses the live ingest path (jobs.ingest_one),
so it writes the same rows and is idempotent. Kicked off by /replaystats backfill; runs as a
background asyncio task, one match at a time (polite to aoe.ms)."""
import asyncio
import time

from core.console import log

from . import store
from .jobs import jobs

_task = None


async def kick_off(days):
    """Start the backfill if not already running. Returns True if it started."""
    global _task
    if _task is not None and not _task.done():
        return False
    _task = asyncio.create_task(_run(days))
    return True


async def _run(days):
    await store.seed_profiles_from_csv()
    done, errors = 0, 0
    while True:
        try:
            work = await store.find_new_match(max_age_days=days)
            if not work:
                break
            now = int(time.time())
            # The tick sweep may pick the same newest match concurrently; that's safe —
            # write_match is idempotent and the single-worker parse pool serializes the work
            # (worst case: one redundant parse + a double-counted attempt, never corruption).
            await jobs.ingest_one(work["aoe2_match_id"], work.get("bot_match_id"),
                                  work.get("at"), now)
            done += 1
            errors = 0
            if done % 20 == 0:
                log.info(f"Replay-stats backfill: {done} matches processed…")
            await asyncio.sleep(2)   # gentle pacing between external fetches
        except Exception as e:
            errors += 1
            log.error(f"Replay-stats backfill iteration error ({errors}/5): {e}")
            if errors >= 5:
                log.error("Replay-stats backfill: too many consecutive errors, stopping.")
                break
            await asyncio.sleep(10)
    log.info(f"Replay-stats backfill finished: {done} matches processed.")
```

> Note: `find_new_match` only returns matches **not yet in `rs_ingest`**, so each successful
> ingest removes that match from the candidate set — the loop terminates naturally and is
> resumable (re-running skips everything already attempted).

- [ ] **Step 2: Lint**

Run: `ruff check bot/replay_stats/backfill.py`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add bot/replay_stats/backfill.py
git commit -m "feat(replay-stats): resumable newest-first backfill"
```

---

## Task 12: Add the parser to the bot image

**Files:**
- Modify: `requirements.txt`, `ruff.toml`

- [ ] **Step 1: Add the replay-parsing deps**

Append to `requirements.txt`:

```
# Replay parsing for the live replay-stats pipeline (bot/replay_stats). mgz is the sanduckhan
# fork pinned to a commit that supports AoE2 DE save_version 67.x; aocref is its reference data.
mgz @ https://github.com/sanduckhan/aoc-mgz/archive/a1683d8eeca67796ced0d0c05b145420c97d862d.tar.gz
aocref==2.0.37
requests==2.32.3
tqdm==4.67.1
```

> The `Dockerfile` already runs `pip install --no-cache-dir -r requirements.txt`, so no
> Dockerfile change is needed. `requests`/`tqdm` are imported by `utils/replay_quiz/download.py`.

- [ ] **Step 2: Keep the offline module out of lint (if not already)**

Confirm `ruff.toml`'s `exclude` already contains `utils/replay_quiz`. The new `bot/replay_stats/`
package **is** linted (it's first-party). No change needed unless `ruff check .` flags the new
package — fix any findings rather than excluding it.

- [ ] **Step 3: Verify the image builds with the new deps**

Run: `docker build -t nammapubobot-test .`
Expected: build succeeds; the mgz fork + aocref install. (If Docker is unavailable locally,
instead create a throwaway venv and run `pip install -r requirements.txt` to confirm the deps
resolve.)

- [ ] **Step 4: Full lint + test sweep**

Run: `ruff check . && pytest tests/ -v`
Expected: ruff clean; all unit tests pass, parity test skipped.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt
git commit -m "build(replay-stats): add mgz fork + aocref + requests/tqdm to the bot image"
```

---

## Rollout & validation (after merge/deploy — operational, not code)

1. Deploy with the flag **OFF** (default `rs_config` row absent → `is_enabled()` is False). The
   `rs_*` tables are created on boot; nothing else runs.
2. **Validate subprocess parsing + parity:** on a box with the deps, set `RS_PARITY_REPLAY` /
   `RS_PARITY_MATCH_ID` to a known game present in `replay_quiz.db` and run
   `pytest tests/test_replay_stats_parity.py -v` → must pass.
3. Run `/replaystats reingest <aoe2_match_id>` for one recent game; confirm rows via
   `/replaystats status` and a quick DB spot-check.
4. **Backfill recent first:** `/replaystats backfill 90` → watch `/replaystats status` until the
   `unavailable` backlog stops shrinking; spot-check a player's `rs_player_games` rows.
5. `/replaystats enable` → the live job keeps it current. Watch `pending_parser_update` counts as
   a signal to bump the mgz fork after AoE2 patches.

---

## Self-review notes (author)

- **Spec coverage:** schema §4 → Task 1; save-version gate/retry §5 → Task 2; row-shaping/attribution §4-5 → Task 3; store/idempotent write/find-query §5-6 → Task 4; async fetch §3 → Task 5; process-pool parse + gate §5 → Task 6; ingest loop + statuses §5 → Task 7; testing §8 → Tasks 2/3/8; observability §9 → Task 9; tick wiring §3 → Task 10; backfill §6 → Task 11; Railway/deps §7 → Task 12; rollout §10 → final section. Phase-2 `/player_details` is intentionally **out of scope** (separate spec).
- **Attribution** uses `rs_profiles` (seeded from `data/profile_resolved.csv`, which carries `user_id`) → `profile_id→user_id`; unmapped ⇒ `user_id=NULL`. Matches the corrected spec.
- **Keys:** long-form tables keyed by `(aoe2_match_id, player_number, X)` with denormalized `profile_id` — matches `extract.py` output (player_number, no profile_id on unit/tech/building rows).
- **Type names checked across tasks:** `find_new_match`, `find_due_retry`, `ingest_one`, `write_match`, `set_enabled`, `is_enabled`, `seed_profiles_from_csv`, `parse_replay`, `fetch_replay`, `save_version_supported`, `unavailable_backoff`, `should_give_up_unavailable`, `parse_failed_exhausted`, `match_row`, `player_game_rows`, `unit_rows`, `tech_rows`, `building_rows`, `pnum_to_profile`, `profile_upserts` — referenced consistently.
- **Open validation item (not a placeholder):** Task 8 / rollout step 2 explicitly validates that `extract_match` runs in `ProcessPoolExecutor` on the deploy platform; if pickling/fork ever misbehaves, `parse._extract` can fall back to a spawned subprocess + JSON without touching callers.
