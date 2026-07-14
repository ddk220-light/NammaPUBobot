#!/usr/bin/env python3
"""Offline calibration report: legacy vs current impact/tag formula.

Compares the pre-July-2026 ("legacy") impact formula against the shared
bot/replay_stats/scoring.py module over a snapshot of parsed matches, and
prints the metrics that drove the recalibration:

  * per-tag fire rates (share of player-games)
  * carries that are reboom-driven (carry with low early eco + high reboom)
  * "Eco carry"/"Boom carry" tags given to below-average early eco (misfires)
  * team-average-impact vs actual winner agreement (sanity: should not drop)
  * carry flips between formulas, with examples

Snapshot input is a JSON file with at least {"player_games": [rs_player_games
rows joined with nothing else]} — produced by any read-only export of the
rs_player_games table (player rows must include the team/winner columns).

Usage:
    python utils/tag_calibration.py --snapshot /path/to/rs_snapshot.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_scoring():
    """Import bot/replay_stats/scoring.py by path so this script doesn't pull in
    the bot package (whose __init__ needs a live DB adapter)."""
    path = ROOT / "bot" / "replay_stats" / "scoring.py"
    spec = importlib.util.spec_from_file_location("rs_scoring_calibration", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Legacy formula (frozen copy of the pre-recalibration implementation) ──
def _avg(rows, key):
    vals = [float(r[key]) for r in rows if r.get(key) is not None]
    return sum(vals) / len(vals) if vals else None


def _std(rows, key):
    vals = [float(r[key]) for r in rows if r.get(key) is not None]
    if len(vals) < 2:
        return 1.0
    mean = sum(vals) / len(vals)
    return max((sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5, 1.0)


def _z(row, rows, key, invert=False):
    if row.get(key) is None:
        return 0.0
    mean = _avg(rows, key)
    if mean is None:
        return 0.0
    val = float(row[key])
    score = (mean - val if invert else val - mean) / _std(rows, key)
    return max(-2.0, min(2.0, score))


def _sc(value):
    return max(0, min(100, round(50 + value * 15)))


def legacy_scores(row, group):
    eco_z = (_z(row, group, "villagers") * 0.65) + (_z(row, group, "vil_pre_castle") * 0.35)
    army_z = (_z(row, group, "military") * 0.65) + (_z(row, group, "mil_pre_castle") * 0.35)
    timing_z = (
        (_z(row, group, "feudal_s", invert=True) * 0.35)
        + (_z(row, group, "castle_s", invert=True) * 0.45)
        + (_z(row, group, "imperial_s", invert=True) * 0.20)
    )
    early_eco_z = _z(row, group, "vil_pre_castle")
    recovery_z = _z(row, group, "villagers") - early_eco_z  # unclamped, by design of the legacy formula
    s = {
        "eco": _sc(eco_z), "army": _sc(army_z), "timing": _sc(timing_z),
        "early_eco": _sc(early_eco_z), "early_army": _sc(_z(row, group, "mil_pre_castle")),
        "reboom": _sc(recovery_z),
    }
    s["impact"] = round(s["army"] * 0.34 + s["eco"] * 0.30 + s["timing"] * 0.18 + s["reboom"] * 0.18)
    return s


def legacy_tags(s):
    tags = []
    if s["army"] >= 68 and s["eco"] < 52:
        tags.append("all_in_pressure")
    elif s["army"] >= 66:
        tags.append("map_pressure")
    if s["eco"] >= 64 and s["early_eco"] >= 56 and s["early_army"] <= 55 and s["impact"] >= 58:
        tags.append("boom_carry")
    elif s["eco"] >= 66:
        tags.append("eco_carry")
    if s["timing"] >= 66:
        tags.append("age_up_tempo")
    if s["reboom"] >= 66:
        tags.append("reboom")
    if s["impact"] >= 72:
        tags.append("high_impact")
    return tags


# ── Report ────────────────────────────────────────────────────────────────
def evaluate(name, by_match, score_fn, tags_fn):
    tag_counts = Counter()
    n_players = 0
    carry_reboom = carries = 0
    carry_tag_low_early = carry_tag_total = 0
    win_agree = win_total = 0
    carry_by_team = {}

    for mid, group in by_match.items():
        if len(group) < 2:
            continue
        scored = [(row, score_fn(row, group)) for row in group]
        for _row, s in scored:
            n_players += 1
            for key in tags_fn(s):
                tag_counts[key] += 1
                if key in ("eco_carry", "boom_carry"):
                    carry_tag_total += 1
                    if s["early_eco"] < 50:
                        carry_tag_low_early += 1
        teams = defaultdict(list)
        for row, s in scored:
            if row.get("team") is not None:
                teams[str(row["team"])].append((row, s))
        if len(teams) != 2:
            continue
        tvals = {}
        for tkey, members in teams.items():
            tvals[tkey] = sum(m[1]["impact"] for m in members) / len(members)
            carry = max(members, key=lambda m: (m[1]["impact"], m[1]["army"], m[1]["eco"]))
            carry_by_team[(mid, tkey)] = (carry[0].get("identity"), carry[1])
            carries += 1
            if carry[1]["reboom"] >= 66 and carry[1]["early_eco"] < 50:
                carry_reboom += 1
        winners = {k: any(m[0].get("winner") for m in v) for k, v in teams.items()}
        if sum(winners.values()) == 1:
            win_total += 1
            wt = next(k for k, v in winners.items() if v)
            if tvals[wt] >= max(tvals.values()):
                win_agree += 1

    print(f"\n=== {name} ===")
    print(f"player-games: {n_players}, team carries: {carries}")
    print("tag rates:")
    for key, c in tag_counts.most_common():
        print(f"  {key:16s} {c:6d}  ({100 * c / n_players:5.1f}%)")
    print(f"carry-tag with below-avg early eco: {carry_tag_low_early}/{carry_tag_total} "
          f"({100 * carry_tag_low_early / max(carry_tag_total, 1):.1f}%)")
    print(f"reboom-driven team carries:         {carry_reboom}/{carries} "
          f"({100 * carry_reboom / max(carries, 1):.1f}%)")
    print(f"higher-avg-impact team won:         {win_agree}/{win_total} "
          f"({100 * win_agree / max(win_total, 1):.1f}%)")
    return carry_by_team


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", required=True, help="JSON file with a player_games list")
    ap.add_argument("--flips", type=int, default=10, help="how many carry-flip examples to print")
    args = ap.parse_args()

    snap = json.loads(Path(args.snapshot).read_text())
    games = snap["player_games"] if isinstance(snap, dict) else snap
    by_match = defaultdict(list)
    for g in games:
        by_match[g["aoe2_match_id"]].append(g)

    scoring = _load_scoring()
    legacy_carries = evaluate("legacy formula", by_match, legacy_scores, legacy_tags)
    new_carries = evaluate("current formula (bot/replay_stats/scoring.py)", by_match,
                           scoring.impact_scores,
                           lambda s: [t["key"] for t in scoring.derive_impact_tags(s)])

    flips = [(k, legacy_carries[k], new_carries[k])
             for k in legacy_carries
             if k in new_carries and legacy_carries[k][0] != new_carries[k][0]]
    # Most informative first: big reboom inflation on the legacy pick. (All-50
    # ties from old matches with unreliable replay data land at the bottom.)
    flips.sort(key=lambda f: -(f[1][1]["reboom"] - f[1][1]["early_eco"]))
    print(f"\ncarry flips legacy -> current: {len(flips)}/{len(legacy_carries)} "
          f"({100 * len(flips) / max(len(legacy_carries), 1):.1f}%)")
    for (mid, team), (old_who, old_s), (new_who, new_s) in flips[:args.flips]:
        print(f"  match {mid} team {team}: {old_who} (reboom {old_s['reboom']}, early_eco {old_s['early_eco']})"
              f" -> {new_who} (army {new_s['army']}, eco {new_s['eco']}, early_eco {new_s['early_eco']})")
    if not flips:
        print("  (none)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
