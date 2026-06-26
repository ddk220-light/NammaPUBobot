# Luck (Spawn / Map-Factor) Analysis — Design

**Date:** 2026-06-25
**Status:** Approved (brainstorming) — pending implementation plan

## Goal

Add a new **"Luck"** analysis stage that measures how map/spawn-given conditions on the
community's Land Nomad 4v4 games affect a player's win/loss: where a player **settles their first
Town Center** relative to allies/enemies, what **gold / stone / food** sits near that settle, and
how **scattered their starting villagers** were. Surface it as a separate **"Luck" tab** on the
website, reusing the existing use-case (classification) machinery, with thresholds **calibrated once
from the corpus and then frozen**.

## Why this is a distinct stage

- The community plays **~98% Land Nomad, ~96% 8-player (4v4)**. On Nomad there is **no pre-placed
  Town Center** — each player settles their first TC wherever they choose, with minimal information
  (you don't yet know where allies/enemies/resources are). So the spawn outcome is treated as
  **luck**.
- Unlike the strategy use cases (binary "did the player DO X", surfaced as win% among the doers,
  with a phase-exclusivity cascade), luck factors are **map-given conditions on every player-game**.
  They are **non-exclusive** (a player can be near-ally AND gold-poor AND scattered at once) and are
  evaluated for **every** player-game — they do **not** participate in the strategy exclusivity
  cascade.
- **Clean null:** a valid 3v3/4v4 has exactly half winners, so the baseline player win-rate is
  **exactly 50%**. Each luck use case reads as "win% vs the 50% baseline (Δ)".

## Population / data hygiene

- **Scope:** map ∈ {Land Nomad, Nomad}, **6 or 8 players** (3v3 / 4v4 team games).
- **Drop matches with no/partial recorded winner.** mgz cannot always detect a victor (e.g. games
  that ended by mass resign) → every player reads as a loss. A match is **valid only if
  `winners == players / 2`**. In the 2026-06-25 calibration, 62 of 716 sampled matches had **zero**
  winners; dropping them (and ~2 partially-labelled) restored the exact 50% baseline. Luck use
  cases must no-op on invalid/out-of-scope games.

## Settle anchor & raw metrics

- **Settle TC** = the player's **first built Town Center** (minimum build time); fall back to
  `start_tc_xy` on fixed maps (Nomad never has one).
- **Proximity** (computed across all players' settle TCs, grouped by team): nearest-**ally**
  distance, nearest-**enemy** distance, nearest-**any-player** distance.
- **Resource proximity** (distance from the settle TC to the nearest `m.gaia` object):
  nearest **Gold Mine**, nearest **Stone Mine**, nearest **huntable food**
  (Wild Boar / Deer / Ibex / Pig). Herdables (cows) are excluded — they are herded, not a settle
  resource.
- **Villager scatter** = **perimeter of the starting-villager triangle** = sum of the 3 pairwise
  distances among the player's 3 starting `Villager` objects (always exactly 3 on this map).

## The 11 use cases + frozen thresholds

Calibrated from **654 valid matches / 5,180 player-games** (2026-06-25), each threshold chosen as
the percentile that lands the bucket at ~20% of player-games (isolated at 25%), satisfying the
15–25%-per-bucket target. Thresholds are **absolute tile values, frozen** and reused for all maps.

| key | trigger (tiles) | category | side | % games | win% | Δ vs 50% |
|---|---|---|---|---|---|---|
| `spawn_near_ally` | nearest ally < 46 | luck | near | 20% | 44.2 | **−5.8** |
| `spawn_isolated` | nearest player > 63 | luck | far | 25% | 52.5 | **+2.5** |
| `spawn_gold_poor` | nearest gold > 17 | luck | far | 20% | 47.9 | −2.1 |
| `scattered_villagers` | perimeter > 429 | luck | far | 20% | 48.1 | −1.9 |
| `spawn_near_gold` | nearest gold < 7 | luck | near | 20% | 51.1 | +1.1 |
| `tight_villagers` | perimeter < 253 | luck | near | 20% | 51.1 | +1.1 |
| `spawn_stone_poor` | nearest stone > 20 | luck | far | 20% | 50.9 | +0.9 |
| `spawn_near_food` | nearest food < 7 | luck | near | 20% | 50.8 | +0.8 |
| `spawn_near_stone` | nearest stone < 9 | luck | near | 20% | 50.6 | +0.6 |
| `spawn_food_poor` | nearest food > 15 | luck | far | 21% | 49.4 | −0.6 |
| `spawn_near_enemy` | nearest enemy < 36 | luck | near | 20% | 49.5 | −0.5 |

**Reading the signal:** `spawn_near_ally` is the standout (−5.8, ~3.7σ) — crowding your teammate
starves you both; together with `spawn_isolated` (+2.5) it says *spatial independence from your team
helps*. Resource starvation costs you (gold_poor −2.1); stone is near-noise. Villager scatter
matters (−1.9 scattered / +1.1 tight). `spawn_near_enemy` is ~neutral in aggregate **by
construction** (two enemies spawn near each other symmetrically, so one always wins) — its real
signal is **per-player** (who handles the pressure better), which the By-player view surfaces.

## Architecture / components

1. **Extraction — extract v4.** Extend `utils/replay_quiz/extract.py:extract_match` to compute per
   player: `settle_tc_xy`; nearest-gold / nearest-stone / nearest-food **distances** (from `m.gaia`
   + settle TC); and `vil_perim` (from `p.objects` Villager positions). `m.gaia` and starting
   villager positions are already in the parsed model — no new parser work, only new derived
   fields. Promote `EXTRACT_VERSION` to a **single shared constant** in `extract.py` and import it
   in both `runner.py` and `pipeline/ingester.py` (today each hard-codes `"v3"`); set it to `"v4"`
   to invalidate the parse cache and force a full re-parse.
2. **gamedata accessors** (`utils/classifications/gamedata.py`): `settle_tc_xy(game, pnum)`;
   `spawn_proximity(game, pnum) -> (d_ally, d_enemy, d_any)` across players by team (reusing `_dist`);
   small readers for the stored resource distances + perimeter; and
   `is_valid_luck_game(game)` (map ∈ Nomad set, 6/8 players, balanced winners) that every luck
   trigger calls first so it no-ops on out-of-scope/invalid games.
3. **Contract:** add `category: str = "strategy"` to `Classification`. Luck classifications set
   `category="luck"`. No DB-schema change — category is read from the registry at request time, like
   `title` / `trigger_spec`.
4. **Luck builder** (`utils/classifications/defs/_luck.py`): `make_luck(key, title, metric, side,
   threshold, label, ...)` returns a `Classification` whose `trigger` = `is_valid_luck_game(game)`
   AND the metric is on the correct side of the frozen threshold; whose `factors` expose the raw
   metric value(s); `category="luck"`. One thin def per use case (or a single module exposing all
   11 `CLASSIFICATION`s), registered in `registry.py`. **Luck defs never import the phase-exclusivity
   helpers** — they are non-exclusive by construction. Also register a **`luck_baseline` cohort**
   classification (category `"luck"`, `subtype="baseline"`) whose trigger is *just*
   `is_valid_luck_game(game)` — it fires for **every** player in a valid luck game, so its
   `cls_results` are the full valid-luck cohort: total count = **N** (the denominator for "% of
   games") and win% = 50 (the baseline reference), both overall and per-player. It is rendered as
   the tab's reference row, not as a "factor".
5. **Web** (`bot/web.py`, `bot/web_page.html`): `/api/strategies` returns each use case's
   `category` (from the registry). Add a new **top-level "Luck" tab** rendering the luck use cases
   with **By-factor** and **By-player** views; columns: factor · condition · % of games · win% ·
   **Δ vs 50%**. The existing Strategies tab filters to `category=="strategy"` so luck rows don't
   appear there. (Top-civs / top-players columns are dropped for Luck — luck is civ-independent.)
6. **Populate + sync (gated).** Re-run the local pipeline at v4 (re-parse all ~738 local replays,
   classify incl. the 11 luck use cases, write the local `analysis.db`), verify counts, **then —
   only on explicit user go-ahead — batch-sync to Railway**. Deploying the web changes (push to
   `main`) is **also gated on explicit approval** (global prod rule).

## Win/loss surfacing

- **Denominator & baseline:** the `luck_baseline` cohort gives **N** = valid-luck player-games
  (overall and per-player) and the 50% reference. "% of games" = use-case count / N; Δ = use-case
  win% − 50.
- **By-factor (aggregate):** win% among player-games that triggered the use case vs the 50%
  baseline (Δ), plus count and % of games.
- **By-player:** per-player luck games (from `luck_baseline`) + per-player win% within each luck
  factor — this is where `spawn_near_enemy`'s per-player signal lives. Samples per player are thin;
  the view notes this.

## Out of scope / YAGNI

- No per-map thresholds (frozen global thresholds, calibrated once).
- No "medium" band use cases (only the near / far tails; the ~60% middle is simply unflagged).
- No Elo / skill controls in v1 — raw win% vs the 50% baseline. (A possible later refinement, since
  stronger players may settle better.)
- No `cls_*` schema change.

## Testing

- **Pure-function unit tests:** `gamedata.spawn_proximity` on synthetic players (ally/enemy/any by
  team); `vil_perim`; `settle_tc_xy` fallback; `is_valid_luck_game` (map / player-count /
  winner-balance filter); each luck `trigger` fires correctly on both sides of its threshold and
  no-ops on invalid games; extract v4 derived fields against a small synthetic model or a known
  replay (golden values).
- The calibration script `utils/classifications/_calibrate_luck.py` is **kept** as the
  re-calibration tool (parses the corpus, prints the survey); its parse cache
  `data/.luck_calib.json` is throwaway.
