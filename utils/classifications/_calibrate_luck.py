"""Calibration pass for the 'luck' (map/spawn) use cases. Parses every on-disk replay (parallel),
and for each player-game computes settle-TC proximity + distance-to-nearest resources + villager
spread, then reports each metric's distribution, the 1.5*min / 0.5*max thresholds the user proposed
(plus robust 1.5*p1 / 0.5*p99 variants), and the WIN% in each near/far bucket. Read-only, offline.

Run:  PYTHONPATH=.replay_scratch python utils/classifications/_calibrate_luck.py
"""
import glob
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, ".replay_scratch"))  # vendored mgz (workers re-import)

import mgz.model  # noqa: E402

GOLD = {"Gold Mine"}
STONE = {"Stone Mine"}
FOOD = {"Wild Boar", "Deer", "Ibex", "Pig"}      # huntable food
BOAR = {"Wild Boar", "Pig"}                       # heavy-food huntables only
R_CTX = 12                                         # radius for the secondary count-in-radius context


def _d(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _first_tc(m, pnum):
    best = None
    for a in m.actions:
        if a.player and a.player.number == pnum and a.type.name == "BUILD" \
                and (a.payload or {}).get("building") == "Town Center" and a.position is not None:
            ts = a.timestamp.total_seconds() if a.timestamp else None
            if ts is not None and (best is None or ts < best[0]):
                best = (ts, (a.position.x, a.position.y))
    return best[1] if best else None


def worker(path):
    """-> list of per-player-game metric dicts (Nomad team games only), or [] on skip/fail."""
    try:
        with open(path, "rb") as fh:
            m = mgz.model.parse_match(fh)
    except Exception:  # noqa: BLE001
        return []
    if getattr(m.map, "name", "") not in ("Land Nomad", "Nomad") or len(m.players) not in (6, 8):
        return []
    n_players = len(m.players)
    m_winners = sum(1 for p in m.players if p.winner)
    tcs = {p.number: _first_tc(m, p.number) for p in m.players}
    teams = {p.number: frozenset(p.team_id) if isinstance(p.team_id, (list, set, tuple)) else p.team_id
             for p in m.players}
    gaia = m.gaia or []
    gold_pts = [(o.position.x, o.position.y) for o in gaia if o.position and getattr(o, "name", "") in GOLD]
    stone_pts = [(o.position.x, o.position.y) for o in gaia if o.position and getattr(o, "name", "") in STONE]
    food_pts = [(o.position.x, o.position.y) for o in gaia if o.position and getattr(o, "name", "") in FOOD]
    boar_pts = [(o.position.x, o.position.y) for o in gaia if o.position and getattr(o, "name", "") in BOAR]

    def nearest(tc, pts):
        return min((_d(tc, q) for q in pts), default=None)

    def count_within(tc, pts, r):
        return sum(1 for q in pts if _d(tc, q) <= r)

    mid = int(os.path.basename(path).split(".")[0])
    out = []
    for p in m.players:
        tc = tcs.get(p.number)
        if tc is None or p.winner is None:
            continue
        ally = [_d(tc, tcs[q]) for q in tcs if q != p.number and tcs[q] and teams[q] == teams[p.number]]
        enemy = [_d(tc, tcs[q]) for q in tcs if q != p.number and tcs[q] and teams[q] != teams[p.number]]
        alld = [_d(tc, tcs[q]) for q in tcs if q != p.number and tcs[q]]
        vils = [(o.position.x, o.position.y) for o in (p.objects or [])
                if getattr(o, "name", "") == "Villager" and o.position]
        perim = sum(_d(vils[i], vils[j]) for i in range(len(vils)) for j in range(i + 1, len(vils))) \
            if len(vils) >= 2 else None
        out.append(dict(
            _mid=mid, _np=n_players, _mw=m_winners,
            win=1 if p.winner else 0,
            d_ally=min(ally) if ally else None,
            d_enemy=min(enemy) if enemy else None,
            d_any=min(alld) if alld else None,
            d_gold=nearest(tc, gold_pts), d_stone=nearest(tc, stone_pts),
            d_food=nearest(tc, food_pts), d_boar=nearest(tc, boar_pts),
            vil_perim=perim,
            c_gold=count_within(tc, gold_pts, R_CTX), c_stone=count_within(tc, stone_pts, R_CTX),
            c_food=count_within(tc, food_pts, R_CTX),
        ))
    return out


def pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    i = q / 100 * (len(s) - 1)
    lo, hi = int(math.floor(i)), int(math.ceil(i))
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (i - lo)


def winrate(rows, key, lo=None, hi=None):
    """win% over rows whose metric is in [lo, hi) (None bound = open)."""
    sel = [r for r in rows if r[key] is not None
           and (lo is None or r[key] >= lo) and (hi is None or r[key] < hi)]
    if not sel:
        return (0, None)
    return (len(sel), 100 * sum(r["win"] for r in sel) / len(sel))


def report_distance(rows, key, label, lower_is_near=True):
    xs = [r[key] for r in rows if r[key] is not None]
    if not xs:
        print(f"\n{label}: no data")
        return
    mn, mx = min(xs), max(xs)
    p1, p99 = pct(xs, 1), pct(xs, 99)
    near_raw, far_raw = 1.5 * mn, 0.5 * mx
    near_rob, far_rob = 1.5 * p1, 0.5 * p99
    print(f"\n=== {label}  (n={len(xs)}) ===")
    print("  min={:.1f}  p1={:.1f}  p5={:.1f}  p25={:.1f}  p50={:.1f}  p75={:.1f}  p95={:.1f}  p99={:.1f}  max={:.1f}"
          .format(mn, p1, pct(xs, 5), pct(xs, 25), pct(xs, 50), pct(xs, 75), pct(xs, 95), p99, mx))
    print("  THRESHOLDS  near=1.5*min={:.1f} (robust 1.5*p1={:.1f})   far=0.5*max={:.1f} (robust 0.5*p99={:.1f})"
          .format(near_raw, near_rob, far_raw, far_rob))
    for tag, thr in (("near(raw)", near_raw), ("near(rob)", near_rob)):
        n, wr = winrate(rows, key, hi=thr)
        print("   {:9s} {:5s} <{:6.1f}:  n={:5d} ({:4.1f}%)  win%={}".format(
            tag, "", thr, n, 100 * n / len(xs), "n/a" if wr is None else f"{wr:.1f}"))
    for tag, thr in (("far(raw)", far_raw), ("far(rob)", far_rob)):
        n, wr = winrate(rows, key, lo=thr)
        print("   {:9s} {:5s} >{:6.1f}:  n={:5d} ({:4.1f}%)  win%={}".format(
            tag, "", thr, n, 100 * n / len(xs), "n/a" if wr is None else f"{wr:.1f}"))


def _load_or_parse():
    """Parse all replays once, cache the per-player-game rows to JSON so threshold iteration is
    instant on re-run. Delete data/.luck_calib.json to force a re-parse."""
    import json
    cache = os.path.join(ROOT, "data", ".luck_calib.json")
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            d = json.load(f)
        print(f"loaded cached calibration rows: {len(d['rows'])} player-games, {d['nmatch']} matches")
        return d["rows"], d["nmatch"]
    paths = sorted(glob.glob(os.path.join(ROOT, "data", "replays", "*.aoe2record")))
    print(f"parsing {len(paths)} replays on {min(20, os.cpu_count() or 4)} workers ...")
    rows, nmatch = [], 0
    with ProcessPoolExecutor(max_workers=min(20, os.cpu_count() or 4)) as ex:
        for res in ex.map(worker, paths, chunksize=4):
            if res:
                nmatch += 1
                rows.extend(res)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "nmatch": nmatch}, f)
    return rows, nmatch


# Each proposed use case: (key, metric, side, threshold-rule). side 'lo' = trigger when metric BELOW
# thr (near/tight); 'hi' = trigger when ABOVE thr (far/poor/scattered). Rule is computed per metric.
def thr_lo_robust(xs):   # near/tight: 1.5 * p1  (robust against single-game outliers)
    return 1.5 * pct(xs, 1)


def thr_hi_robust(xs):   # isolated/scattered: 0.5 * p99
    return 0.5 * pct(xs, 99)


def thr_hi_tail(xs):     # resource "poor": genuine far tail = p90
    return pct(xs, 90)


# Each use case: (key, metric, side, target_pct-of-games-in-bucket). side 'lo' = near/tight (metric
# below thr); 'hi' = far/poor/isolated/scattered (metric above thr). Threshold is the percentile
# that lands the bucket at ~target_pct, so every bucket sits in the user's 15-25% window.
SPECS = [
    ("spawn_near_enemy", "d_enemy", "lo", 20),
    ("spawn_near_ally", "d_ally", "lo", 20),
    ("spawn_isolated", "d_any", "hi", 25),
    ("spawn_near_gold", "d_gold", "lo", 20),
    ("spawn_gold_poor", "d_gold", "hi", 20),
    ("spawn_near_stone", "d_stone", "lo", 20),
    ("spawn_stone_poor", "d_stone", "hi", 20),
    ("spawn_near_food", "d_food", "lo", 20),
    ("spawn_food_poor", "d_food", "hi", 20),
    ("tight_villagers", "vil_perim", "lo", 20),
    ("scattered_villagers", "vil_perim", "hi", 20),
]


def main():
    raw_rows, nmatch = _load_or_parse()
    import collections
    bym = collections.defaultdict(list)
    for r in raw_rows:
        bym[r["_mid"]].append(r)
    # Valid match: 6 or 8 players AND a balanced winner count (winners == half the players). Drops
    # matches with no recorded winner / partial labels. Then keep TC-having player-games from those.
    rows, dropped = [], 0
    for rs in bym.values():
        np_, mw = rs[0]["_np"], rs[0]["_mw"]
        if np_ in (6, 8) and mw * 2 == np_:
            rows.extend(rs)
        else:
            dropped += 1
    base_n = len(rows)
    base_wr = 100 * sum(r["win"] for r in rows) / base_n if base_n else 0
    per_match = collections.Counter(rs[0]["_mw"] for rs in bym.values())
    print(f"\nNomad 6/8p games parsed={nmatch}  winners-per-match: {dict(sorted(per_match.items()))}")
    print(f"  -> dropped {dropped} unbalanced/no-winner; CLEAN matches={nmatch-dropped} "
          f"player-games={base_n}  baseline win%={base_wr:.2f}")

    print("\n================ LUCK USE-CASE SURVEY (baseline {:.1f}%, target 15-25%/bucket) ===========".format(base_wr))
    print("  {:20s} {:18s} {:>7s} {:>6s} {:>7s} {:>15s}".format(
        "use_case", "rule(tiles)", "thr", "n", "%games", "win% (vs base)"))
    for uc, metric, side, target in SPECS:
        xs = [r[metric] for r in rows if r[metric] is not None]
        if side == "lo":
            thr = pct(xs, target)
            n, wr = winrate(rows, metric, hi=thr)
            rule_str = f"{metric} < {thr:.0f}"
        else:
            thr = pct(xs, 100 - target)
            n, wr = winrate(rows, metric, lo=thr)
            rule_str = f"{metric} > {thr:.0f}"
        delta = (wr - base_wr) if wr is not None else None
        print("  {:20s} {:18s} {:7.1f} {:6d} {:6.1f} {:>15s}".format(
            uc, rule_str, thr, n, 100 * n / len(xs),
            "n/a" if wr is None else f"{wr:.1f} ({delta:+.1f})"))


if __name__ == "__main__":
    main()
