#!/usr/bin/env python3
"""Persona assignment report over a snapshot — sanity-check the taxonomy.

Recomputes per-player aggregates (same shape bot/web.py feeds derive_persona)
from a rs_player_games snapshot and prints every player's persona with the
axis evidence, plus the style/role distribution. Use it whenever persona
thresholds or scoring weights change.

Usage:
    python utils/persona_calibration.py --snapshot /path/to/rs_snapshot.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    path = ROOT / "bot" / "replay_stats" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"persona_cal_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--min-games", type=int, default=30)
    args = ap.parse_args()

    scoring = _load("scoring")
    persona = _load("persona")

    snap = json.loads(Path(args.snapshot).read_text())
    by_match = defaultdict(list)
    for g in snap["player_games"]:
        by_match[g["aoe2_match_id"]].append(g)

    agg = defaultdict(lambda: {"n": 0, "army": 0.0, "eco": 0.0, "timing": 0.0, "reboom": 0.0,
                               "impacts": [], "carry": 0, "tags": Counter()})
    for _mid, group in by_match.items():
        if len(group) < 2:
            continue
        teams = defaultdict(list)
        scored = []
        for row in group:
            s = scoring.impact_scores(row, group)
            scored.append((row, s))
            if row.get("team") is not None:
                teams[str(row["team"])].append((row, s))
        tops = {id(max(m, key=lambda x: (x[1]["impact"], x[1]["army"], x[1]["eco"]))[0]) for m in teams.values()}
        for row, s in scored:
            # Key on user/profile id, not display name — two accounts sharing
            # an in-game identity must not merge into one persona bucket.
            key = row.get("user_id") or row.get("profile_id")
            name = row.get("identity")
            if key is None or not name:
                continue
            a = agg[(key, name)]
            a["n"] += 1
            a["army"] += s["army"]
            a["eco"] += s["eco"]
            a["timing"] += s["timing"]
            a["reboom"] += s["reboom"]
            a["impacts"].append(s["impact"])
            if id(row) in tops:
                a["carry"] += 1
            for t in scoring.derive_impact_tags(s):
                a["tags"][scoring.TAG_NAMES[t["key"]]["stored"]] += 1

    combos = Counter()
    print(f"{'player':<20} {'g':>4}  persona")
    for (_key, name), a in sorted(agg.items(), key=lambda kv: -kv[1]["n"]):
        n = a["n"]
        if n < args.min_games:
            continue
        impacts = a["impacts"]
        mean = sum(impacts) / n
        sd = (sum((x - mean) ** 2 for x in impacts) / n) ** 0.5
        stats = {
            "matches": n,
            "avg_army": a["army"] / n, "avg_eco": a["eco"] / n,
            "avg_timing": a["timing"] / n, "avg_recovery": a["reboom"] / n,
            "impact_sd": sd, "carry_rate": 100.0 * a["carry"] / n,
            "tag_rates": {k: 100.0 * v / n for k, v in a["tags"].items()},
        }
        p = persona.derive_persona(stats)
        combos[(p["style"], p["role"])] += 1
        print(f"{name[:20]:<20} {n:>4}  {p['name']} · {p['epithet']}  [{p['style']}/{p['role']}]")
    print("\nstyle/role distribution:")
    for (style, role), c in combos.most_common():
        print(f"  {style:<11} {role:<9} {c}")


if __name__ == "__main__":
    main()
