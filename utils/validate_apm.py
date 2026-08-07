#!/usr/bin/env python3
"""Validation sample for the per-minute eAPM pipeline. Re-parses the N most recent
cached replays, checks the mgz parity invariant, and writes charts to disk.

NOT a backfill: writes no database rows, and opens no database connection at all -- see
the import shim below. See docs/superpowers/specs/2026-07-28-eapm-pipeline-design.md.

Usage:
    PYTHONPATH=. python3 utils/validate_apm.py [--limit 5] [--out /tmp/apm]
"""
import argparse
import glob
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# `from nammaoe2bot.ingest...` below would normally import the `bot` package first, which runs
# bot/__init__.py -- pulling in ~10 feature modules purely for their ensure_table() side effects
# (schema-DDL-capable CREATE TABLE / ALTER TABLE ADD COLUMN checks) against a live
# nammaoe2bot.runtime.database.db handle. This script is validation-only and must never open a database
# connection, so instead of connecting for real (the way PUBobot2.py's own boot sequence does)
# we pre-register minimal fakes for nammaoe2bot.runtime.console / nammaoe2bot.runtime.database / bot in sys.modules, the same
# shim tests/conftest.py uses so unit tests can import bot.* parsers with no live DB. This is a
# trimmed-down copy: only what apm_series + render_apm_curve's import chain actually touches
# (conftest.py additionally fakes nammaoe2bot.runtime.config and aiohttp, which neither needs).
#
# Pre-registering `bot` with an explicit __path__ means bot/__init__.py itself never runs --
# only the real nammaoe2bot.ingest package (and the apm_query / chart submodules we need) do -- so
# every ensure_table() call the import chain makes hits the no-op below instead of a live schema
# check. Neither apm_series (pure) nor render_apm_curve (pure rendering) touches the DB at all.


class _NullLog:
    """Swallows every nammaoe2bot.runtime.console.log call (nammaoe2bot.ingest.jobs/store log through it)."""

    def __getattr__(self, _name):
        return lambda *_a, **_k: None


_fake_core_console = types.ModuleType("nammaoe2bot.runtime.console")
_fake_core_console.log = _NullLog()
_fake_core_console.alive = True
sys.modules["nammaoe2bot.runtime.console"] = _fake_core_console


class _NoDB:
    """Stands in for nammaoe2bot.runtime.database.db. `ensure_table` is the only method the nammaoe2bot.ingest
    import chain calls at module scope (schema declarations), so it's a genuine no-op here. Any
    other attribute access means something is trying to touch a real database -- which this
    validation-only script must never do -- so it raises immediately instead of connecting."""

    class types:
        int = "BIGINT"
        bool = "TINYINT(1)"
        str = "VARCHAR(191)"
        text = "VARCHAR(2000)"
        float = "FLOAT"
        dict = "MEDIUMTEXT"

    def ensure_table(self, *_a, **_k):
        return None

    def __getattr__(self, name):
        raise RuntimeError(f"validate_apm.py must never touch a live database (db.{name})")


_fake_core_database = types.ModuleType("nammaoe2bot.runtime.database")
_fake_core_database.db = _NoDB()
sys.modules["nammaoe2bot.runtime.database"] = _fake_core_database

_fake_bot = types.ModuleType("bot")
_fake_bot.__path__ = [os.path.join(ROOT, "bot")]
sys.modules["bot"] = _fake_bot

from nammaoe2bot.ingest.apm_query import apm_series          # noqa: E402
from nammaoe2bot.ingest.chart import render_apm_curve        # noqa: E402
from utils.replay.extract import extract_match   # noqa: E402


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

    failures = 0
    checks_run = 0
    for path in paths:
        name = os.path.basename(path)
        try:
            result = extract_match(path, {})
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
            checks_run += 1
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

    # A run that checked zero players proves nothing (e.g. every file parsed but came back
    # with an empty player list) -- that is itself a failure, not a vacuous pass.
    print()
    if checks_run == 0:
        failures += 1
        print(f"0 parity checks ran across {len(paths)} file(s) -- nothing was verified.")
    else:
        print(f"{checks_run} parity check(s) ran, {failures} failure(s).")
    print(f"FAILURES: {failures}" if failures else "All parity checks passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
