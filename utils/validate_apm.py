#!/usr/bin/env python3
"""Validation sample for the per-minute eAPM pipeline. Re-parses the N most recent
cached replays, checks the mgz parity invariant, and writes charts to disk.

NOT a backfill: writes no database rows. See
docs/superpowers/specs/2026-07-28-eapm-pipeline-design.md.

Usage:
    PYTHONPATH=. python3 utils/validate_apm.py [--limit 5] [--out /tmp/apm]
"""
import argparse
import asyncio
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# bot/__init__.py calls db.ensure_table(...) at import time (idempotent schema checks
# against the live tables), which requires core.database.db.connect() to have already
# run -- the same sequencing PUBobot2.py itself uses before `import bot`. Without this,
# `from bot.replay_stats...` below raises AttributeError: 'Adapter' object has no
# attribute 'loop'. Not a pipeline change -- just the standalone-script equivalent of
# the bootstrap every entry point into bot.* already requires.
from core import database                                  # noqa: E402
loop = asyncio.get_event_loop()
loop.run_until_complete(database.db.connect())

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
