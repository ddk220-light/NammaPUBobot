# Tale of the Tape Payoff — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the post-game embed react to the pre-game storylines it teased, window all storyline history to 90 days, and add trio + exact-lineup combo lines with team-framed phrasing.

**Architecture:** `bot/team_insights.py` keeps the pure model and grows a window, two candidate generators, a team-framing helper and a seeded RNG. A new `bot/storyline_payoff.py` recomputes the same storylines at report time and resolves each against the winner. `bot/match/match.py` posts it from `finish_match`. The replay-derived analysis embed in `bot/post_game.py` is deleted.

**Tech Stack:** Python 3.11, nextcord, aiomysql/MySQL, pytest 8.3.3.

Spec: `docs/superpowers/specs/2026-07-30-tale-of-the-tape-payoff-design.md`

## Global Constraints

- **Indentation: TABS.** Every file under `bot/` and `tests/` in this plan uses tabs. `utils/` uses 4 spaces. Do not mix.
- **Line length 120**, enforced by `ruff.toml`. Run `ruff check .` before every commit.
- **No `pytest-asyncio` is installed.** An `async def test_` is *silently skipped* and reports as passing. Every test touching async code must be a sync `def test_` that calls `asyncio.run(...)`.
- **Use the object form of `monkeypatch.setattr`** — `monkeypatch.setattr(mod, "db", fake)`, never the string form. `core/` is a namespace package with no `__init__.py` and the string form cannot resolve it.
- **Patch the consuming module's `db`**, not `core.database.db`. Modules bind their own reference at import.
- `bot/team_insights.py` must stay importable with `core.database` stubbed — `utils/preview_insights.py` and the probes load it by path. Do not add new top-level `core.*` imports; keep any new ones lazy inside functions.
- **Window constant: 90 days.** Trio bar: **≥5 games together, ≥75% one direction.** Lineup bar: **≥2 prior games, any record.** These were chosen from measured data; do not "improve" them.
- Run the full suite with `python3 -m pytest tests/ -q` before each commit.

---

### Task 1: Window the history read to 90 days

**Files:**
- Modify: `bot/team_insights.py` (tunables block ~line 31, `_fetch_history` ~line 515, `build_insights_embed` ~line 559)
- Test: `tests/test_team_insights.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `WINDOW_DAYS = 90`, `window_start(now_ts, days=WINDOW_DAYS) -> int`, and `_fetch_history(channel_id, user_ids, since_ts)` (third positional parameter is new and required).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_team_insights.py` (TABS):

```python
# ── history window ───────────────────────────────────────────────────────
def test_window_start_is_ninety_days_back():
	assert ti.WINDOW_DAYS == 90
	assert ti.window_start(1_000_000_000) == 1_000_000_000 - 90 * 86400
	assert ti.window_start(1_000_000_000, days=7) == 1_000_000_000 - 7 * 86400


def test_fetch_history_binds_the_cutoff_into_the_query():
	"""The window is applied in SQL so the read stays cheap as history grows."""
	import asyncio

	seen = {}

	class _DB:
		async def fetchall(self, sql, params=None):
			seen["sql"] = sql
			seen["params"] = params
			return []

	real_db = ti.db
	ti.db = _DB()
	try:
		asyncio.run(ti._fetch_history(7, [1, 2, 3], 1234))
	finally:
		ti.db = real_db
	assert "m.at >= %s" in seen["sql"]
	assert seen["params"] == [7, 1234, 1, 2, 3]


def test_fetch_history_needs_two_players():
	import asyncio
	assert asyncio.run(ti._fetch_history(7, [1], 1234)) == []
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m pytest tests/test_team_insights.py -q -k "window or fetch_history"`
Expected: FAIL — `AttributeError: module 'bot.team_insights' has no attribute 'WINDOW_DAYS'`.

- [ ] **Step 3: Add the constant and helper**

In `bot/team_insights.py`, in the `# ── Tunables ──` block right after `MAX_BULLETS = 4`, add (TABS — these are module level so no leading indent):

```python
WINDOW_DAYS = 90           # hard cutoff: nothing older than this is loaded at all
```

Then, immediately above `# ── Ordered history (pure) ──`, add:

```python
def window_start(now_ts, days=WINDOW_DAYS):
	"""Unix cutoff for the rolling history window."""
	return int(now_ts) - days * 86400
```

- [ ] **Step 4: Window the query**

Replace `_fetch_history` in `bot/team_insights.py` entirely with:

```python
async def _fetch_history(channel_id, user_ids, since_ts):
	"""Ranked-match rows in this channel involving any of ``user_ids``, no older
	than ``since_ts``, ordered by match_id so streaks are reconstructable.

	The window is a hard cutoff applied in SQL. It is not a stylistic choice:
	over a full history everyone's win-rate with everyone converges to their own
	baseline, so the best/worst-teammate swing stops clearing and the line dies.
	"""
	if len(user_ids) < 2:
		return []
	placeholders = ", ".join(["%s"] * len(user_ids))
	rows = await db.fetchall(
		"SELECT pm.match_id, pm.user_id, pm.nick, pm.team, m.winner "
		"FROM qc_player_matches pm "
		"JOIN qc_matches m ON m.match_id = pm.match_id AND m.channel_id = pm.channel_id "
		"WHERE pm.channel_id = %s AND m.ranked = 1 AND pm.team IS NOT NULL "
		"AND m.at >= %s "
		"AND pm.user_id IN (" + placeholders + ") "
		"ORDER BY pm.match_id ASC",
		[channel_id, since_ts, *user_ids]
	)
	return rows or []
```

- [ ] **Step 5: Update the caller**

First extend the stdlib import block at the top of `bot/team_insights.py` to:

```python
import math
import random
import time
from collections import Counter, namedtuple
```

Then in `build_insights_embed` replace this line:

```python
	rows = await _fetch_history(match.qc.id, [p.id for p in players])
```

with:

```python
	rows = await _fetch_history(match.qc.id, [p.id for p in players],
	                            window_start(time.time()))
```

- [ ] **Step 6: Update the footer copy**

The old footer claimed "Recent form from N ranked games" while reading all of history. Now that it is true, make it say so. In `build_insights_embed` replace:

```python
	embed.set_footer(text=f"Recent form from {len(hist.order)} ranked games · just for fun")
```

with:

```python
	embed.set_footer(text=f"Last {WINDOW_DAYS} days · {len(hist.order)} ranked games · just for fun")
```

- [ ] **Step 7: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS. If `tests/test_team_insights.py` has an existing test calling `_fetch_history` with two arguments, update that call to pass a third argument of `0`.

- [ ] **Step 8: Lint and commit**

```bash
ruff check .
git add bot/team_insights.py tests/test_team_insights.py
git commit -m "feat(storylines): hard-window storyline history to 90 days"
```

---

### Task 2: Shared group-record helper

**Files:**
- Modify: `bot/team_insights.py` (after `_teammate_series`, ~line 112)
- Test: `tests/test_team_insights.py`

**Interfaces:**
- Consumes: `_index_history` output shape from Task 1.
- Produces: `_group_series(prior, matches, group) -> list[bool]` where `group` is a `frozenset` of user ids. Tasks 3 and 4 both depend on this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_team_insights.py` (TABS):

```python
# ── group records (trio / lineup) ────────────────────────────────────────
def test_group_series_only_counts_matches_where_all_were_on_one_side():
	h = _hist(
		(1, 0, {1: 0, 2: 0, 3: 0, 4: 1}),   # all three together, won
		(2, 1, {1: 0, 2: 0, 3: 1, 4: 1}),   # 3 switched sides -> not counted
		(3, 1, {1: 0, 2: 0, 3: 0, 4: 1}),   # all three together, lost
		(4, 0, {1: 0, 2: 0, 4: 1}),         # 3 absent -> not counted
	)
	assert ti._group_series([1, 2, 3, 4], h.matches, frozenset((1, 2, 3))) == [True, False]


def test_group_series_drops_draws():
	h = _hist(
		(1, 0, {1: 0, 2: 0, 3: 1}),
		(2, None, {1: 0, 2: 0, 3: 1}),   # draw
	)
	assert ti._group_series([1, 2], h.matches, frozenset((1, 2))) == [True]


def test_group_series_respects_the_prior_cutoff():
	h = _hist(
		(1, 0, {1: 0, 2: 0, 3: 1}),
		(2, 1, {1: 0, 2: 0, 3: 1}),
	)
	assert ti._group_series([1], h.matches, frozenset((1, 2))) == [True]
```

- [ ] **Step 2: Run and watch it fail**

Run: `python3 -m pytest tests/test_team_insights.py -q -k group_series`
Expected: FAIL — `AttributeError: ... has no attribute '_group_series'`.

- [ ] **Step 3: Implement**

In `bot/team_insights.py`, directly after `_teammate_series`, add (TABS):

```python
def _group_series(prior, matches, group):
	"""Ordered results (True win / False loss) for prior matches where every
	member of ``group`` shared one side.

	Unlike ``_teammate_series`` this drops draws rather than recording None:
	trio and lineup lines quote a record, and a draw belongs in neither column.
	"""
	out = []
	for mid in prior:
		t = matches[mid]["teams"]
		if not group <= t.keys():
			continue
		sides = {t[u] for u in group}
		if len(sides) != 1:
			continue
		w = matches[mid]["winner"]
		if w is None:
			continue
		out.append(int(w) == next(iter(sides)))
	return out
```

- [ ] **Step 4: Run and watch it pass**

Run: `python3 -m pytest tests/test_team_insights.py -q -k group_series`
Expected: 3 passed.

- [ ] **Step 5: Lint and commit**

```bash
ruff check .
git add bot/team_insights.py tests/test_team_insights.py
git commit -m "feat(storylines): add the shared group-record helper"
```

---

### Task 3: Trio candidates

**Files:**
- Modify: `bot/team_insights.py` (tunables, `_trios` next to `_pairs` ~line 174, new generator after `_mate_candidates`, `_candidates` ~line 533)
- Test: `tests/test_team_insights.py`

**Interfaces:**
- Consumes: `_group_series` from Task 2.
- Produces: `_trios(ids)` yielding sorted 3-tuples, `_trio_candidates(prior, matches, t0_ids, t1_ids)` returning candidates of `"type": "trio"` whose `data` is `{"ids": [a, b, c], "wins": int, "games": int, "won": bool, "team_idx": int}`. Task 5 phrases it; Task 7 resolves it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_team_insights.py` (TABS):

```python
# ── trio ─────────────────────────────────────────────────────────────────
def _trio_hist(results):
	"""History where players 1,2,3 share side 0 against 4, one match per result."""
	return _hist(*[(i + 1, 0 if won else 1, {1: 0, 2: 0, 3: 0, 4: 1})
	               for i, won in enumerate(results)])


def test_trio_fires_at_five_games_and_seventy_five_percent():
	h = _trio_hist([True, True, True, True, False])   # 4-1 = 80%
	cands = ti._trio_candidates(h.order, h.matches, [1, 2, 3], [4])
	assert len(cands) == 1
	assert cands[0]["type"] == "trio"
	assert cands[0]["data"]["wins"] == 4
	assert cands[0]["data"]["games"] == 5
	assert cands[0]["data"]["won"] is True
	assert cands[0]["players"] == frozenset((1, 2, 3))


def test_trio_is_silent_below_the_share_bar():
	h = _trio_hist([True, True, True, False, False])   # 3-2 = 60%
	assert ti._trio_candidates(h.order, h.matches, [1, 2, 3], [4]) == []


def test_trio_is_silent_below_five_games():
	h = _trio_hist([True, True, True, True])           # 4-0 but only 4 games
	assert ti._trio_candidates(h.order, h.matches, [1, 2, 3], [4]) == []


def test_a_losing_trio_fires_and_is_marked_lost():
	h = _trio_hist([False, False, False, False, True])  # 1-4
	c = ti._trio_candidates(h.order, h.matches, [1, 2, 3], [4])[0]
	assert c["data"]["won"] is False
	assert c["data"]["wins"] == 1


def test_trio_enumeration_is_sorted_and_complete():
	assert list(ti._trios([30, 10, 20, 40])) == [
		(10, 20, 30), (10, 20, 40), (10, 30, 40), (20, 30, 40)]
```

- [ ] **Step 2: Run and watch it fail**

Run: `python3 -m pytest tests/test_team_insights.py -q -k trio`
Expected: FAIL — `AttributeError: ... has no attribute '_trio_candidates'`.

- [ ] **Step 3: Add the tunables**

In the `# ── Tunables ──` block of `bot/team_insights.py`, after `FORM_MIN_STREAK = 5`, add:

```python
TRIO_MIN_GAMES = 5         # T7: min decisive games this exact trio shared a side
TRIO_MIN_SHARE = 0.75      # T7: fraction of them going one direction
```

And in the drama-weights block, after `W_PERFECT = 6.0`, add:

```python
W_TRIO = 7.0
```

Note the existing weights multiply by sample size (`W_PERFECT * len(dec)`), so raw weights are not directly comparable. Trio uses `W_TRIO * games * share`, putting a marginal trio (5 games, 75% → 26.3) just under a minimal perfect pair (5 games → 30) and a strong one comfortably above.

And in the selection-caps block, after `DEADLOCK_TYPE_CAP = 1`, add:

```python
TRIO_TYPE_CAP = 1
```

- [ ] **Step 4: Add the enumerator**

Directly after `_pairs` in `bot/team_insights.py`, add:

```python
def _trios(ids):
	"""Unordered within-team triples, in a deterministic order (mirrors _pairs)."""
	ids = sorted(ids)
	for i in range(len(ids)):
		for j in range(i + 1, len(ids)):
			for k in range(j + 1, len(ids)):
				yield ids[i], ids[j], ids[k]
```

- [ ] **Step 5: Add the generator**

Directly after `_mate_candidates` in `bot/team_insights.py`, add:

```python
def _trio_candidates(prior, matches, t0_ids, t1_ids):
	"""T7 — a three-player subset of one side with a lopsided shared record."""
	cands = []
	for team_idx, ids in ((0, t0_ids), (1, t1_ids)):
		for a, b, c in _trios(ids):
			group = frozenset((a, b, c))
			s = _group_series(prior, matches, group)
			if len(s) < TRIO_MIN_GAMES:
				continue
			wins = sum(s)
			share = max(wins, len(s) - wins) / len(s)
			if share < TRIO_MIN_SHARE:
				continue
			won = wins * 2 > len(s)
			cands.append({
				"type": "trio",
				"score": W_TRIO * len(s) * share * (LOSS_BIAS if not won else 1.0),
				"players": group,
				"teams": frozenset((team_idx,)),
				"data": {"ids": [a, b, c], "wins": wins, "games": len(s),
				         "won": won, "team_idx": team_idx},
			})
	return cands
```

- [ ] **Step 6: Register it**

In `_candidates`, add `_trio_candidates(prior, matches, t0_ids, t1_ids)` to the returned sum, directly after `_mate_candidates(...)`:

```python
	return (
		_perfect_candidates(prior, matches, t0_ids, t1_ids)
		+ _mate_wr_candidates(prior, matches, t0_ids, t1_ids)
		+ _h2h_candidates(prior, matches, t0_ids, t1_ids)
		+ _mate_candidates(prior, matches, t0_ids, t1_ids)
		+ _trio_candidates(prior, matches, t0_ids, t1_ids)
		+ _deadlock_candidates(prior, matches, t0_ids, t1_ids)
		+ _form_candidates(prior, matches, t0_ids + t1_ids, team_of)
	)
```

- [ ] **Step 7: Run and watch it pass**

Run: `python3 -m pytest tests/test_team_insights.py -q -k trio`
Expected: 5 passed.

- [ ] **Step 8: Lint and commit**

```bash
ruff check .
git add bot/team_insights.py tests/test_team_insights.py
git commit -m "feat(storylines): add the trio combo line"
```

---

### Task 4: Exact-lineup candidates

**Files:**
- Modify: `bot/team_insights.py` (tunables, new generator after `_trio_candidates`, `_candidates`)
- Test: `tests/test_team_insights.py`

**Interfaces:**
- Consumes: `_group_series` from Task 2.
- Produces: `_lineup_candidates(prior, matches, t0_ids, t1_ids)` returning `"type": "lineup"` candidates whose `data` is `{"ids": sorted list, "wins": int, "games": int, "one_way": bool, "won": bool, "team_idx": int}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_team_insights.py` (TABS):

```python
# ── exact lineup ─────────────────────────────────────────────────────────
def test_lineup_fires_when_this_exact_side_played_together_before():
	h = _hist(
		(1, 0, {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 1}),
		(2, 1, {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 1}),
	)
	cands = ti._lineup_candidates(h.order, h.matches, [1, 2, 3], [4, 5, 6])
	assert len(cands) == 2                       # one per side
	side0 = next(c for c in cands if c["data"]["team_idx"] == 0)
	assert side0["data"] == {"ids": [1, 2, 3], "wins": 1, "games": 2,
	                         "one_way": False, "won": False, "team_idx": 0}


def test_lineup_needs_two_prior_games():
	h = _hist((1, 0, {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 1}))
	assert ti._lineup_candidates(h.order, h.matches, [1, 2, 3], [4, 5, 6]) == []


def test_a_superset_side_does_not_count_as_the_same_lineup():
	"""A prior 4-man side is a different lineup from tonight's 3-man side."""
	h = _hist(
		(1, 0, {1: 0, 2: 0, 3: 0, 9: 0, 4: 1, 5: 1, 6: 1, 8: 1}),
		(2, 0, {1: 0, 2: 0, 3: 0, 9: 0, 4: 1, 5: 1, 6: 1, 8: 1}),
	)
	# players 1,2,3 were united both times, so the *trio* has history, but the
	# lineup line is about tonight's exact roster and must still fire on it.
	cands = ti._lineup_candidates(h.order, h.matches, [1, 2, 3], [4, 5, 6])
	side0 = next(c for c in cands if c["data"]["team_idx"] == 0)
	assert side0["data"]["games"] == 2


def test_a_one_way_lineup_scores_above_a_mixed_one():
	mixed = _hist(
		(1, 0, {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 1}),
		(2, 1, {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 1}),
	)
	clean = _hist(
		(1, 0, {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 1}),
		(2, 0, {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 1}),
	)
	m = next(c for c in ti._lineup_candidates(mixed.order, mixed.matches, [1, 2, 3], [4, 5, 6])
	         if c["data"]["team_idx"] == 0)
	c = next(c for c in ti._lineup_candidates(clean.order, clean.matches, [1, 2, 3], [4, 5, 6])
	         if c["data"]["team_idx"] == 0)
	assert c["data"]["one_way"] is True
	assert c["score"] > m["score"]


def test_lineup_is_skipped_for_tiny_sides():
	"""On a 2v2 the lineup IS the pair, so it would just duplicate that line."""
	h = _hist(
		(1, 0, {1: 0, 2: 0, 4: 1, 5: 1}),
		(2, 0, {1: 0, 2: 0, 4: 1, 5: 1}),
	)
	assert ti._lineup_candidates(h.order, h.matches, [1, 2], [4, 5]) == []
```

- [ ] **Step 2: Run and watch it fail**

Run: `python3 -m pytest tests/test_team_insights.py -q -k lineup`
Expected: FAIL — `AttributeError: ... has no attribute '_lineup_candidates'`.

- [ ] **Step 3: Add the tunables**

In the `# ── Tunables ──` block, after the trio lines from Task 3, add:

```python
LINEUP_MIN_GAMES = 2       # T8: min prior decisive games as this exact side
LINEUP_MIN_SIDE = 3        # T8: below this the lineup is just the pair line
```

In the drama-weights block, after `W_TRIO = 7.0`, add:

```python
# The (10 + games) term is what keeps the jackpot on top: a plain multiple of
# sample size would let a long perfect-pair run (W_PERFECT * n) outrank it.
W_LINEUP = 9.0
```

In the selection-caps block, after `TRIO_TYPE_CAP = 1`, add:

```python
LINEUP_TYPE_CAP = 1
```

- [ ] **Step 4: Implement**

Directly after `_trio_candidates`, add:

```python
def _lineup_candidates(prior, matches, t0_ids, t1_ids):
	"""T8 — this exact side has shared a team before, inside the window.

	The rarest thing the module can say: only about one team-side in ten has
	*ever* played together before within 90 days, so the line leans on the
	reunion itself rather than on the record.
	"""
	cands = []
	for team_idx, ids in ((0, t0_ids), (1, t1_ids)):
		if len(ids) < LINEUP_MIN_SIDE:
			continue
		group = frozenset(ids)
		s = _group_series(prior, matches, group)
		if len(s) < LINEUP_MIN_GAMES:
			continue
		wins = sum(s)
		one_way = wins == 0 or wins == len(s)
		cands.append({
			"type": "lineup",
			"score": W_LINEUP * (10 + len(s)) * (PERFECT_COND if one_way else 1.0),
			"players": group,
			"teams": frozenset((team_idx,)),
			"data": {"ids": sorted(ids), "wins": wins, "games": len(s),
			         "one_way": one_way, "won": wins * 2 > len(s),
			         "team_idx": team_idx},
		})
	return cands
```

- [ ] **Step 5: Register it**

In `_candidates`, add it first in the sum (highest drama first is only cosmetic here, but keep the reading order consistent with the weights):

```python
	return (
		_lineup_candidates(prior, matches, t0_ids, t1_ids)
		+ _perfect_candidates(prior, matches, t0_ids, t1_ids)
		+ _mate_wr_candidates(prior, matches, t0_ids, t1_ids)
		+ _h2h_candidates(prior, matches, t0_ids, t1_ids)
		+ _mate_candidates(prior, matches, t0_ids, t1_ids)
		+ _trio_candidates(prior, matches, t0_ids, t1_ids)
		+ _deadlock_candidates(prior, matches, t0_ids, t1_ids)
		+ _form_candidates(prior, matches, t0_ids + t1_ids, team_of)
	)
```

- [ ] **Step 6: Run and watch it pass**

Run: `python3 -m pytest tests/test_team_insights.py -q -k lineup`
Expected: 5 passed.

- [ ] **Step 7: Lint and commit**

```bash
ruff check .
git add bot/team_insights.py tests/test_team_insights.py
git commit -m "feat(storylines): add the exact-lineup jackpot line"
```

---

### Task 5: Per-type caps in selection

**Files:**
- Modify: `bot/team_insights.py` (`_select`, ~line 336)
- Test: `tests/test_team_insights.py`

**Interfaces:**
- Consumes: the `trio` / `lineup` types from Tasks 3 and 4.
- Produces: `_TYPE_CAPS` dict. No signature change to `_select`.

Why this is its own task: `_overlaps` tests subset relationships in both directions, so a trio suppresses a *pair drawn from its own members* — but two different trios on the same side (A/B/C and A/B/D) are neither subset nor superset of each other, so without a hard cap both can appear. The existing PASS 3 relaxation also hardcodes `deadlock`, so a new cap declared but not wired there would silently leak.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_team_insights.py` (TABS):

```python
# ── per-type caps ────────────────────────────────────────────────────────
def test_only_one_trio_line_survives_selection():
	cands = [
		_cand("trio", 50, (1, 2, 3), teams=(0,)),
		_cand("trio", 49, (1, 2, 4), teams=(0,)),   # overlaps but is not a subset
		_cand("form", 5, (7,), teams=(1,)),
	]
	chosen = ti._select(cands, rng=random.Random(0))
	assert sum(1 for c in chosen if c["type"] == "trio") == 1


def test_only_one_lineup_line_survives_selection():
	cands = [
		_cand("lineup", 100, (1, 2, 3), teams=(0,)),
		_cand("lineup", 99, (4, 5, 6), teams=(1,)),
		_cand("form", 5, (9,), teams=(1,)),
	]
	chosen = ti._select(cands, rng=random.Random(0))
	assert sum(1 for c in chosen if c["type"] == "lineup") == 1


def test_the_cap_holds_through_the_fill_pass():
	"""PASS 3 relaxes the generic type cap; the hard caps must survive it."""
	cands = [
		_cand("trio", 50, (1, 2, 3), teams=(0,)),
		_cand("trio", 49, (4, 5, 6), teams=(1,)),
		_cand("trio", 48, (7, 8, 9), teams=(0,)),
	]
	chosen = ti._select(cands, rng=random.Random(0))
	assert sum(1 for c in chosen if c["type"] == "trio") == 1
```

- [ ] **Step 2: Run and watch it fail**

Run: `python3 -m pytest tests/test_team_insights.py -q -k "cap or trio_line or lineup_line"`
Expected: FAIL — more than one trio/lineup line is returned.

- [ ] **Step 3: Add the cap table**

In `bot/team_insights.py`, directly below the selection-caps tunables, add:

```python
# Types capped harder than PER_TYPE_CAP. These are the ones whose candidates can
# overlap without being subsets of each other, which _overlaps cannot dedup.
_TYPE_CAPS = {"deadlock": DEADLOCK_TYPE_CAP, "trio": TRIO_TYPE_CAP,
              "lineup": LINEUP_TYPE_CAP}
```

- [ ] **Step 4: Use it in both places**

In `_select`, replace:

```python
	def type_cap(t):
		return DEADLOCK_TYPE_CAP if t == "deadlock" else PER_TYPE_CAP
```

with:

```python
	def type_cap(t):
		return _TYPE_CAPS.get(t, PER_TYPE_CAP)
```

and in PASS 3 replace:

```python
			if c["type"] == "deadlock" and per_type["deadlock"] >= DEADLOCK_TYPE_CAP:
				continue
```

with:

```python
			if c["type"] in _TYPE_CAPS and per_type[c["type"]] >= _TYPE_CAPS[c["type"]]:
				continue
```

Note the membership test rather than a `.get()` default: PASS 3 exists precisely to relax the *generic* cap, so an uncapped type must never match here. A `.get(c["type"], PER_TYPE_CAP + 1)` default looks equivalent and is not — it silently gives uncapped types a hard ceiling of `PER_TYPE_CAP + 1`, so a thin match fills to 3 instead of `limit`.

Update the PASS 3 comment above it from `# PASS 3 — relax the generic type cap (NOT the player cap, NOT dedup, NOT the` / `# hard deadlock cap)` to say `# hard per-type caps)`.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 6: Lint and commit**

```bash
ruff check .
git add bot/team_insights.py tests/test_team_insights.py
git commit -m "feat(storylines): cap trio and lineup at one line each"
```

---

### Task 6: Team framing and deterministic phrasing

**Files:**
- Modify: `bot/team_insights.py` (`_phrase` ~line 418, `build_insights_embed` ~line 546)
- Test: `tests/test_team_insights.py`

**Interfaces:**
- Consumes: candidate dicts from Tasks 3–5.
- Produces:
  - `_join_names(names) -> str`
  - `_positive(c) -> bool`
  - `subject_of(c) -> tuple[str, int]` — `("team", idx)` or `("player", user_id)`. **Task 7 imports this.**
  - `_frame(c, nick, teams_meta, rosters, *, rng=random) -> str`
  - `_phrase(c, nick, teams_meta, rosters, *, rng=random)` — **the `rosters` parameter is new and positional**. `rosters` is `{0: [user_id, ...], 1: [...]}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_team_insights.py` (TABS):

```python
# ── team framing ─────────────────────────────────────────────────────────
_NICK = {1: "Ann", 2: "Bo", 3: "Cy", 4: "Dee", 5: "Eve", 6: "Fay", 7: "Gil", 8: "Hal"}
_META = [{"name": "Alpha", "emoji": "🟦"}, {"name": "Beta", "emoji": "🟥"}]
_ROSTERS = {0: [1, 2, 3, 4], 1: [5, 6, 7, 8]}


def _frame_for(typ, players, data, teams=(0,)):
	c = {"type": typ, "score": 1, "players": frozenset(players),
	     "teams": frozenset(teams), "data": data}
	return ti._frame(c, _NICK, _META, _ROSTERS, rng=random.Random(0))


def test_a_pair_line_names_the_other_two():
	out = _frame_for("mate", (1, 2), {"ids": [1, 2], "won": False, "team_idx": 0})
	assert "Cy" in out and "Dee" in out


def test_a_trio_line_names_the_last_teammate():
	out = _frame_for("trio", (1, 2, 3), {"ids": [1, 2, 3], "won": True, "team_idx": 0})
	assert "Dee" in out
	assert "Cy" not in out


def test_a_lineup_line_has_no_complement_so_it_addresses_the_side():
	out = _frame_for("lineup", (1, 2, 3, 4),
	                 {"ids": [1, 2, 3, 4], "won": True, "team_idx": 0})
	assert "Alpha" in out


def test_a_complement_past_two_collapses_to_the_team_name():
	out = _frame_for("form", (1,), {"p": 1, "k": 5, "won": True})
	assert "the rest of Alpha" in out


def test_join_names():
	assert ti._join_names([]) == ""
	assert ti._join_names(["a"]) == "a"
	assert ti._join_names(["a", "b"]) == "a & b"
	assert ti._join_names(["a", "b", "c"]) == "a, b & c"


def test_positive_reads_the_right_field_per_type():
	assert ti._positive({"type": "mate_wr", "data": {"kind": "best"}}) is True
	assert ti._positive({"type": "mate_wr", "data": {"kind": "worst"}}) is False
	assert ti._positive({"type": "trio", "data": {"won": False}}) is False
	assert ti._positive({"type": "h2h", "data": {}}) is True


def test_subject_of_picks_the_side_whose_win_settles_the_tease():
	assert ti.subject_of({"type": "mate", "data": {"team_idx": 1}}) == ("team", 1)
	assert ti.subject_of({"type": "trio", "data": {"team_idx": 0}}) == ("team", 0)
	assert ti.subject_of({"type": "lineup", "data": {"team_idx": 1}}) == ("team", 1)
	assert ti.subject_of({"type": "perfect", "data": {"team_idx": 0}}) == ("team", 0)
	assert ti.subject_of({"type": "h2h", "data": {"winner": 42}}) == ("player", 42)
	assert ti.subject_of({"type": "deadlock", "data": {"ids": [7, 9]}}) == ("player", 7)
	assert ti.subject_of({"type": "form", "data": {"p": 5}}) == ("player", 5)
	assert ti.subject_of({"type": "mate_wr", "data": {"p": 3, "q": 4}}) == ("player", 3)


def test_every_candidate_type_phrases_with_a_frame():
	"""No type may crash or silently drop the framing clause."""
	cases = [
		("lineup", (1, 2, 3, 4), {"ids": [1, 2, 3, 4], "wins": 2, "games": 2,
		                          "one_way": True, "won": True, "team_idx": 0}, (0,)),
		("trio", (1, 2, 3), {"ids": [1, 2, 3], "wins": 4, "games": 5,
		                     "won": True, "team_idx": 0}, (0,)),
		("perfect", (1, 2), {"ids": [1, 2], "n": 5, "won": False, "team_idx": 0}, (0,)),
		("mate_wr", (1, 2), {"p": 1, "q": 2, "wr": 0.8, "base": 0.5,
		                     "games": 8, "kind": "best"}, (0,)),
		("h2h", (1, 5), {"winner": 1, "loser": 5, "k": 4, "series": 6,
		                 "sweep": False}, (0, 1)),
		("mate", (1, 2), {"ids": [1, 2], "k": 4, "series": 7, "won": True,
		                  "team_idx": 0}, (0,)),
		("deadlock", (1, 5), {"ids": [1, 5], "each": 3, "n": 6}, (0, 1)),
		("form", (1,), {"p": 1, "k": 5, "won": True}, (0,)),
	]
	for typ, players, data, teams in cases:
		c = {"type": typ, "score": 1, "players": frozenset(players),
		     "teams": frozenset(teams), "data": data}
		line = ti._phrase(c, _NICK, _META, _ROSTERS, rng=random.Random(1))
		assert isinstance(line, str) and line.strip(), typ


def test_phrasing_is_deterministic_under_a_seeded_rng():
	c = {"type": "form", "score": 1, "players": frozenset((1,)),
	     "teams": frozenset((0,)), "data": {"p": 1, "k": 5, "won": True}}
	a = ti._phrase(c, _NICK, _META, _ROSTERS, rng=random.Random(7))
	b = ti._phrase(c, _NICK, _META, _ROSTERS, rng=random.Random(7))
	assert a == b
```

- [ ] **Step 2: Run and watch it fail**

Run: `python3 -m pytest tests/test_team_insights.py -q -k "frame or join_names or positive or subject_of or phrases_with"`
Expected: FAIL — `AttributeError: ... has no attribute '_frame'`.

- [ ] **Step 3: Add the helpers**

In `bot/team_insights.py`, directly above `def _phrase(`, add:

```python
def _join_names(names):
	"""Oxford-free join: 'a', 'a & b', 'a, b & c'. Local so the pure layer keeps
	no core.* dependency (utils/preview_insights.py loads this module by path)."""
	if not names:
		return ""
	if len(names) == 1:
		return names[0]
	return ", ".join(names[:-1]) + f" & {names[-1]}"


def _positive(c):
	"""Is this storyline good news for its subject side?"""
	t, d = c["type"], c["data"]
	if t == "mate_wr":
		return d["kind"] == "best"
	if t in ("perfect", "mate", "trio", "lineup", "form"):
		return bool(d.get("won"))
	return True   # h2h and deadlock are neutral until the match settles them


def subject_of(c):
	"""Whose win makes this storyline's tease come true.

	Returns ("team", team_idx) or ("player", user_id). The player form exists
	because h2h/deadlock/form/mate_wr candidates carry no team_idx — the caller
	resolves the player to a side. bot/storyline_payoff.py depends on this.
	"""
	t, d = c["type"], c["data"]
	if t == "h2h":
		return ("player", d["winner"])
	if t == "deadlock":
		return ("player", d["ids"][0])
	if t == "form":
		return ("player", d["p"])
	if t == "mate_wr":
		return ("player", d["p"])
	return ("team", d["team_idx"])


def _frame(c, nick, teams_meta, rosters, *, rng=random):
	"""A closing clause pulling the rest of the side into the story."""
	teams = sorted(c["teams"])
	if len(teams) != 1:
		return rng.choice([
			"Do their teammates get a say?",
			"Six other players would like a word.",
		])
	team_idx = teams[0]
	name = (teams_meta[team_idx]["name"] if team_idx < len(teams_meta)
	        else f"Team {team_idx}")
	rest = [u for u in (rosters.get(team_idx) or []) if u not in c["players"]]
	if not rest:
		return rng.choice([
			f"That is the whole of {name}.",
			f"All of {name}, back together.",
		])
	who = (f"the rest of {name}" if len(rest) > 2
	       else _join_names([f"**{nick.get(u, 'someone')}**" for u in rest]))
	if _positive(c):
		return rng.choice([
			f"{who} along for the ride.",
			f"Good day to be {who}.",
		])
	return rng.choice([
		f"Good luck to {who}.",
		f"{who} inherit the problem.",
	])
```

- [ ] **Step 4: Add the two new phrasings and wire the frame in**

Change the signature of `_phrase` from:

```python
def _phrase(c, nick, teams_meta, *, rng=random):
```

to:

```python
def _phrase(c, nick, teams_meta, rosters, *, rng=random):
```

Directly after the `d = c["data"]` / `t = c["type"]` lines at the top of `_phrase`, add:

```python
	frame = _frame(c, nick, teams_meta, rosters, rng=rng)
```

Then insert these two branches **before** the existing `if t == "perfect":` branch:

```python
	if t == "lineup":
		who = _join_names([name(u) for u in d["ids"]])
		w, g = d["wins"], d["games"]
		if d["one_way"] and w == g:
			opts = [
				f"🃏 Jackpot: this exact side has shared a team {g} times and never lost — **{w}-0**. {frame}",
				f"🎰 {who} — the same {len(d['ids'])} that are **{w}-0** together. Lightning, twice. {frame}",
			]
		elif d["one_way"]:
			opts = [
				f"🃏 This exact side has been assembled {g} times and won none of them (**0-{g}**). {frame}",
				f"🎰 {who}, back for another go at a **0-{g}** record. {frame}",
			]
		else:
			opts = [
				f"🃏 Rare reunion: this exact side has only played together {g} times before — **{w}-{g - w}**. {frame}",
				f"🎰 {who} ride again. Their shared record: **{w}-{g - w}**. {frame}",
			]
		return rng.choice(opts)

	if t == "trio":
		who = _join_names([name(u) for u in d["ids"]])
		w, g = d["wins"], d["games"]
		if d["won"]:
			opts = [
				f"🔱 {who} are **{w}-{g - w}** as a three. {frame}",
				f"⛓️ The trio that keeps delivering: {who}, **{w}-{g - w}** together. {frame}",
			]
		else:
			opts = [
				f"🕳️ {who} are **{w}-{g - w}** whenever all three line up. {frame}",
				f"🥀 History is unkind to {who} as a three — **{w}-{g - w}**. {frame}",
			]
		return rng.choice(opts)
```

Then append `frame` to every existing return. For each of the six existing branches, add ` {frame}` to the end of every string in its `opts` list. For example the `form` branch becomes:

```python
	# form
	p, k = name(d["p"]), d["k"]
	if d["won"]:
		opts = [
			f"🚀 {p} rolls in on a **{k}-game win streak**. {frame}",
			f"👑 **{k} straight wins** for {p}. {frame}",
		]
	else:
		opts = [
			f"🩹 {p} is on a **{k}-game skid**. {frame}",
			f"📉 Rough patch for {p}: **{k} losses** in a row. {frame}",
		]
	return rng.choice(opts)
```

Apply the same treatment to `perfect`, `mate_wr`, `h2h`, `mate` and `deadlock`: drop the trailing rhetorical question that each string currently ends with (the frame now carries that job) and append ` {frame}`.

- [ ] **Step 5: Update the caller**

In `build_insights_embed`, replace the block from `chosen = _select(...)` through `title = random.choice([...])` with:

```python
	# One seeded RNG for the whole embed: bot/storyline_payoff.py recomputes
	# these same lines at report time and must land on the same choices.
	rng = random.Random(match.id)
	chosen = _select(_candidates(hist.order, hist.matches,
	                             [p.id for p in team0], [p.id for p in team1]), rng=rng)
	if not chosen:
		return None

	from nextcord import Colour, Embed

	from core.utils import get_nick

	nick = {p.id: get_nick(p) for p in players}
	teams_meta = [
		{"name": teams[0].name, "emoji": teams[0].emoji},
		{"name": teams[1].name, "emoji": teams[1].emoji},
	]
	rosters = {0: [p.id for p in team0], 1: [p.id for p in team1]}
	lines = [_phrase(c, nick, teams_meta, rosters, rng=rng) for c in chosen]
	title = "⚔️ Tale of the Tape"
```

Note the existing function computes `chosen` before importing nextcord; keep that ordering. The title is pinned rather than randomised — it is the name the community already uses for the feature, and the payoff embed pairs with it.

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS. Any pre-existing test calling `_phrase` with three positional arguments must gain a fourth (`_ROSTERS` or an equivalent literal).

- [ ] **Step 7: Lint and commit**

```bash
ruff check .
git add bot/team_insights.py tests/test_team_insights.py
git commit -m "feat(storylines): frame every line around the rest of the side"
```

---

### Task 7: The payoff module

**Files:**
- Create: `bot/storyline_payoff.py`
- Test: Create `tests/test_storyline_payoff.py`

**Interfaces:**
- Consumes: `bot.team_insights.subject_of`, `_candidates`, `_select`, `_index_history`, `_fetch_history`, `window_start`, `_join_names`, `WINDOW_DAYS`.
- Produces:
  - `resolve(candidate, winner, team_of) -> bool | None`
  - `payoff_phrase(candidate, came_true, nick, teams_meta, rosters, *, rng=random) -> str`
  - `async build_payoff_embed(match) -> Embed | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_storyline_payoff.py` (TABS):

```python
"""Unit tests for the post-game storyline payoff.

Resolution is pure: a candidate plus a winner plus a user->team map. The embed
builder is exercised through asyncio.run with a fake db, because there is no
pytest-asyncio in this project and an `async def test_` would be silently
skipped.
"""
from __future__ import annotations

import random

import bot.storyline_payoff as sp

_NICK = {1: "Ann", 2: "Bo", 3: "Cy", 4: "Dee", 5: "Eve", 6: "Fay", 7: "Gil", 8: "Hal"}
_META = [{"name": "Alpha", "emoji": "🟦"}, {"name": "Beta", "emoji": "🟥"}]
_ROSTERS = {0: [1, 2, 3, 4], 1: [5, 6, 7, 8]}
_TEAM_OF = {1: 0, 2: 0, 3: 0, 4: 0, 5: 1, 6: 1, 7: 1, 8: 1}


def _c(typ, players, data, teams=(0,)):
	return {"type": typ, "score": 1.0, "players": frozenset(players),
	        "teams": frozenset(teams), "data": data}


# ── resolution ───────────────────────────────────────────────────────────
def test_a_team_subject_resolves_on_its_own_side_winning():
	c = _c("mate", (1, 2), {"ids": [1, 2], "k": 4, "series": 7, "won": False,
	                        "team_idx": 0})
	assert sp.resolve(c, 0, _TEAM_OF) is True
	assert sp.resolve(c, 1, _TEAM_OF) is False


def test_a_player_subject_resolves_through_the_team_map():
	c = _c("h2h", (1, 5), {"winner": 5, "loser": 1, "k": 4, "series": 5,
	                       "sweep": False}, teams=(0, 1))
	assert sp.resolve(c, 1, _TEAM_OF) is True     # player 5 is on team 1
	assert sp.resolve(c, 0, _TEAM_OF) is False


def test_a_draw_resolves_nothing():
	c = _c("form", (1,), {"p": 1, "k": 5, "won": True})
	assert sp.resolve(c, None, _TEAM_OF) is None


def test_an_unknown_player_resolves_nothing():
	c = _c("form", (99,), {"p": 99, "k": 5, "won": True})
	assert sp.resolve(c, 0, _TEAM_OF) is None


# ── phrasing truth table ─────────────────────────────────────────────────
_CASES = [
	("lineup", (1, 2, 3, 4), {"ids": [1, 2, 3, 4], "wins": 2, "games": 2,
	                          "one_way": True, "won": True, "team_idx": 0}, (0,)),
	("trio", (1, 2, 3), {"ids": [1, 2, 3], "wins": 4, "games": 5, "won": True,
	                     "team_idx": 0}, (0,)),
	("perfect", (1, 2), {"ids": [1, 2], "n": 5, "won": False, "team_idx": 0}, (0,)),
	("mate_wr", (1, 2), {"p": 1, "q": 2, "wr": 0.8, "base": 0.5, "games": 8,
	                     "kind": "best"}, (0,)),
	("h2h", (1, 5), {"winner": 1, "loser": 5, "k": 4, "series": 6,
	                 "sweep": False}, (0, 1)),
	("mate", (1, 2), {"ids": [1, 2], "k": 4, "series": 7, "won": True,
	                  "team_idx": 0}, (0,)),
	("deadlock", (1, 5), {"ids": [1, 5], "each": 3, "n": 6}, (0, 1)),
	("form", (1,), {"p": 1, "k": 5, "won": True}, (0,)),
]


def test_every_type_phrases_both_outcomes():
	for typ, players, data, teams in _CASES:
		c = _c(typ, players, data, teams)
		for came_true in (True, False):
			line = sp.payoff_phrase(c, came_true, _NICK, _META, _ROSTERS,
			                        rng=random.Random(3))
			assert isinstance(line, str) and line.strip(), (typ, came_true)


def test_the_two_outcomes_differ():
	for typ, players, data, teams in _CASES:
		c = _c(typ, players, data, teams)
		won = sp.payoff_phrase(c, True, _NICK, _META, _ROSTERS, rng=random.Random(3))
		lost = sp.payoff_phrase(c, False, _NICK, _META, _ROSTERS, rng=random.Random(3))
		assert won != lost, typ


def test_payoff_phrasing_is_deterministic():
	c = _c("form", (1,), {"p": 1, "k": 5, "won": True})
	a = sp.payoff_phrase(c, True, _NICK, _META, _ROSTERS, rng=random.Random(11))
	b = sp.payoff_phrase(c, True, _NICK, _META, _ROSTERS, rng=random.Random(11))
	assert a == b
```

- [ ] **Step 2: Run and watch it fail**

Run: `python3 -m pytest tests/test_storyline_payoff.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.storyline_payoff'`.

- [ ] **Step 3: Create the module**

Create `bot/storyline_payoff.py` (TABS):

```python
# -*- coding: utf-8 -*-
"""Close the loop on the pre-game storylines the moment a match reports.

bot/team_insights.py teases — "does the curse break tonight?" — and until now
nothing ever answered. This module answers, immediately at report time, with no
replay parsing: everything it needs is win/loss and who was on which side.

It does not read back what was posted. It recomputes the same storylines from
the same windowed history with the same match-id-seeded RNG, which is why
_select and _phrase take an injected rng. Two consequences worth knowing:

  * A threshold change between a match forming and reporting changes the
    recomputed set.
  * Matches overlap. One that formed while this was live can finish first and
    land in the window with a lower match_id, so the recompute can see history
    the pre-game read did not. Rate lines will not move; a streak line
    occasionally will.

Both are accepted — see the design doc for why storing the claims was rejected.
"""
import random

from bot import team_insights as ti


def resolve(c, winner, team_of):
	"""Did this storyline's subject side win? None when it cannot be settled.

	Every claim reduces to this one boolean, which is what keeps the payoff to
	exactly two texts per type instead of a combinatorial mess.
	"""
	if winner is None:
		return None
	kind, key = ti.subject_of(c)
	side = team_of.get(key) if kind == "player" else key
	if side is None:
		return None
	return int(winner) == int(side)


def payoff_phrase(c, came_true, nick, teams_meta, rosters, *, rng=random):
	"""One line reacting to a storyline, keyed on whether its side won."""
	d, t = c["data"], c["type"]

	def name(uid):
		return f"**{nick.get(uid, 'someone')}**"

	# NOT ti._frame: that one picks its tone from the PRE-GAME direction and is
	# written in future tense. In a payoff the tone must follow tonight's result
	# (the whole side shares it) and the tense must be past, or an unbeaten pair
	# that loses gets "the run is over — good day to be Dee".
	frame = _payoff_frame(c, came_true, nick, teams_meta, rosters, rng=rng)

	if t == "lineup":
		w, g = d["wins"], d["games"]
		nw, ng = w + (1 if came_true else 0), g + 1
		if came_true:
			return rng.choice([
				f"🃏 The reunion delivered — this exact side is now **{nw}-{ng - nw}**.",
				f"🎰 Same side, same result. **{nw}-{ng - nw}** all told. {frame}",
			])
		return rng.choice([
			f"🃏 The reunion fell flat. This exact side drops to **{nw}-{ng - nw}**.",
			f"🎰 Not this time — **{nw}-{ng - nw}** for this exact side. {frame}",
		])

	if t == "trio":
		who = ti._join_names([name(u) for u in d["ids"]])
		w, g = d["wins"], d["games"]
		nw, ng = w + (1 if came_true else 0), g + 1
		if came_true == d["won"]:
			return rng.choice([
				f"🔱 True to form: {who} move to **{nw}-{ng - nw}** as a three.",
				f"⛓️ The trio held serve — **{nw}-{ng - nw}** together now. {frame}",
			])
		return rng.choice([
			f"🔱 Against the grain: {who} are now **{nw}-{ng - nw}** as a three.",
			f"🕳️ The pattern cracked — **{nw}-{ng - nw}** for the three of them. {frame}",
		])

	if t == "perfect":
		a, b, n = name(d["ids"][0]), name(d["ids"][1]), d["n"]
		if d["won"]:
			return (rng.choice([
				f"💯 Still flawless. {a} & {b} are **{n + 1}-0** together.",
				f"🏆 The perfect record survives — **{n + 1}-0**. {frame}",
			]) if came_true else rng.choice([
				f"💔 It ends here. {a} & {b} lose as a pair for the first time — **{n}-1**.",
				f"🧨 The flawless run is over at {n}. {frame}",
			]))
		return (rng.choice([
			f"🎉 THE CURSE IS DEAD. {a} & {b} finally win together — **1-{n}**.",
			f"🔓 {n} tries, and it lands. {a} & {b} are on the board. {frame}",
		]) if came_true else rng.choice([
			f"🪦 The curse holds. {a} & {b} fall to **0-{n + 1}**.",
			f"💀 Still winless together — **0-{n + 1}**. {frame}",
		]))

	if t == "mate_wr":
		p, q = name(d["p"]), name(d["q"])
		if d["kind"] == "best":
			return (rng.choice([
				f"🚀 The pairing delivered again — {p} keeps cashing in next to {q}.",
				f"🍀 Lucky charm confirmed. {p} & {q} do it again. {frame}",
			]) if came_true else rng.choice([
				f"🌧️ Not tonight. Even alongside {q}, {p} came up short.",
				f"📉 The magic pairing missed this one. {frame}",
			]))
		return (rng.choice([
			f"🎯 History bucked — {p} & {q} finally made it work.",
			f"🔨 The bad pairing broke its own pattern. {frame}",
		]) if came_true else rng.choice([
			f"🔁 History repeats. {p} & {q} still do not click.",
			f"🧊 Same story as ever for {p} beside {q}. {frame}",
		]))

	if t == "h2h":
		w, lo, k = name(d["winner"]), name(d["loser"]), d["k"]
		if came_true:
			return rng.choice([
				f"⚔️ Make it **{k + 1} straight** — {w} still owns {lo}.",
				f"🔒 {lo} still has no answer. {k + 1} in a row to {w}. {frame}",
			])
		return rng.choice([
			f"🎊 The streak dies at {k}. {lo} finally beat {w}.",
			f"⛓️‍💥 {lo} breaks the run. {frame}",
		])

	if t == "mate":
		a, b, k = name(d["ids"][0]), name(d["ids"][1]), d["k"]
		if d["won"]:
			return (rng.choice([
				f"🔥 **{k + 1} in a row** together for {a} & {b}.",
				f"📈 The duo streak lives — {k + 1} straight. {frame}",
			]) if came_true else rng.choice([
				f"❄️ The run ends at {k}. {a} & {b} finally drop one together.",
				f"🛑 Streak over. {frame}",
			]))
		return (rng.choice([
			f"🌅 The skid is over — {a} & {b} win one together at last.",
			f"🩹 {k} straight losses, and then this. {frame}",
		]) if came_true else rng.choice([
			f"🪦 Make it **{k + 1} straight losses** for {a} & {b}.",
			f"❄️ Still ice-cold together — {k + 1} in a row. {frame}",
		]))

	if t == "deadlock":
		a, b, each = name(d["ids"][0]), name(d["ids"][1]), d["each"]
		ahead, behind = (a, b) if came_true else (b, a)
		return rng.choice([
			f"⚖️ Tie broken. {ahead} edges ahead of {behind}, **{each + 1}-{each}**.",
			f"🎯 Someone had to. {ahead} takes the decider. {frame}",
		])

	# form. NOTE: make this an explicit `if t == "form":` branch and follow it
	# with `raise ValueError(f"payoff_phrase has no branch for candidate type {t!r}")`
	# — an unmatched type must fail loud, matching ti._phrase.
	p, k = name(d["p"]), d["k"]
	if d["won"]:
		return (rng.choice([
			f"🚀 **{k + 1} straight** for {p}. Nobody has stopped them yet.",
			f"👑 The heater rolls on — {k + 1} in a row. {frame}",
		]) if came_true else rng.choice([
			f"🛑 The run ends at {k}. {p} finally drops one.",
			f"📉 Streak over for {p}. {frame}",
		]))
	return (rng.choice([
		f"🌅 The slump breaks. {p} snaps a {k}-game skid.",
		f"🎊 {k} losses, then this. {frame}",
	]) if came_true else rng.choice([
		f"🩹 Make it **{k + 1}**. The skid goes on for {p}.",
		f"📉 Still searching — {k + 1} straight for {p}. {frame}",
	]))


async def build_payoff_embed(match):
	"""React to the storylines this match's teams were given. None when there is
	nothing to settle (draw, no history, or nothing fired pre-game)."""
	teams = getattr(match, "teams", None)
	if not teams or len(teams) < 2:
		return None
	winner = getattr(match, "winner", None)
	if winner is None:
		return None
	team0 = [p for p in teams[0] if p]
	team1 = [p for p in teams[1] if p]
	if not team0 or not team1:
		return None

	players = team0 + team1
	import time

	rows = await ti._fetch_history(match.qc.id, [p.id for p in players],
	                               ti.window_start(time.time()))
	hist = ti._index_history(rows)
	# This match is already persisted by the time the payoff runs, so the prior
	# has to exclude it explicitly or every storyline would resolve itself.
	prior = [mid for mid in hist.order if mid < match.id]
	if not prior:
		return None

	rng = random.Random(match.id)
	chosen = ti._select(ti._candidates(prior, hist.matches,
	                                   [p.id for p in team0], [p.id for p in team1]),
	                    rng=rng)
	if not chosen:
		return None

	team_of = {**{p.id: 0 for p in team0}, **{p.id: 1 for p in team1}}
	verdicts = [(c, resolve(c, winner, team_of)) for c in chosen]
	verdicts = [(c, v) for c, v in verdicts if v is not None]
	if not verdicts:
		return None

	from nextcord import Colour, Embed

	from core.utils import get_nick

	nick = {p.id: get_nick(p) for p in players}
	teams_meta = [
		{"name": teams[0].name, "emoji": teams[0].emoji},
		{"name": teams[1].name, "emoji": teams[1].emoji},
	]
	rosters = {0: [p.id for p in team0], 1: [p.id for p in team1]}
	# _phrase consumed rng for each chosen line pre-game; burn the same draws
	# here so the payoff's variant picks line up with the teased ones.
	for c in chosen:
		ti._phrase(c, nick, teams_meta, rosters, rng=rng)
	lines = [payoff_phrase(c, v, nick, teams_meta, rosters, rng=rng)
	         for c, v in verdicts]
	embed = Embed(title="⚔️ Final Tale of the Tape", colour=Colour(0xe67e22),
	              description="\n\n".join(lines))
	embed.set_footer(
		text=f"Last {ti.WINDOW_DAYS} days · how the storylines actually ended · just for fun")
	return embed
```

- [ ] **Step 4: Run and watch it pass**

Run: `python3 -m pytest tests/test_storyline_payoff.py -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
ruff check .
git add bot/storyline_payoff.py tests/test_storyline_payoff.py
git commit -m "feat(storylines): resolve pre-game storylines after the report"
```

---

### Task 8: Post the payoff from finish_match

**Files:**
- Modify: `bot/match/match.py:468-483` (`finish_match`)
- Test: `tests/test_storyline_payoff.py`

**Interfaces:**
- Consumes: `build_payoff_embed` from Task 7.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_storyline_payoff.py` (TABS):

```python
# ── embed assembly ───────────────────────────────────────────────────────
class _P:
	def __init__(self, uid):
		self.id = uid


class _T(list):
	def __init__(self, ids, name, emoji):
		super().__init__(_P(i) for i in ids)
		self.name = name
		self.emoji = emoji


class _M:
	def __init__(self, winner):
		self.id = 500
		self.winner = winner
		self.teams = [_T([1, 2, 3, 4], "Alpha", "🟦"), _T([5, 6, 7, 8], "Beta", "🟥")]
		self.qc = type("QC", (), {"id": 77})()


def _history_rows():
	"""Players 1 & 2 have lost their last four as teammates, of seven together.

	Order matters: the three wins are the OLDER match ids and the four losses
	trail, so _trailing_streak sees a 4-game losing run. Seven games clears
	MATE_MIN_TOGETHER=6 and the run clears MATE_MIN_STREAK=4.
	"""
	rows = []
	for mid in range(1, 4):          # 1-3: team 0 wins
		for uid, team in ((1, 0), (2, 0), (5, 1), (6, 1)):
			rows.append({"match_id": mid, "user_id": uid, "nick": f"u{uid}",
			             "team": team, "winner": 0})
	for mid in range(4, 8):          # 4-7: team 0 loses
		for uid, team in ((1, 0), (2, 0), (5, 1), (6, 1)):
			rows.append({"match_id": mid, "user_id": uid, "nick": f"u{uid}",
			             "team": team, "winner": 1})
	return rows


def _run_build(monkeypatch, winner, rows):
	# NOTE: once candidates actually fire, build_payoff_embed reaches
	# `from nextcord import Colour, Embed`, and nextcord is installed nowhere in
	# CI (ci.yml never installs it, and conftest's blank aiohttp stub collides
	# with nextcord.gateway even if you do). So this helper must ALSO stub
	# sys.modules["nextcord"] with a minimal fake exposing Embed(title=,
	# colour=, description=) with a .set_footer(text=) method, plus an identity
	# Colour. Use monkeypatch.setitem so it reverts per test.
	import asyncio
	import sys
	import types

	import bot.team_insights as ti

	class _DB:
		async def fetchall(self, sql, params=None):
			return rows

	monkeypatch.setattr(ti, "db", _DB())

	fake_utils = types.ModuleType("core.utils")
	fake_utils.get_nick = lambda p: f"u{p.id}"
	monkeypatch.setitem(sys.modules, "core.utils", fake_utils)
	return asyncio.run(sp.build_payoff_embed(_M(winner)))


def test_a_draw_builds_no_embed(monkeypatch):
	assert _run_build(monkeypatch, None, _history_rows()) is None


def test_no_history_builds_no_embed(monkeypatch):
	assert _run_build(monkeypatch, 0, []) is None


def test_a_decisive_result_builds_a_titled_embed(monkeypatch):
	embed = _run_build(monkeypatch, 0, _history_rows())
	assert embed is not None
	assert embed.title == "⚔️ Final Tale of the Tape"
	assert embed.description.strip()


def test_the_current_match_is_excluded_from_its_own_prior(monkeypatch):
	"""Match 500 in history must not resolve itself."""
	rows = _history_rows()
	rows += [{"match_id": 500, "user_id": u, "nick": f"u{u}", "team": t, "winner": 0}
	         for u, t in ((1, 0), (2, 0), (5, 1), (6, 1))]
	with_current = _run_build(monkeypatch, 0, rows)
	without = _run_build(monkeypatch, 0, _history_rows())
	assert with_current.description == without.description
```

- [ ] **Step 2: Run and watch it fail or pass**

Run: `python3 -m pytest tests/test_storyline_payoff.py -q`
Expected: these four pass already if Task 7 is correct. If any fail, fix `bot/storyline_payoff.py` — not the test. This step exists to prove the module works before it is wired to Discord.

- [ ] **Step 3: Wire it into finish_match**

In `bot/match/match.py`, in `finish_match`, replace:

```python
		if self.ranked:
			await bot.stats.register_match_ranked(ctx, self)
		else:
			await bot.stats.register_match_unranked(ctx, self)
```

with:

```python
		if self.ranked:
			await bot.stats.register_match_ranked(ctx, self)
		else:
			await bot.stats.register_match_unranked(ctx, self)

		# Close the loop on the pre-game storylines. Purely win/loss, so it can
		# post the instant the match reports — no replay needed. Best-effort:
		# a payoff failure must never touch the report or rating flow.
		if self.ranked:
			try:
				from bot.storyline_payoff import build_payoff_embed
				payoff = await build_payoff_embed(self)
				if payoff is not None:
					await ctx.notice(embed=payoff)
			except Exception as e:
				log.error(f"Storyline payoff failed for match {self.id}: {e}")
```

Confirm `log` is already imported in `bot/match/match.py` — it is used at line 511 (`log.error(f"Lobby watcher stop failed...")`). If the import is missing, add `from core.console import log` alongside the other core imports.

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
ruff check .
git add bot/match/match.py tests/test_storyline_payoff.py
git commit -m "feat(storylines): post the payoff when a match reports"
```

---

### Task 9: Delete the replay-derived analysis embed

**Files:**
- Modify: `bot/post_game.py` (remove `_impact_payload` ~250, `_tag_word` ~320, `_team_tag_summary` ~444, `_match_analysis_lines` ~553, `build_match_analysis_embed` ~846, and the `post_match_analysis` wiring ~952)
- Modify: `tests/test_post_game.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `post_match_analysis` now posts only the Match Cards embed and the APM chart.

Verified before writing this plan: `bot/web.py` defines its **own** `_impact_payload` at `bot/web.py:766`, so the web profile is unaffected, and `bot/replay_stats/scoring.py` keeps its other consumers. Do not touch `bot/replay_stats/`.

- [ ] **Step 1: Delete the dead tests**

In `tests/test_post_game.py`, delete `test_match_analysis_lines_include_win_loss_and_carry` (~line 232) and, in the `post_match_analysis` wiring test (~line 514), remove the line:

```python
	monkeypatch.setattr(pg, "build_match_analysis_embed", _analysis)
```

along with the `_analysis` stub it refers to and any assertion that two embeds are sent. The wiring test should now assert that exactly one embed (the cards) is sent.

- [ ] **Step 2: Run and watch it fail**

Run: `python3 -m pytest tests/test_post_game.py -q`
Expected: FAIL — the wiring test still sees `build_match_analysis_embed` being called and two embeds sent.

- [ ] **Step 3: Delete the functions**

From `bot/post_game.py` delete, in full:

- `def _impact_payload(row, group):`
- `def _tag_word(tags):`
- `def _team_tag_summary(team_rows):`
- `def _match_analysis_lines(player_rows, team_names=None):`
- `async def build_match_analysis_embed(channel_id, bot_match_id, rows=None, team_names=None):`

Keep `_tag_chip`, `_team_impact_rows`, `_card_payload`, `_team_card_fields` and everything else — the Match Cards still use them.

- [ ] **Step 4: Simplify post_match_analysis**

In `post_match_analysis`, replace the block from `rows = await _analysis_rows(bot_match_id)` through `embeds = [e for e in (cards, embed) if e is not None]` with:

```python
			# The Tale of the Tape now posts at report time from
			# bot/storyline_payoff.py, so only the cards are left here.
			rows = await _analysis_rows(bot_match_id)
			team_names = await _team_names(channel_id, bot_match_id)
			cards = await build_match_cards_embed(channel_id, bot_match_id, rows, team_names)
			if cards is None:
				return False
			chart_file = await _apm_chart_file(bot_match_id)
			if chart_file is not None:
				cards.set_image(url="attachment://apm.png")
			embeds = [cards]
```

- [ ] **Step 5: Drop the now-unused import**

Check whether `scoring` is still referenced anywhere in `bot/post_game.py`:

Run: `rg -n "scoring\." bot/post_game.py`

Every remaining hit should be `card_scoring.`. If there are no bare `scoring.` hits, remove the `scoring` import from the module's import block. Leave `card_scoring` alone.

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 7: Lint and commit**

```bash
ruff check .
git add bot/post_game.py tests/test_post_game.py
git commit -m "refactor(post-game): drop the replay-derived Tale of the Tape"
```

---

### Task 10: Extend the preview harness

**Files:**
- Modify: `utils/preview_insights.py` (**4-SPACE indentation** — this is a `utils/` file)

**Interfaces:**
- Consumes: `bot/team_insights.py` and `bot/storyline_payoff.py` loaded by path.
- Produces: a `--payoff` flag that also prints how each storyline actually resolved.

- [ ] **Step 1: Read the existing harness**

Run: `sed -n 1,140p utils/preview_insights.py`

Note how it stubs `core.database` and `core.utils` and loads `bot/team_insights.py` via `importlib.util.spec_from_file_location`. Follow exactly that pattern; do not import `bot.storyline_payoff` normally, because `bot/__init__.py` pulls in nextcord.

- [ ] **Step 2: Load the payoff module the same way**

After the existing `ti` module load block, add (4 SPACES):

```python
import sys as _sys
_sys.modules["bot"] = types.ModuleType("bot")
_sys.modules["bot"].__path__ = [os.path.join(_REPO_ROOT, "bot")]
_sys.modules["bot"].team_insights = ti
_sys.modules["bot.team_insights"] = ti

_sp_spec = importlib.util.spec_from_file_location(
    "storyline_payoff", os.path.join(_REPO_ROOT, "bot", "storyline_payoff.py")
)
sp = importlib.util.module_from_spec(_sp_spec)
_sp_spec.loader.exec_module(sp)
```

The bare `bot` package registration is what lets `from bot import team_insights` inside `storyline_payoff.py` resolve without running `bot/__init__.py`.

- [ ] **Step 3: Window the preview's own history read**

In `preview_one`, replace the history query with one that also applies the 90-day cutoff, anchored on the target match's own `at` so the replay is faithful rather than measured from today:

```python
    rows = await _fetchall(
        pool,
        "SELECT pm.match_id, pm.user_id, pm.nick, pm.team, m.winner "
        "FROM qc_player_matches pm "
        "JOIN qc_matches m ON m.match_id = pm.match_id AND m.channel_id = pm.channel_id "
        "WHERE pm.channel_id = %s AND m.ranked = 1 AND pm.team IS NOT NULL "
        f"AND m.match_id < %s AND m.at >= %s AND pm.user_id IN ({placeholders}) "
        "ORDER BY pm.match_id ASC",
        (ch, mid, ti.window_start(mrow["at"]), *user_ids),
    )
```

- [ ] **Step 4: Render the payoff**

Add `import random` to the imports at the top of the file if it is not already there.

Then replace the tail of `preview_one` — the three lines from `meta = [...]` to the `for c in chosen:` print loop — with (4 SPACES):

```python
    meta = [{"name": a_name, "emoji": ""}, {"name": b_name, "emoji": ""}]
    rosters = {0: t0, 1: t1}
    rng = random.Random(mid)
    chosen = ti._select(ti._candidates(hist.order, hist.matches, t0, t1), rng=rng)
    if not chosen:
        print(f"  → nothing surfaced ({len(hist.order)} prior games, nothing met the thresholds)")
        return
    print(f"  ⚔️ Tale of the Tape (from {len(hist.order)} prior ranked games):")
    for c in chosen:
        print("    " + ti._phrase(c, nick, meta, rosters, rng=rng))

    winner = mrow.get("winner")
    if winner is None:
        print("  ⚔️ Final Tale of the Tape: draw — nothing to settle")
        return
    team_of = {**{u: 0 for u in t0}, **{u: 1 for u in t1}}
    print("  ⚔️ Final Tale of the Tape:")
    for c in chosen:
        verdict = sp.resolve(c, winner, team_of)
        if verdict is None:
            continue
        print("    " + sp.payoff_phrase(c, verdict, nick, meta, rosters, rng=rng))
```

Note this moves the existing `chosen = ti._select(...)` call and its empty-guard down below `meta`/`rosters`, because it now needs the seeded `rng`. Delete the original `chosen = ...` block and its guard from where they currently sit, above `meta`. The single `rng` threaded through `_select` → `_phrase` → `payoff_phrase` mirrors exactly what the bot does, so the preview shows the real pairing of tease and payoff.

- [ ] **Step 5: Verify against real data**

Run: `python3 utils/preview_insights.py 20`

Expected: 20 matches, each showing pre-game lines followed by a `⚔️ Final Tale of the Tape` block. Read them. Every line should name real players, every payoff should agree with the result shown, and no line should read as a non-sequitur.

This step needs `config.cfg` and network access to the live DB. It is READ-ONLY — the harness only issues SELECT. If the DB is unreachable, note it and skip to Step 6 rather than inventing output.

- [ ] **Step 6: Lint and commit**

```bash
ruff check .
git add utils/preview_insights.py
git commit -m "feat(storylines): render the payoff in the preview harness"
```

---

### Task 11: Full verification

**Files:** none modified unless a failure is found.

- [ ] **Step 1: Full suite**

Run: `python3 -m pytest tests/ -q`
Expected: all pass, zero skips beyond the one pre-existing skip.

- [ ] **Step 2: Lint**

Run: `ruff check .`
Expected: `All checks passed!`

- [ ] **Step 3: Confirm no async test is being silently skipped**

Run: `rg -n "async def test_" tests/`
Expected: no output. Any hit is a silently-skipped test and must be rewritten as a sync test driving `asyncio.run`.

- [ ] **Step 4: Confirm the deleted symbols are really gone**

Run: `rg -n "build_match_analysis_embed|_match_analysis_lines|_team_tag_summary" bot/ tests/`
Expected: no output.

- [ ] **Step 5: Confirm team_insights still loads standalone**

Run:

```bash
python3 -c "
import sys, types, importlib.util
m = types.ModuleType('core.database'); m.db = None
sys.modules['core.database'] = m
s = importlib.util.spec_from_file_location('ti', 'bot/team_insights.py')
mod = importlib.util.module_from_spec(s); s.loader.exec_module(mod)
print('team_insights loads standalone:', mod.WINDOW_DAYS)
"
```

Expected: `team_insights loads standalone: 90`. This guards the preview harness and the measurement probes.

- [ ] **Step 6: Commit anything outstanding**

```bash
git status --short
```

Expected: clean apart from untracked `.DS_Store`, which stays untracked.

---

## Self-review notes

Spec coverage check, section by section:

- 90-day hard window, every line type → Task 1
- trio at ≥5 games / ≥75% → Task 3
- exact lineup at ≥2 prior games, any record → Task 4
- one-per-embed caps for both → Task 5
- team framing with the complement rule → Task 6
- seeded RNG and pinned titles → Tasks 6 and 7
- payoff trigger in `finish_match`, all three report paths → Task 8
- resolution truth table, two texts per type → Task 7
- draw posts nothing → Tasks 7 and 8
- deleting the old analysis embed → Task 9
- preview harness renders the payoff → Task 10
- test-harness constraints → Global Constraints, enforced in Task 11 Step 3
