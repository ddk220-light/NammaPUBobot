# Match Cards Revamp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Match Cards embed discriminating instead of flat — replace the compressed impact score and always-on participation tags with medals, real strategy labels, raw stat lines including eAPM, and a spawn phrase.

**Architecture:** A new pure module `bot/replay_stats/card_scoring.py` forks the scoring model used only by the Match Cards; `bot/replay_stats/card_query.py` holds the new per-match DB reads; `bot/post_game.py` renders. `bot/replay_stats/scoring.py` is left byte-identical so the web profile, the stored `rs_player_game_tags` and the Tale of the Tape embed are unaffected.

**Tech Stack:** Python 3.11, nextcord (embeds), aiomysql via `core.database.db`, pytest 8.3.3.

## Global Constraints

- **Indentation: `bot/` uses TABS.** `bot/post_game.py`, `card_scoring.py` and `card_query.py` are all tab-indented. `utils/` and `tests/` use 4 spaces. Do not mix.
- **`bot/replay_stats/scoring.py` must not change.** Verify with `git diff --exit-code bot/replay_stats/scoring.py` before every commit.
- **`card_scoring.py` must be pure:** no DB access, no `core` imports, no relative imports, no `import bot....`. It must be loadable standalone by path.
- **No async test functions.** There is no pytest-asyncio/anyio plugin and CI installs only `pytest==8.3.3`. An `async def test_...` is **silently skipped**, not run. Test async code with a sync function calling `asyncio.run(...)`.
- **`monkeypatch` object form only:** `monkeypatch.setattr(module_object, "db", Fake())`. The string form `monkeypatch.setattr("core.database.db", ...)` raises AttributeError — `core/` is a namespace package with no `__init__.py`.
- **Patch the consuming module's `db`, not `core.database`'s.** Each module does `from core.database import db` at module scope, binding its own reference.
- **Join `rs_*` and `cls_*` tables on `(aoe2_match_id, player_number)`, never `profile_id`** — `profile_id` is a nullable denormalisation on every long-form table and NULL never matches in a join.
- **Never compute average eAPM from `rs_player_apm` buckets.** Bucket rows are sparse (zero-action minutes have no row), so any average over them divides by active minutes. The parity-preserving average is `rs_player_games.eapm`.
- Lint: `ruff check .` — line-length 120.
- Run tests: `pytest tests/ -v`

---

## Context an implementer needs

### Measured facts about the live data (2026-07-29, read-only)

| Fact | Value | Consequence |
|---|---|---|
| Player-games | 8,885 across 1,126 matches | |
| `rs_player_games.eapm` populated | 8,885 / 8,885 (range 3–80) | Average eAPM always available |
| `rs_player_apm` rows | **0** | Peak eAPM renders nowhere until this branch deploys and new matches ingest. Must degrade silently. |
| Player-games with ≥1 strategy label | 5,539 (62.3%) | Median match has 6 of 8 players labelled |
| Player-games with 2–3 strategy labels | 932 | All are shown (per decision) |
| Player-games with a spawn key | 4,743 (53%) | Omit silently when absent |
| Player-games with `rs_player_events` | 2,722 | Events start at parser `+3`; recent matches ~99% |
| `age_reliable = 0` | 24 (0.3%) | Fix is cheap and correct, rarely triggers |
| No `imperial_s` **and** `mil_pre_imperial == military` | 2,370 (26.7%) | The inflation bug is real and common — fix matters |
| Whole-match zero production | 352 matches (31%) | Always all-or-nothing per match, never individual players |

### Exact identifiers (verified against the live DB — do not guess)

- **Buildings** (`rs_player_buildings.building`, with a `count` column): `'Farm'`, `'Town Center'`.
- **Events** (`rs_player_events`): `kind` is always `'queue'`; `t_s` is the click time in seconds and is **never NULL** in practice; `amount` is the batch size; `is_military` is 0/1; `category` is e.g. `'villager'`, `'knight_line'`.
- **Strategy keys** (`cls_results.key`) — exactly these 17, and **the card must filter to this list**, because `cls_results` also holds 12 luck/spawn keys in the same table with no category column:
  `archer_rush, scout_rush, maa_rush, knight_rush, crossbow_rush, cav_archer_rush, camel_rush, ram_push, forward_castle, safe_castle, late_knight, late_crossbow, late_cav_archer, late_camel, late_unique, late_ram, boom_to_imp`
- **Spawn keys**: `spawn_isolated`, `spawn_near_ally`, `spawn_near_enemy`.
- **Display titles** come from `cls_classifications.title` (e.g. `knight_rush` → `'Knight Rush'`). This is the same source `/insights` uses. Do **not** add a hardcoded label map.

### The current card path

- `post_match_analysis` (`bot/post_game.py:817`) → `build_match_cards_embed` (`:749`) and `build_match_analysis_embed` (`:722`), each independently calling `_analysis_rows` (`:692`) and `_team_names` (`:588`).
- `_analysis_rows` merges three queries and returns `list[dict]`. **Its `rs_player_games` SELECT (`:711-713`) omits `aoe2_match_id`, `player_number`, `age_reliable` and `eapm`** — all four are needed.
- `_impact_payload` (`:251`) builds the render payload. `_player_card_line` (`:404`) and `_team_card_fields` (`:419`) render it; `[:1024]` truncation is at `:430`.
- The DB adapter returns **dict rows** (`core/DBAdapters/mysql.py:68` pins `DictCursor`).

### Decisions that differ from the 2026-07-28 spec

1. **No tech terms in the scoring model.** `rs_player_techs` is not read at all. This makes `ECO_MIX`/`ARMY_MIX` identical to `scoring.py`'s, so their calibrated thresholds still apply and **no calibration phase is needed**.
2. **All strategy labels are shown**, not the earliest-phase one. Phase is not stored anywhere, so this removes the need for it entirely.
3. **Spawn comes from the three `spawn_*` classification keys**, not raw distances from `cls_result_metrics`.
4. **Thresholds are inherited verbatim** from `scoring.py`. Only the new production-coverage bar is a fresh number.

---

## File Structure

| File | Responsibility |
|---|---|
| `bot/replay_stats/card_scoring.py` (create) | Pure model: component scores, medal allocation, team tag allocation, production coverage. No DB, no imports outside stdlib. |
| `bot/replay_stats/card_query.py` (create) | The new per-match DB reads, each independently failure-isolated. Returns plain dicts keyed by `player_number`. |
| `bot/post_game.py` (modify) | Card payload + render. Adds columns to `_analysis_rows`, hoists the roster fetch, rewrites `_player_card_line`/`_team_card_fields`. |
| `tests/test_card_scoring.py` (create) | Unit tests for the pure model. |
| `tests/test_card_query.py` (create) | Unit tests for the read layer with a fake db. |
| `tests/test_post_game.py` (modify) | Extend for the new render and the hoisted fetch. |

---

## Task 1: `card_scoring.py` — component scores with the correctness fixes

**Files:**
- Create: `bot/replay_stats/card_scoring.py`
- Test: `tests/test_card_scoring.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ECO_MIX`, `ARMY_MIX`, `IMPACT_WEIGHTS`, `TH`, `REQUIRED_COLUMNS`, `component_scores(row, group) -> dict`, `carry_sort_key(payload) -> tuple`.

`component_scores` returns a dict with keys `eco`, `army`, `early_eco`, `early_army`, `reboom`, `impact` — all ints 0–100.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_card_scoring.py` (4-space indent):

```python
"""Unit tests for the Match Cards scoring fork."""

import bot.replay_stats.card_scoring as cs


def _row(**kw):
    base = dict(villagers=100, vil_pre_castle=40, military=50,
                mil_pre_castle=10, mil_pre_imperial=30,
                imperial_s=1800, age_reliable=1, nick="p")
    base.update(kw)
    return base


def test_scores_are_50_when_every_player_is_identical():
    group = [_row(), _row(), _row()]
    s = cs.component_scores(group[0], group)
    assert s["eco"] == 50
    assert s["army"] == 50
    assert s["impact"] == 50


def test_more_villagers_than_the_group_raises_eco_above_50():
    group = [_row(villagers=200, vil_pre_castle=80), _row(), _row()]
    assert cs.component_scores(group[0], group)["eco"] > 50


def test_timing_columns_are_not_in_required_columns():
    assert "feudal_s" not in cs.REQUIRED_COLUMNS
    assert "castle_s" not in cs.REQUIRED_COLUMNS


def test_required_columns_covers_every_column_the_mixes_read():
    mixed = {k for k, _ in cs.ECO_MIX} | {k for k, _ in ARMY_KEYS}
    assert mixed <= set(cs.REQUIRED_COLUMNS)


ARMY_KEYS = [("military", 0.55), ("mil_pre_imperial", 0.25), ("mil_pre_castle", 0.20)]


def test_impact_weights_sum_to_one_and_exclude_timing():
    assert dict(cs.IMPACT_WEIGHTS).keys() == {"army", "eco"}
    assert abs(sum(w for _, w in cs.IMPACT_WEIGHTS) - 1.0) < 1e-9


def test_missing_value_is_excluded_not_scored_as_average():
    """A None must not read as 'exactly match average'."""
    group = [_row(villagers=None), _row(villagers=10), _row(villagers=200)]
    # vil_pre_castle is identical across the group, so with villagers excluded
    # and the remaining weight renormalised the score must be exactly 50.
    assert cs.component_scores(group[0], group)["eco"] == 50


def test_unreliable_ages_drop_the_pre_age_army_terms():
    """age_reliable == 0 must not let junk age splits score."""
    group = [_row(age_reliable=0, mil_pre_castle=999, mil_pre_imperial=999),
             _row(), _row()]
    # Only the `military` term survives, and it equals the group average.
    assert cs.component_scores(group[0], group)["army"] == 50


def test_never_reached_imperial_does_not_inflate_the_army_score():
    """extract.py sets mil_pre_imperial == military when Imperial was never
    clicked, which would otherwise score a Castle-Age turtle as all-early."""
    turtle = _row(imperial_s=None, military=50, mil_pre_imperial=50)
    reached = _row(imperial_s=1800, military=50, mil_pre_imperial=10)
    group = [turtle, reached, _row()]
    assert cs.component_scores(turtle, group)["army"] <= \
        cs.component_scores(reached, group)["army"] + 1


def test_carry_sort_key_orders_by_impact_then_army_then_eco_then_nick():
    a = {"impact_score": 60, "army_score": 50, "eco_score": 50, "nick": "b"}
    b = {"impact_score": 60, "army_score": 50, "eco_score": 50, "nick": "a"}
    assert sorted([a, b], key=cs.carry_sort_key)[0] is b
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest tests/test_card_scoring.py -v
```
Expected: collection error — `ModuleNotFoundError: No module named 'bot.replay_stats.card_scoring'`.

- [ ] **Step 3: Write the implementation**

Create `bot/replay_stats/card_scoring.py` — **TABS**:

```python
# -*- coding: utf-8 -*-
"""Scoring model for the Match Cards embed only.

A deliberate fork of scoring.py. That module is consumed by the stored
rs_player_game_tags table, the web profile API and the Tale of the Tape embed,
so changing it in place would strand historical tag rows and shift surfaces this
revamp does not own. scoring.py is therefore left byte-identical and this module
is imported only by build_match_cards_embed.

Differences from scoring.py:
  * No timing component. feudal_s / castle_s / imperial_s feed nothing.
  * Impact is army/eco only, renormalised from scoring.py's 0.45/0.32.
  * Missing values are excluded and the remaining weights renormalised,
    instead of being scored as match-average.
  * Age-derived terms are dropped when the parser flagged ages unreliable, and
    the pre-Imperial term is dropped for players who never clicked Imperial
    (extract.py counts the whole game in that case, which inflates them).

ECO_MIX and ARMY_MIX are intentionally identical to scoring.py's, so the
percentile anchors calibrated for those distributions in July 2026 still hold
and TH is inherited verbatim rather than re-derived.

Pure: no DB, no core imports, no relative imports — so it stays unit-testable
and loadable standalone by path.
"""

ECO_MIX = (("villagers", 0.55), ("vil_pre_castle", 0.45))
ARMY_MIX = (("military", 0.55), ("mil_pre_imperial", 0.25), ("mil_pre_castle", 0.20))

# Renormalised from scoring.py's (army 0.45, eco 0.32) with timing removed.
IMPACT_WEIGHTS = (("army", 0.58), ("eco", 0.42))

REQUIRED_COLUMNS = tuple(dict.fromkeys(
	[k for k, _ in ECO_MIX] + [k for k, _ in ARMY_MIX]
	+ ["age_reliable", "imperial_s", "eapm"]))

# Inherited verbatim from scoring.py: ECO_MIX and ARMY_MIX are unchanged, so
# the distributions these anchor on are unchanged too.
TH = {
	"all_in_army": 64,
	"all_in_eco_max": 48,
	"reboom_score": 70,
	"reboom_early_eco_max": 46,
	"reboom_eco_min": 55,
	# New. Share of two-minute buckets containing at least one production click.
	"production_coverage": 0.75,
}

# Terms that are only meaningful when the parser's age clicks are trustworthy.
_AGE_DEPENDENT = frozenset(("vil_pre_castle", "vil_pre_imperial",
                            "mil_pre_castle", "mil_pre_imperial"))


def _usable(row, key):
	"""Whether ``key`` on ``row`` carries real information for this player."""
	if row.get(key) is None:
		return False
	if key in _AGE_DEPENDENT and not row.get("age_reliable"):
		return False
	# extract.py documents that "before age X when never reached X counts the
	# whole game", so this term is the player's whole army, not their early one.
	if key == "mil_pre_imperial" and row.get("imperial_s") is None:
		return False
	return True


def _avg(rows, key):
	vals = [float(r[key]) for r in rows if _usable(r, key)]
	return sum(vals) / len(vals) if vals else None


def _std(rows, key):
	vals = [float(r[key]) for r in rows if _usable(r, key)]
	if len(vals) < 2:
		return 1.0
	mean = sum(vals) / len(vals)
	return max((sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5, 1.0)


def _z(row, rows, key):
	mean = _avg(rows, key)
	if mean is None:
		return None
	return max(-2.0, min(2.0, (float(row[key]) - mean) / _std(rows, key)))


def _mix(row, group, mix):
	"""Weighted mean of the z-scores that are actually available.

	Unlike scoring.py._z, an absent value contributes nothing and its weight is
	redistributed, so a partial replay cannot produce a confident mid score.
	"""
	total = 0.0
	weight = 0.0
	for key, w in mix:
		if not _usable(row, key):
			continue
		z = _z(row, group, key)
		if z is None:
			continue
		total += w * z
		weight += w
	return total / weight if weight else 0.0


def _score_component(value):
	return max(0, min(100, round(50 + value * 15)))


def component_scores(row, group):
	"""0-100 component + impact scores for one rs_player_games row, relative to
	``group`` (every player in the same match)."""
	eco_z = _mix(row, group, ECO_MIX)
	army_z = _mix(row, group, ARMY_MIX)
	early_eco_z = _z(row, group, "vil_pre_castle") if _usable(row, "vil_pre_castle") else 0.0
	early_army_z = _z(row, group, "mil_pre_castle") if _usable(row, "mil_pre_castle") else 0.0
	vil_z = _z(row, group, "villagers") if _usable(row, "villagers") else 0.0
	reboom_z = max(-2.0, min(2.0, (vil_z or 0.0) - (early_eco_z or 0.0)))
	scores = {
		"eco": _score_component(eco_z),
		"army": _score_component(army_z),
		"early_eco": _score_component(early_eco_z or 0.0),
		"early_army": _score_component(early_army_z or 0.0),
		"reboom": _score_component(reboom_z),
	}
	scores["impact"] = round(sum(scores[k] * w for k, w in IMPACT_WEIGHTS))
	return scores


def carry_sort_key(payload):
	"""Deterministic ordering inside a team: highest impact first, army then eco
	break ties, nick keeps it stable when everything ties."""
	return (
		-(payload.get("impact_score") or 0),
		-(payload.get("army_score") or 0),
		-(payload.get("eco_score") or 0),
		str(payload.get("nick") or ""),
	)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/test_card_scoring.py -v
```
Expected: all 10 PASS.

- [ ] **Step 5: Confirm `scoring.py` is untouched, then commit**

```bash
git diff --exit-code bot/replay_stats/scoring.py && ruff check . && git add bot/replay_stats/card_scoring.py tests/test_card_scoring.py && git commit -m "feat(cards): fork the card scoring model without timing or tech terms"
```

---

## Task 2: `card_scoring.py` — medals, production coverage, team tags

**Files:**
- Modify: `bot/replay_stats/card_scoring.py`
- Test: `tests/test_card_scoring.py`

**Interfaces:**
- Consumes: `component_scores`, `TH` from Task 1.
- Produces:
  - `assign_medals(payloads) -> list[dict]` — parallel to `payloads`, each `{"military_medal": int|None, "villager_medal": int|None}` where the int is 1, 2 or 3.
  - `production_coverage(click_times_s, match_end_s) -> float|None` — share of two-minute buckets holding ≥1 click, or `None` when there is no usable timeline.
  - `assign_team_tags(payloads) -> list[list[str]]` — parallel to `payloads`, the tag display names each player earned.

Each payload passed in must carry: `nick`, `team`, `villagers`, `military`, `eco_score`, `army_score`, `early_eco_score`, `reboom_score`, `production_coverage`, `has_production`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_card_scoring.py`:

```python
def _p(nick, team=0, vil=100, mil=50, **kw):
    base = dict(nick=nick, team=team, villagers=vil, military=mil,
                eco_score=50, army_score=50, early_eco_score=50,
                reboom_score=50, production_coverage=None, has_production=True)
    base.update(kw)
    return base


def test_medals_go_to_the_top_three_across_the_whole_match():
    ps = [_p("a", mil=90), _p("b", mil=80), _p("c", mil=70),
          _p("d", team=1, mil=60)]
    medals = cs.assign_medals(ps)
    assert [m["military_medal"] for m in medals] == [1, 2, 3, None]


def test_medals_are_match_wide_not_per_team():
    ps = [_p("a", team=0, mil=10), _p("b", team=1, mil=90)]
    medals = cs.assign_medals(ps)
    assert medals[1]["military_medal"] == 1
    assert medals[0]["military_medal"] == 2


def test_villager_and_military_medals_are_independent():
    ps = [_p("a", vil=200, mil=1), _p("b", vil=1, mil=200)]
    medals = cs.assign_medals(ps)
    assert medals[0]["villager_medal"] == 1
    assert medals[0]["military_medal"] == 2
    assert medals[1]["military_medal"] == 1


def test_players_without_production_are_excluded_from_medals():
    ps = [_p("a", has_production=False, mil=999), _p("b", mil=10)]
    medals = cs.assign_medals(ps)
    assert medals[0]["military_medal"] is None
    assert medals[1]["military_medal"] == 1


def test_fewer_than_three_players_with_data_still_awards_what_it_can():
    ps = [_p("a", mil=50), _p("b", mil=40)]
    assert [m["military_medal"] for m in cs.assign_medals(ps)] == [1, 2]


def test_medal_ties_break_deterministically_by_other_count_then_nick():
    ps = [_p("zed", mil=50, vil=10), _p("amy", mil=50, vil=10)]
    medals = cs.assign_medals(ps)
    # Equal on both counts, so nick ascending decides: amy first.
    assert medals[1]["military_medal"] == 1
    assert medals[0]["military_medal"] == 2


def test_production_coverage_is_one_when_every_bucket_has_a_click():
    clicks = [0, 130, 250, 370]
    assert cs.production_coverage(clicks, 480) == 1.0


def test_production_coverage_drops_for_a_long_idle_gap():
    clicks = [0, 130, 700]
    cov = cs.production_coverage(clicks, 720)
    assert 0.0 < cov < 0.6


def test_batch_queued_production_still_marks_its_bucket():
    """One click can queue five villagers, so bucketing must not read an
    efficient batch as idleness."""
    assert cs.production_coverage([10, 130, 250], 360) == 1.0


def test_production_coverage_is_none_without_a_timeline():
    assert cs.production_coverage([], 600) is None
    assert cs.production_coverage([10, 20], 0) is None


def test_a_tag_goes_to_the_highest_scorer_on_the_team_who_clears_the_bar():
    ps = [_p("a", army_score=70, eco_score=40),
          _p("b", army_score=66, eco_score=40)]
    tags = cs.assign_team_tags(ps)
    assert "Low-eco pressure" in tags[0]
    assert tags[1] == []


def test_nobody_gets_a_tag_when_nobody_clears_the_bar():
    ps = [_p("a", army_score=50, eco_score=50), _p("b", army_score=51)]
    assert cs.assign_team_tags(ps) == [[], []]


def test_tags_are_scoped_per_team_so_both_teams_can_award_one():
    ps = [_p("a", team=0, army_score=70, eco_score=40),
          _p("b", team=1, army_score=68, eco_score=40)]
    tags = cs.assign_team_tags(ps)
    assert "Low-eco pressure" in tags[0]
    assert "Low-eco pressure" in tags[1]


def test_one_player_can_sweep_several_tags():
    ps = [_p("a", army_score=70, eco_score=40, reboom_score=75,
             early_eco_score=40, production_coverage=0.9),
          _p("b")]
    assert len(cs.assign_team_tags(ps)[0]) == 3


def test_constant_production_needs_coverage_above_the_bar():
    below = [_p("a", production_coverage=0.5)]
    above = [_p("a", production_coverage=0.95)]
    assert cs.assign_team_tags(below) == [[]]
    assert "Constant production" in cs.assign_team_tags(above)[0]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest tests/test_card_scoring.py -v -k "medal or production or tag"
```
Expected: FAIL with `AttributeError: module 'bot.replay_stats.card_scoring' has no attribute 'assign_medals'`.

- [ ] **Step 3: Write the implementation**

Append to `bot/replay_stats/card_scoring.py` — **TABS**:

```python
# Medals rank on raw counts, deliberately distinct from the component scores.
# The medal answers "who made the most", which is a plain fact; the component
# score answers "who contributed most overall".
MEDAL_AXES = (("military_medal", "military", "villagers"),
              ("villager_medal", "villagers", "military"))

PRODUCTION_BUCKET_S = 120

TAG_NAMES = {
	"all_in_pressure": "Low-eco pressure",
	"reboom": "Recovery",
	"constant_production": "Constant production",
}


def assign_medals(payloads):
	"""Top-three medals per axis, ranked across every player in the match.

	Returns a list parallel to ``payloads``. Players with no production data are
	excluded entirely rather than ranked last — showing them bare would read as
	"played badly" when the truth is "not measured".
	"""
	out = [{"military_medal": None, "villager_medal": None} for _ in payloads]
	for field, primary, secondary in MEDAL_AXES:
		ranked = sorted(
			(i for i, p in enumerate(payloads) if p.get("has_production")),
			key=lambda i: (
				-(payloads[i].get(primary) or 0),
				-(payloads[i].get(secondary) or 0),
				str(payloads[i].get("nick") or ""),
			))
		for place, i in enumerate(ranked[:3], start=1):
			out[i][field] = place
	return out


def production_coverage(click_times_s, match_end_s):
	"""Share of two-minute buckets between the first click and match end that
	contain at least one production click.

	Bucketing rather than gap-measuring because production clicks are batched —
	one click can queue five villagers, covering roughly two minutes. A
	gap-based metric would read efficient batch-queuing as idleness, and
	correcting for that needs per-unit train times the parser does not store.
	"""
	times = sorted(t for t in (click_times_s or []) if t is not None)
	if not times or not match_end_s or match_end_s <= times[0]:
		return None
	first = times[0]
	span = match_end_s - first
	total = max(1, int(span // PRODUCTION_BUCKET_S) + (1 if span % PRODUCTION_BUCKET_S else 0))
	hit = {int((t - first) // PRODUCTION_BUCKET_S) for t in times if t <= match_end_s}
	return min(1.0, len(hit) / total)


def _tag_candidates(p):
	"""(tag key, ranking score) for every tag this player clears the bar for."""
	found = []
	if (p.get("army_score") or 0) >= TH["all_in_army"] \
			and (p.get("eco_score") or 0) <= TH["all_in_eco_max"]:
		found.append(("all_in_pressure", p.get("army_score") or 0))
	if (p.get("reboom_score") or 0) >= TH["reboom_score"] \
			and (p.get("early_eco_score") or 0) <= TH["reboom_early_eco_max"] \
			and (p.get("eco_score") or 0) >= TH["reboom_eco_min"]:
		found.append(("reboom", p.get("reboom_score") or 0))
	cov = p.get("production_coverage")
	if cov is not None and cov >= TH["production_coverage"]:
		found.append(("constant_production", cov))
	return found


def assign_team_tags(payloads):
	"""Award each tag to the single highest-scoring player on the team who
	clears its absolute threshold; nobody gets it if nobody clears.

	Tags describe game *shape*, which medals cannot express. Scope is per-team
	(medals are match-wide) — the tag answers "what was the shape of this
	player's game relative to their team".
	"""
	out = [[] for _ in payloads]
	by_team = {}
	for i, p in enumerate(payloads):
		if not p.get("has_production"):
			continue
		by_team.setdefault(p.get("team"), []).append(i)
	for members in by_team.values():
		best = {}
		for i in members:
			for key, score in _tag_candidates(payloads[i]):
				if key not in best or score > best[key][1] or (
						score == best[key][1]
						and str(payloads[i].get("nick") or "") < str(payloads[best[key][0]].get("nick") or "")):
					best[key] = (i, score)
		for key, (i, _score) in best.items():
			out[i].append(TAG_NAMES[key])
	for names in out:
		names.sort()
	return out
```

- [ ] **Step 4: Run the full test file**

```bash
pytest tests/test_card_scoring.py -v
```
Expected: all PASS (25 tests).

- [ ] **Step 5: Commit**

```bash
git diff --exit-code bot/replay_stats/scoring.py && ruff check . && git add bot/replay_stats/card_scoring.py tests/test_card_scoring.py && git commit -m "feat(cards): add medal allocation, production coverage and team tags"
```

---

## Task 3: `card_query.py` — the new per-match reads

**Files:**
- Create: `bot/replay_stats/card_query.py`
- Test: `tests/test_card_query.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `async def fetch_card_signals(aoe2_match_id, match_end_s) -> dict` returning exactly:

```python
{
  "buildings": {player_number: {"farms": int, "tcs": int}},
  "clicks":    {player_number: [t_s, ...]},
  "strategies":{player_number: [title, ...]},
  "spawn":     {player_number: "spawned alone" | "spawned next to enemy" | "spawned with team"},
  "peak_eapm": {player_number: int},
}
```

Every sub-fetch is independently wrapped, so one missing table or failing query degrades that signal to empty rather than blanking the card.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_card_query.py` (4-space indent):

```python
"""Unit tests for the Match Cards read layer."""

import asyncio

import bot.replay_stats.card_query as cq


class _FakeDB:
    """Returns canned rows per SQL fragment; records what was asked."""

    def __init__(self, responses, fail_on=None):
        self.responses = responses
        self.fail_on = fail_on or ()
        self.seen = []

    async def fetchall(self, sql, params=None):
        self.seen.append(sql)
        for fragment in self.fail_on:
            if fragment in sql:
                raise RuntimeError(f"simulated failure for {fragment}")
        for fragment, rows in self.responses.items():
            if fragment in sql:
                return rows
        return []


def _run(monkeypatch, db, match_end_s=600):
    monkeypatch.setattr(cq, "db", db)
    return asyncio.run(cq.fetch_card_signals(1, match_end_s))


def test_buildings_are_split_into_farms_and_tcs(monkeypatch):
    db = _FakeDB({"rs_player_buildings": [
        {"player_number": 1, "building": "Farm", "count": 14},
        {"player_number": 1, "building": "Town Center", "count": 3},
        {"player_number": 2, "building": "Farm", "count": 8},
        {"player_number": 2, "building": "Barracks", "count": 5},
    ]})
    out = _run(monkeypatch, db)
    assert out["buildings"][1] == {"farms": 14, "tcs": 3}
    assert out["buildings"][2] == {"farms": 8, "tcs": 0}


def test_only_the_seventeen_strategy_keys_are_returned(monkeypatch):
    db = _FakeDB({"cls_results c": [
        {"player_number": 1, "key": "knight_rush", "title": "Knight Rush"},
        {"player_number": 1, "key": "safe_castle", "title": "Safe Castle"},
    ]})
    out = _run(monkeypatch, db)
    assert out["strategies"][1] == ["Knight Rush", "Safe Castle"]


def test_strategy_query_constrains_by_the_key_allowlist(monkeypatch):
    """cls_results holds luck/spawn keys in the same table with no category
    column, so an unconstrained query would render 'All valid spawns' as a
    strategy label."""
    db = _FakeDB({})
    _run(monkeypatch, db)
    strategy_sql = next(s for s in db.seen if "cls_results c" in s)
    assert "knight_rush" in strategy_sql
    assert "luck_baseline" not in strategy_sql


def test_spawn_keys_map_to_phrases_with_enemy_taking_priority(monkeypatch):
    db = _FakeDB({"FROM cls_results WHERE": [
        {"player_number": 1, "key": "spawn_isolated"},
        {"player_number": 2, "key": "spawn_near_ally"},
        {"player_number": 2, "key": "spawn_near_enemy"},
    ]})
    out = _run(monkeypatch, db)
    assert out["spawn"][1] == "spawned alone"
    assert out["spawn"][2] == "spawned next to enemy"


def test_peak_eapm_is_the_max_bucket_per_player(monkeypatch):
    db = _FakeDB({"rs_player_apm": [
        {"player_number": 1, "peak": 89},
        {"player_number": 2, "peak": 71},
    ]})
    out = _run(monkeypatch, db)
    assert out["peak_eapm"] == {1: 89, 2: 71}


def test_an_empty_apm_table_yields_no_peaks_rather_than_zeros(monkeypatch):
    out = _run(monkeypatch, _FakeDB({}))
    assert out["peak_eapm"] == {}


def test_one_failing_query_does_not_break_the_others(monkeypatch):
    db = _FakeDB(
        {"rs_player_buildings": [{"player_number": 1, "building": "Farm", "count": 9}]},
        fail_on=("rs_player_apm",))
    out = _run(monkeypatch, db)
    assert out["buildings"][1]["farms"] == 9
    assert out["peak_eapm"] == {}


def test_clicks_are_grouped_by_player_in_time_order(monkeypatch):
    db = _FakeDB({"rs_player_events": [
        {"player_number": 1, "t_s": 300},
        {"player_number": 1, "t_s": 100},
        {"player_number": 2, "t_s": 50},
    ]})
    out = _run(monkeypatch, db)
    assert out["clicks"][1] == [100, 300]
    assert out["clicks"][2] == [50]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest tests/test_card_query.py -v
```
Expected: collection error — no module named `bot.replay_stats.card_query`.

- [ ] **Step 3: Write the implementation**

Create `bot/replay_stats/card_query.py` — **TABS**:

```python
# -*- coding: utf-8 -*-
"""Per-match reads that only the Match Cards embed needs.

Every fetch is independently guarded: a missing table or a failing query
degrades that one signal to empty rather than costing the whole card. All joins
key on (aoe2_match_id, player_number) — profile_id is a nullable
denormalisation on the long-form tables and NULL never matches in a join.
"""

from core.database import db

# cls_results mixes strategy rows and luck/spawn rows in one table with no
# category column, so the card must constrain by an explicit allowlist.
STRATEGY_KEYS = (
	"archer_rush", "scout_rush", "maa_rush", "knight_rush", "crossbow_rush",
	"cav_archer_rush", "camel_rush", "ram_push", "forward_castle", "safe_castle",
	"late_knight", "late_crossbow", "late_cav_archer", "late_camel",
	"late_unique", "late_ram", "boom_to_imp",
)

# Ordered by how much the phrase is worth saying: a nearby enemy is the most
# consequential spawn fact, so it wins when several keys fire for one player.
SPAWN_PHRASES = (
	("spawn_near_enemy", "spawned next to enemy"),
	("spawn_isolated", "spawned alone"),
	("spawn_near_ally", "spawned with team"),
)

FARM_BUILDING = "Farm"
TC_BUILDING = "Town Center"


async def _safe(coro, default):
	from core.console import log
	try:
		return await coro
	except Exception as e:
		log.error(f"Match card signal fetch failed: {e}")
		return default


async def _buildings(aoe2_match_id):
	rows = await db.fetchall(
		"SELECT player_number, building, count FROM rs_player_buildings "
		"WHERE aoe2_match_id=%s AND building IN (%s, %s)",
		[aoe2_match_id, FARM_BUILDING, TC_BUILDING])
	out = {}
	for r in rows or []:
		entry = out.setdefault(r["player_number"], {"farms": 0, "tcs": 0})
		field = "farms" if r["building"] == FARM_BUILDING else "tcs"
		entry[field] = int(r.get("count") or 0)
	return out


async def _clicks(aoe2_match_id):
	rows = await db.fetchall(
		"SELECT player_number, t_s FROM rs_player_events "
		"WHERE aoe2_match_id=%s AND t_s IS NOT NULL ORDER BY player_number, t_s",
		[aoe2_match_id])
	out = {}
	for r in rows or []:
		out.setdefault(r["player_number"], []).append(int(r["t_s"]))
	for times in out.values():
		times.sort()
	return out


async def _strategies(aoe2_match_id):
	placeholders = ",".join(["%s"] * len(STRATEGY_KEYS))
	rows = await db.fetchall(
		"SELECT c.player_number, c.`key`, r.title FROM cls_results c "
		"LEFT JOIN cls_classifications r ON r.`key`=c.`key` "
		f"WHERE c.aoe2_match_id=%s AND c.`key` IN ({placeholders}) "
		"ORDER BY c.player_number, c.`key`",
		[aoe2_match_id, *STRATEGY_KEYS])
	out = {}
	for r in rows or []:
		label = r.get("title") or str(r["key"]).replace("_", " ").title()
		out.setdefault(r["player_number"], []).append(label)
	return out


async def _spawn(aoe2_match_id):
	keys = [k for k, _ in SPAWN_PHRASES]
	placeholders = ",".join(["%s"] * len(keys))
	rows = await db.fetchall(
		"SELECT player_number, `key` FROM cls_results WHERE aoe2_match_id=%s "
		f"AND `key` IN ({placeholders})",
		[aoe2_match_id, *keys])
	found = {}
	for r in rows or []:
		found.setdefault(r["player_number"], set()).add(r["key"])
	out = {}
	for pnum, present in found.items():
		for key, phrase in SPAWN_PHRASES:
			if key in present:
				out[pnum] = phrase
				break
	return out


async def _peak_eapm(aoe2_match_id):
	"""Max per-minute bucket. Peak is not stored anywhere — it is derived.

	rs_player_apm is forward-only, so this is empty for every match ingested
	before the eAPM pipeline deployed. Callers must omit the figure rather than
	render a zero.
	"""
	rows = await db.fetchall(
		"SELECT player_number, MAX(actions) AS peak FROM rs_player_apm "
		"WHERE aoe2_match_id=%s GROUP BY player_number",
		[aoe2_match_id])
	return {r["player_number"]: int(r["peak"]) for r in rows or []
	        if r.get("peak") is not None}


async def fetch_card_signals(aoe2_match_id, match_end_s=None):
	"""Every card signal outside rs_player_games, keyed by player_number."""
	return {
		"buildings": await _safe(_buildings(aoe2_match_id), {}),
		"clicks": await _safe(_clicks(aoe2_match_id), {}),
		"strategies": await _safe(_strategies(aoe2_match_id), {}),
		"spawn": await _safe(_spawn(aoe2_match_id), {}),
		"peak_eapm": await _safe(_peak_eapm(aoe2_match_id), {}),
	}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/test_card_query.py -v
```
Expected: all 8 PASS.

- [ ] **Step 5: Commit**

```bash
ruff check . && git add bot/replay_stats/card_query.py tests/test_card_query.py && git commit -m "feat(cards): add the card-only per-match read layer"
```

---

## Task 4: Widen `_analysis_rows` and hoist the shared fetch

**Files:**
- Modify: `bot/post_game.py:692-719` (`_analysis_rows`), `:722-746` (`build_match_analysis_embed`), `:749-772` (`build_match_cards_embed`), `:817-853` (`post_match_analysis`)
- Test: `tests/test_post_game.py`

**Interfaces:**
- Produces: `_analysis_rows` rows now additionally carry `aoe2_match_id`, `player_number`, `age_reliable`, `eapm` and `duration_s`. Both embed builders gain an optional `rows` / `team_names` parameter so the caller can fetch once.

**Why:** the two builders always post together and each independently ran the same three queries. This task adds five more reads, which would otherwise double too.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_post_game.py` (**tabs** — this file is tab-indented):

```python
def test_analysis_rows_selects_every_column_the_card_needs():
	"""The card joins on (aoe2_match_id, player_number) and reads age_reliable
	and eapm, none of which the original SELECT carried."""
	import inspect

	src = inspect.getsource(pg._analysis_rows)
	for column in ("aoe2_match_id", "player_number", "age_reliable", "eapm", "duration_s"):
		assert column in src, f"{column} missing from the _analysis_rows SELECT"


def test_required_card_columns_are_all_selected():
	from bot.replay_stats import card_scoring
	import inspect

	src = inspect.getsource(pg._analysis_rows)
	for column in card_scoring.REQUIRED_COLUMNS:
		assert column in src, f"{column} is in REQUIRED_COLUMNS but not SELECTed"


def test_post_match_analysis_fetches_the_roster_once(monkeypatch):
	"""Both embeds always post together, so the shared reads must happen once."""
	import asyncio

	calls = []

	async def _rows(bot_match_id):
		calls.append(bot_match_id)
		return [{"user_id": 1, "nick": "a", "bot_team": 0, "result": "W"}]

	async def _names(channel_id, bot_match_id):
		return {0: "Alpha", 1: "Beta"}

	async def _channel_id(_):
		return 99

	async def _chart(_):
		return None

	channel = _FakeChannel(fail_with_file=False)
	monkeypatch.setattr(pg, "_analysis_rows", _rows)
	monkeypatch.setattr(pg, "_team_names", _names)
	monkeypatch.setattr(pg, "_match_channel_id", _channel_id)
	monkeypatch.setattr(pg, "_apm_chart_file", _chart)
	monkeypatch.setattr(pg, "_card_signals_for", lambda rows: {})

	class _FakeClient:
		def get_channel(self, _):
			return channel

	import types
	monkeypatch.setitem(__import__("sys").modules, "core.client",
	                    types.SimpleNamespace(dc=_FakeClient()))

	asyncio.run(pg.post_match_analysis(7))
	assert calls == [7], f"expected one roster fetch, got {len(calls)}"
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/test_post_game.py -v -k "analysis_rows or fetches_the_roster or required_card"
```
Expected: FAIL — the columns are absent and `_analysis_rows` is called twice.

- [ ] **Step 3: Widen the SELECT**

In `bot/post_game.py`, replace the `replay_rows` query inside `_analysis_rows` (currently lines 710-718) with — **TABS**:

```python
	replay_rows = await db.fetchall(
		"SELECT g.user_id, g.identity, g.civ, g.team AS replay_team, g.winner, "
		"g.aoe2_match_id, g.player_number, g.age_reliable, g.eapm, "
		"g.villagers, g.vil_pre_castle, g.vil_pre_imperial, g.military, "
		"g.mil_pre_castle, g.mil_pre_imperial, "
		"g.feudal_s, g.castle_s, g.imperial_s, rm.duration_s "
		"FROM rs_matches rm "
		"JOIN rs_player_games g ON g.aoe2_match_id=rm.aoe2_match_id "
		"WHERE rm.bot_match_id=%s "
		"ORDER BY g.team, g.identity",
		[bot_match_id])
```

`feudal_s` and `castle_s` stay in the SELECT because the Tale of the Tape still scores timing through the untouched `scoring.py`.

- [ ] **Step 4: Hoist the shared fetch**

Change both builders to accept pre-fetched data, keeping their current behaviour when called without it. Replace the signature and first lines of `build_match_analysis_embed` (`:722-724`):

```python
async def build_match_analysis_embed(channel_id, bot_match_id, rows=None, team_names=None):
	"""Replay-derived post-game team read. Built only after rs_* rows exist."""
	if rows is None:
		rows = await _analysis_rows(bot_match_id)
	if not rows:
		return None
	player_rows = [_impact_payload(row, rows) for row in rows]
	if not any(p.get("result") in ("W", "L") for p in player_rows):
		return None
	if team_names is None:
		team_names = await _team_names(channel_id, bot_match_id)
	lines = _match_analysis_lines(player_rows, team_names)
```

Then in `post_match_analysis`, replace the two builder calls (`:829-830`) with:

```python
		rows = await _analysis_rows(bot_match_id)
		team_names = await _team_names(channel_id, bot_match_id)
		cards = await build_match_cards_embed(channel_id, bot_match_id, rows, team_names)
		embed = await build_match_analysis_embed(channel_id, bot_match_id, rows, team_names)
```

`build_match_cards_embed` gets the same two extra parameters in Task 5.

- [ ] **Step 5: Run the tests**

```bash
pytest tests/test_post_game.py -v
```
Expected: all PASS. The `_card_signals_for` stub in the new test is satisfied in Task 5; until then remove that one `monkeypatch.setattr` line if it errors.

- [ ] **Step 6: Commit**

```bash
git diff --exit-code bot/replay_stats/scoring.py && ruff check . && git add bot/post_game.py tests/test_post_game.py && git commit -m "refactor(cards): widen the analysis SELECT and fetch the roster once"
```

---

## Task 5: Render the new card

**Files:**
- Modify: `bot/post_game.py` — add `_card_payload`, `_card_signals_for`; rewrite `_player_card_line` (`:404-416`) and `_team_card_fields` (`:419-433`); rewrite `build_match_cards_embed` (`:749-772`)
- Test: `tests/test_post_game.py`

**Interfaces:**
- Consumes: `card_scoring.component_scores`, `assign_medals`, `assign_team_tags`, `production_coverage`, `carry_sort_key`; `card_query.fetch_card_signals`.
- Produces: `_card_payload(row, group, signals) -> dict` carrying `nick`, `civ`, `team`, `result`, `strategies`, `spawn`, `villagers`, `military`, `farms`, `tcs`, `eapm`, `peak_eapm`, `has_production`, plus the component scores and `production_coverage`.

**Target rendering:**

```
🟩 Alpha · W

👑 Deepak — Franks · Knight Rush, Safe Castle
   ⚔⚔⚔ 🌾🌾 · `Low-eco pressure`
   84 vils · 46 military · 14 farms · 3 TC · 62 eAPM (pk 89) · spawned alone

• Ravi — Britons · Archer Rush
   71 vils · 38 military · 11 farms · 2 TC · 55 eAPM

• Kumar — Mayans · ⚠ partial replay data
```

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_post_game.py` (**tabs**):

```python
def _card_row(nick, team, **kw):
	base = dict(nick=nick, bot_team=team, civ="Franks", result="W",
	            player_number=1, villagers=100, military=50,
	            vil_pre_castle=40, mil_pre_castle=10, mil_pre_imperial=30,
	            imperial_s=1800, age_reliable=1, eapm=45, duration_s=2400)
	base.update(kw)
	return base


def _signals(**kw):
	base = dict(buildings={}, clicks={}, strategies={}, spawn={}, peak_eapm={})
	base.update(kw)
	return base


def test_card_line_shows_every_strategy_label_for_the_player():
	rows = [_card_row("a", 0)]
	sig = _signals(strategies={1: ["Knight Rush", "Safe Castle"]})
	line = pg._player_card_line(pg._card_payload(rows[0], rows, sig))
	assert "Knight Rush, Safe Castle" in line


def test_card_line_omits_the_strategy_segment_when_none_fired():
	rows = [_card_row("a", 0)]
	line = pg._player_card_line(pg._card_payload(rows[0], rows, _signals()))
	assert "None" not in line
	assert "?" not in line.split("\n")[0].split("—")[1]


def test_stats_line_shows_counts_and_eapm_with_peak():
	rows = [_card_row("a", 0, villagers=84, military=46, eapm=62)]
	sig = _signals(buildings={1: {"farms": 14, "tcs": 3}}, peak_eapm={1: 89})
	line = pg._player_card_line(pg._card_payload(rows[0], rows, sig))
	assert "84 vils" in line
	assert "46 military" in line
	assert "14 farms" in line
	assert "3 TC" in line
	assert "62 eAPM (pk 89)" in line


def test_peak_is_omitted_when_the_apm_table_has_no_rows():
	rows = [_card_row("a", 0, eapm=55)]
	line = pg._player_card_line(pg._card_payload(rows[0], rows, _signals()))
	assert "55 eAPM" in line
	assert "pk" not in line


def test_eapm_is_omitted_entirely_when_it_was_never_stored():
	rows = [_card_row("a", 0, eapm=None)]
	line = pg._player_card_line(pg._card_payload(rows[0], rows, _signals()))
	assert "eAPM" not in line


def test_spawn_phrase_is_appended_when_present():
	rows = [_card_row("a", 0)]
	line = pg._player_card_line(pg._card_payload(rows[0], rows,
	                                             _signals(spawn={1: "spawned alone"})))
	assert "spawned alone" in line


def test_no_production_renders_the_warning_instead_of_stats():
	rows = [_card_row("a", 0, villagers=0, military=0)]
	line = pg._player_card_line(pg._card_payload(rows[0], rows, _signals()))
	assert "partial replay data" in line
	assert "vils" not in line


def test_the_impact_score_and_glyphs_are_gone():
	rows = [_card_row("a", 0)]
	line = pg._player_card_line(pg._card_payload(rows[0], rows, _signals()))
	for gone in ("▲", "▼", "⏱", "CARRY"):
		assert gone not in line


def test_no_player_ever_shows_a_participation_fallback_tag():
	rows = [_card_row("a", 0), _card_row("b", 0, player_number=2)]
	sig = _signals()
	lines = [pg._player_card_line(pg._card_payload(r, rows, sig)) for r in rows]
	for banned in ("All-rounder", "Army-leaning", "Eco-leaning",
	               "Tempo-leaning", "Uphill battle", "No tags"):
		assert not any(banned in ln for ln in lines)


def test_team_field_stays_within_the_discord_character_cap():
	rows = [_card_row("x" * 40, 0, player_number=i, civ="Y" * 30,
	                  villagers=999, military=999, eapm=99)
	        for i in range(1, 5)]
	sig = _signals(
		buildings={i: {"farms": 999, "tcs": 99} for i in range(1, 5)},
		strategies={i: ["Late Cav Archers", "Safe Castle", "Ram Push"] for i in range(1, 5)},
		spawn={i: "spawned next to enemy" for i in range(1, 5)},
		peak_eapm={i: 999 for i in range(1, 5)})
	fields = pg._team_card_fields([pg._card_payload(r, rows, sig) for r in rows])
	assert len(fields[0]["value"]) <= 1024
	assert fields[0]["value"].count("**") >= 8, "all four players must survive"
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/test_post_game.py -v -k "card_line or stats_line or team_field or peak or eapm or spawn_phrase or participation"
```
Expected: FAIL with `AttributeError: module 'bot.post_game' has no attribute '_card_payload'`.

- [ ] **Step 3: Add the payload builder and signal helper**

In `bot/post_game.py`, add after `_impact_payload` (which stays, because the Tale of the Tape still uses it) — **TABS**:

```python
def _card_payload(row, group, signals):
	"""Render payload for one player on the Match Cards.

	Deliberately separate from _impact_payload: that one feeds the Tale of the
	Tape through the untouched scoring.py, this one feeds the cards through
	card_scoring.py.
	"""
	from bot.replay_stats import card_scoring

	pnum = row.get("player_number")
	scores = card_scoring.component_scores(row, group)
	buildings = (signals.get("buildings") or {}).get(pnum) or {}
	produced = (row.get("villagers") or 0) + (row.get("military") or 0)
	return {
		"nick": row.get("nick") or row.get("identity") or str(row.get("user_id") or ""),
		"civ": row.get("civ"),
		"team": int(row["bot_team"]) if row.get("bot_team") in (0, 1, "0", "1") else None,
		"result": row.get("result") or ("W" if row.get("winner") else "L" if row.get("winner") is not None else None),
		"strategies": (signals.get("strategies") or {}).get(pnum) or [],
		"spawn": (signals.get("spawn") or {}).get(pnum),
		"villagers": row.get("villagers"),
		"military": row.get("military"),
		"farms": buildings.get("farms"),
		"tcs": buildings.get("tcs"),
		"eapm": row.get("eapm"),
		"peak_eapm": (signals.get("peak_eapm") or {}).get(pnum),
		"has_production": bool(produced),
		"production_coverage": card_scoring.production_coverage(
			(signals.get("clicks") or {}).get(pnum), row.get("duration_s")),
		"impact_score": scores["impact"],
		"army_score": scores["army"],
		"eco_score": scores["eco"],
		"early_eco_score": scores["early_eco"],
		"reboom_score": scores["reboom"],
	}


async def _card_signals_for(rows):
	"""Card-only signals for the match these rows belong to, or empty dicts."""
	from bot.replay_stats.card_query import fetch_card_signals

	aoe2_id = next((r.get("aoe2_match_id") for r in rows if r.get("aoe2_match_id")), None)
	if aoe2_id is None:
		return {}
	duration = next((r.get("duration_s") for r in rows if r.get("duration_s")), None)
	return await fetch_card_signals(aoe2_id, duration)
```

- [ ] **Step 4: Rewrite the render helpers**

Replace `_player_card_line` and `_team_card_fields` (lines 404-433) entirely with — **TABS**:

```python
MEDAL_GLYPHS = (("military_medal", "⚔"), ("villager_medal", "🌾"))


def _medal_text(medals):
	"""Top-three medals as repeated glyphs: 1st gets three, 3rd gets one."""
	parts = []
	for field, glyph in MEDAL_GLYPHS:
		place = (medals or {}).get(field)
		if place:
			parts.append(glyph * (4 - place))
	return " ".join(parts)


def _stats_text(player):
	bits = []
	if player.get("villagers") is not None:
		bits.append(f"{player['villagers']} vils")
	if player.get("military") is not None:
		bits.append(f"{player['military']} military")
	if player.get("farms") is not None:
		bits.append(f"{player['farms']} farms")
	if player.get("tcs") is not None:
		bits.append(f"{player['tcs']} TC")
	# Average comes from the stored rs_player_games.eapm, never from the APM
	# buckets. Peak is forward-only, so it is simply absent for older matches.
	if player.get("eapm") is not None:
		peak = player.get("peak_eapm")
		bits.append(f"{player['eapm']} eAPM" + (f" (pk {peak})" if peak else ""))
	if player.get("spawn"):
		bits.append(player["spawn"])
	return " · ".join(bits)


def _player_card_line(player, carry=False, medals=None, tags=None, with_stats=True):
	head = "👑 " if carry else "• "
	name = f"{head}**{_clip(player.get('nick'), 24)}** — **{_clip(player.get('civ'), 18)}**"
	strategies = ", ".join(player.get("strategies") or [])
	if strategies:
		name += f" · {strategies}"
	if not player.get("has_production"):
		# Ranking them last and showing them bare would read as "played badly"
		# when the truth is "not measured".
		return f"{name} · ⚠ partial replay data"
	lines = [name]
	badge = " · ".join(x for x in (_medal_text(medals),
	                               " ".join(_tag_chip(t) for t in (tags or []))) if x)
	if badge:
		lines.append(f"  {badge}")
	if with_stats:
		stats = _stats_text(player)
		if stats:
			lines.append(f"  {stats}")
	return "\n".join(lines)


def _team_card_fields(player_rows, team_names=None):
	"""One embed field per team. Medals rank match-wide, tags per team."""
	from bot.replay_stats import card_scoring

	team_names = team_names or {0: "Alpha", 1: "Beta"}
	medals = card_scoring.assign_medals(player_rows)
	tags = card_scoring.assign_team_tags(player_rows)
	extras = {id(p): (medals[i], tags[i]) for i, p in enumerate(player_rows)}
	teams = _team_impact_rows(player_rows)
	fields = []
	for team in sorted(teams):
		rows = sorted(teams[team], key=card_scoring.carry_sort_key)
		result = next((p.get("result") for p in rows if p.get("result")), None)
		icon = "🟩" if result == "W" else "🟥" if result == "L" else "⬜"

		def render(with_stats):
			return "\n\n".join(
				_player_card_line(p, carry=(p is rows[0]),
				                  medals=extras[id(p)][0], tags=extras[id(p)][1],
				                  with_stats=with_stats)
				for p in rows)

		value = render(True)
		if len(value) > 1024:
			# Drop the stats line before dropping a player.
			value = render(False)
		fields.append({
			"name": f"{icon} {team_names.get(team, f'Team {team}')} · {result or '?'}",
			"value": value[:1024] or "No players",
			"inline": True,
		})
	return fields
```

- [ ] **Step 5: Rewrite `build_match_cards_embed`**

Replace lines 749-772 with — **TABS**:

```python
async def build_match_cards_embed(channel_id, bot_match_id, rows=None, team_names=None):
	"""Card-like team summary for Discord. Uses embed fields to mimic side-by-side cards."""
	if rows is None:
		rows = await _analysis_rows(bot_match_id)
	if not rows:
		return None
	signals = await _card_signals_for(rows)
	player_rows = [_card_payload(row, rows, signals) for row in rows]
	if team_names is None:
		team_names = await _team_names(channel_id, bot_match_id)
	fields = _team_card_fields(player_rows, team_names)
	if not fields:
		return None

	from nextcord import Colour, Embed

	embed = Embed(
		title="🧾 Match Cards",
		colour=Colour(0x2ecc71),
		description=(
			"Medals rank the whole match on raw counts — ⚔ military, 🌾 villagers.\n"
			"Tags describe the shape of a player's game against their own team."
		),
	)
	for f in fields[:2]:
		embed.add_field(name=f["name"], value=f["value"], inline=f["inline"])
	embed.set_footer(text="Replay-derived · counts and activity, never combat outcomes")
	return embed
```

- [ ] **Step 6: Run the full suite**

```bash
pytest tests/ -v
```
Expected: every test passes, including the pre-existing 547.

- [ ] **Step 7: Commit**

```bash
git diff --exit-code bot/replay_stats/scoring.py && ruff check . && git add bot/post_game.py tests/test_post_game.py && git commit -m "feat(cards): render medals, strategies, stats line and spawn on the match cards"
```

---

## Task 6: Verify against real data

**Files:**
- Create: `utils/card_preview.py` (4-space indent — `utils/` convention)

A read-only script that renders the exact card text for recent matches, so the design is checked against reality before it posts to Discord.

- [ ] **Step 1: Write the preview script**

Create `utils/card_preview.py`:

```python
#!/usr/bin/env python3
"""Render Match Card text for recent matches, read-only, straight from the DB.

Usage:  python3 utils/card_preview.py [--limit 5]

SELECT only. Exists so the card can be reviewed against real matches without
posting to Discord.
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


async def main(limit):
    from core.database import db
    from bot import post_game as pg

    rows = await db.fetchall(
        "SELECT rm.bot_match_id FROM rs_matches rm "
        "WHERE rm.bot_match_id IS NOT NULL "
        "ORDER BY rm.parsed_at DESC LIMIT %s", [limit])
    for r in rows or []:
        bot_match_id = r["bot_match_id"]
        analysis = await pg._analysis_rows(bot_match_id)
        if not analysis:
            continue
        signals = await pg._card_signals_for(analysis)
        payloads = [pg._card_payload(row, analysis, signals) for row in analysis]
        print(f"\n{'=' * 70}\nbot match {bot_match_id}\n{'=' * 70}")
        for field in pg._team_card_fields(payloads):
            print(f"\n{field['name']}\n{field['value']}")
            print(f"\n[{len(field['value'])} chars of 1024]")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()
    asyncio.run(main(args.limit))
```

- [ ] **Step 2: Run it against the live DB**

```bash
python3 utils/card_preview.py --limit 5
```
Expected: five matches of rendered card text. Check that strategy labels appear for roughly 6 of 8 players, no field exceeds 1024 characters, no player shows a fallback tag, and `pk` is absent everywhere (`rs_player_apm` is empty until this branch deploys).

- [ ] **Step 3: Commit**

```bash
ruff check . && git add utils/card_preview.py && git commit -m "feat(cards): add a read-only card preview script"
```

---

## Out of scope

- `bot/replay_stats/scoring.py`, `player_tags.py`, `rs_player_game_tags`, the web profile API, the tag leaderboard and the Tale of the Tape embed — all unchanged by design. The three correctness fixes are applied in `card_scoring.py` only and stay live on those surfaces.
- Calibration tooling. Thresholds are inherited because `ECO_MIX`/`ARMY_MIX` are unchanged from the already-calibrated versions.
- `rs_player_techs`. Not read at all.
- Backfill of any kind. `rs_player_apm` fills forward once this branch deploys.

## The one number to confirm

`TH["production_coverage"] = 0.75` in `card_scoring.py` — a player earns `Constant production` when at least 75% of the two-minute buckets from their first click to match end contain a click. It is one line to change, and it is the only threshold in this plan that was not inherited from the existing calibrated set.
