# Luck (Spawn / Map-Factor) Analysis — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Luck" analysis stage — 11 spawn/map-factor use cases (+ a baseline cohort) measuring how a player's first-TC settle position, nearby gold/stone/food, and starting-villager spread affect win/loss on Nomad 4v4 — surfaced on a new website "Luck" tab.

**Architecture:** Reuse the existing classification framework. Extend the replay extractor (v4) with per-player spawn metrics; add `gamedata` accessors + a validity gate; tag the new `Classification`s with `category="luck"` (non-exclusive, no phase cascade); register 12 luck classifications built from one shared builder; add a Luck web tab. Populate the local SQLite DB; prod sync + deploy are **gated on explicit user approval**.

**Tech Stack:** Python 3.11, mgz (vendored fork, `PYTHONPATH=.replay_scratch`), aiomysql/SQLite, aiohttp + vanilla-JS SPA (`bot/web_page.html`), pytest, ruff.

**Conventions:** `utils/classifications/**` and `utils/replay_quiz/extract.py` use **4-space** indent; `bot/web.py` uses **tabs**. Line length 120. Run all tests from repo root with `PYTHONPATH=.replay_scratch` available.

**Frozen thresholds (calibrated 2026-06-25, absolute tiles):** near_enemy<36, near_ally<46, isolated>63, near_gold<7, gold_poor>17, near_stone<9, stone_poor>20, near_food<7, food_poor>15, tight_villagers<253, scattered_villagers>429.

---

### Task 1: Add `category` to the Classification contract

**Files:**
- Modify: `utils/classifications/contract.py`
- Test: `tests/test_contract_category.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contract_category.py
from utils.classifications.contract import Classification


def _c(**kw):
    base = dict(key="k", title="t", version=1, trigger_spec="s", requirements=[],
                trigger=lambda g, p: False, factors=lambda g, p: {})
    base.update(kw)
    return Classification(**base)


def test_category_defaults_to_strategy():
    assert _c().category == "strategy"


def test_category_can_be_luck():
    assert _c(category="luck").category == "luck"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_contract_category.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'category'`

- [ ] **Step 3: Add the field**

In `utils/classifications/contract.py`, inside the `@dataclass class Classification`, add after `status`:

```python
    status: str = "active"             # 'active' or 'draft'
    category: str = "strategy"         # 'strategy' (default) or 'luck' (map/spawn factors)
    factor_specs: list = field(default_factory=list)  # ordered [{metric,label,kind}] for reports
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_contract_category.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add utils/classifications/contract.py tests/test_contract_category.py
git commit -m "feat(classifications): add category field to Classification (strategy|luck)"
```

---

### Task 2: Extract v4 — shared version constant + spawn metrics

Adds, per player, the data the luck use cases need: `settle_tc_xy` (first built TC, else pre-placed), `spawn_gold_d`/`spawn_stone_d`/`spawn_food_d` (distance from settle to the nearest neutral gold/stone/huntable), and `vil_perim` (starting-villager triangle perimeter). Promotes `EXTRACT_VERSION` to one shared constant so the parse cache invalidates everywhere.

**Files:**
- Modify: `utils/replay_quiz/extract.py`
- Modify: `utils/classifications/runner.py:26`
- Modify: `utils/classifications/pipeline/ingester.py:19`
- Test: `tests/test_extract_spawn.py`

- [ ] **Step 1: Write the failing test (pure helpers)**

```python
# tests/test_extract_spawn.py
from utils.replay_quiz.extract import _nearest, _perimeter


def test_nearest_returns_min_distance():
    assert _nearest((0.0, 0.0), [(3.0, 4.0), (6.0, 8.0)]) == 5.0


def test_nearest_none_when_no_points_or_no_pos():
    assert _nearest((0.0, 0.0), []) is None
    assert _nearest(None, [(1.0, 1.0)]) is None


def test_perimeter_of_3_4_5_triangle():
    # right triangle legs 3 and 4 -> sides 3,4,5 -> perimeter 12
    assert round(_perimeter([(0.0, 0.0), (3.0, 0.0), (0.0, 4.0)]), 3) == 12.0


def test_perimeter_none_when_fewer_than_two():
    assert _perimeter([(1.0, 1.0)]) is None
    assert _perimeter([]) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_extract_spawn.py -v`
Expected: FAIL — `ImportError: cannot import name '_nearest'`

- [ ] **Step 3: Implement the helpers + module constant + resource sets**

In `utils/replay_quiz/extract.py`, after the existing module constants (after the `WARSHIP = (...)` block near the top), add:

```python
EXTRACT_VERSION = "v4"   # parse-cache version; bump when extract_match output changes.
                         # v4: per-player settle_tc_xy + nearest gold/stone/food distances + vil_perim

RES_GOLD = {"Gold Mine"}
RES_STONE = {"Stone Mine"}
RES_FOOD = {"Wild Boar", "Deer", "Ibex", "Pig"}   # huntable food (herdables excluded)


def _nearest(pos, pts):
    """Min euclidean distance from pos=(x,y) to any point in pts; None if pos or pts is empty."""
    if pos is None or not pts:
        return None
    return min(((pos[0] - x) ** 2 + (pos[1] - y) ** 2) ** 0.5 for x, y in pts)


def _perimeter(pts):
    """Sum of all pairwise distances among pts (triangle perimeter for the 3 starting villagers).
    None if fewer than 2 points."""
    if len(pts) < 2:
        return None
    return sum(((pts[i][0] - pts[j][0]) ** 2 + (pts[i][1] - pts[j][1]) ** 2) ** 0.5
               for i in range(len(pts)) for j in range(i + 1, len(pts)))
```

- [ ] **Step 4: Run helper test to verify it passes**

Run: `pytest tests/test_extract_spawn.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Wire the new fields into `extract_match`**

In `utils/replay_quiz/extract.py`, inside `extract_match`, **after** the existing loop that fills `start_tc_xy` from `p.objects` (the `for p in m.players: for o in (p.objects or [])` block ending ~line 116), add the neutral-resource point lists and per-player starting-villager capture:

```python
    # neutral (gaia) resource positions for spawn-quality metrics
    gold_pts, stone_pts, food_pts = [], [], []
    for o in (m.gaia or []):
        pos = getattr(o, "position", None)
        if pos is None:
            continue
        nm = getattr(o, "name", "") or ""
        if nm in RES_GOLD:
            gold_pts.append((pos.x, pos.y))
        elif nm in RES_STONE:
            stone_pts.append((pos.x, pos.y))
        elif nm in RES_FOOD:
            food_pts.append((pos.x, pos.y))
    start_vils = {n: [] for n in players}
    for p in m.players:
        for o in (p.objects or []):
            pos = getattr(o, "position", None)
            if pos is not None and (getattr(o, "name", "") or "") == "Villager":
                start_vils[p.number].append((pos.x, pos.y))
```

Then, in the per-player output loop (`for pnum, p in players.items():`), compute the settle position and metrics and add them to the `out_players.append(dict(...))` call. Insert just before that `out_players.append(`:

```python
        builds = tc_xy[pnum]
        settle = ({"x": min(builds, key=lambda b: b["t_s"])["x"],
                   "y": min(builds, key=lambda b: b["t_s"])["y"]} if builds
                  else start_tc_xy.get(pnum))
        spos = (settle["x"], settle["y"]) if settle else None
```

and add these keys inside the `dict(...)` (e.g. right after `castle_builds=castle_xy[pnum],`):

```python
            settle_tc_xy=settle,
            spawn_gold_d=_nearest(spos, gold_pts),
            spawn_stone_d=_nearest(spos, stone_pts),
            spawn_food_d=_nearest(spos, food_pts),
            vil_perim=_perimeter(start_vils[pnum]),
```

- [ ] **Step 6: Point both cache-version constants at the shared one**

In `utils/classifications/runner.py`, replace line 26 (`EXTRACT_VERSION = "v3" ...` and its comment block) with an import-based constant. Change the existing import line (currently `from utils.classifications import dbio, shape`) to also import extract, and set:

```python
from utils.replay_quiz.extract import EXTRACT_VERSION   # noqa: E402  (single source of cache version)
```

Delete the old `EXTRACT_VERSION = "v3"` assignment + its `# v2/v3` comment lines in `runner.py`.

In `utils/classifications/pipeline/ingester.py`, replace line 19 (`EXTRACT_VERSION = "v3"`) with:

```python
from utils.replay_quiz.extract import EXTRACT_VERSION
```

(Place it with the other `from utils.replay_quiz...` imports; remove the standalone `EXTRACT_VERSION = "v3"` line.)

- [ ] **Step 7: Smoke-test extract_match on a real replay**

Run:
```bash
PYTHONPATH=.replay_scratch python -c "
import glob, utils.replay_quiz.extract as ex
p = sorted(glob.glob('data/replays/*.aoe2record'))[0]
g = ex.extract_match(p, ex.load_resolved(), ex.load_date_map())
pl = g['players'][0]
print('version', ex.EXTRACT_VERSION)
print({k: pl.get(k) for k in ('settle_tc_xy','spawn_gold_d','spawn_stone_d','spawn_food_d','vil_perim')})
"
```
Expected: `version v4` and a dict where `settle_tc_xy` is `{x,y}` and the four metrics are floats (or None if that resource/villager set is absent). No exception.

- [ ] **Step 8: Run the full test suite + lint**

Run: `pytest tests/ -q && ruff check utils/replay_quiz/extract.py utils/classifications/runner.py utils/classifications/pipeline/ingester.py`
Expected: all pass; ruff clean.

- [ ] **Step 9: Commit**

```bash
git add utils/replay_quiz/extract.py utils/classifications/runner.py utils/classifications/pipeline/ingester.py tests/test_extract_spawn.py
git commit -m "feat(extract): v4 spawn metrics (settle TC, nearest gold/stone/food, villager perimeter)"
```

---

### Task 3: gamedata — settle, proximity, validity gate

**Files:**
- Modify: `utils/classifications/gamedata.py`
- Test: `tests/test_gamedata_luck.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gamedata_luck.py
from utils.classifications import gamedata as gd


def _p(n, team, winner, settle=None, **extra):
    d = {"player_number": n, "team": team, "winner": winner, "settle_tc_xy": settle}
    d.update(extra)
    return d


def _game(players, map_name="Land Nomad"):
    return {"match": {"map": map_name}, "players": players}


def test_spawn_proximity_ally_enemy_any():
    # P1 at origin; ally P2 at (10,0); enemies P3 at (3,4)->5, P4 at (30,0)
    g = _game([
        _p(1, "1+2", True, {"x": 0.0, "y": 0.0}),
        _p(2, "1+2", True, {"x": 10.0, "y": 0.0}),
        _p(3, "3+4", False, {"x": 3.0, "y": 4.0}),
        _p(4, "3+4", False, {"x": 30.0, "y": 0.0}),
    ])
    d_ally, d_enemy, d_any = gd.spawn_proximity(g, 1)
    assert round(d_ally, 1) == 10.0
    assert round(d_enemy, 1) == 5.0
    assert round(d_any, 1) == 5.0


def test_spawn_proximity_none_without_settle():
    g = _game([_p(1, "1", True, None), _p(2, "2", False, {"x": 1.0, "y": 1.0})])
    assert gd.spawn_proximity(g, 1) == (None, None, None)


def test_is_valid_luck_game_balanced_nomad():
    players = [_p(i, "A" if i <= 4 else "B", i <= 4, {"x": float(i), "y": 0.0}) for i in range(1, 9)]
    assert gd.is_valid_luck_game(_game(players)) is True


def test_is_valid_luck_game_rejects_no_winner():
    players = [_p(i, "A" if i <= 4 else "B", False, {"x": float(i), "y": 0.0}) for i in range(1, 9)]
    assert gd.is_valid_luck_game(_game(players)) is False


def test_is_valid_luck_game_rejects_wrong_map_and_count():
    players8 = [_p(i, "A" if i <= 4 else "B", i <= 4, {"x": float(i), "y": 0.0}) for i in range(1, 9)]
    assert gd.is_valid_luck_game(_game(players8, map_name="Arabia")) is False
    players7 = players8[:7]
    assert gd.is_valid_luck_game(_game(players7)) is False


def test_spawn_metric_reads_player_field():
    g = _game([_p(1, "A", True, {"x": 0.0, "y": 0.0}, spawn_gold_d=4.2)])
    assert gd.spawn_metric(g, 1, "spawn_gold_d") == 4.2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gamedata_luck.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'spawn_proximity'`

- [ ] **Step 3: Implement the accessors**

Append to `utils/classifications/gamedata.py` (it already defines `player`, `_xy`, `_dist`):

```python
# --- Luck (spawn/map-factor) accessors (needs extract v4) ----------------------------------------

LUCK_MAPS = ("Land Nomad", "Nomad")


def settle_tc_xy(game, pnum):
    """The player's settle position = first built TC (extract stores it as settle_tc_xy), as (x,y)."""
    p = player(game, pnum)
    return _xy(p.get("settle_tc_xy")) if p else None


def spawn_proximity(game, pnum):
    """(d_ally, d_enemy, d_any): distance from this player's settle TC to the nearest same-team /
    different-team / any other player's settle TC. Each is None if no such settle is known."""
    me = player(game, pnum)
    mine = settle_tc_xy(game, pnum)
    if not me or mine is None:
        return (None, None, None)
    my_team = me.get("team")
    ally, enemy = [], []
    for op in game.get("players", []):
        if op["player_number"] == pnum:
            continue
        d = _dist(mine, settle_tc_xy(game, op["player_number"]))
        if d is None:
            continue
        (ally if op.get("team") == my_team else enemy).append(d)
    alld = ally + enemy
    return (min(ally) if ally else None, min(enemy) if enemy else None, min(alld) if alld else None)


def spawn_metric(game, pnum, key):
    """Read a stored per-player spawn metric (spawn_gold_d / spawn_stone_d / spawn_food_d / vil_perim)."""
    p = player(game, pnum)
    return p.get(key) if p else None


def is_valid_luck_game(game):
    """True for an in-scope luck game: map is Nomad, 6 or 8 players, and a balanced recorded result
    (winners == half the players). Drops non-Nomad, odd sizes, and games with no/partial winner."""
    mp = (game.get("match") or {}).get("map", "")
    players = game.get("players", [])
    n = len(players)
    if mp not in LUCK_MAPS or n not in (6, 8):
        return False
    winners = sum(1 for p in players if p.get("winner") in (1, True))
    return winners * 2 == n
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gamedata_luck.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add utils/classifications/gamedata.py tests/test_gamedata_luck.py
git commit -m "feat(gamedata): settle_tc_xy, spawn_proximity, spawn_metric, is_valid_luck_game"
```

---

### Task 4: Luck builder + 12 classifications

**Files:**
- Create: `utils/classifications/defs/luck.py`
- Test: `tests/test_luck_classifications.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_luck_classifications.py
from utils.classifications.defs import luck


def _p(n, team, winner, settle, **extra):
    d = {"player_number": n, "team": team, "winner": winner, "settle_tc_xy": settle}
    d.update(extra)
    return d


def _valid_game(**p1_extra):
    # 8 players, 4 winners, Land Nomad -> valid. P1 carries the metric under test.
    players = [_p(1, "A", True, {"x": 0.0, "y": 0.0}, **p1_extra)]
    players += [_p(i, "A" if i <= 4 else "B", i <= 4, {"x": float(i * 100), "y": 0.0}) for i in range(2, 9)]
    return {"match": {"map": "Land Nomad"}, "players": players}


def _by_key():
    return {c.key: c for c in luck.CLASSIFICATIONS}


def test_twelve_luck_classifications_all_category_luck():
    assert len(luck.CLASSIFICATIONS) == 12
    assert all(c.category == "luck" for c in luck.CLASSIFICATIONS)
    assert "luck_baseline" in _by_key()


def test_near_gold_fires_below_threshold_only():
    c = _by_key()["spawn_near_gold"]            # near_gold < 7
    assert c.trigger(_valid_game(spawn_gold_d=5.0), 1) is True
    assert c.trigger(_valid_game(spawn_gold_d=9.0), 1) is False


def test_gold_poor_fires_above_threshold_only():
    c = _by_key()["spawn_gold_poor"]            # gold_poor > 17
    assert c.trigger(_valid_game(spawn_gold_d=20.0), 1) is True
    assert c.trigger(_valid_game(spawn_gold_d=10.0), 1) is False


def test_luck_trigger_noop_on_invalid_game():
    c = _by_key()["spawn_near_gold"]
    g = _valid_game(spawn_gold_d=5.0)
    g["match"]["map"] = "Arabia"                # now invalid
    assert c.trigger(g, 1) is False


def test_baseline_fires_for_every_player_in_valid_game():
    c = _by_key()["luck_baseline"]
    assert c.trigger(_valid_game(spawn_gold_d=5.0), 1) is True
    assert c.trigger(_valid_game(spawn_gold_d=5.0), 5) is True


def test_isolated_uses_proximity_metric():
    c = _by_key()["spawn_isolated"]             # nearest any > 63; P1's nearest other is 200 here
    assert c.trigger(_valid_game(), 1) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_luck_classifications.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'utils.classifications.defs.luck'`

- [ ] **Step 3: Implement the builder + definitions**

```python
# utils/classifications/defs/luck.py
"""The 'luck' family: map/spawn factors (settle proximity, nearby gold/stone/food, villager spread)
and their win/loss impact. Each use case is a non-exclusive Classification (category 'luck') that
only fires on a valid Nomad 6/8p game (gd.is_valid_luck_game). Thresholds are absolute tiles, frozen
from the 2026-06-25 calibration (~20% of player-games per bucket; isolated 25%). A luck_baseline
cohort fires for every player in a valid game -> the denominator (N) and the 50% reference."""
from utils.classifications import gamedata as gd
from utils.classifications.contract import Classification, req

# metric(game, pnum) -> float|None
_PROX = lambda i: (lambda g, p: gd.spawn_proximity(g, p)[i])   # 0 ally, 1 enemy, 2 any  # noqa: E731
_FIELD = lambda k: (lambda g, p: gd.spawn_metric(g, p, k))     # noqa: E731

_REQ = [
    req("settle_tc_xy", source="extract.players.settle_tc_xy", status="available",
        note="first built TC (Nomad has no pre-placed TC); extract v4"),
    req("spawn_resources", source="extract.players.spawn_gold_d/stone_d/food_d", status="available",
        note="distance from settle to nearest gaia gold/stone/huntable; extract v4"),
    req("vil_perim", source="extract.players.vil_perim", status="available",
        note="starting-villager triangle perimeter; extract v4"),
    req("winner", source="extract.players.winner", status="available",
        note="outcome dimension; consumed by the runner, not trigger/factors"),
]


def _make(key, title, metric, side, threshold, label, spec):
    def trigger(game, pnum):
        if not gd.is_valid_luck_game(game):
            return False
        v = metric(game, pnum)
        if v is None:
            return False
        return v < threshold if side == "near" else v > threshold

    def factors(game, pnum):
        v = metric(game, pnum)
        return {label: round(v, 1) if v is not None else None}

    return Classification(
        key=key, title=title, version=1, trigger_spec=spec, category="luck",
        requirements=_REQ, trigger=trigger, factors=factors,
        factor_specs=[dict(metric=label, label=label.replace("_", " ").title(), kind="count")])


# (key, title, metric, side, threshold, factor-label, human trigger_spec)
_SPECS = [
    ("spawn_near_enemy", "Spawn near enemy", _PROX(1), "near", 36, "enemy_dist",
     "settled within 36 tiles of the nearest enemy's first TC"),
    ("spawn_near_ally", "Spawn near ally", _PROX(0), "near", 46, "ally_dist",
     "settled within 46 tiles of the nearest ally's first TC"),
    ("spawn_isolated", "Spawn isolated", _PROX(2), "far", 63, "nearest_player_dist",
     "settled more than 63 tiles from every other player's first TC"),
    ("spawn_near_gold", "Spawn near gold", _FIELD("spawn_gold_d"), "near", 7, "gold_dist",
     "settled within 7 tiles of gold"),
    ("spawn_gold_poor", "Spawn gold-poor", _FIELD("spawn_gold_d"), "far", 17, "gold_dist",
     "nearest gold more than 17 tiles from the settle"),
    ("spawn_near_stone", "Spawn near stone", _FIELD("spawn_stone_d"), "near", 9, "stone_dist",
     "settled within 9 tiles of stone"),
    ("spawn_stone_poor", "Spawn stone-poor", _FIELD("spawn_stone_d"), "far", 20, "stone_dist",
     "nearest stone more than 20 tiles from the settle"),
    ("spawn_near_food", "Spawn near food", _FIELD("spawn_food_d"), "near", 7, "food_dist",
     "settled within 7 tiles of huntable food (boar/deer)"),
    ("spawn_food_poor", "Spawn food-poor", _FIELD("spawn_food_d"), "far", 15, "food_dist",
     "nearest huntable food more than 15 tiles from the settle"),
    ("tight_villagers", "Tight villager spawn", _FIELD("vil_perim"), "near", 253, "vil_perimeter",
     "starting villagers close together (triangle perimeter < 253 tiles)"),
    ("scattered_villagers", "Scattered villager spawn", _FIELD("vil_perim"), "far", 429, "vil_perimeter",
     "starting villagers spread out (triangle perimeter > 429 tiles)"),
]

CLASSIFICATIONS = [_make(*s) for s in _SPECS]


def _baseline():
    def trigger(game, pnum):
        return gd.is_valid_luck_game(game)

    def factors(game, pnum):
        d_ally, d_enemy, d_any = gd.spawn_proximity(game, pnum)
        out = {"ally_dist": d_ally, "enemy_dist": d_enemy, "nearest_player_dist": d_any,
               "gold_dist": gd.spawn_metric(game, pnum, "spawn_gold_d"),
               "stone_dist": gd.spawn_metric(game, pnum, "spawn_stone_d"),
               "food_dist": gd.spawn_metric(game, pnum, "spawn_food_d"),
               "vil_perimeter": gd.spawn_metric(game, pnum, "vil_perim")}
        return {k: round(v, 1) for k, v in out.items() if v is not None}

    return Classification(
        key="luck_baseline", title="All valid spawns (baseline)", version=1, category="luck",
        trigger_spec="every player in a valid Nomad 6/8p game (balanced winner) — the 50% reference",
        requirements=_REQ, trigger=trigger, factors=factors,
        factor_specs=[dict(metric=m, label=m.replace("_", " ").title(), kind="count")
                      for m in ("ally_dist", "enemy_dist", "nearest_player_dist",
                                "gold_dist", "stone_dist", "food_dist", "vil_perimeter")])


CLASSIFICATIONS.append(_baseline())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_luck_classifications.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Lint**

Run: `ruff check utils/classifications/defs/luck.py`
Expected: clean (the `# noqa: E731` on the lambda assignments suppresses the lambda-assignment warning).

- [ ] **Step 6: Commit**

```bash
git add utils/classifications/defs/luck.py tests/test_luck_classifications.py
git commit -m "feat(classifications): 11 luck use cases + luck_baseline cohort"
```

---

### Task 5: Register the luck classifications

**Files:**
- Modify: `utils/classifications/registry.py`
- Test: `tests/test_registry_luck.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_registry_luck.py
from utils.classifications.registry import REGISTRY

LUCK_KEYS = {
    "spawn_near_enemy", "spawn_near_ally", "spawn_isolated", "spawn_near_gold", "spawn_gold_poor",
    "spawn_near_stone", "spawn_stone_poor", "spawn_near_food", "spawn_food_poor",
    "tight_villagers", "scattered_villagers", "luck_baseline",
}


def test_all_luck_keys_registered_with_category_luck():
    assert LUCK_KEYS <= set(REGISTRY)
    for k in LUCK_KEYS:
        assert REGISTRY[k].category == "luck"


def test_strategy_keys_untouched():
    assert REGISTRY["knight_rush"].category == "strategy"
    assert REGISTRY["boom_to_imp"].category == "strategy"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_registry_luck.py -v`
Expected: FAIL — `KeyError: 'spawn_near_enemy'` / assertion on subset.

- [ ] **Step 3: Wire the registry**

In `utils/classifications/registry.py`: add `luck` to the `from utils.classifications.defs import (...)` import block, and append its classifications to `_ALL`. Change the final lines from:

```python
_ALL = [
    archer_rush.CLASSIFICATION,
    ...
    boom_to_imp.CLASSIFICATION,
]

REGISTRY = {c.key: c for c in _ALL}
```

to:

```python
_ALL = [
    archer_rush.CLASSIFICATION,
    ...
    boom_to_imp.CLASSIFICATION,
] + luck.CLASSIFICATIONS

REGISTRY = {c.key: c for c in _ALL}
```

(Add `luck,` to the imported names — note `luck` exposes `CLASSIFICATIONS` (a list), not a single `CLASSIFICATION`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_registry_luck.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Full suite + lint**

Run: `pytest tests/ -q && ruff check utils/classifications/`
Expected: all pass; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add utils/classifications/registry.py tests/test_registry_luck.py
git commit -m "feat(classifications): register luck family in REGISTRY"
```

---

### Task 6: Web — expose `category` + add the Luck tab

The API returns each use case's `category`; the front-end gets a new "Luck" page (By-factor / By-player), and the Strategies tab is filtered to exclude luck rows. The `luck_baseline` row provides the denominator N and the 50% reference; it is not rendered as a factor.

**Files:**
- Modify: `bot/web.py:426-432` (the `strategies.append({...})` dict)
- Modify: `bot/web_page.html` (nav link, `page-luck`, JS)

- [ ] **Step 1: Add `category` to the API payload**

In `bot/web.py`, inside `handle_strategies`, in the `strategies.append({...})` dict (around line 426), add a `category` key sourced from the registry object `c`:

```python
		strategies.append({
			"key": key, "title": c.title, "phase": _STRATEGY_PHASE.get(key, ""),
			"category": getattr(c, "category", "strategy"),
			"condition": c.trigger_spec, "games": tg, "players": len(roster),
			"wins": tw, "losses": tl,
			"winrate": round(100 * tw / (tw + tl)) if (tw + tl) else None,
			"roster": roster, "top_civs": top_civs, "top_players": top_players,
		})
```

- [ ] **Step 2: Verify the API change in isolation**

Run: `python -c "import ast,sys; ast.parse(open('bot/web.py').read()); print('web.py parses OK')"`
Expected: `web.py parses OK`. (Full end-to-end is verified after the populate + sync steps; the bot reads Railway, which has no luck rows until Task 8.)

- [ ] **Step 3: Add the Luck nav link**

In `bot/web_page.html`, after the Strategies `tb-link` (line ~656), add:

```html
  <div class="tb-link" onclick="showPage('luck')" style="cursor:pointer;">
    <span>Luck</span>
  </div>
```

- [ ] **Step 4: Add the Luck page markup**

In `bot/web_page.html`, after the Strategies page `</div>` that closes `#page-strategies` (line ~709), add:

```html
  <!-- Luck Page (spawn / map factors) -->
  <div class="page" id="page-luck">
    <div class="stats-container">
      <header>
        <h1>Luck Insights</h1>
        <div class="subtitle">Map-given spawn factors &mdash; settle position, nearby resources &amp; villager spread vs win rate (baseline 50%)</div>
      </header>
      <div class="strat-controls">
        <div class="strat-toggle">
          <button id="luck-tab-total" class="strat-tab selected" onclick="setLuckView('total')">By factor</button>
          <button id="luck-tab-player" class="strat-tab" onclick="setLuckView('player')">By player</button>
        </div>
        <select id="luck-player-select" style="display:none" onchange="onLuckPlayer(this.value)"></select>
      </div>
      <div class="table-wrap">
        <table class="stats-table" id="luck-table">
          <thead><tr id="luck-header"></tr></thead>
          <tbody id="luck-body"><tr><td class="loading">Loading luck factors&hellip;</td></tr></tbody>
        </table>
      </div>
      <footer>Facts only &mdash; Nomad 4v4 player-games; 50% baseline by construction.</footer>
    </div>
  </div>
```

- [ ] **Step 5: Wire `showPage` + initial load to handle 'luck'**

In `bot/web_page.html`, in `showPage` (line ~833), add a luck branch next to the dashboard one:

```javascript
  if (page === 'dashboard') loadDashboard();
  if (page === 'luck') initLuck();
```

- [ ] **Step 6: Filter luck rows out of the Strategies views**

In `bot/web_page.html`, in `renderStratTotal` (line ~1015), change the sort source from `stratData.slice()` to a strategy-only copy. Replace:

```javascript
  var rows = stratData.slice().sort(function (a, b) {
```
with:
```javascript
  var rows = stratData.filter(function (s) { return s.category !== 'luck'; }).sort(function (a, b) {
```

In `renderStratPlayer` (line ~1038), change `stratData.forEach(` to skip luck:

```javascript
  stratData.forEach(function (s) {
    if (s.category === 'luck') return;
    var p = (s.roster || []).find(function (x) { return x.player === stratPlayer; });
```

- [ ] **Step 7: Add the Luck JS (factor + player views)**

In `bot/web_page.html`, after `renderStratPlayer` (after line ~1056), add:

```javascript
/* ─── Luck Insights (spawn / map factors) ─── */
var luckView = 'total', luckPlayer = '';

function luckRows() { return stratData.filter(function (s) { return s.category === 'luck' && s.key !== 'luck_baseline'; }); }
function luckBaseline() { return stratData.find(function (s) { return s.key === 'luck_baseline'; }); }

async function initLuck() {
  if (!stratData.length) { try { await initStrategies(); } catch (e) {} }
  buildLuckPlayerSelect();
  renderLuck();
}

function buildLuckPlayerSelect() {
  var base = luckBaseline();
  var players = base ? (base.roster || []).slice().sort(function (a, b) { return b.games - a.games; }).map(function (p) { return p.player; }) : [];
  if (!luckPlayer && players.length) luckPlayer = players[0];
  document.getElementById('luck-player-select').innerHTML = players.map(function (p) {
    return '<option value="' + esc(p) + '"' + (p === luckPlayer ? ' selected' : '') + '>' + esc(p) + '</option>';
  }).join('');
}

function setLuckView(v) {
  luckView = v;
  document.getElementById('luck-tab-total').classList.toggle('selected', v === 'total');
  document.getElementById('luck-tab-player').classList.toggle('selected', v === 'player');
  document.getElementById('luck-player-select').style.display = v === 'player' ? '' : 'none';
  renderLuck();
}

function onLuckPlayer(p) { luckPlayer = p; renderLuck(); }

function deltaCell(wr) {
  if (wr == null) return '<td>-</td>';
  var d = wr - 50, cls = d > 0 ? 'wr wr-high' : d < 0 ? 'wr wr-low' : 'wr wr-mid';
  return '<td class="' + cls + '">' + (d > 0 ? '+' : '') + d + '</td>';
}

function renderLuck() { if (luckView === 'total') renderLuckTotal(); else renderLuckPlayer(); }

function renderLuckTotal() {
  document.getElementById('luck-header').innerHTML =
    '<th>Factor</th><th>Games</th><th>% of games</th><th>Win %</th><th>&Delta; vs 50</th><th>Condition</th>';
  var base = luckBaseline(), N = base ? base.games : 0;
  var rows = luckRows().slice().sort(function (a, b) {
    return Math.abs((b.winrate == null ? 50 : b.winrate) - 50) - Math.abs((a.winrate == null ? 50 : a.winrate) - 50);
  });
  document.getElementById('luck-body').innerHTML = rows.map(function (s) {
    var pct = N ? Math.round(100 * s.games / N) + '%' : '-';
    return '<tr><td>' + esc(s.title) + '</td><td class="games">' + s.games + '</td><td class="games">' + pct + '</td>'
      + wrPctCell(s.winrate) + deltaCell(s.winrate)
      + '<td class="strat-cond">' + esc(s.condition || '') + '</td></tr>';
  }).join('') || '<tr><td colspan="6" class="loading">No luck data yet.</td></tr>';
}

function renderLuckPlayer() {
  document.getElementById('luck-header').innerHTML =
    '<th>Factor</th><th>Games</th><th>% of luck games</th><th>Win %</th><th>&Delta; vs 50</th>';
  var base = luckBaseline();
  var pb = base ? (base.roster || []).find(function (x) { return x.player === luckPlayer; }) : null;
  var N = pb ? pb.games : 0;
  var rows = luckRows().map(function (s) {
    var p = (s.roster || []).find(function (x) { return x.player === luckPlayer; });
    return { title: s.title, games: p ? p.games : 0, winrate: p ? p.winrate : null };
  }).sort(function (a, b) { return b.games - a.games; });
  document.getElementById('luck-body').innerHTML = rows.map(function (r) {
    var pct = N ? Math.round(100 * r.games / N) + '%' : '-';
    return '<tr><td>' + esc(r.title) + '</td><td class="games">' + r.games + '</td><td class="games">' + pct + '</td>'
      + wrPctCell(r.winrate) + deltaCell(r.winrate) + '</tr>';
  }).join('') || '<tr><td colspan="5" class="loading">No data for this player.</td></tr>';
}
```

- [ ] **Step 8: Sanity-check the HTML loads (no JS syntax errors)**

Run: `python -c "open('bot/web_page.html').read(); print('read ok')"` then open the file in a browser if convenient. (Programmatic JS validation isn't available offline; verify visually after Task 8 deploy, or by serving `bot/web.py` locally against the populated DB.)
Expected: `read ok`; the new functions reference only existing helpers (`esc`, `wrPctCell`, `wrPctClass`, `initStrategies`, `stratData`).

- [ ] **Step 9: Commit**

```bash
git add bot/web.py bot/web_page.html
git commit -m "feat(web): Luck tab (spawn/map factors) + category in /api/strategies"
```

---

### Task 7: Populate the local DB at v4 + verify

Re-parse all local replays at the v4 extractor (re-classifies strategy + new luck use cases) into the local `data/analysis.db`, then verify the luck counts match the calibration survey. **This writes only the local SQLite DB — not prod.**

- [ ] **Step 1: Reset the ledger so the ingester re-parses every match at v4**

```bash
python -c "
from utils.classifications.pipeline import localdb
c = localdb.connect(); localdb.ensure_schema(c)
c.execute(\"UPDATE ingest_ledger SET status='pending' WHERE status IN ('ingested','parse_failed')\")
c.commit()
print('pending:', len(localdb.pending_match_ids(c)))
"
```
Expected: `pending:` a few hundred (the previously-ingested matches, now queued for re-parse).

- [ ] **Step 2: Tell the ingester the downloader is finished (all replays already on disk)**

```bash
touch data/replays/.done
```

- [ ] **Step 3: Run the ingester (re-parse v4 + classify, sole writer)**

Run: `PYTHONPATH=.replay_scratch python -m utils.classifications.pipeline.ingester`
Expected: periodic `ingester: ingested=… parse_failed=… unavailable=… pending=…` lines, ending `ingester DONE.` with `ingested` ≈ 730+ and `parse_failed` ≈ 3 (the known corrupt files). Re-parsing the full corpus takes a few minutes.

- [ ] **Step 4: Verify luck counts + win% against the calibration survey**

```bash
python -c "
import sqlite3
c = sqlite3.connect('data/analysis.db')
base = c.execute(\"SELECT COUNT(*) FROM cls_results WHERE key='luck_baseline'\").fetchone()[0]
print('luck_baseline N =', base, '(expect ~5000-5200)')
for k in ('spawn_near_ally','spawn_isolated','spawn_gold_poor','tight_villagers'):
    r = c.execute('SELECT COUNT(*), AVG(winner)*100 FROM cls_results WHERE key=?', (k,)).fetchone()
    print('%-20s n=%4d  win%%=%.1f  (%%games=%.1f)' % (k, r[0], r[1] or 0, 100.0*r[0]/base))
"
```
Expected (within a few points of the survey): `near_ally` win% ~44, `spawn_isolated` ~52, `gold_poor` ~48, `tight_villagers` ~52; each `%games` ~20% (isolated ~25%). If `luck_baseline N` is 0, the registry/extract wiring didn't take — stop and debug before proceeding.

- [ ] **Step 5: Commit the calibration tool + ignore its cache**

Add the calibration script (kept as the re-calibration tool) and gitignore its throwaway parse cache and the local DB.

```bash
printf '\n# luck analysis local artifacts\ndata/analysis.db\ndata/.luck_calib.json\n' >> .gitignore
git add utils/classifications/_calibrate_luck.py .gitignore
git commit -m "chore(luck): keep calibration tool; ignore local analysis DB + calib cache"
```

(Confirm `data/analysis.db` and `data/.luck_calib.json` are not already tracked; if `.gitignore` already lists `analysis.db`, skip the duplicate line.)

---

### Task 8: Final review, then GATED prod sync + deploy

- [ ] **Step 1: Dispatch a final full-implementation code review** (subagent-driven-development final reviewer) over the whole branch diff vs `main`. Address any blocking findings.

- [ ] **Step 2: Run the full test suite + lint one more time**

Run: `pytest tests/ -q && ruff check .`
Expected: all pass; ruff clean.

- [ ] **Step 3: STOP — request explicit approval for prod actions.**

The remaining steps touch production and require explicit per-action user buy-in (global rule):
  - **(a) Sync** the local `cls_*` (now including luck) to Railway: `python -m utils.classifications.pipeline.sync` — prints per-table `local=… remote=… OK` and `SYNC VERIFIED`.
  - **(b) Deploy** the web changes by merging/pushing `feat/luck-spawn-analysis` to `main` (Railway auto-deploys).

Do **not** run either until the user says so. Present them as two separate yes/no asks. After the sync, the Luck tab renders live; after deploy, the new tab/JS ship.

- [ ] **Step 4: Finish the branch** via superpowers:finishing-a-development-branch (merge locally / PR / keep), per the user's choice.

---

## Self-review

**Spec coverage:**
- extract v4 (settle + nearest gold/stone/food + villager perimeter) → Task 2 ✅
- gamedata accessors + `is_valid_luck_game` → Task 3 ✅
- `Classification.category` → Task 1 ✅
- `_luck` builder + 11 use cases + `luck_baseline` → Task 4 ✅
- registry wiring → Task 5 ✅
- web Luck tab + category in API + Strategies filtered → Task 6 ✅
- populate local + verify → Task 7 ✅
- gated sync + deploy → Task 8 ✅
- frozen thresholds → encoded in Task 4 `_SPECS` ✅
- data hygiene (drop no-winner / non-6-8p) → `is_valid_luck_game` (Task 3) ✅
- denominator/baseline cohort → `luck_baseline` (Task 4) + Luck tab N (Task 6) ✅
- testing (pure-function unit tests) → Tasks 1,2,3,4,5 ✅

**Type/name consistency:** metric field names are identical across extract (`settle_tc_xy`, `spawn_gold_d`, `spawn_stone_d`, `spawn_food_d`, `vil_perim`), gamedata (`spawn_metric` reads those keys), and the luck builder (`_FIELD("spawn_gold_d")` …). Factor labels (`gold_dist`, `vil_perimeter`, …) are consistent between the 11 use cases and `luck_baseline`. The 12 luck keys in Task 4 `_SPECS` + `luck_baseline` match the `LUCK_KEYS` set asserted in Task 5.

**Placeholder scan:** no TBD/TODO; every code step shows complete code; thresholds are concrete; commands have expected output.
