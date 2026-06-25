#!/usr/bin/env python3
"""Offline classification runner. For a date window: list matches (MySQL) -> ensure each
replay is cached in data/replays/ (download if missing) -> parse once (cached) -> run every
registered classification -> upsert results to MySQL cls_* tables -> print a report.

Run from the repo root with the vendored mgz fork importable:
    PYTHONPATH=.replay_scratch python -m utils.classifications.runner --days 90

Replays are NEVER deleted (kept for ongoing analysis)."""
import argparse
import asyncio
import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, ".replay_scratch"))   # vendored mgz fork

from utils.db_helpers import create_pool                      # noqa: E402
from utils.classifications import dbio, shape                 # noqa: E402
from utils.classifications.registry import REGISTRY           # noqa: E402

CACHE_DIR = os.path.join(_ROOT, "data", ".replay_extract_cache")
REPLAY_DIR = os.path.join(_ROOT, "data", "replays")
EXTRACT_VERSION = "v3"     # bump to invalidate the parse cache when extract output changes
                           # v2: adds players[].tc_build_s (TC build timestamps) for window logic
                           # v3: adds players[].start_tc_xy / tc_builds / castle_builds (positions)
                           #     for forward/safe castle placement


def _cache_path(aoe2_match_id):
    return os.path.join(CACHE_DIR, "{}.{}.json".format(aoe2_match_id, EXTRACT_VERSION))


async def _ensure_replay(aoe2_match_id, no_download=False):
    """Return (path, downloaded). Uses the cached .aoe2record if present (downloaded=False);
    otherwise downloads it unless no_download is set. (None, False) if unavailable. Never deletes."""
    path = os.path.join(REPLAY_DIR, "{}.aoe2record".format(aoe2_match_id))
    if os.path.exists(path):
        return path, False
    if no_download:
        return None, False
    from utils.replay_quiz import download as dl
    pids = await dl.resolve_profile_ids(aoe2_match_id)
    for pid in pids:
        got, status = await dl.download_replay(aoe2_match_id, pid)
        if got and os.path.exists(got):
            return got, True
    return None, False


def _extract_cached(path, aoe2_match_id, resolved, date_map):
    """Parse once; cache the JSON-serializable extract output keyed by id + EXTRACT_VERSION."""
    cp = _cache_path(aoe2_match_id)
    if os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            return json.load(f)
    from utils.replay_quiz.extract import extract_match
    data = extract_match(path, resolved, date_map)
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


async def run(days, only_key=None, no_download=False):
    pool = await create_pool()
    if pool is None:
        print("No DB pool (check config.cfg DB_URI).", file=sys.stderr)
        return 1
    from utils.replay_quiz.extract import load_resolved, load_date_map
    resolved, date_map = load_resolved(), load_date_map()
    classifications = [c for c in REGISTRY.values()
                       if only_key is None or c.key == only_key]

    try:
        await dbio.ensure_tables(pool)
        for c in classifications:
            await dbio.upsert_classification(pool, c)
            await dbio.wipe_results(pool, c.key)   # full-window rebuild: clear stale rows so a match
            #                                        that no longer matches leaves nothing behind

        matches = await dbio.window_matches(pool, days)
        print("window: {} matches in last {}d across {} classification(s)".format(
            len(matches), days, len(classifications)))
        stats = {c.key: 0 for c in classifications}
        player_totals = {}   # identity -> [games, wins, losses] over ALL scanned player-games
        scanned = fetched = failed = 0

        for m in matches:
            mid = m["aoe2_match_id"]
            played_at = m["played_at"]
            path, downloaded = await _ensure_replay(mid, no_download)
            if not path:
                failed += 1
                continue
            if downloaded:
                fetched += 1
            try:
                game = _extract_cached(path, mid, resolved, date_map)
            except Exception as e:                       # corrupt/unsupported replay -> skip
                failed += 1
                print("  parse failed {}: {}".format(mid, e))
                continue
            scanned += 1
            for p in game.get("players", []):
                ident = p.get("identity") or "?"
                t = player_totals.setdefault(ident, [0, 0, 0])
                t[0] += 1
                if p.get("winner") in (1, True):
                    t[1] += 1
                elif p.get("winner") in (0, False):
                    t[2] += 1
            for c in classifications:
                result_rows, metric_rows = [], []
                for p in game.get("players", []):
                    pnum = p["player_number"]
                    if not c.trigger(game, pnum):
                        continue
                    result_rows.append(shape.result_row(c.key, mid, p, played_at))
                    metric_rows.extend(shape.metric_rows(c.key, mid, pnum, c.factors(game, pnum)))
                if result_rows:
                    await dbio.upsert_results(pool, c.key, mid, result_rows, metric_rows)
                    stats[c.key] += len(result_rows)

        if only_key is None:   # full run -> rebuild the per-player corpus totals
            await dbio.write_player_totals(pool, {k: tuple(v) for k, v in player_totals.items()})
        print("scanned={} newly_downloaded={} failed/unavailable={}".format(
            scanned, fetched, failed))
        for k, n in stats.items():
            print("  {}: {} matched player-games".format(k, n))
        return 0
    finally:
        pool.close()
        await pool.wait_closed()


def main():
    ap = argparse.ArgumentParser(description="Offline classification runner")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--key", default=None, help="run only this classification key")
    ap.add_argument("--no-download", action="store_true",
                    help="only use replays already cached in data/replays/")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.days, args.key, args.no_download)))


if __name__ == "__main__":
    main()
